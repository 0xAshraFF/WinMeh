"""System-wide hotkeys via Win32 RegisterHotKey (no keyboard hook, no admin)."""

from __future__ import annotations

import threading
from typing import Callable

from ..config import IS_WINDOWS

MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
VK = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, **{f"f{i}": 0x6F + i for i in range(1, 13)}}
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


def parse(combo: str) -> tuple[int, int]:
    mods, vk = 0, 0
    for part in combo.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif part in VK:
            vk = VK[part]
        elif len(part) == 1:
            vk = ord(part.upper())
    if not vk:
        raise ValueError(f"bad hotkey: {combo}")
    return mods | MOD_NOREPEAT, vk


class Hotkeys:
    def __init__(self):
        self.bindings: list[tuple[str, Callable[[], None]]] = []
        self.failed: list[str] = []
        self._tid = 0

    def add(self, combo: str, fn: Callable[[], None]) -> None:
        self.bindings.append((combo, fn))

    def start(self) -> None:
        if IS_WINDOWS and self.bindings:
            threading.Thread(target=self._loop, daemon=True, name="hotkeys").start()

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        for i, (combo, _) in enumerate(self.bindings, start=1):
            mods, vk = parse(combo)
            if not user32.RegisterHotKey(None, i, mods, vk):
                self.failed.append(combo)      # already taken by another app
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and 1 <= msg.wParam <= len(self.bindings):
                try:
                    self.bindings[msg.wParam - 1][1]()
                except Exception:
                    pass
        for i in range(1, len(self.bindings) + 1):
            user32.UnregisterHotKey(None, i)

    def stop(self) -> None:
        if IS_WINDOWS and self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
