"""Classifier, hand-off URLs, permission flow, accessibility and kid-safe behaviour."""

import time
import urllib.parse
from pathlib import Path

import pytest

from winmeh.config import Settings
from winmeh.core import calc, classifier as C, handoff
from winmeh.core.assistant import Assistant, looks_weak
from winmeh.core.router import route
from winmeh.system import web

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ classifier
@pytest.fixture(scope="module")
def model():
    m = C.Classifier.load(C.MODEL_FILE)
    assert m is not None
    return m


def test_heldout_accuracy_above_90(model):
    texts, labels = C.load_eval(ROOT / "data")
    assert len(texts) >= 150 and set(labels) == set(C.LABELS)
    acc = C.accuracy(model, texts, labels)
    assert acc > 0.90, f"held-out accuracy {acc:.1%}"


def test_eval_set_is_not_in_training_data():
    train = {t.strip().lower() for t in C.load_training(ROOT / "data")[0]}
    assert not [t for t in C.load_eval(ROOT / "data")[0] if t.strip().lower() in train]


def test_inference_is_about_a_millisecond(model):
    qs = ["write a cover letter for a teaching job", "weather in london tomorrow", "tell me a joke"] * 50
    t = time.perf_counter()
    for q in qs:
        model.decide(q)
    per = (time.perf_counter() - t) * 1000 / len(qs)
    assert per < 5, f"{per:.2f} ms per query"      # ~0.2 ms locally; generous bound for slow CI runners


@pytest.mark.parametrize("text,label", [
    ("hello how are you", C.LOCAL),
    ("what's the bitcoin price today", C.WEB),
    ("Traceback (most recent call last):\n  File 'x.py', line 3\nKeyError: 'name'", C.ONLINE),
    ("write a 1000 word essay about the french revolution", C.ONLINE),
])
def test_obvious_cases(model, text, label):
    assert C.LABELS[int(model.probs(text).argmax())] == label


def test_hand_features_fire():
    f = C.hand_features("```python\ndef f(): pass\n```\nTypeError: x")
    assert f[2] > 0                                              # code signals
    assert C.hand_features("please summarize and translate this")[3] > 0
    assert C.hand_features("latest news today")[4] > 0
    assert C.hand_features("integrate x^2 dx")[5] > 0


class FixedModel:
    def __init__(self, label, prob):
        self.label, self.prob = label, prob

    def decide(self, text):
        action = "local" if self.label == C.LOCAL or self.prob < C.ASK else "act" if self.prob > C.ACT else "ask"
        return C.Decision(self.label, self.prob, action, {self.label: self.prob})


@pytest.mark.parametrize("p,action", [
    ([0.05, 0.05, 0.90], "act"),       # ONLINE > 0.8 -> act (the hand-off still asks permission)
    ([0.10, 0.85, 0.05], "act"),       # WEB > 0.8 -> search right away
    ([0.30, 0.00, 0.70], "ask"),       # 0.5-0.8 -> ask the user
    ([0.45, 0.00, 0.55], "ask"),
    ([0.30, 0.25, 0.45], "local"),     # top class under 0.5 -> answer locally
    ([0.90, 0.05, 0.05], "local"),     # LOCAL wins
])
def test_threshold_policy(model, p, action, monkeypatch):
    import numpy as np
    monkeypatch.setattr(model, "probs", lambda t: np.array(p))
    assert model.decide("x").action == action


def test_feedback_retrains_for_this_user(monkeypatch, tmp_path):
    root = tmp_path / "data"
    (root / "router").mkdir(parents=True)
    for lab, rows in {"local_chat": ["hi", "hello", "how are you", "tell me a joke", "good morning", "thanks"],
                      "web_search": ["weather today", "news today", "bitcoin price", "score last night", "gold price now",
                                     "latest news"],
                      "online_llm": ["write an essay", "fix my code", "summarize this article", "translate this letter",
                                     "write a cover letter", "debug this error"]}.items():
        (root / "router" / f"{lab}.txt").write_text("\n".join(rows))
    monkeypatch.setattr(C, "data_root", lambda: root)
    for _ in range(6):
        C.log_feedback("plan my garden layout", C.ONLINE, C.LOCAL)      # this user keeps saying "No"
    m, msg = C.retrain_for_user(feedback_weight=8.0)
    assert m is not None and "6" in msg
    assert C.LABELS[int(m.probs("plan my garden layout").argmax())] == C.LOCAL
    assert C.load_default().meta.get("feedback") == 6                    # user model is picked up next start


