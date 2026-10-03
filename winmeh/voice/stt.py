"""Speech-to-text: mic -> energy-based endpointing -> faster-whisper (tiny.en, int8, CPU).

Latency budget on a typical 4-core laptop: model already loaded at startup,
~0.2-0.5 s to transcribe a 3 s command with tiny.en, beam_size=1.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

SAMPLE_RATE = 16000
BLOCK = 480                  # 30 ms


class STT:
    def __init__(self, model_size: str = "tiny.en"):
        self.model_size = model_size
        self.model = None
        self.error: str | None = None
        self._stop = threading.Event()
        self.listening = False

    def load(self) -> bool:
        try:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8",
                                      cpu_threads=0, download_root=None)
            # warm-up so the first real command doesn't pay graph-init cost
            import numpy as np
            list(self.model.transcribe(np.zeros(SAMPLE_RATE // 2, dtype="float32"), beam_size=1, language="en")[0])
            return True
        except Exception as e:  # missing package, no network on first download, etc.
            self.error = f"{type(e).__name__}: {e}"
            return False

    def stop(self) -> None:
        """Stop recording now and transcribe what we have (push-to-talk release)."""
        self._stop.set()

    def listen(self, on_level: Callable[[float], None] | None = None, max_seconds: float = 12.0,
               silence_ms: int = 700) -> str:
        if self.model is None:
            raise RuntimeError(self.error or "speech model not loaded")
        import numpy as np
        import sounddevice as sd

        q: queue.Queue = queue.Queue()
        self._stop.clear()
        self.listening = True

        def cb(indata, frames, t, status):
            q.put(indata[:, 0].copy())

        chunks, speech_started, silent_for, noise = [], False, 0, None
        start = time.time()
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK, callback=cb):
                while not self._stop.is_set() and time.time() - start < max_seconds:
                    try:
                        block = q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    chunks.append(block)
                    rms = float(np.sqrt(np.mean(block ** 2)) + 1e-9)
                    if on_level:
                        on_level(min(1.0, rms * 12))
                    # adaptive noise floor from the first ~150 ms
                    if noise is None and len(chunks) >= 5:
                        noise = float(np.median([np.sqrt(np.mean(c ** 2)) for c in chunks]))
                    thresh = max(0.012, (noise or 0.01) * 3.0)
                    if rms > thresh:
                        speech_started, silent_for = True, 0
                    elif speech_started:
                        silent_for += 30
                        if silent_for >= silence_ms:
                            break
                    elif time.time() - start > 5:        # nobody spoke
                        break
        finally:
            self.listening = False
        if not speech_started or not chunks:
            return ""
        audio = np.concatenate(chunks)
        segments, _ = self.model.transcribe(audio, beam_size=1, language="en", vad_filter=False,
                                            without_timestamps=True, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()
