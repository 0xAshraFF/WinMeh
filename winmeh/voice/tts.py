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
    def __init__(self):
        self.enabled = True
        self.available = IS_WINDOWS
        self._q: queue.Queue = queue.Queue()
        if self.available:
            threading.Thread(target=self._loop, daemon=True, name="tts").start()

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
                    voice.Speak(text, SVSF_ASYNC | SVSF_PURGE)   # new reply interrupts old one
            except Exception:
                pass


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"https?://\S+", "a link", text)
    return re.sub(r"\s+", " ", text).strip()[:600]
