"""Learns the machine: hardware, OS, disks, installed apps, user folders.

The profile is cached to profile.json so answers are instant on next start and
refreshed in the background.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import string
import sys
import time
from pathlib import Path

from ..config import IS_WINDOWS, data_dir
from . import gpu, reg

UNINSTALL_KEYS = [
    (reg.HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (reg.HKLM, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (reg.HKCU, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _ram_bytes() -> int:
    try:
        import psutil
        return int(psutil.virtual_memory().total)
    except ImportError:
        pass
    if IS_WINDOWS:
        class MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMSTAT()
        m.dwLength = ctypes.sizeof(MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return int(m.ullTotalPhys)
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def _cpu_name() -> str:
    name = reg.get_value(reg.HKLM, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
    if name:
        return str(name).strip()
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "Unknown CPU"


def _os_name() -> str:
    if IS_WINDOWS:
        cur = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        product = reg.get_value(reg.HKLM, cur, "ProductName", "Windows")
        build = reg.get_value(reg.HKLM, cur, "CurrentBuildNumber", "")
        display = reg.get_value(reg.HKLM, cur, "DisplayVersion", "")
        # Windows 11 still reports "Windows 10" in ProductName; build >= 22000 is 11.
        if str(build).isdigit() and int(build) >= 22000:
            product = str(product).replace("Windows 10", "Windows 11")
        return f"{product} {display} (build {build})".strip()
    return f"{platform.system()} {platform.release()}"


def _disks() -> list[dict]:
    roots = [f"{c}:\\" for c in string.ascii_uppercase] if IS_WINDOWS else ["/"]
    disks = []
    for r in roots:
        if IS_WINDOWS and not os.path.exists(r):
            continue
        try:
            u = shutil.disk_usage(r)
        except OSError:
            continue
        disks.append({"mount": r, "total_gb": round(u.total / 1024**3, 1), "free_gb": round(u.free / 1024**3, 1)})
    return disks


def installed_apps() -> list[dict]:
    seen, apps = set(), []
    for root, path in UNINSTALL_KEYS:
        for sub in reg.subkeys(root, path):
            v = reg.values(root, path + "\\" + sub)
            name = v.get("DisplayName")
            if not name or v.get("SystemComponent") == 1 or name in seen:
                continue
            seen.add(name)
            apps.append({"name": str(name), "version": str(v.get("DisplayVersion", "")),
                         "publisher": str(v.get("Publisher", "")), "location": str(v.get("InstallLocation", ""))})
    apps.sort(key=lambda a: a["name"].lower())
    return apps


def user_folders() -> dict[str, str]:
    home = Path.home()
    folders = {n: str(home / n) for n in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")}
    if IS_WINDOWS:
        # Honour folder redirection (e.g. Pictures moved to D:\ or OneDrive).
        shell = reg.values(reg.HKCU, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        mapping = {"Desktop": "Desktop", "Personal": "Documents", "My Pictures": "Pictures",
                   "My Video": "Videos", "My Music": "Music",
                   "{374DE290-123F-4565-9164-39C4925E467B}": "Downloads"}
        for key, label in mapping.items():
            if key in shell:
                folders[label] = os.path.expandvars(str(shell[key]))
        for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            if os.environ.get(env):
                folders["OneDrive"] = os.environ[env]
                break
    return {k: v for k, v in folders.items() if os.path.isdir(v)}


def collect() -> dict:
    gpus = gpu.detect()
    return {
        "collected_at": time.time(),
        "computer": platform.node(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER", ""),
        "os": _os_name(),
        "cpu": _cpu_name(),
        "cores": os.cpu_count() or 0,
        "ram_gb": round(_ram_bytes() / 1024**3, 1),
        "gpus": [g.to_dict() for g in gpus],
        "disks": _disks(),
        "folders": user_folders(),
        "apps": installed_apps(),
        "python": sys.version.split()[0],
    }


def cache_path() -> Path:
    return data_dir() / "profile.json"


def load_cached() -> dict | None:
    try:
        return json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(profile: dict) -> None:
    cache_path().write_text(json.dumps(profile, indent=1), encoding="utf-8")


def summary(p: dict) -> str:
    """Compact one-paragraph description used as LLM context."""
    gpus = "; ".join(f"{g['name']} ({g['vram_gb']} GB VRAM)" for g in p.get("gpus", [])) or "unknown GPU"
    disks = ", ".join(f"{d['mount']} {d['free_gb']}/{d['total_gb']} GB free" for d in p.get("disks", []))
    return (f"OS: {p.get('os')}. CPU: {p.get('cpu')} ({p.get('cores')} threads). RAM: {p.get('ram_gb')} GB. "
            f"GPU: {gpus}. Disks: {disks}. Installed apps: {len(p.get('apps', []))}.")
