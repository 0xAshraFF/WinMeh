"""Download the small models WinMeh uses (one time, ~0.6 GB total by default).

  python scripts/setup_models.py              # Qwen2.5-0.5B (Q4_K_M, ~400 MB) + llama.cpp server + whisper tiny.en
  python scripts/setup_models.py --model 1.5b # smarter, ~1 GB, still fast on most PCs
  python scripts/setup_models.py --cpu        # force the CPU build of llama.cpp

Already use Ollama? Skip this and run:  ollama pull qwen2.5:0.5b
WinMeh auto-detects Ollama at 127.0.0.1:11434.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from winmeh.config import Settings, models_dir  # noqa: E402

MODELS = {
    "0.5b": ("Qwen/Qwen2.5-0.5B-Instruct-GGUF", "qwen2.5-0.5b-instruct-q4_k_m.gguf", "qwen2.5-0.5b-instruct"),
    "1.5b": ("Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf", "qwen2.5-1.5b-instruct"),
}


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  already have {dest.name}")
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "WinMeh-setup"})
    with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {dest.name}: {done * 100 // total}% of {total // 2**20} MB", end="", flush=True)
    print()
    tmp.replace(dest)


def pick_llama_asset(assets: list[dict], prefer_gpu: bool) -> dict | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    order = ([f"bin-win-vulkan-{arch}.zip"] if prefer_gpu else []) + [f"bin-win-cpu-{arch}.zip", f"bin-win-avx2-{arch}.zip"]
    for suffix in order:
        for a in assets:
            if a["name"].endswith(suffix):
                return a
    return None


def has_dedicated_gpu() -> bool:
    try:
        from winmeh.system import gpu
        return any(not g.is_integrated and g.vram_bytes >= 2 * 1024**3 for g in gpu.detect())
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=MODELS, default="0.5b")
    ap.add_argument("--cpu", action="store_true", help="use the CPU build of llama.cpp even if you have a GPU")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--skip-voice", action="store_true")
    args = ap.parse_args()
    md = models_dir()
    s = Settings.load()

    if not args.skip_llm:
        repo, fname, alias = MODELS[args.model]
        print(f"[1/3] LLM: {fname}")
        download(f"https://huggingface.co/{repo}/resolve/main/{fname}", md / fname)
        s.llm_gguf, s.llm_model = fname, alias

        print("[2/3] llama.cpp server")
        if sys.platform != "win32":
            print("  not Windows: install llama.cpp yourself (e.g. `brew install llama.cpp`) and put llama-server in",
                  md / "llama.cpp")
        else:
            rel = json.load(urllib.request.urlopen(urllib.request.Request(
                "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest", headers={"User-Agent": "WinMeh"})))
            asset = pick_llama_asset(rel["assets"], prefer_gpu=has_dedicated_gpu() and not args.cpu)
            if not asset:
                print("  couldn't find a Windows build in the latest release; see README for manual steps")
                return 1
            z = md / asset["name"]
            download(asset["browser_download_url"], z)
            out = md / "llama.cpp"
            shutil.rmtree(out, ignore_errors=True)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(out)
            z.unlink()
            print(f"  installed {rel['tag_name']} ({asset['name']})")

    if not args.skip_voice:
        print(f"[3/3] speech: whisper {s.stt_model}")
        try:
            from faster_whisper import WhisperModel
            WhisperModel(s.stt_model, device="cpu", compute_type="int8")
            print("  ready")
        except ImportError:
            print("  pip install faster-whisper sounddevice   (then re-run this)")

    s.save()
    print("\nDone. Start WinMeh with:  python -m winmeh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
