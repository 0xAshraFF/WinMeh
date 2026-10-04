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
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from ..config import IS_WINDOWS, Settings, data_dir
from ..system import apps, cache, drivers, files, games, packages, profile, web
from . import calc, classifier, handoff
from .router import Intent, route

Emit = Callable[[str, object], None]   # ("html", str) | ("token", str) | ("done", speak_text)


@dataclass
class Pending:
    description: str
    run: Callable[[], tuple[str, str]]          # -> (html, speak)
    created: float = field(default_factory=time.time)
    options: list = field(default_factory=list)  # alternatives the user can pick with "1", "2", ...
    pick: Callable[[int], tuple[str, str]] | None = None
    on_cancel: Callable[[], tuple[str, str] | None] | None = None   # what "No" does (None return = it streamed)
    extra: dict = field(default_factory=dict)    # more answers, e.g. {"specs": callable} for "yes, include my specs"


def _q(path: str) -> str:
    return urllib.parse.quote(path, safe="")


def link_open(path: str, label: str | None = None) -> str:
    return f'<a href="open:{_q(path)}">{html.escape(label or os.path.basename(path))}</a>'


def link_reveal(path: str, label: str = "show in folder") -> str:
    return f'<a href="reveal:{_q(path)}">{html.escape(label)}</a>'


def buttons(*pairs: tuple[str, str]) -> str:
    return "<br>" + " &nbsp;·&nbsp; ".join(f'<a class="btn" href="act:{a}">{html.escape(l)}</a>' for l, a in pairs)


WEAK = re.compile(r"\b(i'?m not sure|i don'?t know|i do not know|i cannot|i can'?t (help|answer|do)|"
                                r"as an ai|i am unable|i'?m unable|no information|not able to)\b", re.I)


