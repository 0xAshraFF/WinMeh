import time

import pytest

from winmeh.core.router import route


@pytest.mark.parametrize("text,intent", [
    # the README's definition-of-done questions, typos included
    ("how much is my vram?", "vram"),
    ("wher is my wedding photo", "find_file"),
    ("how to clear the cache", "clear_cache"),
    ("get me the games list that I can run in this machine", "games"),
    ("stop the vlc palyer update message", "vlc_updates"),
    # more phrasings
    ("How much VRAM do I have", "vram"),
    ("what graphics card is in this pc", "vram"),
    ("where are my wedding pictures", "find_file"),
    ("find my resume", "find_file"),
    ("clean temp files", "clear_cache"),
    ("what games can I play", "games"),
    ("can I run Cyberpunk 2077?", "can_run"),
    ("can i run games on this machine", "games"),
    ("disable vlc update notification", "vlc_updates"),
    ("how much ram do i have", "ram"),
    ("what are my specs", "specs"),
    ("how much space is left on my drive", "disk"),
    ("open spotify", "open_app"),
    ("open my wedding photo", "find_file"),
    ("yes", "confirm"),
    ("cancel", "cancel"),
    ("remember that my car is blue", "remember"),
    ("rescan my pc", "reindex"),
])
def test_routes(text, intent):
    r = route(text)
    assert r is not None and r.name == intent, (text, r)


@pytest.mark.parametrize("text", ["hello", "tell me a joke", "what is the capital of France"])
def test_falls_through_to_llm(text):
    assert route(text) is None


def test_extracts_game_name():
    assert route("can I run Elden Ring?").args["game"] == "Elden Ring"


def test_router_is_fast():
    t = time.perf_counter()
    for _ in range(1000):
        route("get me the games list that I can run in this machine")
    assert (time.perf_counter() - t) / 1000 < 0.001  # < 1 ms per query
