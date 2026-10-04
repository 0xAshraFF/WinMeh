import pytest

from winmeh.config import Settings
from winmeh.core.assistant import Assistant

PROFILE = {
    "os": "Windows 11 Pro 24H2 (build 26100)", "cpu": "AMD Ryzen 5 5600X 6-Core Processor", "cores": 12,
    "ram_gb": 16.0, "disks": [{"mount": "C:\\", "total_gb": 476.0, "free_gb": 120.5}], "folders": {}, "apps": [],
    "gpus": [{"name": "NVIDIA GeForce RTX 3060", "vram_gb": 12.0, "integrated": False}],
}


@pytest.fixture
def a():
    s = Settings()
    s.online_lookups = False
    x = Assistant(s, llm=None)
    x.profile = dict(PROFILE)
    x._games = []
    return x


def ask(a, q):
    out = {}
    a.handle(q, lambda k, p: out.setdefault(k, p))
    return out


def test_vram_answer(a):
    out = ask(a, "how much is my vram?")
    assert "12 GB" in out["html"] and "RTX 3060" in out["html"]
    assert "12 gigabytes" in out["done"]


def test_games_answer_without_installed_games(a):
    out = ask(a, "get me the games list that I can run in this machine")
    assert "enthusiast" in out["html"]


def test_cache_requires_confirmation(a, tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    temp.mkdir()
    (temp / "junk.tmp").write_bytes(b"x" * (2 * 1024**2))
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    out = ask(a, "how to clear the cache")
    assert "act:confirm" in out["html"]
    assert (temp / "junk.tmp").exists()          # nothing deleted before confirming
    out = ask(a, "yes")
    assert "Freed" in out["html"] and not (temp / "junk.tmp").exists() and temp.exists()


def test_confirm_without_pending(a):
    assert "nothing" in ask(a, "yes")["html"].lower()


def test_chat_without_llm_offers_handoff(a):
    out = ask(a, "tell me a joke")["html"]
    assert "isn't running" in out and "act:confirm" in out and a.pending is not None


def test_remember_persists(a):
    ask(a, "remember that my backup drive is E:")
    assert Assistant(Settings(), None).memory == ["my backup drive is E:"]


def test_vram_unknown_is_not_reported_as_zero(a):
    a.profile["gpus"] = [{"name": "Some GPU", "vram_gb": 0.0, "integrated": False}]
    assert "doesn't report" in ask(a, "how much vram")["html"]
