"""Discover installed games and check whether this PC meets their requirements."""

from __future__ import annotations

import glob
import html
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import reg

# Steam "apps" that are not games.
STEAM_NON_GAMES = {228980, 1070560, 1391110, 1628350, 1493710, 1826330, 1887720, 2180100, 250820, 1007}
STEAM_NON_GAME_WORDS = ("redistributable", "proton", "steam linux runtime", "steamvr", "soundtrack",
                        "dedicated server", "sdk", "benchmark", "wallpaper engine")


@dataclass
class Game:
    name: str
    store: str
    path: str = ""
    size_gb: float = 0.0
    app_id: str = ""
    verdict: str = ""                     # "ok" | "below" | "unknown"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ VDF (Valve KeyValues)
def parse_vdf(text: str) -> dict:
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    stack: list[dict] = [{}]
    key = None
    for quoted, brace in tokens:
        if brace == "{":
            new: dict = {}
            stack[-1][key] = new
            stack.append(new)
            key = None
        elif brace == "}":
            stack.pop()
        elif key is None:
            key = quoted
        else:
            stack[-1][key] = quoted.replace("\\\\", "\\")
            key = None
    return stack[0]


# ------------------------------------------------------------------ stores
def steam_root() -> str | None:
    p = reg.get_value(reg.HKCU, r"Software\Valve\Steam", "SteamPath") or \
        reg.get_value(reg.HKLM, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")
    if p and os.path.isdir(p):
        return os.path.normpath(p)
    for c in (r"C:\Program Files (x86)\Steam", os.path.expanduser("~/.steam/steam"),
              os.path.expanduser("~/.local/share/Steam"),
              os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.local/share/Steam")):
        if os.path.isdir(c):
            return c
    return None


def steam_games(root: str | None = None) -> list[Game]:
    root = root or steam_root()
    if not root:
        return []
    libs = {os.path.normpath(root)}
    lf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    if os.path.exists(lf):
        data = parse_vdf(Path(lf).read_text(encoding="utf-8", errors="ignore"))
        for v in data.get("libraryfolders", {}).values():
            if isinstance(v, dict) and v.get("path"):
                libs.add(os.path.normpath(v["path"]))
            elif isinstance(v, str) and os.path.isdir(v):   # old format
                libs.add(os.path.normpath(v))
    games = []
    for lib in libs:
        for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            try:
                st = parse_vdf(Path(acf).read_text(encoding="utf-8", errors="ignore")).get("AppState", {})
            except OSError:
                continue
            name, appid = st.get("name", ""), st.get("appid", "0")
            if not name or (appid.isdigit() and int(appid) in STEAM_NON_GAMES):
                continue
            if any(w in name.lower() for w in STEAM_NON_GAME_WORDS):
                continue
            size = int(st.get("SizeOnDisk", "0") or 0)
            games.append(Game(name, "Steam", os.path.join(lib, "steamapps", "common", st.get("installdir", "")),
                              round(size / 1024**3, 1), appid))
    return games


def epic_games(manifest_dir: str = r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests") -> list[Game]:
    games = []
    for item in glob.glob(os.path.join(manifest_dir, "*.item")):
        try:
            m = json.loads(Path(item).read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        cats = [c.lower() for c in m.get("AppCategories", [])]
        if cats and "games" not in cats:
            continue
        games.append(Game(m.get("DisplayName", "?"), "Epic", m.get("InstallLocation", ""),
                          round(int(m.get("InstallSize", 0) or 0) / 1024**3, 1)))
    return games


def gog_games() -> list[Game]:
    base = r"SOFTWARE\WOW6432Node\GOG.com\Games"
    out = []
    for sub in reg.subkeys(reg.HKLM, base):
        v = reg.values(reg.HKLM, base + "\\" + sub)
        if v.get("gameName"):
            out.append(Game(str(v["gameName"]), "GOG", str(v.get("path", ""))))
    return out


def ubisoft_games() -> list[Game]:
    base = r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"
    out = []
    for sub in reg.subkeys(reg.HKLM, base):
        d = reg.get_value(reg.HKLM, base + "\\" + sub, "InstallDir")
        if d:
            out.append(Game(os.path.basename(os.path.normpath(str(d))), "Ubisoft", str(d)))
    return out


def xbox_games() -> list[Game]:
    out = []
    for drive in "CDEFGH":
        root = f"{drive}:\\XboxGames"
        if os.path.isdir(root):
            for name in os.listdir(root):
                if os.path.isdir(os.path.join(root, name)) and name.lower() != "gamesave":
                    out.append(Game(name, "Xbox", os.path.join(root, name)))
    return out


def installed_games() -> list[Game]:
    games: list[Game] = []
    for finder in (steam_games, epic_games, gog_games, ubisoft_games, xbox_games):
        try:
            games += finder()
        except Exception:
            continue
    seen, uniq = set(), []
    for g in games:
        k = g.name.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(g)
    return sorted(uniq, key=lambda g: g.name.lower())


# ------------------------------------------------------------------ requirements
def gpu_tier(vram_gb: float, integrated: bool) -> str:
    if integrated or vram_gb < 2:
        return "light"       # indie / older / esports titles at low settings
    if vram_gb < 4:
        return "entry"
    if vram_gb < 8:
        return "mid"
    if vram_gb < 12:
        return "high"
    return "enthusiast"


TIER_HINT = {
    "light": "indie, older and esports games (e.g. Minecraft, CS2/Valorant/LoL at low settings)",
    "entry": "most esports titles and games up to ~2018 at 1080p low-medium",
    "mid": "most current games at 1080p medium-high",
    "high": "modern AAA games at 1080p-1440p high",
    "enthusiast": "basically everything, including 1440p/4K high",
}


def _gb(text: str, label: str) -> float | None:
    m = re.search(label + r"[^0-9]{0,40}?(\d+(?:\.\d+)?)\s*(GB|MB)", text, re.I)
    if not m:
        return None
    v = float(m.group(1))
    return v / 1024 if m.group(2).upper() == "MB" else v


def parse_requirements(req_html: str) -> dict:
    text = html.unescape(re.sub(r"<[^>]+>", " ", req_html or ""))
    text = re.sub(r"\s+", " ", text)
    out = {"ram_gb": _gb(text, r"(?:Memory|RAM)\s*:"), "storage_gb": _gb(text, r"(?:Storage|Hard (?:Disk|Drive)(?: Space)?)\s*:"),
           "vram_gb": None, "gpu_text": ""}
    g = re.search(r"Graphics\s*:\s*(.{0,160}?)(?:\s(?:DirectX|Storage|Network|Sound|Additional|Hard)\b|$)", text, re.I)
    if g:
        out["gpu_text"] = g.group(1).strip()
        vm = re.search(r"(\d+(?:\.\d+)?)\s*GB\s*(?:VRAM|of VRAM|video|dedicated|GDDR)", g.group(1), re.I) or \
            re.search(r"(?:VRAM|video memory)[^0-9]{0,10}(\d+(?:\.\d+)?)\s*GB", g.group(1), re.I)
        if vm:
            out["vram_gb"] = float(vm.group(1))
    return out


def judge(req: dict, ram_gb: float, vram_gb: float) -> tuple[str, list[str]]:
    notes, below, known = [], False, False
    if req.get("ram_gb"):
        known = True
        if ram_gb + 0.5 < req["ram_gb"]:
            below = True
            notes.append(f"needs {req['ram_gb']:g} GB RAM, you have {ram_gb:g} GB")
    if req.get("vram_gb"):
        known = True
        if vram_gb + 0.25 < req["vram_gb"]:
            below = True
            notes.append(f"needs {req['vram_gb']:g} GB VRAM, you have {vram_gb:g} GB")
    if req.get("gpu_text"):
        notes.append(f"min GPU: {req['gpu_text'][:90]}")
    return ("below" if below else "ok" if known else "unknown"), notes


def _get_json(url: str, timeout: float = 4.0):
    req = urllib.request.Request(url, headers={"User-Agent": "WinMeh/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def steam_requirements(app_id: str) -> dict | None:
    try:
        data = _get_json(f"https://store.steampowered.com/api/appdetails?appids={app_id}&filters=basic")
    except Exception:
        return None
    info = (data or {}).get(str(app_id), {})
    if not info.get("success"):
        return None
    pc = info["data"].get("pc_requirements") or {}
    if isinstance(pc, list):
        return None
    req = parse_requirements(pc.get("minimum", ""))
    req["name"] = info["data"].get("name", "")
    return req


def steam_search(name: str) -> str | None:
    q = urllib.parse.quote(name)
    try:
        data = _get_json(f"https://store.steampowered.com/api/storesearch/?term={q}&l=english&cc=us")
    except Exception:
        return None
    items = (data or {}).get("items") or []
    return str(items[0]["id"]) if items else None