# ------------------------------------------------------------------ hand-off URLs
def test_chatgpt_and_claude_urls_encode_everything():
    text = "fix this: a & b = c?\nnext line ünïcode #1"
    u = handoff.build_url(handoff.SERVICES["chatgpt"], text)
    assert u.startswith("https://chatgpt.com/?q=")
    assert urllib.parse.unquote(u.split("?q=", 1)[1]) == text
    assert " " not in u and "&" not in u.split("?q=", 1)[1] and "#" not in u
    c = handoff.build_url(handoff.SERVICES["claude"], text)
    assert c.startswith("https://claude.ai/new?q=") and urllib.parse.unquote(c.split("?q=", 1)[1]) == text


def test_deepseek_and_long_text_use_clipboard():
    assert handoff.build_url(handoff.SERVICES["deepseek"], "hi") is None
    assert handoff.build_url(handoff.SERVICES["chatgpt"], "x" * 7000) is None


def test_compose_only_adds_specs_when_asked():
    assert handoff.compose("  why is the sky blue  ") == "why is the sky blue"
    assert "My computer: 8 GB RAM" in handoff.compose("q", "8 GB RAM")


def test_detect_and_choose():
    found = handoff.detect(["ChatGPT", "Claude", "Notepad"])
    assert found["chatgpt"] == "app" and found["claude"] == "app"
    assert handoff.choose("auto", found).key == "claude"
    assert handoff.choose("deepseek", found).key == "deepseek"
    assert handoff.choose("auto", handoff.detect([])).key == "chatgpt"
    assert handoff.choose("claude_code", {"chatgpt": "web"}).key == "chatgpt"    # not installed -> fallback


def test_claude_code_query_cannot_inject_commands():
    q = 'x"; rm -rf ~ & del C:\\* | calc'
    argv, env, _ = handoff.claude_code_command(q, str(ROOT))
    assert env["WINMEH_QUERY"] == q
    assert all(q not in a for a in argv)              # travels only through the environment


# ------------------------------------------------------------------ permission flow
class FakeLLM:
    status = "ready"

    def __init__(self, reply="Here is a friendly answer for you."):
        self.reply, self.seen = reply, []

    def stream(self, msgs, **kw):
        self.seen.append(msgs)
        yield self.reply


@pytest.fixture
def a(monkeypatch):
    s = Settings()
    s.handoff_service = "chatgpt"
    x = Assistant(s, llm=None)
    x.profile = {"os": "Windows 10", "cpu": "Intel i3", "cores": 4, "ram_gb": 4.0, "gpus": [], "disks": [], "apps": []}
    x._available = {"chatgpt": "web", "claude": "web", "deepseek": "web"}
    x.opened, x.copied = [], []
    x.open_url = lambda u: x.opened.append(u) or True
    x.copy_to_clipboard = lambda t: x.copied.append(t) or True
    return x


def say(a, text):
    out = {"html": [], "extra": [], "token": []}
    a.handle(text, lambda k, p: out.setdefault(k, []).append(p))
    return out


def feedback():
    return C.load_feedback()


def test_online_query_asks_first_and_shows_exact_text(a):
    a.classifier = FixedModel(C.ONLINE, 0.95)
    q = "write a cover letter for a nursing job"
    h = say(a, q)["html"][-1]
    assert "This needs a bigger AI" in h and "ChatGPT" in h and q in h
    assert "nothing else" in h and "act:confirm" in h and "act:cancel" in h
    assert a.opened == []                               # nothing sent before Yes
    h = say(a, "yes")["html"][-1]
    assert a.opened and urllib.parse.unquote(a.opened[0]) == "https://chatgpt.com/?q=" + q
    assert "My computer" not in urllib.parse.unquote(a.opened[0])
    assert feedback() == ([q], [C.ONLINE])


