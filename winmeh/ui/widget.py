"""The glass desktop widget."""

from __future__ import annotations

import html
import os
import threading
import urllib.parse

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget)

from ..config import IS_WINDOWS, Settings
from ..system import apps
from . import glass

RADIUS = 18

QSS = """
QWidget { color: #F4F6FB; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 13px; }
#title { font-weight: 600; font-size: 14px; letter-spacing: .4px; }
#status { color: rgba(255,255,255,.55); font-size: 11px; }
QScrollArea, #chat { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,.25); border-radius: 3px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QLabel#bubble_user { background: rgba(120,160,255,.30); border: 1px solid rgba(255,255,255,.18);
    border-radius: 14px; padding: 8px 11px; }
QLabel#bubble_bot { background: rgba(255,255,255,.10); border: 1px solid rgba(255,255,255,.14);
    border-radius: 14px; padding: 8px 11px; }
QLineEdit { background: rgba(255,255,255,.10); border: 1px solid rgba(255,255,255,.20); border-radius: 16px;
    padding: 7px 12px; selection-background-color: rgba(120,160,255,.6); }
QLineEdit:focus { border: 1px solid rgba(150,185,255,.75); background: rgba(255,255,255,.14); }
QPushButton { background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.20); border-radius: 16px;
    min-width: 32px; min-height: 32px; font-size: 15px; }
QPushButton:hover { background: rgba(255,255,255,.22); }
QPushButton#mic[live="true"] { background: rgba(255,90,110,.65); border-color: rgba(255,170,180,.9); }
QPushButton#flat { background: transparent; border: none; min-width: 24px; min-height: 24px; font-size: 13px;
    color: rgba(255,255,255,.7); }
QPushButton#flat:hover { color: white; background: rgba(255,255,255,.12); border-radius: 12px; }
"""

LINK_CSS_NORMAL = ("<style>a{color:#AFC8FF;text-decoration:none} a.btn{color:#fff;font-weight:600}"
                   " .dim{color:rgba(255,255,255,.55);font-size:11px} .warn{color:#FFD28A;font-size:11px}"
                   " .preview{color:#fff;font-family:Consolas,monospace}"
                   "</style>")
# Accessibility: big text, pure white on black, yellow underlined links (WCAG AAA contrast).
LINK_CSS_ACCESSIBLE = ("<style>a{color:#FFE14D;text-decoration:underline} a.btn{color:#FFE14D;font-weight:700;font-size:20px}"
                       " .dim{color:#FFFFFF;font-size:16px} .warn{color:#FFE14D;font-size:16px} .big{font-size:21px;font-weight:600}"
                       " .preview{color:#fff;font-size:18px}</style>")
LINK_CSS = LINK_CSS_NORMAL

QSS_ACCESSIBLE = """
QWidget { font-size: 19px; color: #FFFFFF; }
#title { font-size: 20px; }
#status { color: #FFFFFF; font-size: 15px; }
QLabel#bubble_user { background: #0B3D91; border: 2px solid #FFFFFF; border-radius: 10px; padding: 10px 13px; }
QLabel#bubble_bot { background: #000000; border: 2px solid #FFFFFF; border-radius: 10px; padding: 10px 13px; }
QLineEdit { background: #000000; border: 2px solid #FFFFFF; border-radius: 10px; padding: 9px 12px; }
QLineEdit:focus { border: 3px solid #FFE14D; }
QPushButton { background: #000000; border: 2px solid #FFFFFF; min-width: 44px; min-height: 44px; font-size: 20px; }
QPushButton:focus { border: 3px solid #FFE14D; }
QPushButton#flat { color: #FFFFFF; min-width: 36px; min-height: 36px; }
"""


def make_icon(size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QRadialGradient(size * .35, size * .3, size * .8)
    g.setColorAt(0, QColor(170, 205, 255))
    g.setColorAt(1, QColor(80, 90, 230))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.setPen(QColor(255, 255, 255))
    f = QFont("Segoe UI", int(size * .42), QFont.Bold)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "W")
    p.end()
    return QIcon(pm)


class Bridge(QObject):
    """Thread -> UI signals (Qt queues cross-thread emits onto the UI thread)."""
    html = Signal(str)
    token = Signal(str)
    done = Signal(str)
    status = Signal(str)
    heard = Signal(str)
    level = Signal(float)
    notice = Signal(str)
    extra = Signal(str)            # an additional bubble after a streamed answer (safety-net offer)
    clipboard = Signal(str)
    pin_request = Signal(str, bool)
    settings_changed = Signal()
    talk = Signal()
    toggle = Signal()


