"""Zero-setup model handling.

Lookup order for every model file:
  1. bundled next to the app      <install dir>/models/...      (the one-click installers ship these)
  2. downloaded before            %LOCALAPPDATA%/WinMeh/models/... or ~/.local/share/winmeh/models/...
  3. otherwise download it now (first run only), reporting progress.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from ..config import IS_WINDOWS, models_dir

Progress = Callable[[str], None]

GGUF_REPOS = {
    "qwen2.5-0.5b-instruct-q4_k_m.gguf": "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
    "qwen2.5-1.5b-instruct-q4_k_m.gguf": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
}
LLAMA_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=15"
GPU_BUILD_WORDS = ("cuda", "cudart", "vulkan", "hip", "rocm", "sycl", "opencl", "kompute", "musa", "openvino", "kleidi")
SERVER_EXE = "llama-server.exe" if IS_WINDOWS else "llama-server"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def search_dirs() -> list[Path]:
    return [app_dir() / "models", models_dir()]


def find_gguf(name: str, dirs: list[Path] | None = None) -> Path | None:
    for d in dirs or search_dirs():
        if (d / name).is_file():
            return d / name
    return None


def find_llama_server(dirs: list[Path] | None = None) -> Path | None:
    for d in dirs or search_dirs():
        root = d / "llama.cpp"
        if root.is_dir():
            for p in [root / SERVER_EXE, *root.glob(f"**/{SERVER_EXE}")]:
                if p.is_file():
                    return p
    return None


def whisper_model(size: str) -> str:
    """Local folder if bundled/downloaded, else the size name (faster-whisper then downloads it itself)."""
    for d in search_dirs():
        p = d / f"whisper-{size}"
        if (p / "model.bin").is_file():
            return str(p)
    return size


# ------------------------------------------------------------------ downloading
def _download(url: str, dest: Path, label: str, progress: Progress) -> None:
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "WinMeh"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total, done, last = int(r.headers.get("Content-Length", 0)), 0, -1
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            pct = done * 100 // total if total else -1
            if pct != last and pct % 5 == 0:
                progress(f"downloading {label} {pct}%" if pct >= 0 else f"downloading {label} {done >> 20} MB")
                last = pct
    tmp.replace(dest)


def llama_asset_suffixes() -> list[str]:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    if IS_WINDOWS:
        return [f"bin-win-cpu-{arch}.zip", f"bin-win-avx2-{arch}.zip"]
    if sys.platform.startswith("linux"):
        return [f"bin-ubuntu-{arch}.zip", f"bin-ubuntu-{arch}.tar.gz"]
    return [f"bin-macos-{arch}.zip", f"bin-macos-{arch}.tar.gz"]


def pick_asset(assets: list[dict], suffixes: list[str]) -> dict | None:
    for suf in suffixes:
        for a in assets:
            if a.get("name", "").endswith(suf):
                return a
    # Naming changes between llama.cpp releases: fall back to "plain CPU build for this OS/arch".
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    os_words = ("win",) if IS_WINDOWS else ("ubuntu", "linux") if sys.platform.startswith("linux") else ("macos",)
    for a in assets:
        n = a.get("name", "").lower()
        if (n.endswith((".zip", ".tar.gz")) and any(w in n for w in os_words) and (arch in n or "amd64" in n)
                and not any(w in n for w in GPU_BUILD_WORDS)):
            return a
    return None


def _extract(archive: Path, out: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(out)
    else:
        with tarfile.open(archive) as t:
            t.extractall(out, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(out)
    if not IS_WINDOWS:                       # zip doesn't keep the executable bit
        for p in out.rglob("*"):
            if p.is_file() and (p.name.startswith("llama-") or ".so" in p.name):
                p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_llama_server(dest_root: Path, progress: Progress) -> Path | None:
    headers = {"User-Agent": "WinMeh", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):                       # CI: avoid the anonymous rate limit
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    releases = json.load(urllib.request.urlopen(urllib.request.Request(LLAMA_RELEASES_API, headers=headers), timeout=30))
    asset = None
    for rel in releases:                                     # newest first; skip ones without binaries
        asset = pick_asset(rel.get("assets", []), llama_asset_suffixes())
        if asset:
            break
    if not asset:
        seen = "; ".join(f"{r.get('tag_name')}: {', '.join(a['name'] for a in r.get('assets', [])[:12])}"
                         for r in releases[:3])
        progress("no llama.cpp build for this platform. Recent releases: " + seen)
        return None
    archive = dest_root / asset["name"]
    _download(asset["browser_download_url"], archive, "AI engine", progress)
    _extract(archive, dest_root / "llama.cpp")
    archive.unlink(missing_ok=True)
    return find_llama_server([dest_root])


def ensure_llm(gguf_name: str, progress: Progress, dest_root: Path | None = None) -> bool:
    """Make sure a GGUF model and llama-server exist, downloading what's missing.
    With dest_root, only that folder counts (used to build the bundled installers)."""
    dirs = [dest_root] if dest_root else None
    dest_root = dest_root or models_dir()
    try:
        if not find_gguf(gguf_name, dirs):
            repo = GGUF_REPOS.get(gguf_name)
            if not repo:
                return False
            _download(f"https://huggingface.co/{repo}/resolve/main/{gguf_name}", dest_root / gguf_name,
                      "chat model", progress)
        if not find_llama_server(dirs) and not install_llama_server(dest_root, progress):
            return False
        return True
    except Exception as e:
        progress(f"download failed: {e}")
        return False


def ensure_whisper(size: str, progress: Progress, dest_root: Path | None = None) -> str:
    """Download the faster-whisper model into our models folder (so the installer can bundle it)."""
    local = whisper_model(size)
    if local != size and not dest_root:
        return local
    dest = (dest_root or models_dir()) / f"whisper-{size}"
    try:
        from huggingface_hub import snapshot_download
        progress("downloading voice model")
        snapshot_download(f"Systran/faster-whisper-{size}", local_dir=str(dest))
        return str(dest)
    except Exception:
        return size


if __name__ == "__main__":   # used by CI/installers: python -m winmeh.core.bootstrap <dest>
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else models_dir()
    target.mkdir(parents=True, exist_ok=True)
    say = lambda m: print(m, flush=True)  # noqa: E731
    ok = ensure_llm("qwen2.5-0.5b-instruct-q4_k_m.gguf", say, target)
    w = ensure_whisper("tiny.en", say, target)
    print("llm:", ok, "whisper:", w)
    sys.exit(0 if ok and w != "tiny.en" else 1)
