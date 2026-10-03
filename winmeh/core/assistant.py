"""The brain: route -> run a local skill instantly, otherwise stream from the small LLM.

Replies are tiny HTML snippets for the chat bubble plus a short plain `speak`
line for text-to-speech. Links use custom schemes the UI understands:
  open:<path>   reveal:<path>   act:confirm   act:cancel
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from ..config import Settings, data_dir
from ..system import apps, cache, files, games, profile
from .router import Intent, route

Emit = Callable[[str, object], None]   # ("html", str) | ("token", str) | ("done", speak_text)


@dataclass
class Pending:
    description: str
    run: Callable[[], tuple[str, str]]          # -> (html, speak)
    created: float = field(default_factory=time.time)


def _q(path: str) -> str:
    return urllib.parse.quote(path, safe="")


def link_open(path: str, label: str | None = None) -> str:
    return f'<a href="open:{_q(path)}">{html.escape(label or os.path.basename(path))}</a>'


def link_reveal(path: str, label: str = "show in folder") -> str:
    return f'<a href="reveal:{_q(path)}">{html.escape(label)}</a>'


def buttons(*pairs: tuple[str, str]) -> str:
    return "<br>" + " &nbsp; ".join(f'<a class="btn" href="act:{a}">{html.escape(l)}</a>' for l, a in pairs)


class Assistant:
    def __init__(self, settings: Settings, llm=None):
        self.s = settings
        self.llm = llm
        self.history: list[dict] = []
        self.pending: Pending | None = None
        self.profile: dict = profile.load_cached() or {}
        self.index = files.FileIndex()
        self._games: list[games.Game] | None = None
        self._shortcuts: dict[str, str] | None = None
        self.memory_path = data_dir() / "memory.json"
        self.memory: list[str] = self._load_memory()

    # ------------------------------------------------------------ background learning
    def learn(self, force: bool = False, on_done: Callable[[str], None] | None = None) -> None:
        """Refresh profile, file index and games in a background thread."""
        def work():
            try:
                self.profile = profile.collect()
                profile.save(self.profile)
                self._games = None
                stale = time.time() - self.index.last_built() > 24 * 3600
                if force or stale or self.index.count() == 0:
                    roots = list(dict.fromkeys(list(self.profile.get("folders", {}).values()) + self.s.extra_search_roots))
                    self.index.build(roots)
                self.games()
                msg = f"Learned this PC: {self.index.count():,} files indexed, {len(self.games())} games found."
            except Exception as e:
                msg = f"Couldn't finish learning this PC: {e}"
            if on_done:
                on_done(msg)
        threading.Thread(target=work, daemon=True, name="learn").start()

    def ensure_profile(self) -> dict:
        if not self.profile:
            self.profile = profile.collect()
            profile.save(self.profile)
        return self.profile

    def games(self) -> list[games.Game]:
        if self._games is None:
            self._games = games.installed_games()
        return self._games

    # ------------------------------------------------------------ entry point
    def handle(self, text: str, emit: Emit) -> None:
        text = text.strip()
        if not text:
            return
        self.history.append({"role": "user", "content": text})
        intent = route(text)
        if intent is None:
            self._chat(emit)
            return
        try:
            out_html, speak = getattr(self, f"do_{intent.name}")(intent)
        except Exception as e:  # never crash the widget over one skill
            out_html, speak = f"Sorry, that failed: {html.escape(str(e))}", "Sorry, that failed."
        self.history.append({"role": "assistant", "content": speak or _strip(out_html)})
        emit("html", out_html)
        emit("done", speak)

    # ------------------------------------------------------------ LLM fallback
    def _chat(self, emit: Emit) -> None:
        if self.llm is None or self.llm.status != "ready":
            msg = ("My language model isn't running, so I can only do built-in commands right now "
                   "(try <i>help</i>). Run <b>setup</b> from the README to download it.")
            emit("html", msg)
            emit("done", "My language model isn't running yet.")
            return
        from .llm import build_messages
        msgs = build_messages(self.history, profile.summary(self.ensure_profile()), self.memory)
        out = []
        try:
            for tok in self.llm.stream(msgs):
                out.append(tok)
                emit("token", tok)
        except Exception as e:
            emit("html", f"LLM error: {html.escape(str(e))}")
            emit("done", "")
            return
        reply = "".join(out).strip()
        self.history.append({"role": "assistant", "content": reply})
        emit("done", reply)

    # ------------------------------------------------------------ skills
    def do_help(self, _: Intent):
        return ("Try: <i>how much VRAM do I have</i> · <i>where is my wedding photo</i> · <i>clear the cache</i> · "
                "<i>what games can I run</i> · <i>can I run Elden Ring</i> · <i>stop VLC update message</i> · "
                "<i>open spotify</i> · <i>remember that…</i> · <i>rescan my pc</i>. Anything else goes to the local AI. "
                "Hold <b>Ctrl+Alt+Space</b> to talk."), "Here's what I can do."

    def do_vram(self, _: Intent):
        gpus = self.ensure_profile().get("gpus", [])
        if not gpus:
            return "I couldn't detect a graphics card.", "I couldn't detect a graphics card."
        main = gpus[0]
        lines = [f"<b>{html.escape(g['name'])}</b>: {g['vram_gb']:g} GB"
                 + (" (shared/integrated)" if g.get("integrated") else " dedicated VRAM") for g in gpus]
        speak = f"You have {main['vram_gb']:g} gigabytes of VRAM on your {main['name']}."
        return "<br>".join(lines), speak

    def do_ram(self, _: Intent):
        r = self.ensure_profile().get("ram_gb", 0)
        return f"You have <b>{r:g} GB</b> of RAM.", f"You have {r:g} gigabytes of RAM."

    def do_cpu(self, _: Intent):
        p = self.ensure_profile()
        return f"<b>{html.escape(p.get('cpu', '?'))}</b> with {p.get('cores')} threads.", f"You have a {p.get('cpu')}."

    def do_disk(self, _: Intent):
        d = self.ensure_profile().get("disks", [])
        rows = [f"<b>{x['mount']}</b> {x['free_gb']:g} GB free of {x['total_gb']:g} GB" for x in d]
        main = d[0] if d else None
        speak = f"Drive {main['mount'][0]} has {main['free_gb']:.0f} gigabytes free." if main else "No drives found."
        return "<br>".join(rows) or "No drives found.", speak

    def do_os(self, _: Intent):
        o = self.ensure_profile().get("os", "?")
        return html.escape(o), f"You're running {o.split('(')[0]}."

    def do_specs(self, _: Intent):
        p = self.ensure_profile()
        g = p.get("gpus") or [{}]
        rows = [f"<b>OS</b> {html.escape(p.get('os', '?'))}", f"<b>CPU</b> {html.escape(p.get('cpu', '?'))} ({p.get('cores')} threads)",
                f"<b>RAM</b> {p.get('ram_gb')} GB"]
        rows += [f"<b>GPU</b> {html.escape(x.get('name', '?'))} ({x.get('vram_gb', 0):g} GB)" for x in p.get("gpus", [])]
        rows += [f"<b>{x['mount']}</b> {x['free_gb']:g}/{x['total_gb']:g} GB free" for x in p.get("disks", [])]
        speak = f"{p.get('cpu', '')}, {p.get('ram_gb')} gigabytes of RAM, and a {g[0].get('name', 'unknown')} graphics card."
        return "<br>".join(rows), speak

    def do_time(self, _: Intent):
        now = dt.datetime.now()
        s = now.strftime("%A, %d %B %Y, %I:%M %p")
        return s, now.strftime("It's %I:%M %p, %A.")

    def do_find_file(self, intent: Intent):
        q = intent.args.get("q", "")
        terms, kind = files.parse_query(q)
        if not terms:
            return "What should I look for? e.g. <i>where is my wedding photo</i>", "What should I look for?"
        hits, source = files.search(q, self.index)
        if not hits:
            extra = " I'm still indexing your files - try again in a minute." if self.index.indexing else ""
            return (f"I couldn't find anything matching <b>{html.escape(' '.join(t[0] for t in terms))}</b>"
                    f"{' ' + kind + 's' if kind else ''}.{extra} If your photos aren't named or tagged, "
                    "try a date or place, e.g. <i>photos from 2019</i>."), "I couldn't find it."
        folders = files.group_by_folder(hits)
        top = [f"{link_open(h.path)} · {link_reveal(h.path)}" for h in hits[:6]]
        out = "<br>".join(top)
        if len(folders) and folders[0][1] >= 2:
            d = folders[0][0]
            out = f"Most are in {link_open(d, os.path.basename(d) or d)}<br>" + out
        out += f'<br><span class="dim">{len(hits)} results · {source}</span>'
        speak = f"Found {len(hits)} matches. The top one is {os.path.splitext(hits[0].name)[0]}."
        return out, speak

    def do_open_app(self, intent: Intent):
        name = intent.args.get("app", "")
        if self._shortcuts is None:
            self._shortcuts = apps.start_menu_shortcuts()
        lnk = apps.find_app(name, self._shortcuts)
        if not lnk:
            return f"I couldn't find an app called <b>{html.escape(name)}</b>.", f"I couldn't find {name}."
        apps.open_path(lnk)
        label = os.path.splitext(os.path.basename(lnk))[0]
        return f"Opening <b>{html.escape(label)}</b>.", f"Opening {label}."

    def do_vlc_updates(self, _: Intent):
        ok, msg = apps.disable_vlc_update_popup()
        return html.escape(msg), ("Done. VLC won't nag you about updates anymore." if ok else msg)

    def do_clear_cache(self, _: Intent):
        found = cache.scan()
        total = sum(t.size for t in found)
        if total < 1024**2:
            return "Your caches are already clean (under 1 MB) - nothing worth clearing.", "Your caches are already clean."
        rows = [f"<b>{html.escape(t.label)}</b> {cache.human(t.size)}"
                + (f' <span class="dim">({html.escape(t.note)})</span>' if t.note else "")
                + "".join(f'<br><span class="warn">{html.escape(x)}</span>' for x in t.extra) for t in found]

        def run():
            freed, skipped, done = cache.clear(found)
            msg = f"Freed <b>{cache.human(freed)}</b>."
            if skipped:
                msg += f' <span class="dim">{skipped} items were in use and skipped.</span>'
            return msg, f"Done. I freed {cache.human(freed)}."

        self.pending = Pending("clear caches", run)
        html_out = (f"I can safely clear about <b>{cache.human(total)}</b>:<br>" + "<br>".join(rows)
                    + "<br>Only cache contents are removed; your files, passwords and logins stay."
                    + buttons(("Clear now", "confirm"), ("Cancel", "cancel")))
        return html_out, f"I found {cache.human(total)} of cache. Say yes to clear it."

    def do_games(self, _: Intent):
        p = self.ensure_profile()
        g = (p.get("gpus") or [{"vram_gb": 0, "integrated": True, "name": "unknown GPU"}])[0]
        tier = games.gpu_tier(g.get("vram_gb", 0), g.get("integrated", True))
        installed = self.games()
        head = (f"Your <b>{html.escape(g.get('name', '?'))}</b> ({g.get('vram_gb', 0):g} GB) + {p.get('ram_gb')} GB RAM "
                f"is a <b>{tier}</b> gaming PC: good for {games.TIER_HINT[tier]}.")
        if not installed:
            return head + "<br>I didn't find any installed games (Steam, Epic, GOG, Ubisoft, Xbox).", \
                f"I didn't find installed games, but your PC is good for {games.TIER_HINT[tier]}."
        if self.s.online_lookups:
            self._check_steam(installed[:25], p.get("ram_gb", 0), g.get("vram_gb", 0))
        icon = {"ok": "✅", "below": "⚠️", "unknown": "•", "": "•"}
        rows = [f"{icon[x.verdict]} {link_open(x.path, x.name) if x.path else html.escape(x.name)}"
                f' <span class="dim">{x.store}{" · " + str(x.size_gb) + " GB" if x.size_gb else ""}</span>'
                + (f'<br><span class="warn">&nbsp;&nbsp;{html.escape("; ".join(n for n in x.notes if "needs" in n))}</span>'
                   if x.verdict == "below" else "") for x in installed]
        ok = sum(1 for x in installed if x.verdict == "ok")
        foot = '<br><span class="dim">✅ meets the published minimum RAM/VRAM · ⚠️ below it · • not checked. Ask <i>can I run &lt;game&gt;</i> for any title.</span>'
        return head + f"<br><b>{len(installed)} installed:</b><br>" + "<br>".join(rows) + foot, \
            f"You have {len(installed)} games installed{f', and {ok} meet their minimum requirements' if ok else ''}."

    def _check_steam(self, gs: list[games.Game], ram: float, vram: float) -> None:
        from concurrent.futures import ThreadPoolExecutor

        def one(game: games.Game):
            if game.store != "Steam" or game.verdict:
                return
            req = games.steam_requirements(game.app_id)
            if req:
                game.verdict, game.notes = games.judge(req, ram, vram)
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(one, gs))

    def do_can_run(self, intent: Intent):
        name = intent.args.get("game", "")
        p = self.ensure_profile()
        g = (p.get("gpus") or [{"vram_gb": 0, "name": "?"}])[0]
        if not self.s.online_lookups:
            return "Online lookups are off, so I can't fetch requirements.", "Online lookups are off."
        appid = next((x.app_id for x in self.games() if x.store == "Steam" and x.name.lower() == name.lower()), None) \
            or games.steam_search(name)
        req = games.steam_requirements(appid) if appid else None
        if not req:
            return f"I couldn't find requirements for <b>{html.escape(name)}</b> on Steam.", "I couldn't find its requirements."
        verdict, notes = games.judge(req, p.get("ram_gb", 0), g.get("vram_gb", 0))
        title = html.escape(req.get("name") or name)
        mine = f"You: {p.get('ram_gb')} GB RAM, {html.escape(g.get('name', '?'))} {g.get('vram_gb', 0):g} GB."
        detail = "<br>".join(html.escape(n) for n in notes)
        if verdict == "ok":
            return (f"✅ <b>{title}</b>: you meet the minimum RAM/VRAM.<br>{detail}<br><span class='dim'>{mine} "
                    "GPU model speed isn't compared yet - check the min GPU above.</span>"), f"Yes, you meet the minimum for {title}."
        if verdict == "below":
            return f"⚠️ <b>{title}</b> is probably too heavy.<br>{detail}<br><span class='dim'>{mine}</span>", \
                f"Probably not. {notes[0] if notes else ''}"
        return f"<b>{title}</b>: Steam doesn't list clear numbers.<br>{detail}<br><span class='dim'>{mine}</span>", \
            "Steam doesn't list clear requirements for that one."

    def do_confirm(self, _: Intent):
        if not self.pending or time.time() - self.pending.created > 300:
            self.pending = None
            return "There's nothing waiting for confirmation.", "Nothing to confirm."
        p, self.pending = self.pending, None
        return p.run()

    def do_cancel(self, _: Intent):
        had = self.pending is not None
        self.pending = None
        return ("Cancelled." if had else "Okay."), ("Cancelled." if had else "Okay.")

    def do_remember(self, intent: Intent):
        fact = intent.args.get("fact", "").strip()
        if fact:
            self.memory.append(fact)
            self.memory_path.write_text(json.dumps(self.memory, indent=1), encoding="utf-8")
        return f"Got it, I'll remember: <i>{html.escape(fact)}</i>", "Got it."

    def do_reindex(self, _: Intent):
        self.learn(force=True)
        return "Re-learning this PC in the background (specs, files, games)…", "Okay, rescanning your PC."

    def _load_memory(self) -> list[str]:
        try:
            return json.loads(self.memory_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []


def _strip(h: str) -> str:
    import re
    return html.unescape(re.sub(r"<[^>]+>", " ", h)).strip()
