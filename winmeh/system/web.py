"""Open websites and web searches in the user's default browser."""

from __future__ import annotations

import re
import urllib.parse
import webbrowser

SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com", "twitter": "https://x.com",
    "x": "https://x.com", "reddit": "https://www.reddit.com", "github": "https://github.com",
    "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.com", "wikipedia": "https://www.wikipedia.org",
    "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai", "linkedin": "https://www.linkedin.com",
    "whatsapp web": "https://web.whatsapp.com", "maps": "https://maps.google.com", "google maps": "https://maps.google.com",
    "outlook": "https://outlook.live.com", "drive": "https://drive.google.com", "google drive": "https://drive.google.com",
    "news": "https://news.google.com", "weather": "https://www.google.com/search?q=weather",
}
DOMAIN = re.compile(r"^(https?://)?([a-z0-9\-]+\.)+[a-z]{2,}(/\S*)?$", re.I)


def site_url(name: str) -> str | None:
    n = name.lower().strip().removesuffix(" website").removesuffix(" site").strip()
    if n in SITES:
        return SITES[n]
    if DOMAIN.match(n):
        return n if n.startswith("http") else "https://" + n
    return None


def search_url(query: str, where: str = "") -> str:
    q = urllib.parse.quote_plus(query.strip())
    if "youtube" in where:
        return f"https://www.youtube.com/results?search_query={q}"
    return f"https://www.google.com/search?q={q}"


def open_url(url: str) -> bool:
    return webbrowser.open(url, new=2)
