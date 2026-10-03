"""Install apps from trusted package managers - never random download sites.

Windows: winget (Microsoft's catalog, installers are hash-checked).
Linux:   flatpak (Flathub) if present, otherwise the distro's apt / dnf / pacman.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import proc

IS_WINDOWS = proc.IS_WINDOWS

# Popular apps so the common cases are instant and unambiguous: winget id, flatpak id, native package.
KNOWN: dict[str, tuple[str, str, str]] = {
    "vlc": ("VideoLAN.VLC", "org.videolan.VLC", "vlc"),
    "chrome": ("Google.Chrome", "com.google.Chrome", ""),
    "google chrome": ("Google.Chrome", "com.google.Chrome", ""),
    "firefox": ("Mozilla.Firefox", "org.mozilla.firefox", "firefox"),
    "brave": ("Brave.Brave", "com.brave.Browser", ""),
    "spotify": ("Spotify.Spotify", "com.spotify.Client", ""),
    "discord": ("Discord.Discord", "com.discordapp.Discord", ""),
    "steam": ("Valve.Steam", "com.valvesoftware.Steam", "steam"),
    "telegram": ("Telegram.TelegramDesktop", "org.telegram.desktop", "telegram-desktop"),
    "zoom": ("Zoom.Zoom", "us.zoom.Zoom", ""),
    "obs": ("OBSProject.OBSStudio", "com.obsproject.Studio", "obs-studio"),
    "gimp": ("GIMP.GIMP", "org.gimp.GIMP", "gimp"),
    "libreoffice": ("TheDocumentFoundation.LibreOffice", "org.libreoffice.LibreOffice", "libreoffice"),
    "7zip": ("7zip.7zip", "", "p7zip-full"),
    "7-zip": ("7zip.7zip", "", "p7zip-full"),
    "vscode": ("Microsoft.VisualStudioCode", "com.visualstudio.code", ""),
    "vs code": ("Microsoft.VisualStudioCode", "com.visualstudio.code", ""),
    "visual studio code": ("Microsoft.VisualStudioCode", "com.visualstudio.code", ""),
    "notepad++": ("Notepad++.Notepad++", "", ""),
    "git": ("Git.Git", "", "git"),
    "python": ("Python.Python.3.12", "", "python3"),
    "node": ("OpenJS.NodeJS.LTS", "", "nodejs"),
    "nodejs": ("OpenJS.NodeJS.LTS", "", "nodejs"),
    "ollama": ("Ollama.Ollama", "", ""),
    "audacity": ("Audacity.Audacity", "org.audacityteam.Audacity", "audacity"),
    "thunderbird": ("Mozilla.Thunderbird", "org.mozilla.Thunderbird", "thunderbird"),
    "qbittorrent": ("qBittorrent.qBittorrent", "org.qbittorrent.qBittorrent", "qbittorrent"),
    "handbrake": ("HandBrake.HandBrake", "fr.handbrake.ghb", "handbrake"),
    "everything": ("voidtools.Everything", "", ""),
    "powertoys": ("Microsoft.PowerToys", "", ""),
    "epic games": ("EpicGames.EpicGamesLauncher", "", ""),
    "epic games launcher": ("EpicGames.EpicGamesLauncher", "", ""),
    "anydesk": ("AnyDesk.AnyDesk", "com.anydesk.Anydesk", ""),
    "teamviewer": ("TeamViewer.TeamViewer", "", ""),
    "slack": ("SlackTechnologies.Slack", "com.slack.Slack", ""),
    "skype": ("Microsoft.Skype", "com.skype.Client", ""),
    "kdenlive": ("KDE.Kdenlive", "org.kde.kdenlive", "kdenlive"),
    "inkscape": ("Inkscape.Inkscape", "org.inkscape.Inkscape", "inkscape"),
    "blender": ("BlenderFoundation.Blender", "org.blender.Blender", "blender"),
    "winrar": ("RARLab.WinRAR", "", ""),
    "adobe reader": ("Adobe.Acrobat.Reader.64-bit", "", ""),
    "acrobat reader": ("Adobe.Acrobat.Reader.64-bit", "", ""),
    "java": ("EclipseAdoptium.Temurin.21.JRE", "", "default-jre"),
}


@dataclass
class Package:
    name: str
    id: str
    manager: str          # winget | flatpak | apt | dnf | pacman
    version: str = ""

    def install_cmd(self) -> list[str]:
        return {
            "winget": ["winget", "install", "--id", self.id, "-e", "--silent", "--disable-interactivity",
                       "--accept-package-agreements", "--accept-source-agreements"],
            "flatpak": ["flatpak", "install", "-y", "--noninteractive", "flathub", self.id],
            "apt": ["pkexec", "apt-get", "install", "-y", self.id],
            "dnf": ["pkexec", "dnf", "install", "-y", self.id],
            "pacman": ["pkexec", "pacman", "-S", "--noconfirm", self.id],
        }[self.manager]


def available_managers() -> list[str]:
    if IS_WINDOWS:
        return ["winget"] if proc.which("winget") else []
    return [m for m in ("flatpak", "apt", "dnf", "pacman") if proc.which("apt-get" if m == "apt" else m)]


# ------------------------------------------------------------------ parsing
def parse_winget_table(text: str) -> list[Package]:
    """Parse `winget search` output (a fixed-width table)."""
    lines = [l.rstrip() for l in re.split(r"[\r\n]+", text)]
    for i, line in enumerate(lines[:-1]):
        if re.fullmatch(r"-{10,}", lines[i + 1].strip()) and " Id" in line:
            head = line[line.find("Name"):] if "Name" in line else line
            offset = len(line) - len(head)
            cols = [m.start() + offset for m in re.finditer(r"\S+", head)]
            names = re.findall(r"\S+", head)
            out = []
            for row in lines[i + 2:]:
                if not row.strip():
                    continue
                cells = [row[cols[j]:cols[j + 1] if j + 1 < len(cols) else None].strip() for j in range(len(cols))]
                rec = dict(zip(names, cells))
                if rec.get("Id"):
                    out.append(Package(rec.get("Name", rec["Id"]), rec["Id"], "winget", rec.get("Version", "")))
            return out
    return []


def parse_flatpak_search(text: str) -> list[Package]:
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and "." in parts[1]:
            out.append(Package(parts[0].strip(), parts[1].strip(), "flatpak", parts[2].strip() if len(parts) > 2 else ""))
    return out


# ------------------------------------------------------------------ lookup
def _native_exists(manager: str, pkg: str) -> bool:
    cmd = {"apt": ["apt-cache", "show", pkg], "dnf": ["dnf", "info", "-q", pkg], "pacman": ["pacman", "-Si", pkg]}[manager]
    rc, _ = proc.run(cmd, timeout=30)
    return rc == 0


def find(name: str) -> list[Package]:
    """Best candidates for an app name, most likely first."""
    key = name.lower().strip()
    managers = available_managers()
    known = KNOWN.get(key)
    out: list[Package] = []
    if known:
        win, flat, native = known
        if "winget" in managers and win:
            out.append(Package(name, win, "winget"))
        if "flatpak" in managers and flat:
            out.append(Package(name, flat, "flatpak"))
        for m in ("apt", "dnf", "pacman"):
            if m in managers and native:
                out.append(Package(name, native, m))
        if out:
            return out[:1]
    if "winget" in managers:
        rc, text = proc.run(["winget", "search", name, "--accept-source-agreements", "--disable-interactivity"],
                            timeout=60)
        hits = [p for p in parse_winget_table(text) if not p.id.lower().startswith("msstore")]
        return _rank(hits, key)[:5]
    if "flatpak" in managers:
        rc, text = proc.run(["flatpak", "search", "--columns=name,application,version", name], timeout=60)
        out = _rank(parse_flatpak_search(text), key)[:5]
        if out:
            return out
    for m in ("apt", "dnf", "pacman"):
        if m in managers:
            pkg = re.sub(r"[^a-z0-9.+\-]", "-", key)
            if _native_exists(m, pkg):
                return [Package(name, pkg, m)]
    return []


def _rank(pkgs: list[Package], key: str) -> list[Package]:
    def score(p: Package) -> tuple:
        n = p.name.lower()
        return (n != key, not n.startswith(key), key not in n, len(n))
    return sorted(pkgs, key=score)


def is_installed(p: Package) -> bool:
    if p.manager == "winget":
        rc, out = proc.run(["winget", "list", "--id", p.id, "-e", "--accept-source-agreements",
                            "--disable-interactivity"], timeout=60)
        return rc == 0 and p.id.lower() in out.lower()
    if p.manager == "flatpak":
        rc, out = proc.run(["flatpak", "info", p.id], timeout=20)
        return rc == 0
    cmd = {"apt": ["dpkg", "-s", p.id], "dnf": ["rpm", "-q", p.id], "pacman": ["pacman", "-Q", p.id]}[p.manager]
    return proc.run(cmd, timeout=20)[0] == 0


def install(p: Package) -> tuple[bool, str]:
    rc, out = proc.run(p.install_cmd(), timeout=1800)
    if rc == 0:
        return True, f"Installed {p.name}."
    tail = " ".join(out.strip().splitlines()[-3:])[-300:]
    if p.manager == "winget" and ("0x8a150011" in out.lower() or "already installed" in out.lower()):
        return True, f"{p.name} is already installed."
    if rc in (126, 127) and p.manager in ("apt", "dnf", "pacman"):
        return False, "The password prompt was cancelled."
    return False, f"Install failed ({rc}): {tail}"