def test_middle_probability_uses_softer_question(a):
    a.classifier = FixedModel(C.ONLINE, 0.65)
    assert "might need a bigger AI" in say(a, "compare two phones")["html"][-1]


def test_no_answers_locally_with_the_original_question(a):
    a.llm = FakeLLM()
    a.classifier = FixedModel(C.ONLINE, 0.9)
    say(a, "plan a party for my mum")
    out = say(a, "cancel")
    assert a.opened == [] and out["token"] == ["Here is a friendly answer for you."]
    assert a.llm.seen[-1][-1] == {"role": "user", "content": "plan a party for my mum"}
    assert feedback()[1] == [C.LOCAL]


def test_include_specs_is_explicit(a):
    a.classifier = FixedModel(C.ONLINE, 0.9)
    h = say(a, "why does everything take ages to load")["html"][-1]
    assert "act:specs" in h and "4.0 GB" in h                    # says exactly what would be added
    say(a, "yes, include my specs")
    assert "My computer:" in urllib.parse.unquote(a.opened[0])


def test_deepseek_copies_then_opens_site(a):
    a.s.handoff_service = "deepseek"
    a._available["deepseek"] = "web"
    a.classifier = FixedModel(C.ONLINE, 0.9)
    say(a, "write a poem about rain")
    h = say(a, "yes")["html"][-1]
    assert a.copied == ["write a poem about rain"] and a.opened == ["https://chat.deepseek.com/"]
    assert "Ctrl+V" in h


