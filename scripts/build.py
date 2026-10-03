"""Build the app folder (and optionally bundle the models) with PyInstaller.

  python scripts/build.py                 -> dist/WinMeh/
  python scripts/build.py --with-models   -> also dist/WinMeh/models/ (chat model, llama.cpp, whisper)
  python scripts/build.py --with-models --model-cache .model-cache   (reuse downloads between builds)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIN = sys.platform == "win32"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-models", action="store_true")
    ap.add_argument("--model-cache", default=str(ROOT / ".model-cache"))
    a = ap.parse_args()

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", "WinMeh",
           "--paths", str(ROOT), "--collect-submodules", "winmeh",
           "--collect-all", "faster_whisper", "--collect-binaries", "ctranslate2",
           "--icon", str(ROOT / "installer" / "winmeh.ico"),
           "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build")]
    if WIN:
        cmd += ["--disable-windowed-traceback", "--hidden-import", "win32com.client", "--hidden-import", "pythoncom"]
    cmd.append(str(ROOT / "scripts" / "winmeh_entry.py"))
    subprocess.run(cmd, check=True)

    if a.with_models:
        cache = Path(a.model_cache)
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "winmeh.core.bootstrap", str(cache)], check=True, cwd=ROOT)
        dest = ROOT / "dist" / "WinMeh" / "models"
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(cache, dest, ignore=shutil.ignore_patterns("*.part", ".cache"))
        print("bundled models:", sorted(p.name for p in dest.iterdir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
