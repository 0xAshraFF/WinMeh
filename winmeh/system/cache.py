"""Find and clear caches. Always scans first; deletes only after the user confirms.

Only the *contents* of known cache folders are deleted, never the folders
themselves. Locked files (in use by a running app) are skipped, not forced.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, field

from ..config import IS_WINDOWS


@dataclass
class CacheTarget:
    key: str
    label: str
    paths: list[str]
    note: str = ""
    size: int = 0
    files: int = 0
    running_app: str = ""   # process name that keeps these files locked
    extra: list[str] = field(default_factory=list)

    @property
    def size_mb(self) -> float:
        return round(self.size / 1024**2, 1)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _browser_cache_dirs(user_data: str) -> list[str]:
    out = []
    for prof in glob.glob(os.path.join(user_data, "*")):
        for sub in ("Cache", "Code Cache", "GPUCache", os.path.join("Service Worker", "CacheStorage")):
            p = os.path.join(prof, sub)
            if os.path.isdir(p):
                out.append(p)
    return out


def targets() -> list[CacheTarget]:
    local, temp = _env("LOCALAPPDATA"), _env("TEMP") or _env("TMP")
    t = [CacheTarget("temp", "Your temp files", [temp] if temp else [])]
    if IS_WINDOWS:
        t.append(CacheTarget("wintemp", "Windows temp", [os.path.join(_env("SystemRoot", r"C:\Windows"), "Temp")],
                             note="needs admin for some files"))
        t.append(CacheTarget("thumbs", "Thumbnail cache",
                             glob.glob(os.path.join(local, "Microsoft", "Windows", "Explorer", "thumbcache_*.db")),
                             note="Explorer rebuilds it automatically", running_app="explorer.exe"))
    browsers = [
        ("chrome", "Chrome cache", os.path.join(local, "Google", "Chrome", "User Data"), "chrome.exe"),
        ("edge", "Edge cache", os.path.join(local, "Microsoft", "Edge", "User Data"), "msedge.exe"),
        ("brave", "Brave cache", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"), "brave.exe"),
    ]
    for key, label, ud, exe in browsers:
        if local and os.path.isdir(ud):
            t.append(CacheTarget(key, label, _browser_cache_dirs(ud), note="close the browser first", running_app=exe))
    ff = glob.glob(os.path.join(local, "Mozilla", "Firefox", "Profiles", "*", "cache2")) if local else []
    if ff:
        t.append(CacheTarget("firefox", "Firefox cache", ff, note="close Firefox first", running_app="firefox.exe"))
    if IS_WINDOWS:
        t.append(CacheTarget("dns", "DNS cache", [], note="flushed with ipconfig /flushdns"))
    else:
        t += linux_targets()
    return [x for x in t if x.paths or x.key == "dns"]


def linux_targets() -> list[CacheTarget]:
    c = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    t = [CacheTarget("thumbs", "Thumbnail cache", [p for p in [os.path.join(c, "thumbnails")] if os.path.isdir(p)],
                     note="rebuilt automatically")]
    for key, label, sub, exe in [("chrome", "Chrome cache", "google-chrome", "chrome"),
                                 ("chromium", "Chromium cache", "chromium", "chromium"),
                                 ("brave", "Brave cache", "BraveSoftware/Brave-Browser", "brave"),
                                 ("edge", "Edge cache", "microsoft-edge", "msedge")]:
        dirs = [d for d in glob.glob(os.path.join(c, sub, "*", "Cache")) + glob.glob(os.path.join(c, sub, "*", "Code Cache"))
                if os.path.isdir(d)]
        if dirs:
            t.append(CacheTarget(key, label, dirs, note="close the browser first", running_app=exe))
    ff = glob.glob(os.path.join(c, "mozilla", "firefox", "*", "cache2"))
    if ff:
        t.append(CacheTarget("firefox", "Firefox cache", ff, note="close Firefox first", running_app="firefox"))
    for key, label, sub in [("pip", "pip download cache", "pip"), ("npm", "npm cache", os.path.join("..", ".npm", "_cacache"))]:
        p = os.path.normpath(os.path.join(c, sub))
        if os.path.isdir(p):
            t.append(CacheTarget(key, label, [p], note="re-downloaded when needed"))
    return t


def _size_of(path: str) -> tuple[int, int]:
    if os.path.isfile(path):
        try:
            return os.path.getsize(path), 1
        except OSError:
            return 0, 0
    total = n = 0
    for dp, _, fns in os.walk(path):
        for f in fns:
            try:
                total += os.path.getsize(os.path.join(dp, f))
                n += 1
            except OSError:
                pass
    return total, n


def scan() -> list[CacheTarget]:
    found = targets()
    running = running_processes()
    for t in found:
        for p in t.paths:
            s, n = _size_of(p)
            t.size += s
            t.files += n
        if t.running_app and (t.running_app.lower() in running or t.running_app.lower() + ".exe" in running):
            t.extra.append(f"{t.running_app} is running - its locked files will be skipped")
    return found


def running_processes() -> set[str]:
    from .proc import running
    return running()


def clear_path(path: str) -> tuple[int, int]:
    """Delete contents of `path` (or the file itself). Returns (bytes_freed, items_skipped)."""
    freed = skipped = 0
    if os.path.isfile(path):
        try:
            sz = os.path.getsize(path)
            os.remove(path)
            return sz, 0
        except OSError:
            return 0, 1
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0, 1
    for e in entries:
        try:
            is_dir = e.is_dir(follow_symlinks=False)
            before = _size_of(e.path)[0] if is_dir else e.stat(follow_symlinks=False).st_size
        except OSError:
            skipped += 1
            continue
        if is_dir:
            # ignore_errors keeps going past locked files; measure what actually went.
            shutil.rmtree(e.path, ignore_errors=True)
            after = _size_of(e.path)[0] if os.path.exists(e.path) else 0
            freed += max(0, before - after)
            if after:
                skipped += 1
        else:
            try:
                os.remove(e.path)
                freed += before
            except OSError:
                skipped += 1
    return freed, skipped


def clear(found: list[CacheTarget], keys: set[str] | None = None) -> tuple[int, int, list[str]]:
    freed = skipped = 0
    done = []
    for t in found:
        if keys and t.key not in keys:
            continue
        if t.key == "dns":
            if IS_WINDOWS:
                subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=0x08000000)
                done.append(t.label)
            continue
        for p in t.paths:
            f, s = clear_path(p)
            freed += f
            skipped += s
        done.append(t.label)
    return freed, skipped, done


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