def test_claude_code_needs_second_confirmation(a, monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(handoff, "launch_claude_code", lambda q, f: launched.append((q, f)) or (True, "ok"))
    a._available["claude_code"] = "command"
    a.s.handoff_service = "claude_code"
    a.s.handoff_folder = str(tmp_path)
    a.classifier = FixedModel(C.ONLINE, 0.9)
    say(a, "refactor my project")
    h = say(a, "yes")["html"][-1]
    assert launched == [] and str(tmp_path) in h and "change files" in h
    say(a, "yes")
    assert launched == [("refactor my project", str(tmp_path))]


def test_kid_safe_handoff_needs_pin(a):
    a.s.kid_safe = True
    a.s.set_pin("2468")
    a.ask_pin = lambda prompt, new=False: "0000"
    a.classifier = FixedModel(C.ONLINE, 0.9)
    say(a, "write my homework essay")
    assert "parent PIN" in say(a, "yes")["html"][-1] and a.opened == []
    a.ask_pin = lambda prompt, new=False: "2468"
    say(a, "write my homework essay")
    say(a, "yes")
    assert a.opened


def test_web_act_searches_and_ask_waits(a):
    a.classifier = FixedModel(C.WEB, 0.9)
    say(a, "bitcoin price today")
    assert a.opened == ["https://www.google.com/search?q=bitcoin+price+today"]
    a.classifier = FixedModel(C.WEB, 0.6)
    h = say(a, "best phones")["html"][-1]
    assert "Search the web" in h and len(a.opened) == 1
    say(a, "yes")
    assert len(a.opened) == 2 and feedback()[1] == [C.WEB]


def test_kid_safe_search_uses_safesearch(a):
    a.s.kid_safe = True
    say(a, "search youtube for funny cats")
    assert "safe=active" in a.opened[0] and "youtube.com" in urllib.parse.unquote(a.opened[0])


def test_local_answer_runs_when_classifier_says_local(a):
    a.llm = FakeLLM()
    a.classifier = FixedModel(C.LOCAL, 0.9)
    out = say(a, "tell me a joke")
    assert out["token"] and not out["extra"] and a.pending is None


def test_safety_net_offers_handoff_after_weak_answer(a):
    a.llm = FakeLLM("I'm not sure.")
    a.classifier = FixedModel(C.LOCAL, 0.9)
    out = say(a, "what's a good mortgage strategy")
    assert out["extra"] and "may not be good enough" in out["extra"][0] and a.pending is not None
    assert looks_weak("ok") and not looks_weak("Paris is the capital of France.")


def test_explicit_ask_ai_still_confirms(a):
    h = say(a, "ask claude to write a haiku about tea")["html"][-1]
    assert "Claude" in h and "write a haiku about tea" in h and a.opened == []
    say(a, "yes")
    assert a.opened[0].startswith("https://claude.ai/new?q=")


def test_set_ai_saves_preference(a):
    say(a, "set my ai to deepseek")
    assert a.s.handoff_service == "deepseek"


# ------------------------------------------------------------------ links, accessibility, kid-safe, calc
def test_unknown_link_warns_before_opening(a):
    h = say(a, "go to some-random-site.xyz")["html"][-1]
    assert "isn't on my list" in h and a.opened == []
    say(a, "yes")
    assert a.opened == ["https://some-random-site.xyz"]
    say(a, "go to youtube.com")
    assert a.opened[-1] == "https://youtube.com"


def test_is_known():
    assert web.is_known("https://mail.google.com/x") and web.is_known("https://chatgpt.com/?q=a")
    assert not web.is_known("https://google.com.evil.io") and not web.is_known("javascript:alert(1)")


def test_accessibility_mode_plain_buttons(a):
    say(a, "turn on accessibility mode")
    assert a.s.accessible
    a.classifier = FixedModel(C.ONLINE, 0.9)
    h = say(a, "write a speech")["html"][-1]
    assert "✅ Yes, ask ChatGPT" in h and "❌ No, answer here" not in h and "❌ No<" in h and "No, no" not in h


def test_kid_safe_toggle_requires_pin(a):
    a.ask_pin = lambda prompt, new=False: "1357"
    say(a, "kid safe mode on")
    assert a.s.kid_safe and a.s.check_pin("1357")
    a.ask_pin = lambda prompt, new=False: "9999"
    say(a, "kid safe mode off")
    assert a.s.kid_safe                                   # wrong PIN: stays on
    a.ask_pin = lambda prompt, new=False: "1357"
    say(a, "kid safe mode off")
    assert not a.s.kid_safe


def test_kid_safe_install_needs_pin(a, monkeypatch):
    from winmeh.system import packages
    monkeypatch.setattr(packages, "available_managers", lambda: ["winget"])
    monkeypatch.setattr(packages, "find", lambda n: [packages.Package("VLC", "VideoLAN.VLC", "winget")])
    done = []
    monkeypatch.setattr(packages, "install", lambda p: done.append(p) or (True, "ok"))
    a.s.kid_safe = True
    a.s.set_pin("1111")
    a.ask_pin = lambda prompt, new=False: None
    say(a, "install vlc")
    say(a, "yes")
    assert done == []


@pytest.mark.parametrize("q,ans", [("what is 12*7", "84"), ("15% of 80", "12"), ("what's 3 x 4?", "12"),
                                   ("how much is 1200/12", "100"), ("2^10", "1,024")])
def test_calculator(a, q, ans):
    assert route(q).name == "calc"
    assert f"<b>{ans}</b>" in say(a, q)["html"][-1]


def test_calc_rejects_code():
    with pytest.raises(ValueError):
        calc.evaluate("__import__('os').system('x')")
    with pytest.raises(ValueError):
        calc.evaluate("9**999999")


@pytest.mark.parametrize("text,intent", [
    ("set my ai to claude", "set_ai"), ("use chatgpt for big questions", "set_ai"),
    ("ask chatgpt to write a poem", "ask_ai"), ("learn from my choices", "retrain"),
    ("accessibility on", "accessibility"), ("kid safe mode on", "kid_safe"), ("yes, include my specs", "confirm_specs"),
])
def test_new_routes(text, intent):
    assert route(text).name == intent
