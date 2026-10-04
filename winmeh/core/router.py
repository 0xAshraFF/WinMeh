"""Instant intent routing: regex rules, no model. Runs in well under 1 ms.

Anything the rules can't place goes to the small LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Intent:
    name: str
    args: dict = field(default_factory=dict)


_R = lambda p: re.compile(p, re.I)  # noqa: E731

RULES: list[tuple[str, re.Pattern]] = [
    ("confirm", _R(r"^\s*(yes|yeah|yep|sure|ok(ay)?|do it|go ahead|confirm|proceed|clear (it|them))\s*[.!]*\s*$")),
    ("cancel", _R(r"^\s*(no|nope|cancel|stop|never ?mind|don'?t)\s*[.!]*\s*$")),
    ("pick", _R(r"^\s*(#|number |option )?(?P<n>[1-5])\s*$")),
    ("help", _R(r"^\s*(help|what can you do|commands)\s*\??\s*$")),
    ("confirm_specs", _R(r"^\s*yes,?\s+(and\s+)?include\s+(my\s+)?(pc\s+|computer\s+)?(specs|details)\s*[.!]*$")),
    # settings and hand-off
    ("set_ai", _R(r"^\s*(set|change|switch)\s+(my\s+)?(big\s+|online\s+|default\s+)?ai\s+to\s+(?P<svc>(?:chatgpt|chat gpt|claude code|claude|deepseek|deep seek))\s*[.!]*$|"
                  r"^\s*(use|prefer)\s+(?P<svc2>(?:chatgpt|chat gpt|claude code|claude|deepseek|deep seek))\s+for\s+(big|hard|difficult|bigger|online)\s+(questions|tasks|stuff|things)\s*[.!]*$")),
    ("ask_ai", _R(r"^\s*(please\s+)?ask\s+(?P<svc>(?:chatgpt|chat gpt|claude code|claude|deepseek|deep seek))\s*(to|about|:|,|-)?\s+(?P<q>.+)$")),
    ("retrain", _R(r"\b(retrain|re-train)\b|\blearn from my (choices|answers|feedback)\b")),
    ("accessibility", _R(r"\b(turn|switch)\s+(?P<state>on|off)\s+(the\s+)?(accessibility|large text|big text|high contrast)\b|"
                         r"\b(accessibility|large text|big text|high contrast)(\s+mode)?\s+(?P<state2>on|off)\b")),
    ("kid_safe", _R(r"\b(turn|switch)\s+(?P<state>on|off)\s+(the\s+)?(kid|kids|child|children)[- ]?(safe|safety|mode)|"
                    r"\b(kid|kids|child)[- ]?(safe|safety)(\s+mode)?\s+(?P<state2>on|off)\b|\bparental controls?\s+(?P<state3>on|off)\b")),
    ("calc", _R(r"^\s*(what(?:'s| is)\s+|calculate\s+|compute\s+|how much is\s+)?"
                r"(?P<expr>[\d.]+\s*(?:%|percent)\s+of\s+[\d.]+|[-+*/x×÷^%().,\d\s]*\d[-+*/x×÷^%().,\d\s]*)\s*[?=]?\s*$")),
    ("remember", _R(r"^\s*remember (that )?(?P<fact>.+)$")),
    ("vlc_updates", _R(r"\bvlc\b.*\b(update|updates|notif\w*|message|popup|pop-up|prompt|nag)|"
                       r"\b(update|notif\w*|message|popup|pop-up|nag)\b.*\bvlc\b")),
    ("can_run", _R(r"\bcan (i|my (pc|computer|laptop|machine)|this (pc|machine|computer|laptop)|it) "
                   r"(run|play|handle) (?P<game>.+?)\??$")),
    ("drivers", _R(r"\bdrivers?\b|\bdevice manager\b|\b(device|hardware)s? (not working|problem|issue)")),
    # web before files: "search the web for X" / "google X" / "search X on youtube"
    ("web_search", _R(r"^\s*google\s+(?P<q>.+)$|"
                      r"\b(search|look up|find)\s+(the\s+)?(?P<where>web|internet|online|google|youtube)\s+(for\s+)?(?P<q2>.+)$|"
                      r"^\s*(search|look up|find)\s+(for\s+)?(?P<q3>.+?)\s+(on|in|at)\s+(the\s+)?(?P<where2>web|internet|google|youtube|online)\s*$|"
                      r"^\s*look up\s+(?P<q4>.+)$")),
    ("open_site", _R(r"^\s*(go to|visit|browse( to)?|open (the )?website)\s+(?P<site>.+?)\s*$")),
    ("install", _R(r"^\s*(please\s+)?(can you\s+)?(install|download|set ?up|reinstall)\s+(the\s+|a\s+)?(?P<pkg>.+?)"
                   r"(\s+(app|application|program|software|for me|please))*\s*[.!?]*$")),
    ("games", _R(r"\bgames?\b")),
    ("clear_cache", _R(r"\b(clear|clean|delete|empty|wipe|remove|free up|flush)\b.*\b(cache|caches|temp|junk|temporary)\b|"
                       r"\b(cache|temp files?)\b.*\b(clear|clean|delete)")),
    ("reindex", _R(r"\b(re-?index|rescan|refresh|re-?learn) (my )?(files|machine|pc|profile|system|computer)\b")),
    ("vram", _R(r"\b(vram|video ?memory|graphics memory|gpu memory|graphics card|gpu|video card)\b")),
    ("ram", _R(r"\b(ram|memory)\b")),
    ("cpu", _R(r"\b(cpu|processor|cores?)\b")),
    ("disk", _R(r"\b(disk|drive|storage|ssd|hdd)\b.*\b(space|free|left|full|size)|\b(space|storage)\b.*\b(left|free)|how full")),
    # "wher" / "whr": voice + fast typing produce these a lot
    ("find_file", _R(r"^\s*(wh?ere?\s*(is|are|'s|did i (put|save|keep))|wh?ere'?s|whr|find|locate|search( for)?|look for|show me)\b(?P<q>.+)$")),
    ("open_app", _R(r"^\s*(open|launch|start|run)\s+(?P<app>.+?)\s*$")),
    ("specs", _R(r"\b(specs?|specifications|system info|about (my|this) (pc|machine|computer|laptop)|my (pc|machine|computer|laptop)\b)")),
    ("os", _R(r"\b(windows version|which windows|what windows|os version|operating system)\b")),
    ("time", _R(r"\b(what time|the time|what'?s the date|today'?s date|what day)\b")),
]


def route(text: str) -> Intent | None:
    t = text.strip()
    if not t:
        return None
    for name, rx in RULES:
        m = rx.search(t)
        if not m:
            continue
        args = {k: v.strip(" ?.!") for k, v in m.groupdict().items() if v}
        # 'open' + a file-ish thing is a file search ("open my wedding photo")
        if name == "open_app" and re.search(r"\b(photo|picture|pic|video|file|document|pdf|folder)s?\b", t, re.I):
            return Intent("find_file", {"q": args.get("app", t)})
        if name == "web_search":
            q = next((args[k] for k in ("q", "q2", "q3", "q4") if args.get(k)), t)
            where = (args.get("where") or args.get("where2") or "").lower()
            return Intent("web_search", {"q": q, "where": where})
        if name == "install" and re.search(r"\b(games?|drivers?)\b", args.get("pkg", ""), re.I):
            continue    # "get me the games list", "download drivers" belong to other rules
        if name == "calc" and not re.search(r"\d\s*([-+*/x×÷^%]|percent)\s*[\d(]|percent of|% of", args.get("expr", "")):
            continue    # a bare number isn't a calculation
        if name in ("accessibility", "kid_safe"):
            return Intent(name, {"on": (args.get("state") or args.get("state2") or args.get("state3")) == "on"})
        if name in ("set_ai", "ask_ai"):
            svc = (args.get("svc") or args.get("svc2") or "").lower().replace(" ", "")
            svc = {"chatgpt": "chatgpt", "claudecode": "claude_code", "claude": "claude", "deepseek": "deepseek"}[svc]
            return Intent(name, {"svc": svc, "q": args.get("q", "")})
        if name == "can_run" and re.search(r"\bgames?\b", args.get("game", ""), re.I):
            return Intent("games", {})
        return Intent(name, args)
    return None