class Bubble(QLabel):
    def __init__(self, text: str, mine: bool, on_link):
        super().__init__()
        self.setObjectName("bubble_user" if mine else "bubble_bot")
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.setOpenExternalLinks(False)
        self.linkActivated.connect(on_link)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.raw = ""
        self.set_html(text)

    def set_html(self, h: str) -> None:
        self.setText(LINK_CSS + h)

    def append_plain(self, tok: str) -> None:
        self.raw += tok
        self.set_html(html.escape(self.raw).replace("\n", "<br>"))


class Widget(QWidget):
    def __init__(self, settings: Settings, assistant, stt=None, tts=None, llm=None):
        super().__init__()
        self.s, self.assistant, self.stt, self.tts, self.llm = settings, assistant, stt, tts, llm
        self.bridge = Bridge()
        self._drag: QPoint | None = None
        self._busy = False
        self._live_bubble: Bubble | None = None
        self.blurred = False

        self.setWindowTitle("WinMeh")
        self.setWindowIcon(make_icon())
        self._apply_flags()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._pin_answer: str | None = None
        self._pin_done = threading.Event()
        self.apply_accessibility(resize=False)
        self.resize(settings.width, settings.height)
        self._build()
        self._wire()
        self._place()
        # Let the assistant (worker thread) reach the UI thread safely.
        if hasattr(assistant, "copy_to_clipboard"):
            assistant.copy_to_clipboard = self.copy_text
            assistant.ask_pin = self.request_pin
            assistant.on_settings_changed = self.bridge.settings_changed.emit

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 14)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color:#7CF2A0;font-size:10px")
        title = QLabel("WinMeh")
        title.setObjectName("title")
        self.status = QLabel("ready")
        self.status.setObjectName("status")
        self.pin = QPushButton("📌" if self.s.always_on_top else "📍")
        self.pin.setObjectName("flat")
        self.pin.setToolTip("Toggle: always on top / live on desktop")
        hide = QPushButton("—")
        hide.setObjectName("flat")
        hide.setToolTip("Hide (Ctrl+Alt+W)")
        hide.clicked.connect(lambda *_: self.hide())
        self.pin.clicked.connect(lambda *_: self.toggle_pin())
        for w in (self.dot, title, self.status):
            head.addWidget(w)
        head.addStretch(1)
        head.addWidget(self.pin)
        head.addWidget(hide)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        chat = QWidget()
        chat.setObjectName("chat")
        self.chat = QVBoxLayout(chat)
        self.chat.setContentsMargins(0, 0, 4, 0)
        self.chat.setSpacing(6)
        self.chat.addStretch(1)
        self.scroll.setWidget(chat)
        # Keep the newest message in view: scroll once the layout has actually grown (a long answer with
        # buttons at the bottom would otherwise stay cut off above the fold).
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _lo, hi: bar.setValue(hi))
        root.addWidget(self.scroll, 1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask me anything…")
        self.mic = QPushButton("🎙")
        self.mic.setObjectName("mic")
        self.mic.setToolTip("Talk (Ctrl+Alt+Space)")
        send = QPushButton("➤")
        send.clicked.connect(self.submit)
        self.input.returnPressed.connect(self.submit)
        self.mic.clicked.connect(lambda *_: self.talk())
        row.addWidget(self.input, 1)
        row.addWidget(self.mic)
        row.addWidget(send)
        root.addLayout(row)

        self.add_bubble("Hi! I'm WinMeh. Ask about your PC, find files, or just chat. Type <i>help</i> to see tricks.",
                        mine=False)

    def _wire(self) -> None:
        b = self.bridge
        b.html.connect(self._on_html)
        b.token.connect(self._on_token)
        b.done.connect(self._on_done)
        b.status.connect(self.set_status)
        b.heard.connect(self._on_heard)
        b.level.connect(self._on_level)
        b.notice.connect(lambda t: self.add_bubble(html.escape(t), mine=False))
        b.extra.connect(lambda h: self.add_bubble(h, mine=False))
        b.clipboard.connect(lambda t: QApplication.clipboard().setText(t))
        b.pin_request.connect(self._show_pin_dialog)
        b.settings_changed.connect(lambda: self.apply_accessibility(resize=True))
        b.talk.connect(self.talk)
        b.toggle.connect(self.toggle_visible)

    def _apply_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool
        flags |= Qt.WindowStaysOnTopHint if self.s.always_on_top else Qt.WindowStaysOnBottomHint
        self.setWindowFlags(flags)

    def _place(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        if self.s.x is not None and self.s.y is not None and any(
                sc.availableGeometry().contains(QPoint(self.s.x + 20, self.s.y + 20)) for sc in QApplication.screens()):
            self.move(self.s.x, self.s.y)
        else:   # dock to the right edge, a bit below the top
            self.move(screen.right() - self.width() - 16, screen.top() + 80)

    # ------------------------------------------------------------ painting
    def showEvent(self, e):
        super().showEvent(e)
        if IS_WINDOWS and not self.blurred:
            acrylic = glass.windows_build() >= 22000      # acrylic lags while dragging on Win10
            self.blurred = glass.apply(int(self.winId()), 0x30201410 if acrylic else 0x01000000, acrylic)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, RADIUS, RADIUS)
        a = 0.35 if self.blurred else self.s.opacity
        if self.s.accessible:            # high contrast: solid black, no see-through background
            p.fillPath(path, QColor(0, 0, 0))
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawPath(path)
            return
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(40, 48, 72, int(255 * a)))
        g.setColorAt(1, QColor(18, 20, 34, int(255 * min(1.0, a + 0.1))))
        p.fillPath(path, g)
        # glass sheen + hairline border
        sheen = QLinearGradient(0, 0, 0, 90)
        sheen.setColorAt(0, QColor(255, 255, 255, 34))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(path, sheen)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.drawPath(path)

    # ------------------------------------------------------------ dragging (+ snap to screen edges)
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        self._drag = None
        geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        x, y, snap = self.x(), self.y(), 24
        if abs(x - geo.left()) < snap:
            x = geo.left() + 8
        if abs(geo.right() - (x + self.width())) < snap:
            x = geo.right() - self.width() - 8
        if abs(y - geo.top()) < snap:
            y = geo.top() + 8
        if abs(geo.bottom() - (y + self.height())) < snap:
            y = geo.bottom() - self.height() - 8
        self.move(x, y)
        self.s.x, self.s.y = x, y
        self.s.save()

    # ------------------------------------------------------------ chat
    def add_bubble(self, text: str, mine: bool) -> Bubble:
        b = Bubble(text, mine, self.on_link)
        row = QHBoxLayout()
        if mine:
            row.addStretch(1)
        row.addWidget(b, 0 if mine else 1)
        if not mine:
            row.addSpacing(24)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.chat.addWidget(holder)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))
        return b

    def submit(self, text: str | None = None) -> None:
        text = (text if isinstance(text, str) else self.input.text()).strip()
        if not text or self._busy:
            return
        self.input.clear()
        self.add_bubble(html.escape(text), mine=True)
        self._busy = True
        self._live_bubble = None
        if self.tts:
            self.tts.stop()
        self.set_status("thinking…")

        def emit(kind, payload):
            getattr(self.bridge, kind).emit(payload if payload is not None else "")

        def work():
            try:
                self.assistant.handle(text, emit)
            except Exception as ex:   # last-resort guard
                self.bridge.html.emit(f"Error: {html.escape(str(ex))}")
                self.bridge.done.emit("")
        threading.Thread(target=work, daemon=True).start()

    def _on_html(self, h: str) -> None:
        if self._live_bubble is None:
            self._live_bubble = self.add_bubble(h, mine=False)
        else:
            self._live_bubble.set_html(h)

    def _on_token(self, tok: str) -> None:
        if self._live_bubble is None:
            self._live_bubble = self.add_bubble("", mine=False)
            self.set_status("answering…")
        self._live_bubble.append_plain(tok)
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def _on_done(self, speak: str) -> None:
        self._busy = False
        self.set_status("ready")
        if self.tts and self.s.speak_replies and speak:
            self.tts.say(speak)

    def on_link(self, href: str) -> None:
        scheme, _, rest = href.partition(":")
        path = urllib.parse.unquote(rest)
        if scheme == "open" and os.path.exists(path):
            apps.open_path(path)
        elif scheme == "reveal" and os.path.exists(path):
            apps.reveal(path)
        elif scheme == "url":
            from ..system import web
            if not web.is_known(path):
                ok = QMessageBox.question(self, "WinMeh", f"{web.host(path) or path} isn't on my list of known sites.\n"
                                          "Only open it if you trust it. Open it anyway?")
                if ok != QMessageBox.Yes:
                    return
            web.open_url(path)
        elif scheme == "act":
            self.submit({"confirm": "yes", "cancel": "cancel", "specs": "yes, include my specs"}.get(path, path))

    # ------------------------------------------------------------ accessibility, clipboard, parent PIN
    def apply_accessibility(self, resize: bool = True) -> None:
        global LINK_CSS
        on = self.s.accessible
        LINK_CSS = LINK_CSS_ACCESSIBLE if on else LINK_CSS_NORMAL
        self.setStyleSheet(QSS + (QSS_ACCESSIBLE if on else ""))
        self.setMinimumSize(420, 560) if on else self.setMinimumSize(300, 360)
        if resize and on and self.width() < 440:
            self.resize(460, 680)
        if self.tts:
            self.tts.slow = on
        self.update()

    def copy_text(self, text: str) -> bool:
        """Called from the worker thread: QClipboard must be touched on the UI thread."""
        self.bridge.clipboard.emit(text)
        return True

    def request_pin(self, prompt: str, new: bool = False) -> str | None:
        """Worker thread: ask for the parent PIN in a dialog on the UI thread and wait for the answer."""
        self._pin_done.clear()
        self._pin_answer = None
        self.bridge.pin_request.emit(prompt, new)
        self._pin_done.wait(180)
        return self._pin_answer

    def _show_pin_dialog(self, prompt: str, new: bool) -> None:
        pin, ok = QInputDialog.getText(self, "WinMeh - parent PIN", prompt, QLineEdit.Password)
        if ok and new and pin:
            again, ok = QInputDialog.getText(self, "WinMeh - parent PIN", "Type the same PIN again", QLineEdit.Password)
            ok = ok and again == pin
        self._pin_answer = pin if ok and pin else None
        self._pin_done.set()

    # ------------------------------------------------------------ voice
    def talk(self) -> None:
        if not self.isVisible():
            self.show_front()
        if self.stt is None or self.stt.model is None:
            why = (self.stt.error if self.stt else None) or "still loading"
            self.add_bubble(f"Voice isn't ready ({html.escape(why)}).", mine=False)
            return
        if self.stt.listening:
            self.stt.stop()
            return
        if self.tts:
            self.tts.stop()
        self.mic.setProperty("live", True)
        self.mic.style().polish(self.mic)
        self.set_status("listening…")

        def work():
            try:
                text = self.stt.listen(on_level=self.bridge.level.emit)
            except Exception as ex:
                text = ""
                self.bridge.notice.emit(f"Mic error: {ex}")
            self.bridge.heard.emit(text)
        threading.Thread(target=work, daemon=True).start()

    def _on_heard(self, text: str) -> None:
        self.mic.setProperty("live", False)
        self.mic.style().polish(self.mic)
        self.mic.setText("🎙")
        self.set_status("ready")
        if text:
            self.submit(text)

    def _on_level(self, lvl: float) -> None:
        self.mic.setText("●" if lvl > 0.15 else "🎙")

    # ------------------------------------------------------------ window state
    def set_status(self, t: str) -> None:
        self.status.setText(t)
        color = {"ready": "#7CF2A0", "listening…": "#FF6B81"}.get(t, "#FFD28A")
        self.dot.setStyleSheet(f"color:{color};font-size:10px")

    def toggle_pin(self) -> None:
        self.s.always_on_top = not self.s.always_on_top
        self.s.save()
        self.pin.setText("📌" if self.s.always_on_top else "📍")
        pos = self.pos()
        self._apply_flags()
        self.blurred = False
        self.move(pos)
        self.show()

    def show_front(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def toggle_visible(self) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self.show_front()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.s.width, self.s.height = self.width(), self.height()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)


