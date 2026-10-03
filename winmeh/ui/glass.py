"""Native Windows blur-behind for a frameless translucent Qt window.

Windows 10 1803+/11: SetWindowCompositionAttribute with ACCENT_ENABLE_ACRYLICBLURBEHIND.
Windows 11: also ask DWM for rounded corners. Everything is best-effort; if it
fails the window still works with Qt's own semi-transparent painting.
"""

from __future__ import annotations

import ctypes
import sys

ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


def apply(hwnd: int, tint_abgr: int = 0x40201810, acrylic: bool = True) -> bool:
    """tint is 0xAABBGGRR. Returns True if blur was enabled."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        accent = ACCENT_POLICY(ACCENT_ENABLE_ACRYLICBLURBEHIND if acrylic else ACCENT_ENABLE_BLURBEHIND,
                               2, tint_abgr, 0)
        data = WINCOMPATTRDATA(WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                               ctypes.sizeof(accent))
        ok = bool(user32.SetWindowCompositionAttribute(ctypes.c_void_p(hwnd), ctypes.byref(data)))
        try:
            pref = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
                                                       ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass
        return ok
    except Exception:
        return False


def windows_build() -> int:
    if sys.platform != "win32":
        return 0
    return sys.getwindowsversion().build  # type: ignore[attr-defined]
