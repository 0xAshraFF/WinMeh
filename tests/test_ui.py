import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from winmeh.config import Settings  # noqa: E402
from winmeh.core.assistant import Assistant  # noqa: E402


def test_widget_builds_and_answers(qtbot=None):
    from PySide6.QtCore import QCoreApplication
    from winmeh.ui.widget import Widget

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    s = Settings()
    a = Assistant(s, None)
    a.profile = {"gpus": [{"name": "Test GPU", "vram_gb": 8.0, "integrated": False}]}
    w = Widget(s, a)
    w.show()
    before = w.chat.count()
    w.submit("how much is my vram?")
    import time
    deadline = time.time() + 5
    while w._busy and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    assert not w._busy
    assert w.chat.count() == before + 2           # user bubble + reply bubble
    assert "8 GB" in w._live_bubble.text()
    w.close()