def make_tray(app: QApplication, w: Widget, on_quit) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(make_icon(), app)
    tray.setToolTip("WinMeh")
    menu = QMenu()

    def add(label, fn, checkable=False, checked=False):
        a = QAction(label, menu, checkable=checkable)
        a.setChecked(checked)
        a.triggered.connect(fn)
        menu.addAction(a)
        return a

    add("Show / hide   Ctrl+Alt+W", w.toggle_visible)
    add("Always on top", lambda *_: w.toggle_pin(), True, w.s.always_on_top)

    def toggle_speak(on):
        w.s.speak_replies = on
        w.s.save()
    add("Speak replies", toggle_speak, True, w.s.speak_replies)
    if IS_WINDOWS:
        add("Start with Windows", apps.set_autostart, True, apps.autostart_enabled())
    add("Re-learn this PC", lambda *_: w.submit("rescan my pc"))
    menu.addSeparator()
    add("Accessibility mode (large text)", lambda on: w.submit(f"accessibility {'on' if on else 'off'}"), True,
        w.s.accessible)
    add("Kid-safe mode (parent PIN)", lambda on: w.submit(f"kid safe {'on' if on else 'off'}"), True, w.s.kid_safe)
    ai = menu.addMenu("Bigger AI for hard questions")
    for key, label in (("auto", "Automatic"), ("chatgpt", "ChatGPT"), ("claude", "Claude"),
                       ("deepseek", "DeepSeek"), ("claude_code", "Claude Code (can change files)")):
        a = QAction(label, ai, checkable=True)
        a.setChecked(w.s.handoff_service == key)

        def pick(_=False, k=key):
            w.s.handoff_service = k
            w.s.save()
            for act in ai.actions():
                act.setChecked(act.data() == k)
        a.setData(key)
        a.triggered.connect(pick)
        ai.addAction(a)
    add("Learn from my choices", lambda *_: w.submit("learn from my choices"))
    menu.addSeparator()
    add("Quit", on_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: w.toggle_visible() if r == QSystemTrayIcon.Trigger else None)
    tray.show()
    return tray
