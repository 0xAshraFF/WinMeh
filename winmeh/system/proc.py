"""Cross-platform process helpers."""

from __future__ import annotations

import shutil
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def run(cmd: list[str], timeout: float = 30) -> tuple[int, str]:
    """Run a command without flashing a console. Returns (exit code, stdout+stderr); -1 if it couldn't run."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)
    out = (p.stdout or b"") + (p.stderr or b"")
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return p.returncode, out.decode(enc)
        except UnicodeDecodeError:
            continue
    return p.returncode, out.decode("utf-8", "ignore")


def powershell(script: str, timeout: float = 60) -> tuple[int, str]:
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
               timeout)


def which(name: str) -> str | None:
    return shutil.which(name)


def running() -> set[str]:
    """Lower-case names of running processes (e.g. 'chrome.exe', 'firefox')."""
    try:
        import psutil
        out = set()
        for p in psutil.process_iter(["name"]):
            n = p.info.get("name")
            if n:
                out.add(n.lower())
        return out
    except Exception:
        return set()


def run_elevated_script(ps1_path: str) -> bool:
    """Windows: run a PowerShell script in a visible window behind a UAC prompt."""
    if not IS_WINDOWS:
        return False
    import ctypes
    args = f'-NoProfile -ExecutionPolicy Bypass -File "{ps1_path}"'
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", args, None, 1)
    return rc > 32          # > 32 means the process started; the user may still decline UAC (then rc == 5)
