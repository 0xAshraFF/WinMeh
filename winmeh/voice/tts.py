"""Text-to-speech through Windows' built-in SAPI voices: zero download, starts instantly.

Runs on its own thread (SAPI is COM, so it needs its own apartment).
"""

from __future__ import annotations

import queue
import re
import threading

from ..config import IS_WINDOWS

SVSF_ASYNC, SVSF_PURGE = 1, 2


class TTS:
    """Windows: SAPI. Linux: speech-dispatcher (spd-say) or espeak-ng, if installed."""

    def __init__(self):
        import shutil
        self.enabled = True
        self._linux_cmd = None
        if not IS_WINDOWS:
            if shutil.which("spd-say"):
                self._linux_cmd = ["spd-say", "-w"]
            elif shutil.which("espeak-ng") or shutil.which("espeak"):
                self._linux_cmd = [shutil.which("espeak-ng") or shutil.which("espeak")]
        self.available = IS_WINDOWS or self._linux_cmd is not None
        self._q: queue.Queue = queue.Queue()
        self._proc = None
        self.slow = False
        if self.available:
            threading.Thread(target=self._loop if IS_WINDOWS else self._linux_loop, daemon=True, name="tts").start()

    def _linux_loop(self) -> None:
        import subprocess
        while True:
            cmd, text = self._q.get()
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
            if cmd == "say":
                rate = (["-r", "-40"] if "spd-say" in self._linux_cmd[0] else ["-s", "130"]) if self.slow else []
                try:
                    self._proc = subprocess.Popen(self._linux_cmd + rate + [text], stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.DEVNULL)
                except OSError:
                    pass

    def say(self, text: str) -> None:
        if self.enabled and self.available and text:
            self._q.put(("say", _clean(text)))

    def stop(self) -> None:
        if self.available:
            self._q.put(("stop", ""))

    def _loop(self) -> None:
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voice.Rate = 1
        except Exception:
            self.available = False
            return
        while True:
            cmd, text = self._q.get()
            try:
                if cmd == "stop":
                    voice.Speak("", SVSF_ASYNC | SVSF_PURGE)
                else:
                    voice.Rate = -3 if self.slow else 1          # accessibility mode speaks slower
                    voice.Speak(text, SVSF_ASYNC | SVSF_PURGE)   # new reply interrupts old one
            except Exception:
                pass


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"https?://\S+", "a link", text)
    return re.sub(r"\s+", " ", text).strip()[:600]
