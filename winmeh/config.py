"""Settings and per-user data paths."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def data_dir() -> Path:
    """%LOCALAPPDATA%\\WinMeh on Windows, ~/.local/share/winmeh elsewhere."""
    override = os.environ.get("WINMEH_HOME")
    if override:
        base = Path(override)
    elif IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WinMeh"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "winmeh"
    base.mkdir(parents=True, exist_ok=True)
    return base


def models_dir() -> Path:
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Settings:
    # --- window ---
    x: int | None = None
    y: int | None = None
    width: int = 360
    height: int = 540
    always_on_top: bool = False      # False = lives on the desktop layer like a widget
    opacity: float = 0.82

    # --- LLM (any OpenAI-compatible server: llama-server, Ollama, LM Studio) ---
    llm_url: str = "http://127.0.0.1:8765/v1"
    llm_model: str = "qwen2.5-0.5b-instruct"
    llm_autostart: bool = True        # spawn bundled llama-server if not already running
    llm_gguf: str = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
    llm_max_tokens: int = 256
    llm_threads: int = 0              # 0 = auto
    auto_download: bool = True        # first run: fetch the chat model + engine if nothing is bundled/installed

    # --- voice ---
    stt_model: str = "tiny.en"        # faster-whisper size: tiny.en | base.en | small.en
    speak_replies: bool = True
    hotkey_talk: str = "ctrl+alt+space"
    hotkey_toggle: str = "ctrl+alt+w"

    # --- knowledge ---
    extra_search_roots: list[str] = field(default_factory=list)
    online_lookups: bool = True       # allow Steam store requirement lookups

    @classmethod
    def path(cls) -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        s = cls()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
            except (OSError, ValueError):
                pass
        return s

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
