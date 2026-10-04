"""Render docs/demo.mp4: a scripted walkthrough of the REAL WinMeh widget and assistant.

The UI, router, classifier, permission flow and replies are the actual code. What is
simulated, so the video shows a typical old PC on any build machine: the machine
profile, photos, games, cache sizes, driver report, package manager and the small
LLM's streamed reply. Browser launches are shown as an overlay instead of opening.

  QT_QPA_PLATFORM=offscreen python scripts/make_demo_video.py [out.mp4]
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="winmeh-demo-"))
os.environ["WINMEH_HOME"] = str(TMP / "data")
os.environ["APPDATA"] = str(TMP / "appdata")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen,  # noqa: E402
                           QRadialGradient)
from PySide6.QtWidgets import QApplication  # noqa: E402

W, H, FPS = 1280, 720, 24
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "demo.mp4"

app = QApplication([])
app.setFont(QFont("DejaVu Sans", 10))

from winmeh.config import Settings  # noqa: E402
from winmeh.core.assistant import Assistant  # noqa: E402
from winmeh.system import cache, drivers, files, games, packages  # noqa: E402
from winmeh.ui.widget import Widget  # noqa: E402

# ------------------------------------------------------------------ a simulated old PC
pics = TMP / "Pictures"
for folder, names in {"Wedding 2019": [f"IMG_{i:04d}.JPG" for i in range(1201, 1240)],
                      "Holiday Cox's Bazar": ["beach.jpg", "sunset.jpg"], "": ["nikah_stage.jpg", "cat.png"]}.items():
    (pics / folder).mkdir(parents=True, exist_ok=True)
    for n in names:
        (pics / folder / n).write_bytes(b"x")
(TMP / "appdata" / "vlc").mkdir(parents=True)
(TMP / "appdata" / "vlc" / "vlcrc").write_text("[qt] # Qt interface\n#qt-updates-notif=1\n")

PROFILE = {
    "os": "Windows 10 Home 22H2 (build 19045)", "cpu": "Intel(R) Core(TM) i5-4590 CPU @ 3.30GHz", "cores": 4,
    "ram_gb": 8.0, "folders": {"Pictures": str(pics)}, "apps": [{}] * 63,
    "gpus": [{"name": "NVIDIA GeForce GTX 1050 Ti", "vram_gb": 4.0, "integrated": False}],
    "disks": [{"mount": "C:\\", "total_gb": 223.0, "free_gb": 17.4}, {"mount": "D:\\", "total_gb": 931.0, "free_gb": 402.0}],
}
GAMES = [games.Game("Counter-Strike 2", "Steam", "C:\\Games\\cs2", 33.0, "730", "ok"),
         games.Game("Cyberpunk 2077", "Steam", "D:\\Steam\\cp2077", 70.0, "1091500", "below",
                    ["needs 12 GB RAM, you have 8 GB"]),
         games.Game("Stardew Valley", "Steam", "D:\\Steam\\sdv", 0.6, "413150", "ok"),
         games.Game("Rocket League", "Epic", "D:\\Epic\\RL", 25.0),
         games.Game("Minecraft", "Xbox", "C:\\XboxGames\\Minecraft")]


def fake_cache_scan():
    t = [cache.CacheTarget("temp", "Your temp files", ["x"], size=int(2.1 * 1024**3)),
         cache.CacheTarget("chrome", "Chrome cache", ["x"], note="close the browser first", size=int(780 * 1024**2)),
         cache.CacheTarget("thumbs", "Thumbnail cache", ["x"], note="Explorer rebuilds it automatically",
                           size=int(96 * 1024**2)),
         cache.CacheTarget("dns", "DNS cache", [], note="flushed with ipconfig /flushdns")]
    return t


cache.scan = fake_cache_scan
cache.clear = lambda found, keys=None: (int(2.83 * 1024**3), 14, [t.label for t in found])
drivers.report = lambda check_updates=True: (time.sleep(1.2), drivers.Report(
    problems=[drivers.Problem("Realtek PCIe GbE Family Controller", 28)],
    updates=[drivers.DriverUpdate("Realtek - Net - 10.68.815.2023", 1.4),
             drivers.DriverUpdate("NVIDIA - Display - 32.0.15.6094", 690.0)], updates_checked=True,
    gpu=[drivers.GpuDriver("NVIDIA GeForce GTX 1050 Ti", "27.21.14.5671", __import__("datetime").date(2021, 3, 2),
                           drivers.vendor_page("nvidia"))]))[1]
packages.available_managers = lambda: ["winget"]
packages.find = lambda name: (time.sleep(0.5), [packages.Package("Spotify", "Spotify.Spotify", "winget", "1.2.48")])[1]
packages.install = lambda p: (time.sleep(1.5), (True, f"Installed {p.name}."))[1]


class DemoLLM:
    status = "ready"
    replies = {"joke": "Why did the computer go to the doctor? Because it had a virus! 😄 "
                       "Want another one?"}

    def stream(self, msgs, **kw):
        for word in self.replies["joke"].split(" "):
            time.sleep(0.09)
            yield word + " "


# ------------------------------------------------------------------ recorder
class Recorder:
    def __init__(self):
        OUT.parent.mkdir(parents=True, exist_ok=True)
        self.ff = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "24", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(OUT)], stdin=subprocess.PIPE)
        self.title, self.sub, self.step = "", "", ""
        self.toast, self.toast_until = "", 0.0
        self.widget = None
        self.frames = 0
        self.card: tuple[str, str] | None = None

    def wallpaper(self, p: QPainter):
        g = QLinearGradient(0, 0, W, H)
        g.setColorAt(0, QColor(22, 54, 110))
        g.setColorAt(0.55, QColor(92, 52, 130))
        g.setColorAt(1, QColor(214, 112, 72))
        p.fillRect(0, 0, W, H, g)
        r = QRadialGradient(260, 600, 380)
        r.setColorAt(0, QColor(255, 210, 140, 120))
        r.setColorAt(1, QColor(255, 210, 140, 0))
        p.fillRect(0, 0, W, H, r)

    def frame(self):
        img = QImage(W, H, QImage.Format_RGB888)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        self.wallpaper(p)
        if self.card:
            self._card(p, *self.card)
        else:
            self._captions(p)
            if self.widget is not None:
                pm = self.widget.grab()
                x = W - pm.width() - 70
                y = (H - pm.height()) // 2
                p.drawPixmap(x, y, pm)
            if self.toast and time.time() < self.toast_until:
                self._browser(p, self.toast)
        p.setPen(QColor(255, 255, 255, 120))
        p.setFont(QFont("DejaVu Sans", 9))
        p.drawText(QRectF(0, H - 26, W - 20, 20), Qt.AlignRight,
                   "Real WinMeh UI and logic, rendered offscreen · PC data simulated for the demo")
        p.end()
        self.ff.stdin.write(bytes(img.constBits())[: W * H * 3] if img.bytesPerLine() == W * 3
                            else b"".join(bytes(img.constScanLine(y))[: W * 3] for y in range(H)))
        self.frames += 1

    def _card(self, p, title, sub):
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("DejaVu Sans", 46, QFont.Bold))
        p.drawText(QRectF(0, H / 2 - 120, W, 80), Qt.AlignCenter, title)
        p.setFont(QFont("DejaVu Sans", 18))
        p.setPen(QColor(235, 235, 255))
        p.drawText(QRectF(120, H / 2 - 20, W - 240, 160), Qt.AlignHCenter | Qt.TextWordWrap, sub)

    def _captions(self, p):
        p.setPen(QColor(255, 255, 255, 170))
        p.setFont(QFont("DejaVu Sans", 13, QFont.Bold))
        p.drawText(QRectF(70, 150, 640, 30), Qt.AlignLeft, self.step)
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont("DejaVu Sans", 30, QFont.Bold))
        p.drawText(QRectF(70, 185, 660, 130), Qt.AlignLeft | Qt.TextWordWrap, self.title)
        p.setPen(QColor(232, 232, 250))
        p.setFont(QFont("DejaVu Sans", 16))
        p.drawText(QRectF(70, 320, 640, 260), Qt.AlignLeft | Qt.TextWordWrap, self.sub)

    def _browser(self, p, url):
        r = QRectF(70, 560, 650, 70)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.fillPath(path, QColor(250, 250, 252))
        p.setPen(QPen(QColor(200, 200, 210), 1))
        p.drawPath(path)
        for i, c in enumerate((QColor(255, 95, 86), QColor(255, 189, 46), QColor(39, 201, 63))):
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(r.x() + 20 + i * 18, r.y() + 20), 5.5, 5.5)
        p.setPen(QColor(90, 90, 100))
        p.setFont(QFont("DejaVu Sans", 10))
        p.drawText(QRectF(r.x() + 76, r.y() + 10, 500, 20), Qt.AlignLeft, "Your browser opened:")
        p.setPen(QColor(25, 80, 200))
        p.setFont(QFont("DejaVu Sans Mono", 12))
        p.drawText(QRectF(r.x() + 18, r.y() + 36, r.width() - 30, 24), Qt.AlignLeft | Qt.TextSingleLine,
                   url if len(url) < 70 else url[:67] + "…")

    def hold(self, seconds: float):
        t0 = time.time()
        for i in range(int(seconds * FPS)):
            QCoreApplication.processEvents()
            self.frame()
            delay = t0 + (i + 1) / FPS - time.time()
            if delay > 0:
                time.sleep(delay)

    def close(self):
        self.ff.stdin.close()
        self.ff.wait()


rec = Recorder()


def make_widget(accessible=False):
    s = Settings()
    s.accessible = accessible
    s.speak_replies = False
    s.handoff_service = "chatgpt"
    s.online_lookups = False
    a = Assistant(s, DemoLLM())
    a.profile = dict(PROFILE)
    a._games = GAMES
    a._available = {"chatgpt": "web", "claude": "web", "deepseek": "web"}
    a.index.build([str(pics)])

    def opened(url):
        rec.toast, rec.toast_until = url, time.time() + 3.5
        return True
    a.open_url = opened
    w = Widget(s, a)
    w.resize(420, 640) if not accessible else w.resize(470, 660)
    w.show()
    for _ in range(5):
        QCoreApplication.processEvents()
    rec.widget = w
    return w


def scene(step, title, sub):
    rec.step, rec.title, rec.sub = step, title, sub


def type_and_send(w, text, cps=22):
    for i in range(1, len(text) + 1):
        w.input.setText(text[:i])
        rec.hold(1 / cps)
    rec.hold(0.3)
    w.submit()
    rec.hold(0.2)
    while w._busy:
        rec.hold(0.1)


def click(w, action, label):
    rec.sub += f"\n\n👆 Clicks “{label}”"
    rec.hold(1.0)
    w.on_link(f"act:{action}")
    rec.hold(0.2)
    while w._busy:
        rec.hold(0.1)


def clear(w):
    while w.chat.count() > 1:                  # keep the stretch at index 0
        item = w.chat.takeAt(1)
        if item.widget():
            item.widget().hide()
            item.widget().setParent(None)      # deleteLater never runs without a real event loop
    w._live_bubble = None


# ------------------------------------------------------------------ the script
rec.card = ("WinMeh", "A friendly, local-first helper that turns an old PC into something anyone can talk or type to.\n"
                      "Windows 10/11 · Linux · runs on 4 GB RAM")
rec.hold(4)
rec.card = None

w = make_widget()
scene("1 · Knows your PC", "“How much is my VRAM?”",
      "Answered instantly from the machine itself. No AI needed, nothing leaves the PC.")
rec.hold(0.8)
type_and_send(w, "how much is my vram?")
rec.hold(2.5)

scene("2 · Finds your files", "“wher is my wedding photo”",
      "Typos are fine. It searches the Windows index and its own file index, and understands "
      "related words like nikah or shaadi.")
type_and_send(w, "wher is my wedding photo")
rec.hold(3.5)

clear(w)
scene("3 · Games you can run", "“what games can I run on this machine?”",
      "It finds Steam, Epic, GOG, Ubisoft and Xbox games and checks each one against Steam's published "
      "minimum requirements.")
type_and_send(w, "get me the games list that I can run in this machine")
rec.hold(4.5)

clear(w)
scene("4 · Frees up space", "“how to clear the cache”",
      "It scans first and shows the sizes. Nothing is deleted until you say yes.")
type_and_send(w, "how to clear the cache")
rec.hold(2.5)
click(w, "confirm", "Clear now")
rec.hold(2.5)

clear(w)
scene("5 · Fixes annoyances", "“stop the vlc player update message”",
      "It edits VLC's own settings file and keeps a backup.")
type_and_send(w, "stop the vlc palyer update message")
rec.hold(3)

clear(w)
scene("6 · Driver health", "“check my drivers”",
      "It flags broken devices and old graphics drivers, and installs updates from Windows Update only after "
      "you confirm. It never uses shady 'driver updater' sites.")
type_and_send(w, "check my drivers")
rec.hold(4.5)

clear(w)
scene("7 · Installs apps safely", "“install spotify”",
      "It uses winget on Windows (flatpak or apt on Linux). It always asks first.")
type_and_send(w, "install spotify")
rec.hold(2)
click(w, "confirm", "Install")
rec.hold(2.5)

clear(w)
scene("8 · Chats locally", "“tell me a joke”",
      "Small talk goes to a tiny on-device model (Qwen2.5-0.5B). It works offline, and answers start in a "
      "fraction of a second.")
type_and_send(w, "tell me a joke")
rec.hold(1.5)
scene("8 · Chats locally", "“what is 15% of 80?”", "Arithmetic goes to a built-in calculator, because small AIs "
      "get maths wrong.")
type_and_send(w, "what is 15% of 80")
rec.hold(2.5)

clear(w)
scene("9 · Knows its limits", "“write a cover letter for a nursing job”",
      "A 0.2 ms classifier spots that this needs a bigger AI. WinMeh asks first and shows exactly what "
      "will be sent. Your PC details are never included unless you choose to.")
type_and_send(w, "write a cover letter for a nursing job")
rec.hold(4.5)
click(w, "confirm", "Yes, ask ChatGPT")
rec.sub = "It opens ChatGPT (or Claude or DeepSeek) in your own browser with the question filled in. No API keys, no automation."
rec.hold(4)

rec.widget.close()
w = make_widget(accessible=True)
scene("10 · For everyone", "Accessibility mode",
      "Large text, high contrast, slower speech, and simple yes/no buttons that say exactly what will "
      "happen. Kid-safe mode adds SafeSearch and a parent PIN.")
rec.hold(0.6)
type_and_send(w, "write a short speech for my dad's birthday")
rec.hold(5)

rec.widget.close()
rec.widget = None
rec.card = ("Try WinMeh", "Open source (MIT) · github.com/0xAshraFF/WinMeh\n"
                          "One-click installer for Windows 10/11 · one command on Linux")
rec.hold(4)
rec.close()
print(f"wrote {OUT} - {rec.frames} frames, {rec.frames / FPS:.0f} s")
