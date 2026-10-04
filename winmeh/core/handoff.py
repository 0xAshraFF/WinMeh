"""Hand a question to an online AI the user already has - always after asking.

No API keys and no browser automation: we open a normal web link (or copy the
text to the clipboard), or start the `claude` command in a terminal. The `?q=`
links are NOT official APIs; ChatGPT and Claude could change or drop them, in
which case the clipboard fallback still works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass

IS_WINDOWS = os.name == "nt"
MAX_URL = 6000                     # longer prompts go via the clipboard: browsers/servers truncate long URLs


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    site: str
    query_url: str = ""            # "{q}" is replaced with the url-encoded text; "" = clipboard only


SERVICES = {
    "chatgpt": Service("chatgpt", "ChatGPT", "https://chatgpt.com/", "https://chatgpt.com/?q={q}"),
    "claude": Service("claude", "Claude", "https://claude.ai/new", "https://claude.ai/new?q={q}"),
    "deepseek": Service("deepseek", "DeepSeek", "https://chat.deepseek.com/"),
    "claude_code": Service("claude_code", "Claude Code", ""),
}


# ------------------------------------------------------------------ detection
def detect(app_names: list[str] | None = None) -> dict[str, str]:
    """Which services this machine has, and how: {'claude_code': 'command', 'chatgpt': 'app', ...}.
    Web services are always reachable through the browser."""
    found = {"chatgpt": "web", "claude": "web", "deepseek": "web"}
    if app_names is None:
        try:
            from ..system.apps import start_menu_shortcuts
            app_names = list(start_menu_shortcuts())
        except Exception:
            app_names = []
    names = [n.lower() for n in app_names]
    if any("chatgpt" in n for n in names):
        found["chatgpt"] = "app"
    if any(n == "claude" or (n.startswith("claude ") and "code" not in n) for n in names):
        found["claude"] = "app"
    if shutil.which("claude"):
        found["claude_code"] = "command"
    return found


def choose(preferred: str, available: dict[str, str]) -> Service:
    """The user's setting wins; 'auto' picks an installed desktop app first, then ChatGPT on the web."""
    if preferred in SERVICES and preferred in available:
        return SERVICES[preferred]
    for key in ("claude", "chatgpt"):
        if available.get(key) == "app":
            return SERVICES[key]
    return SERVICES["chatgpt"]


# ------------------------------------------------------------------ building what gets sent
def compose(query: str, profile_summary: str | None = None) -> str:
    """Exactly the text that will be sent. Machine details only if the user said yes to including them."""
    text = query.strip()
    if profile_summary:
        text += f"\n\n(My computer: {profile_summary})"
    return text


def build_url(service: Service, text: str) -> str | None:
    """Prefilled link, or None if this service needs the clipboard (no link format, or text too long)."""
    if not service.query_url:
        return None
    url = service.query_url.replace("{q}", urllib.parse.quote(text, safe=""))
    return url if len(url) <= MAX_URL else None


# ------------------------------------------------------------------ Claude Code
def claude_code_command(query: str, folder: str) -> tuple[list[str], dict, int]:
    """(argv, env, creationflags) that open a terminal running `claude "<query>"` in `folder`.

    The query travels in an environment variable and is expanded by the shell as ONE
    argument, so quotes, &, | or ; in it can't break out into extra commands."""
    env = dict(os.environ, WINMEH_QUERY=query)
    if IS_WINDOWS:
        return (["powershell.exe", "-NoExit", "-NoProfile", "-Command", "claude $env:WINMEH_QUERY"],
                env, 0x00000010)                       # CREATE_NEW_CONSOLE
    inner = 'claude "$WINMEH_QUERY"; exec bash'
    for term, args in (("x-terminal-emulator", ["-e", "bash", "-c", inner]),
                       ("gnome-terminal", ["--", "bash", "-c", inner]),
                       ("konsole", ["-e", "bash", "-c", inner]),
                       ("xfce4-terminal", ["-x", "bash", "-c", inner]),
                       ("xterm", ["-e", "bash", "-c", inner])):
        if shutil.which(term):
            return [term, *args], env, 0
    return [], env, 0


def launch_claude_code(query: str, folder: str) -> tuple[bool, str]:
    argv, env, flags = claude_code_command(query, folder)
    if not argv:
        return False, "I couldn't find a terminal program to open."
    if not os.path.isdir(folder):
        return False, f"The folder {folder} doesn't exist."
    try:
        subprocess.Popen(argv, cwd=folder, env=env, creationflags=flags)
    except OSError as e:
        return False, f"Couldn't start Claude Code: {e}"
    return True, f"Opened Claude Code in {folder}."


# ------------------------------------------------------------------ clipboard (headless fallback)
def system_copy(text: str) -> bool:
    """Used when there is no Qt window (e.g. --ask). The widget copies through Qt instead."""
    cmds = ([["clip"]] if IS_WINDOWS else
            [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]])
    for cmd in cmds:
        if shutil.which(cmd[0]):
            try:
                data = text.encode("utf-16-le") if IS_WINDOWS else text.encode()
                if IS_WINDOWS:
                    data = b"\xff\xfe" + data          # BOM so clip.exe reads UTF-16 (keeps non-English text)
                subprocess.run(cmd, input=data, timeout=5, check=True,
                               creationflags=0x08000000 if IS_WINDOWS else 0)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False