def looks_weak(reply: str) -> bool:
    """A local answer that probably didn't help: very short, or the model saying it doesn't know."""
    r = reply.strip()
    return len(r) < 15 or bool(WEAK.search(r))


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
        self._emit: Emit = lambda kind, payload: None
        self.classifier = classifier.load_default()
        # The widget replaces these with UI-thread versions; the defaults work headless and in tests.
        self.copy_to_clipboard: Callable[[str], bool] = handoff.system_copy
        self.open_url: Callable[[str], bool] = web.open_url
        self.ask_pin: Callable[[str, bool], str | None] = lambda prompt, new=False: None
        self.on_settings_changed: Callable[[], None] = lambda: None
        self._available: dict[str, str] | None = None

    # ------------------------------------------------------------ plain-language confirmations
    def ask(self, question: str, yes: str, no: str, *more: tuple[str, str], detail: str = "") -> str:
        """A question with buttons. Accessibility mode: the question in big type, then one big
        button per line whose label says exactly what happens ("✅ Yes, ask ChatGPT")."""
        if not self.s.accessible:
            return question + (f"<br>{detail}" if detail else "") + buttons((yes, "confirm"), *more, (no, "cancel"))

        def plain(label: str, word: str, mark: str) -> str:
            rest = label[len(word):].lstrip(" ,") if label.lower().startswith(word.lower()) else label
            return f"{mark} {word}, {rest[0].lower() + rest[1:]}" if rest else f"{mark} {word}"
        rows = [(plain(yes, "Yes", "✅"), "confirm")] + [(plain(l, "Yes", "✅"), a) for l, a in more] + \
               [(plain(no, "No", "❌"), "cancel")]
        links = "".join(f'<br><br><a class="btn" href="act:{a}">{html.escape(l)}</a>' for l, a in rows)
        return f'<span class="big">{question}</span>' + (f"<br>{detail}" if detail else "") + links

    def _parent_ok(self, what: str) -> bool:
        """Kid-safe mode: a parent PIN gates installs, hand-offs and unknown links. Otherwise always OK."""
        if not self.s.kid_safe:
            return True
        return self.s.check_pin(self.ask_pin(f"Parent PIN needed to {what}", False))

    def progress(self, h: str) -> None:
        self._emit("html", h + ' <span class="dim">…</span>')

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
        self._emit = emit          # lets long skills show progress before their final answer
        try:
            result = self._route_unknown(text, emit) if intent is None else getattr(self, f"do_{intent.name}")(intent)
        except Exception as e:  # never crash the widget over one skill
            result = f"Sorry, that failed: {html.escape(str(e))}", "Sorry, that failed."
        if result is None:         # the skill already streamed its answer
            return
        out_html, speak = result
        self.history.append({"role": "assistant", "content": speak or _strip(out_html)})
        emit("html", out_html)
        emit("done", speak)

    # ------------------------------------------------------------ LLM fallback
    def local_ready(self) -> bool:
        return self.llm is not None and self.llm.status == "ready"

    def _chat(self, emit: Emit, safety_net: bool = True) -> None:
        if not self.local_ready():
            msg = ("My language model isn't running, so I can only do built-in commands right now "
                   "(try <i>help</i>). It downloads automatically on first start, or use Ollama.")
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
        question = next((m["content"] for m in reversed(self.history) if m["role"] == "user"), "")
        if safety_net and question and looks_weak(reply):
            # Safety net: the small model struggled - offer the bigger AI afterwards (still asks first).
            offer, _ = self._offer_handoff(question, None, weak=True)
            emit("extra", offer)
        emit("done", reply)

    # ------------------------------------------------------------ "who should handle this?"
    def _route_unknown(self, text: str, emit: Emit):
        d = self.classifier.decide(text) if (self.classifier and self.s.smart_routing) else None
        if d is None or d.action == "local":
            if self.local_ready():
                self._chat(emit)
                return None
            return self._offer_handoff(text, d, offline=True)
        if d.label == classifier.WEB:
            intent = Intent("web_search", {"q": text, "where": ""})
            if d.action == "act":
                return self.do_web_search(intent)
            self.pending = Pending("search the web", lambda: self._chosen(text, d, classifier.WEB, self.do_web_search(intent)),
                                   on_cancel=lambda: self._answer_locally(text, d))
            return (self.ask(f"This sounds like it needs fresh information. Search the web for "
                             f"<b>{html.escape(text)}</b>?", "Search the web", "Answer here"),
                    "Should I search the web for that?")
        return self._offer_handoff(text, d)

    def _chosen(self, text: str, d, label: str, result):
        if d is not None:
            classifier.log_feedback(text, d.label, label)
        return result

    def _answer_locally(self, text: str, d):
        if d is not None:
            classifier.log_feedback(text, d.label, classifier.LOCAL)
        if not self.local_ready():
            return "Okay, I won't send it anywhere.", "Okay."
        # Drop the offer and the "no" so the model answers the original question, not the word "cancel".
        while self.history and not (self.history[-1]["role"] == "user" and self.history[-1]["content"] == text):
            self.history.pop()
        if not self.history:
            self.history.append({"role": "user", "content": text})
        self._chat(self._emit, safety_net=False)
        return None

    # ------------------------------------------------------------ hand-off to an online AI (always asks)
    def available_ai(self) -> dict[str, str]:
        if self._available is None:
            self._available = handoff.detect()
        return self._available

    def _offer_handoff(self, query: str, d, offline: bool = False, weak: bool = False, service: str | None = None):
        svc = handoff.SERVICES[service] if service else handoff.choose(self.s.handoff_service, self.available_ai())
        text = handoff.compose(query)
        specs = profile.summary(self.ensure_profile())
        if weak:
            head = f"That answer may not be good enough. Want me to ask {svc.name} instead?"
        elif offline:
            head = f"My small AI isn't running right now. Want me to ask {svc.name}?"
        elif d is not None and d.action == "ask":
            head = f"This might need a bigger AI. Want me to ask {svc.name}?"
        else:
            head = f"This needs a bigger AI. Want me to ask {svc.name}?"
        how = ("It opens in Claude Code, which can change files." if svc.key == "claude_code" else
               "I'll copy it so you can paste it there." if not svc.query_url else "It opens in your browser.")
        detail = (f"<span class='dim'>This exact text will be sent - nothing else, not your files or PC "
                  f"details:</span>{preview_box(text)}<span class='dim'>{how}</span>")
        more = (("Yes, include my PC specs", "specs"),) if specs else ()
        self.pending = Pending(
            f"ask {svc.name}", lambda: self._chosen(query, d, classifier.ONLINE, self._send(svc, text)),
            on_cancel=lambda: self._answer_locally(query, d) if not weak else ("Okay, I'll leave it there.", "Okay."),
            extra={"specs": lambda: self._chosen(query, d, classifier.ONLINE,
                                                 self._send(svc, handoff.compose(query, specs)))} if specs else {})
        if specs:
            detail += f"<br><span class='dim'>“Include my PC specs” adds this line: (My computer: {html.escape(specs)})</span>"
        no = "No, answer here" if self.local_ready() and not weak else "No"
        return (self.ask(head, f"Yes, ask {svc.name}", no, *more, detail=detail),
                f"This needs a bigger AI. Should I ask {svc.name}?")

    def _send(self, svc, text: str):
        if not self._parent_ok(f"ask {svc.name}"):
            return "That needs the parent PIN, so I didn't send anything.", "I need the parent PIN for that."
        if svc.key == "claude_code":
            folder = self.s.handoff_folder or os.path.expanduser("~")
            self.pending = Pending("open Claude Code", lambda: _say(*handoff.launch_claude_code(text, folder)))
            return (self.ask(f"⚠️ Claude Code can <b>read and change files</b> in <b>{html.escape(folder)}</b>. "
                             f"Open it there with your question?", "Open Claude Code in this folder", "Don't open it"),
                    f"Claude Code can change files in {folder}. Are you sure?")
        url = handoff.build_url(svc, text)
        if url:
            self.open_url(url)
            return (f"Opened {svc.name} in your browser with your question. If it isn't sent automatically, "
                    f"press Enter there.", f"I opened {svc.name}.")
        copied = self.copy_to_clipboard(text)
        self.open_url(svc.site)
        if copied:
            return (f"I copied your question and opened {svc.name}. Click the message box there, press "
                    f"<b>Ctrl+V</b> to paste, then Enter.", f"I copied your question. Paste it into {svc.name}.")
        return (f"I opened {svc.name}. I couldn't copy automatically - please copy this text:{preview_box(text)}",
                f"I opened {svc.name}.")

    # ------------------------------------------------------------ skills
    def do_help(self, _: Intent):
        return ("Try: <i>how much VRAM do I have</i> · <i>where is my wedding photo</i> · <i>clear the cache</i> · "
                "<i>what games can I run</i> · <i>can I run Elden Ring</i> · <i>stop VLC update message</i> · "
                "<i>open spotify</i> · <i>check my drivers</i> · <i>install vlc</i> · <i>google best laptops</i> · "
                "<i>go to youtube</i> · <i>what is 15% of 80</i> · <i>ask chatgpt …</i> · <i>set my ai to claude</i> · "
                "<i>accessibility on</i> · <i>kid safe on</i> · <i>remember that…</i> · <i>rescan my pc</i>. Anything else goes to the local AI. "
                "Hold <b>Ctrl+Alt+Space</b> to talk."), "Here's what I can do."

    def do_vram(self, _: Intent):
        gpus = self.ensure_profile().get("gpus", [])
        if not gpus:
            return "I couldn't detect a graphics card.", "I couldn't detect a graphics card."
        main = gpus[0]

        def line(g):
            if not g.get("vram_gb"):
                return f"<b>{html.escape(g['name'])}</b>: the driver doesn't report its VRAM size"
            kind = " shared system memory (integrated GPU)" if g.get("integrated") else " dedicated VRAM"
            return f"<b>{html.escape(g['name'])}</b>: {g['vram_gb']:g} GB{kind}"
        lines = [line(g) for g in gpus]
        speak = (f"You have {main['vram_gb']:g} gigabytes of VRAM on your {main['name']}." if main.get("vram_gb")
                 else f"Your {main['name']} doesn't report its VRAM size.")
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
        url = web.site_url(name)
        if url and (name.lower().strip() in web.SITES or "." in name):
            return self.do_open_site(Intent("open_site", {"site": name}))
        if self._shortcuts is None:
            self._shortcuts = apps.start_menu_shortcuts()
        lnk = apps.find_app(name, self._shortcuts)
        if not lnk:
            if url:
                return self.do_open_site(Intent("open_site", {"site": name}))
            return (f"I couldn't find an app called <b>{html.escape(name)}</b>. Want it? Say <i>install {html.escape(name)}</i>.",
                    f"I couldn't find {name}.")
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
        html_out = self.ask(f"I can safely clear about <b>{cache.human(total)}</b>. Clear it now?", "Clear now", "Cancel",
                            detail="<br>".join(rows) + "<br>Only cache contents are removed; your files, passwords "
                                                       "and logins stay.")
        return html_out, f"I found {cache.human(total)} of cache. Say yes to clear it."

    def do_games(self, _: Intent):
        p = self.ensure_profile()
        g = (p.get("gpus") or [{"vram_gb": 0, "integrated": True, "name": "unknown GPU"}])[0]
        tier = games.gpu_tier(g.get("vram_gb", 0), g.get("integrated", True))
        installed = self.games()
        head = (f"Your <b>{html.escape(g.get('name', '?'))}</b> ({g.get('vram_gb', 0):g} GB) + {p.get('ram_gb')} GB RAM "
                f"is a <b>{tier}</b> gaming PC: good for {games.TIER_HINT[tier]}.")
        if not installed:
            stores = "Steam, Epic, GOG, Ubisoft, Xbox" if IS_WINDOWS else "Steam"
            return head + f"<br>I didn't find any installed games ({stores}).", \
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

    # ------------------------------------------------------------ web
    def do_web_search(self, intent: Intent):
        q, where = intent.args.get("q", ""), intent.args.get("where", "")
        if self.s.kid_safe and "youtube" in where:      # no URL switch forces YouTube Restricted Mode
            q, where = f"site:youtube.com {q}", ""
        url = web.search_url(q, where, safe=self.s.kid_safe)
        self.open_url(url)
        site = "YouTube" if "youtube" in where else "Google" + (" (SafeSearch on)" if self.s.kid_safe else "")
        return (f'Searching {site} for <b>{html.escape(q)}</b> in your browser. <a href="url:{_q(url)}">open again</a>',
                f"Searching {site} for {q}.")

    def do_open_site(self, intent: Intent):
        site = intent.args.get("site", "")
        url = web.site_url(site)
        if not url:
            return self.do_web_search(Intent("web_search", {"q": site}))
        if not web.is_known(url):
            def go():
                if not self._parent_ok(f"open {web.host(url)}"):
                    return "That needs the parent PIN, so I didn't open it.", "I need the parent PIN."
                self.open_url(url)
                return f"Opening {html.escape(url)}", f"Opening {site}."
            self.pending = Pending(f"open {url}", go)
            return (self.ask(f"⚠️ <b>{html.escape(web.host(url))}</b> isn't on my list of known sites. Only open it if "
                             f"you trust it. Open it anyway?", "Open it", "Don't open"),
                    "I don't know that site. Open it anyway?")
        self.open_url(url)
        return f'Opening <a href="url:{_q(url)}">{html.escape(url)}</a>', f"Opening {site}."

    # ------------------------------------------------------------ drivers
    def do_drivers(self, _: Intent):
        self.progress("Checking your devices and asking " + ("Windows Update" if IS_WINDOWS else "your system")
                      + " for driver updates (can take up to a minute)")
        r = drivers.report()
        parts = []
        if r.problems:
            rows = [f"⚠️ <b>{html.escape(p.name)}</b>: {html.escape(p.reason)}" for p in r.problems[:8]]
            parts.append(f"<b>{len(r.problems)} device(s) with problems:</b><br>" + "<br>".join(rows))
        else:
            parts.append("✅ No devices with driver problems.")
        for g in r.gpu:
            age = g.age_days
            when = g.date.strftime("%b %Y") if g.date else "unknown date"
            line = f"Graphics driver: <b>{html.escape(g.name)}</b> {html.escape(g.version)} ({when})"
            if age is not None and age > 365 and g.vendor_page:
                line += f' - over a year old. <a href="url:{_q(g.vendor_page)}">Get the latest from the maker</a>'
            parts.append(line)
        if r.updates:
            rows = [f"• {html.escape(u.title)}" + (f' <span class="dim">{u.size_mb:g} MB</span>' if u.size_mb else "")
                    for u in r.updates[:8]]
            parts.append(f"<b>{len(r.updates)} driver update(s) available:</b><br>" + "<br>".join(rows))

            def run():
                if not self._parent_ok("install driver updates"):
                    return "That needs the parent PIN, so nothing was installed.", "I need the parent PIN."
                self.progress("Installing driver updates")
                ok, msg = drivers.install(r)
                return html.escape(msg), msg
            self.pending = Pending("install driver updates", run)
            parts.append(self.ask("Install them now?", "Install updates", "Not now"))
            speak = f"{len(r.updates)} driver updates are available. Say yes to install them."
        elif r.updates_checked:
            parts.append("✅ No driver updates waiting.")
            speak = "Your drivers look fine." if not r.problems else f"{len(r.problems)} devices have problems."
        else:
            speak = f"{len(r.problems)} devices have problems." if r.problems else "No device problems found."
        parts += [f'<span class="dim">{html.escape(n)}</span>' for n in r.notes]
        if r.problems and not r.updates and IS_WINDOWS:
            parts.append('<span class="dim">Tip: open Device Manager, right-click the device, choose '
                         '<i>Update driver</i> or <i>Uninstall device</i> and restart.</span>')
        return "<br>".join(parts), speak

    # ------------------------------------------------------------ install apps
    def do_install(self, intent: Intent):
        name = intent.args.get("pkg", "").strip()
        if not packages.available_managers():
            url = web.search_url(f"{name} official download")
            how = ("winget isn't available - install 'App Installer' from the Microsoft Store." if IS_WINDOWS
                   else "No package manager I know (flatpak/apt/dnf/pacman) was found.")
            return (f'{how} <a href="url:{_q(url)}">Search for the official download</a>', how)
        self.progress(f"Looking up <b>{html.escape(name)}</b>")
        found = packages.find(name)
        if not found:
            url = web.search_url(f"{name} official download")
            return (f'I couldn\'t find <b>{html.escape(name)}</b> in {", ".join(packages.available_managers())}. '
                    f'<a href="url:{_q(url)}">Search the web</a>'), f"I couldn't find {name}."

        def installer(p: packages.Package):
            def run():
                if not self._parent_ok(f"install {p.name}"):
                    return "That needs the parent PIN, so nothing was installed.", "I need the parent PIN."
                self.progress(f"Installing <b>{html.escape(p.name)}</b> - this can take a few minutes")
                ok, msg = packages.install(p)
                return ("✅ " if ok else "❌ ") + html.escape(msg), msg
            return run

        top = found[0]
        self.pending = Pending(f"install {top.name}", installer(top), options=found,
                               pick=lambda i: installer(found[i])())
        shown_id = "" if top.id.lower() == top.name.lower() else html.escape(top.id) + " · "
        label = f"<b>{html.escape(top.name)}</b> <span class='dim'>{shown_id}{top.manager}" \
                f"{' · ' + html.escape(top.version) if top.version else ''}</span>"
        out = self.ask(f"Install {label}?" + (" <span class='dim'>(needs the parent PIN)</span>" if self.s.kid_safe else ""),
                       "Install", "Cancel")
        if len(found) > 1:
            alts = "<br>".join(f'<a href="act:{i + 1}">{i + 1}. {html.escape(p.name)}</a> '
                               f'<span class="dim">{html.escape(p.id)}</span>' for i, p in enumerate(found))
            out += f"<br><span class='dim'>Not the right one? Pick:</span><br>{alts}"
        return out, f"Should I install {top.name}?"

    def do_pick(self, intent: Intent):
        n = int(intent.args.get("n", "1")) - 1
        p = self.pending
        if not p or not p.pick or not (0 <= n < len(p.options)):
            return "There's no list to pick from right now.", "Nothing to pick."
        self.pending = None
        return p.pick(n)

    def do_confirm(self, _: Intent):
        if not self.pending or time.time() - self.pending.created > 300:
            self.pending = None
            return "There's nothing waiting for confirmation.", "Nothing to confirm."
        p, self.pending = self.pending, None
        return p.run()

    def do_cancel(self, _: Intent):
        p, self.pending = self.pending, None
        if p and p.on_cancel:
            return p.on_cancel()
        return ("Cancelled." if p else "Okay."), ("Cancelled." if p else "Okay.")

    def do_confirm_specs(self, _: Intent):
        p = self.pending
        if not p or "specs" not in p.extra:
            return "There's nothing waiting for that.", "Nothing to confirm."
        self.pending = None
        return p.extra["specs"]()

    # ------------------------------------------------------------ calculator
    def do_calc(self, intent: Intent):
        expr = intent.args.get("expr", "")
        try:
            v = calc.fmt(calc.evaluate(expr))
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as e:
            return f"I can't calculate that ({html.escape(str(e) or 'invalid')}).", "I can't calculate that."
        return f"{html.escape(expr.strip())} = <b>{v}</b>", f"That's {v}."

    # ------------------------------------------------------------ hand-off settings / direct requests
    def do_ask_ai(self, intent: Intent):
        q = intent.args.get("q", "").strip()
        if not q:
            return "What should I ask?", "What should I ask?"
        return self._offer_handoff(q, None, service=intent.args["svc"])

    def do_set_ai(self, intent: Intent):
        svc = intent.args["svc"]
        self.s.handoff_service = svc
        self.s.save()
        name = handoff.SERVICES[svc].name
        note = ""
        if svc == "claude_code" and "claude_code" not in self.available_ai():
            note = " (I can't find the <i>claude</i> command on this PC yet, so I'll use ChatGPT until it's installed.)"
        return f"Okay - big questions will go to <b>{name}</b>, and I'll still ask you every time.{note}", f"Okay, I'll use {name}."

    def do_retrain(self, _: Intent):
        self.progress("Learning from your Yes/No choices")
        model, msg = classifier.retrain_for_user()
        if model is None:
            return f"Nothing to learn yet: {html.escape(msg)}.", "Nothing to learn yet."
        self.classifier = model
        return f"Done - I {html.escape(msg)}. I'll use that from now on.", "Done, I learned from your choices."

    # ------------------------------------------------------------ accessibility + kid-safe
    def do_accessibility(self, intent: Intent):
        self.s.accessible = bool(intent.args.get("on"))
        self.s.save()
        self.on_settings_changed()
        if self.s.accessible:
            return ("Accessibility mode is on: bigger text, stronger colours, slower speech and simple yes/no "
                    "questions.", "Accessibility mode is on.")
        return "Accessibility mode is off.", "Accessibility mode is off."

    def do_kid_safe(self, intent: Intent):
        on = bool(intent.args.get("on"))
        if on == self.s.kid_safe:
            return f"Kid-safe mode is already {'on' if on else 'off'}.", "It's already set."
        if on:
            if not self.s.parent_pin:
                pin = self.ask_pin("Choose a parent PIN (at least 4 digits)", True)
                if not pin or len(pin) < 4:
                    return "Kid-safe mode needs a parent PIN of at least 4 digits, so it's still off.", "It's still off."
                self.s.set_pin(pin)
        elif not self.s.check_pin(self.ask_pin("Parent PIN to turn kid-safe mode off", False)):
            return "Wrong or no PIN, so kid-safe mode stays on.", "Kid-safe mode stays on."
        self.s.kid_safe = on
        self.s.save()
        self.on_settings_changed()
        if on:
            return ("Kid-safe mode is on: SafeSearch for web searches, and installing apps, asking online AIs or "
                    "opening unknown links needs the parent PIN.", "Kid-safe mode is on.")
        return "Kid-safe mode is off.", "Kid-safe mode is off."

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


def preview_box(text: str) -> str:
    """The exact text to be sent, in a box. A one-cell table, because Qt's rich text doesn't close <div>s reliably."""
    body = html.escape(text).replace("\n", "<br>")
    return f'<table width="100%" cellpadding="6" bgcolor="#1c2230"><tr><td class="preview">{body}</td></tr></table>'


def _say(ok: bool, msg: str) -> tuple[str, str]:
    return ("" if ok else "❌ ") + html.escape(msg), msg


def _strip(h: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", h)).strip()
