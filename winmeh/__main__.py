"""Entry point.

  python -m winmeh                 start the widget
  python -m winmeh --ask "..."     one question, answer printed (no UI, no voice)
  python -m winmeh --selftest      run the built-in commands headless and time them
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time

from .config import IS_WINDOWS, Settings, data_dir


# ------------------------------------------------------------------ headless modes
def _plain(h: str) -> str:
    import html
    h = re.sub(r"<br\s*/?>", "\n", h)
    return html.unescape(re.sub(r"<[^>]+>", "", h))


def ask(question: str, use_llm: bool = True) -> str:
    from .core.assistant import Assistant
    s = Settings.load()
    llm = None
    if use_llm:
        from .core.llm import LLM
        llm = LLM(s)
        llm.ensure()
    a = Assistant(s, llm)
    out: list[str] = []
    try:
        a.handle(question, lambda k, p: out.append(p if k == "token" else _plain(p) + "\n" if k == "html" else ""))
    finally:
        if llm:
            llm.close()          # don't leave the llama-server we started running
    return "".join(out).strip()


SELFTEST = [
    "how much is my vram?",
    "what are my specs",
    "wher is my wedding photo",
    "how to clear the cache",
    "get me the games list that I can run in this machine",
    "stop the vlc palyer update message",
    "check my drivers",
    "install vlc",               # only looks the package up; installing needs a "yes"
]


def selftest() -> int:
    from .core.assistant import Assistant
    s = Settings.load()
    s.online_lookups = os.environ.get("WINMEH_OFFLINE") != "1"
    t0 = time.perf_counter()
    a = Assistant(s, None)
    a.ensure_profile()
    print(f"[profile] {time.perf_counter() - t0:.2f}s (first run, uncached)")
    t0 = time.perf_counter()
    n = a.index.build(list(a.profile.get("folders", {}).values()))
    print(f"[file index] {n} files in {time.perf_counter() - t0:.2f}s\n")
    failures = 0
    for q in SELFTEST:
        if "vlc" in q and os.environ.get("CI"):
            q_note = " (skipped writing vlcrc on CI)"
            print(f"> {q}{q_note}\n")
            continue
        out: list[str] = []
        t = time.perf_counter()
        out_final: list[str] = []
        a.handle(q, lambda k, p: out_final.append(_plain(p)) if k == "html" else None)
        out = out_final[-1:]      # skip "working on it…" progress messages
        ms = (time.perf_counter() - t) * 1000
        text = "\n".join(out).strip()
        print(f"> {q}   [{ms:.0f} ms]\n{text}\n")
        if not text or "failed" in text.lower():
            failures += 1
    return 1 if failures else 0


# ------------------------------------------------------------------ GUI
IPC_NAME = "winmeh-" + (os.environ.get("USERNAME") or os.environ.get("USER") or "user")


def send_to_running(command: str) -> bool:
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(IPC_NAME)
    if not sock.waitForConnected(1000):
        return False
    sock.write(command.encode() + b"\n")
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def listen_for_commands(app, handlers: dict) -> None:
    from PySide6.QtNetwork import QLocalServer
    QLocalServer.removeServer(IPC_NAME)          # stale socket after a crash
    server = QLocalServer(app)

    def on_conn():
        sock = server.nextPendingConnection()

        def read():
            for line in bytes(sock.readAll()).decode(errors="ignore").split():
                fn = handlers.get(line.strip())
                if fn:
                    fn()
        sock.readyRead.connect(read)
    server.newConnection.connect(on_conn)
    server.listen(IPC_NAME)
    app._ipc = server


def run_gui(command: str = "show") -> int:
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication

    from .core.assistant import Assistant
    from .core.llm import LLM
    from .ui.widget import Widget, make_tray
    from .voice.hotkey import Hotkeys
    from .voice.stt import STT
    from .voice.tts import TTS

    if IS_WINDOWS:   # crisp text on high-DPI screens + own taskbar identity
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WinMeh.Assistant")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    lock = QLockFile(str(data_dir() / "winmeh.lock"))
    if not lock.tryLock(100):
        # Already running: hand the command to that instance (this is how Linux hotkeys work).
        ok = send_to_running(command)
        print("Sent to the running WinMeh." if ok else "WinMeh is already running.")
        return 0

    s = Settings.load()
    llm = LLM(s)
    stt = STT(s.stt_model)
    tts = TTS()
    assistant = Assistant(s, llm)
    w = Widget(s, assistant, stt, tts, llm)
    w.show()

    # Heavy stuff loads in the background; the widget is usable immediately.
    def boot():
        w.bridge.status.emit("learning your PC…")
        assistant.learn(on_done=lambda m: w.bridge.status.emit("ready"))
        w.bridge.status.emit("loading voice…")
        if not stt.load():
            w.bridge.notice.emit(f"Voice is off ({stt.error}). Typing still works.")
        w.bridge.status.emit("loading AI…")
        first_run = {"told": False}

        def progress(msg: str):
            if not first_run["told"]:
                first_run["told"] = True
                w.bridge.notice.emit("First start: downloading the small chat model (~450 MB, one time). "
                                     "Everything else already works - try asking about your PC.")
            w.bridge.status.emit(msg)
        if llm.ensure(progress):
            llm.warm()
            if first_run["told"]:
                w.bridge.notice.emit("Chat is ready.")
        else:
            w.bridge.notice.emit("Chat is offline (no internet for the first download?). Built-in commands still "
                                 "work; I'll retry next start.")
        w.bridge.status.emit("ready")
    threading.Thread(target=boot, daemon=True, name="boot").start()

    listen_for_commands(app, {"show": w.show_front, "toggle": w.toggle_visible, "talk": w.talk,
                              "quit": lambda: app.quit()})
    if command == "talk":
        w.talk()

    hk = Hotkeys()
    hk.add(s.hotkey_talk, w.bridge.talk.emit)
    hk.add(s.hotkey_toggle, w.bridge.toggle.emit)
    hk.start()

    def quit_all():
        s.save()
        hk.stop()
        llm.close()
        app.quit()

    app._tray = make_tray(app, w, quit_all)   # keep a reference so it is not garbage-collected
    app.aboutToQuit.connect(lambda: (s.save(), llm.close()))
    return app.exec()


def main() -> int:
    if sys.stdout is None or sys.stderr is None:      # pythonw / windowed exe: keep a log instead
        log = open(data_dir() / "winmeh.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    ap = argparse.ArgumentParser(prog="winmeh")
    ap.add_argument("--ask", help="answer one question in the terminal")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--talk", action="store_true", help="start listening (bind to a desktop shortcut on Linux)")
    ap.add_argument("--toggle", action="store_true", help="show/hide the widget")
    ap.add_argument("--quit", action="store_true", help="quit the running instance")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.ask:
        print(ask(a.ask, use_llm=not a.no_llm))
        return 0
    command = "talk" if a.talk else "toggle" if a.toggle else "quit" if a.quit else "show"
    return run_gui(command)


if __name__ == "__main__":
    sys.exit(main())
