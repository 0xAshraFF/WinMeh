"""Streaming client for any OpenAI-compatible local server.

Works with llama.cpp's `llama-server` (managed by WinMeh), Ollama
(http://127.0.0.1:11434/v1) and LM Studio (http://127.0.0.1:1234/v1).
Pure stdlib - no requests/openai dependency.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator

from ..config import IS_WINDOWS, Settings, models_dir

FALLBACK_URLS = ["http://127.0.0.1:11434/v1", "http://127.0.0.1:1234/v1"]   # Ollama, LM Studio


def _port_open(url: str, timeout: float = 0.25) -> bool:
    u = urllib.parse.urlparse(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def llama_server_exe() -> Path | None:
    name = "llama-server.exe" if IS_WINDOWS else "llama-server"
    for p in (models_dir() / "llama.cpp" / name, *models_dir().glob(f"llama.cpp/**/{name}")):
        if p.exists():
            return p
    return None


class LLM:
    def __init__(self, settings: Settings):
        self.s = settings
        self.url = settings.llm_url.rstrip("/")
        self.model = settings.llm_model
        self.proc: subprocess.Popen | None = None
        self.status = "starting"

    # ------------------------------------------------------------ lifecycle
    def ensure(self) -> bool:
        """Find or start a server. Returns True if one is reachable."""
        if _port_open(self.url):
            self.status = "ready"
            return True
        if self.s.llm_autostart and self._spawn():
            return True
        for alt in FALLBACK_URLS:
            if _port_open(alt):
                self.url = alt
                if "11434" in alt:
                    self.model = self._first_ollama_model() or self.model
                self.status = "ready"
                return True
        self.status = "offline"
        return False

    def _first_ollama_model(self) -> str | None:
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1) as r:
                models = [m["name"] for m in json.loads(r.read()).get("models", [])]
        except (OSError, ValueError):
            return None
        small = sorted(models, key=lambda m: (not any(t in m for t in ("0.5b", "0.6b", "1b", "1.5b", "270m", "350m")), m))
        return small[0] if small else None

    def _spawn(self) -> bool:
        exe = llama_server_exe()
        gguf = models_dir() / self.s.llm_gguf
        if not exe or not gguf.exists():
            return False
        port = urllib.parse.urlparse(self.url).port or 8765
        threads = self.s.llm_threads or max(2, (os.cpu_count() or 4) // 2)
        cmd = [str(exe), "-m", str(gguf), "--host", "127.0.0.1", "--port", str(port),
               "-c", "4096", "-t", str(threads), "-ngl", "99",      # offload to GPU if the build supports it
               "--no-webui", "--log-disable"]
        flags = 0x08000000 if IS_WINDOWS else 0                      # CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError:
            return False
        import time
        for _ in range(120):                                          # model load: usually < 3 s
            if self.proc.poll() is not None:
                # some older builds don't know --no-webui: retry without it once
                if "--no-webui" in cmd:
                    cmd.remove("--no-webui")
                    self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                 creationflags=flags)
                    continue
                return False
            if _port_open(self.url) and self._health():
                self.status = "ready"
                return True
            time.sleep(0.1)
        return False

    def _health(self) -> bool:
        base = self.url[:-3] if self.url.endswith("/v1") else self.url
        try:
            with urllib.request.urlopen(base + "/health", timeout=0.5) as r:
                return r.status == 200
        except (OSError, urllib.error.HTTPError):
            return False

    def warm(self) -> None:
        """Process a tiny prompt so the system prompt KV-cache is hot for the first real question."""
        try:
            for _ in self.stream([{"role": "user", "content": "hi"}], max_tokens=1):
                pass
        except Exception:
            pass

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    # ------------------------------------------------------------ inference
    def stream(self, messages: list[dict], max_tokens: int | None = None, temperature: float = 0.4) -> Iterator[str]:
        body = json.dumps({
            "model": self.model, "messages": messages, "stream": True, "temperature": temperature,
            "max_tokens": max_tokens or self.s.llm_max_tokens, "cache_prompt": True,
        }).encode()
        req = urllib.request.Request(self.url + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


SYSTEM_PROMPT = """You are WinMeh, a fast, friendly desktop assistant living on the user's Windows PC.
Answer in 1-3 short sentences unless asked for detail. Be concrete and honest; if you don't know, say so.
Facts about this machine (trust these over your own guesses):
{profile}
{memory}
Things you can do directly when asked: report specs/VRAM/RAM/disk, find files and photos, list installed games and check if a game can run, clear caches, open apps, stop VLC's update popup. Tell the user to ask for these directly."""


def build_messages(history: list[dict], profile_summary: str, memory: list[str]) -> list[dict]:
    mem = ("Things the user told you to remember: " + "; ".join(memory[-20:])) if memory else ""
    return [{"role": "system", "content": SYSTEM_PROMPT.format(profile=profile_summary, memory=mem)}] + history[-8:]


if __name__ == "__main__":  # quick manual test: python -m winmeh.core.llm "hello"
    llm = LLM(Settings.load())
    print("server:", llm.ensure(), llm.url, llm.model)
    for tok in llm.stream([{"role": "user", "content": " ".join(sys.argv[1:]) or "hello"}]):
        print(tok, end="", flush=True)
    print()
