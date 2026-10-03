"""Small, safe wrappers around winreg. All functions return empty results off-Windows."""

from __future__ import annotations

from typing import Any, Iterator

try:
    import winreg  # type: ignore
except ImportError:  # not Windows
    winreg = None  # type: ignore

HKLM = getattr(winreg, "HKEY_LOCAL_MACHINE", None)
HKCU = getattr(winreg, "HKEY_CURRENT_USER", None)


def get_value(root, path: str, name: str, default: Any = None) -> Any:
    if winreg is None or root is None:
        return default
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return default


def subkeys(root, path: str) -> Iterator[str]:
    if winreg is None or root is None:
        return
    try:
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    yield winreg.EnumKey(k, i)
                except OSError:
                    return
                i += 1
    except OSError:
        return


def values(root, path: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if winreg is None or root is None:
        return out
    try:
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[name] = val
                i += 1
    except OSError:
        pass
    return out


def set_value(root, path: str, name: str, value: str) -> bool:
    if winreg is None or root is None:
        return False
    try:
        with winreg.CreateKey(root, path) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        return True
    except OSError:
        return False


def delete_value(root, path: str, name: str) -> bool:
    if winreg is None or root is None:
        return False
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
        return True
    except OSError:
        return False
