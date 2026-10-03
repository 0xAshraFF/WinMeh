"""App tweaks and launching."""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from ..config import IS_WINDOWS

# ------------------------------------------------------------------ VLC
VLC_SETTINGS = {
    "qt-updates-notif": "0",   # "Activate the updates availability notification"
    "qt-privacy-ask": "0",     # first-run "Privacy and Network Access Policy" dialog
}


def vlcrc_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "vlc" / "vlcrc"
    return Path.home() / ".config" / "vlc" / "vlcrc"


def vlc_installed() -> bool:
    if vlcrc_path().parent.exists():
        return True
    return any(os.path.exists(p) for p in (r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                                           r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe")) or bool(shutil.which("vlc"))


def apply_vlcrc(text: str, settings: dict[str, str] = VLC_SETTINGS) -> str:
    """Set keys inside the [qt] section, uncommenting or adding them as needed."""
    lines = text.splitlines()
    remaining = dict(settings)
    for i, line in enumerate(lines):
        m = re.match(r"^\s*#?\s*([\w\-]+)\s*=", line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            lines[i] = f"{key}={remaining.pop(key)}"
    if remaining:
        add = [f"{k}={v}" for k, v in remaining.items()]
        idx = next((i for i, l in enumerate(lines) if l.strip().lower() == "[qt]"), None)
        if idx is None:
            lines += ["", "[qt] # Qt interface"] + add
        else:
            lines[idx + 1:idx + 1] = add
    return "\n".join(lines) + "\n"


def disable_vlc_update_popup() -> tuple[bool, str]:
    rc = vlcrc_path()
    if not vlc_installed():
        return False, "I couldn't find VLC on this machine."
    from .proc import running as running_procs
    procs = running_procs()
    running = "vlc.exe" in procs or "vlc" in procs
    rc.parent.mkdir(parents=True, exist_ok=True)
    original = rc.read_text(encoding="utf-8", errors="ignore") if rc.exists() else ""
    if original:
        backup = rc.with_name(f"vlcrc.winmeh-backup-{int(time.time())}")
        backup.write_text(original, encoding="utf-8")
    rc.write_text(apply_vlcrc(original), encoding="utf-8")
    msg = "Done - VLC's update notification and first-run privacy prompt are turned off (vlcrc: qt-updates-notif=0)."
    if original:
        msg += " I kept a backup of your old settings next to it."
    if running:
        msg += " VLC is open right now: close it and reopen it, and if you save preferences in that window first it may undo this."
    return True, msg


# ------------------------------------------------------------------ launching
def start_menu_shortcuts() -> dict[str, str]:
    """App name (lower-case) -> launchable file: .lnk on Windows, .desktop on Linux."""
    if not IS_WINDOWS:
        from .profile import linux_desktop_apps
        return {a["name"].lower(): a["location"] for a in linux_desktop_apps()}
    roots = [os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
             os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs")]
    out = {}
    for r in roots:
        for lnk in glob.glob(os.path.join(r, "**", "*.lnk"), recursive=True):
            name = os.path.splitext(os.path.basename(lnk))[0]
            if "uninstall" not in name.lower():
                out.setdefault(name.lower(), lnk)
    return out


def find_app(name: str, shortcuts: dict[str, str]) -> str | None:
    n = name.lower().strip()
    if n in shortcuts:
        return shortcuts[n]
    starts = [k for k in shortcuts if k.startswith(n)]
    if starts:
        return shortcuts[min(starts, key=len)]
    contains = [k for k in shortcuts if n in k]
    return shortcuts[min(contains, key=len)] if contains else None


def open_path(path: str) -> None:
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
        return
    if path.endswith(".desktop"):
        if shutil.which("gio"):
            subprocess.Popen(["gio", "launch", path])
            return
        if shutil.which("gtk-launch"):
            subprocess.Popen(["gtk-launch", os.path.basename(path)])
            return
    if shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", path])


def reveal(path: str) -> None:
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path))


# ------------------------------------------------------------------ run at login
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_cmd() -> str:
    import sys
    if getattr(sys, "frozen", False):          # PyInstaller build
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pyw if os.path.exists(pyw) else sys.executable}" -m winmeh'


def _linux_autostart_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "winmeh.desktop"


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return _linux_autostart_file().exists()
    from . import reg
    return bool(reg.get_value(reg.HKCU, RUN_KEY, "WinMeh"))


def set_autostart(on: bool) -> None:
    if not IS_WINDOWS:
        f = _linux_autostart_file()
        if on:
            import sys
            exe = f'"{sys.executable}"' if getattr(sys, "frozen", False) else f'"{sys.executable}" -m winmeh'
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"[Desktop Entry]\nType=Application\nName=WinMeh\nExec={exe}\nX-GNOME-Autostart-enabled=true\n")
        elif f.exists():
            f.unlink()
        return
    from . import reg
    if on:
        reg.set_value(reg.HKCU, RUN_KEY, "WinMeh", _launch_cmd())
    else:
        reg.delete_value(reg.HKCU, RUN_KEY, "WinMeh")
