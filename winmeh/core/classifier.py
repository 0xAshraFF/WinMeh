"""'Who should handle this?' - LOCAL_CHAT / WEB_SEARCH / ONLINE_LLM in about a millisecond.

Model: character 2-4-gram TF-IDF + a few hand-made features -> multinomial logistic
regression. Training and inference are plain numpy (no scikit-learn), and the weights
ship as a small JSON file, so this stays light enough for 4 GB machines.

Runs only after the regex router (core/router.py) found no built-in skill.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import data_dir

LABELS = ["LOCAL_CHAT", "WEB_SEARCH", "ONLINE_LLM"]
LOCAL, WEB, ONLINE = LABELS
ACT, ASK = 0.8, 0.5                       # probability thresholds from the product spec

MODEL_FILE = Path(__file__).with_name("router_model.json")     # shipped weights
FEEDBACK_FILE = "router_feedback.jsonl"                         # in the user's data dir
USER_MODEL_FILE = "router_model_user.json"                      # retrained on this user's choices

# ------------------------------------------------------------------ hand-made features
_CODE = re.compile(r"```|\bdef \w+\(|\bclass \w+|\bfunction\b|=>|\bTraceback\b|\b\w*(Error|Exception):|"
                   r"\bline \d+\b|\bimport \w+|#include|\bSELECT\b.+\bFROM\b|\bconsole\.log|[{};]\s*$|"
                   r"\bnull\b|\bundefined\b|\bnpm\b|\bpip install\b|\bsegfault\b|\bstack ?trace\b", re.I | re.M)
_TASK = re.compile(r"\b(write|rewrite|draft|compose|fix|debug|refactor|optimi[sz]e|compare|summari[sz]e|"
                   r"translate|explain why|analy[sz]e|review|proofread|outline|plan|design|generate|create a)\b", re.I)
_RECENT = re.compile(r"\b(latest|newest|today|tonight|tomorrow|yesterday|this (week|weekend|month|year)|news|"
                     r"prices?|cost|costs|score|scores|results?|release[ds]?|current(ly)?|right now|live|"
                     r"weather|forecast|stock|who won|schedule|open now|near me|20[2-3]\d)\b", re.I)
_MATH = re.compile(r"\b(integral|integrate|derivative|differentiate|solve|equation|prove|proof|matrix|"
                   r"probability|calculus|algebra|theorem|limit of|sigma)\b|\d\s*[\^=]\s*\d|x\^2|\bdx\b", re.I)

N_HAND = 8


def hand_features(text: str) -> np.ndarray:
    words = text.split()
    return np.array([
        min(math.log1p(len(text)) / 6.0, 1.5),            # length
        1.0 if len(words) > 25 else 0.0,                   # long request
        min(len(_CODE.findall(text)), 3) / 3.0,            # code signals
        min(len(_TASK.findall(text)), 2) / 2.0,            # task verbs
        min(len(_RECENT.findall(text)), 2) / 2.0,          # recency words
        min(len(_MATH.findall(text)), 2) / 2.0,            # math
        1.0 if "?" in text else 0.0,
        1.0 if text.count("\n") >= 2 else 0.0,             # pasted multi-line text
    ], dtype=np.float32)


# ------------------------------------------------------------------ n-grams
def normalize(text: str) -> str:
    return " " + re.sub(r"\s+", " ", text.lower()).strip() + " "


def ngrams(text: str, lo: int = 2, hi: int = 4) -> Counter:
    t = normalize(text)[:600]                          # long pastes: the start carries the signal
    c: Counter = Counter()
    for n in range(lo, hi + 1):
        for i in range(len(t) - n + 1):
            c[t[i:i + n]] += 1
    return c


# ------------------------------------------------------------------ model
@dataclass
class Decision:
    label: str               # LOCAL_CHAT | WEB_SEARCH | ONLINE_LLM
    prob: float              # probability of that label
    action: str              # "local" | "ask" | "act"
    probs: dict
    ms: float = 0.0


class Classifier:
    def __init__(self, vocab: dict[str, int], idf: np.ndarray, W: np.ndarray, b: np.ndarray, meta: dict | None = None):
        self.vocab, self.idf, self.W, self.b = vocab, idf, W, b
        self.meta = meta or {}

    # ---------------- inference
    def _vector(self, text: str) -> tuple[np.ndarray, np.ndarray]:
        idx, vals = [], []
        for g, tf in ngrams(text).items():
            j = self.vocab.get(g)
            if j is not None:
                idx.append(j)
                vals.append((1.0 + math.log(tf)) * self.idf[j])
        v = np.asarray(vals, dtype=np.float32)
        norm = float(np.sqrt((v * v).sum())) or 1.0
        return np.asarray(idx, dtype=np.int64), v / norm

    def probs(self, text: str) -> np.ndarray:
        idx, v = self._vector(text)
        V = len(self.vocab)
        logits = self.b.copy()
        if len(idx):
            logits += v @ self.W[idx]
        logits += hand_features(text) @ self.W[V:]
        e = np.exp(logits - logits.max())
        return e / e.sum()

    def decide(self, text: str) -> Decision:
        t0 = time.perf_counter()
        p = self.probs(text)
        k = int(p.argmax())
        label, prob = LABELS[k], float(p[k])
        if label == LOCAL or prob < ASK:
            action = "local"
        elif prob > ACT:
            action = "act"
        else:
            action = "ask"
        return Decision(label, prob, action, {l: round(float(x), 3) for l, x in zip(LABELS, p)},
                        (time.perf_counter() - t0) * 1000)

    # ---------------- persistence
    def to_json(self) -> dict:
        terms = sorted(self.vocab, key=self.vocab.get)
        return {"labels": LABELS, "ngram": [2, 4], "n_hand": N_HAND, "meta": self.meta,
                "vocab": terms, "idf": [round(float(x), 4) for x in self.idf],
                "W": [[round(float(x), 4) for x in row] for row in self.W], "b": [round(float(x), 4) for x in self.b]}

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_json(), separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Classifier":
        d = json.loads(path.read_text(encoding="utf-8"))
        vocab = {t: i for i, t in enumerate(d["vocab"])}
        return cls(vocab, np.asarray(d["idf"], np.float32), np.asarray(d["W"], np.float32),
                   np.asarray(d["b"], np.float32), d.get("meta"))


def load_default() -> Classifier | None:
    """The user's retrained model if there is one, else the shipped model. None if neither exists."""
    for p in (data_dir() / USER_MODEL_FILE, MODEL_FILE):
        if p.exists():
            try:
                return Classifier.load(p)
            except (OSError, ValueError, KeyError):
                continue
    return None


# ------------------------------------------------------------------ training (numpy only)
def train(texts: list[str], labels: list[str], weights: list[float] | None = None, vocab_size: int = 5000,
          epochs: int = 250, l2: float = 1e-4, lr: float = 0.05, seed: int = 0) -> Classifier:
    y = np.array([LABELS.index(l) for l in labels])
    sw = np.asarray(weights if weights is not None else [1.0] * len(texts), np.float32)
    grams = [ngrams(t) for t in texts]
    df: Counter = Counter()
    for g in grams:
        df.update(g.keys())
    terms = [t for t, c in df.most_common() if c >= 2][:vocab_size]
    vocab = {t: i for i, t in enumerate(terms)}
    n = len(texts)
    idf = np.array([math.log((1 + n) / (1 + df[t])) + 1.0 for t in terms], np.float32)

    V = len(vocab)
    X = np.zeros((n, V + N_HAND), np.float32)
    for r, (g, t) in enumerate(zip(grams, texts)):
        for term, tf in g.items():
            j = vocab.get(term)
            if j is not None:
                X[r, j] = (1.0 + math.log(tf)) * idf[j]
        norm = np.linalg.norm(X[r, :V]) or 1.0
        X[r, :V] /= norm
        X[r, V:] = hand_features(t)

    # balance classes so the biggest file doesn't win by default
    counts = np.bincount(y, minlength=len(LABELS)).astype(np.float32)
    sw = sw * (n / (len(LABELS) * np.maximum(counts, 1)))[y]
    sw = sw / sw.mean()

    rng = np.random.default_rng(seed)
    W = (rng.standard_normal((V + N_HAND, len(LABELS))) * 0.01).astype(np.float32)
    b = np.zeros(len(LABELS), np.float32)
    Y = np.eye(len(LABELS), dtype=np.float32)[y]
    mW, vW, mb, vb = np.zeros_like(W), np.zeros_like(W), np.zeros_like(b), np.zeros_like(b)
    b1, b2, eps = 0.9, 0.999, 1e-8
    for step in range(1, epochs + 1):                  # full-batch Adam on softmax cross-entropy
        Z = X @ W + b
        Z -= Z.max(axis=1, keepdims=True)
        P = np.exp(Z)
        P /= P.sum(axis=1, keepdims=True)
        G = (P - Y) * sw[:, None] / n
        gW = X.T @ G + l2 * W
        gb = G.sum(axis=0)
        mW = b1 * mW + (1 - b1) * gW
        vW = b2 * vW + (1 - b2) * gW * gW
        mb = b1 * mb + (1 - b1) * gb
        vb = b2 * vb + (1 - b2) * gb * gb
        corr = math.sqrt(1 - b2 ** step) / (1 - b1 ** step)
        W -= lr * corr * mW / (np.sqrt(vW) + eps)
        b -= lr * corr * mb / (np.sqrt(vb) + eps)
    return Classifier(vocab, idf, W, b, {"trained_on": n, "vocab": V})


# ------------------------------------------------------------------ data + feedback
def data_root() -> Path | None:
    """Where the training texts live: repo `data/` in source runs, bundled copy in builds."""
    from .bootstrap import app_dir
    for base in (app_dir(), Path(getattr(sys, "_MEIPASS", "")) if hasattr(sys, "_MEIPASS") else None):
        if base and (base / "data" / "router").is_dir():
            return base / "data"
    return None


def load_training(root: Path) -> tuple[list[str], list[str]]:
    texts, labels = [], []
    for label in LABELS:
        f = root / "router" / f"{label.lower()}.txt"
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                texts.append(line.replace("\\n", "\n"))
                labels.append(label)
    return texts, labels


def load_eval(root: Path) -> tuple[list[str], list[str]]:
    texts, labels = [], []
    for line in (root / "router_eval.tsv").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            label, text = line.split("\t", 1)
            texts.append(text.replace("\\n", "\n"))
            labels.append(label.strip())
    return texts, labels


def log_feedback(text: str, predicted: str, chosen: str) -> None:
    """chosen = the label the user's Yes/No implies (No to a hand-off => LOCAL_CHAT)."""
    rec = {"t": round(time.time()), "text": text[:2000], "predicted": predicted, "label": chosen}
    with open(data_dir() / FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def load_feedback() -> tuple[list[str], list[str]]:
    texts, labels = [], []
    try:
        for line in (data_dir() / FEEDBACK_FILE).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("label") in LABELS and r.get("text"):
                texts.append(r["text"])
                labels.append(r["label"])
    except OSError:
        pass
    return texts, labels


def retrain_for_user(feedback_weight: float = 4.0) -> tuple[Classifier | None, str]:
    root = data_root()
    if root is None:
        return None, "the training data isn't installed with this copy"
    texts, labels = load_training(root)
    ft, fl = load_feedback()
    if not ft:
        return None, "there are no Yes/No choices to learn from yet"
    model = train(texts + ft, labels + fl, [1.0] * len(texts) + [feedback_weight] * len(ft))
    model.meta["feedback"] = len(ft)
    model.save(data_dir() / USER_MODEL_FILE)
    return model, f"learned from {len(ft)} of your choices"


def accuracy(model: Classifier, texts: list[str], labels: list[str]) -> float:
    hits = sum(LABELS[int(model.probs(t).argmax())] == l for t, l in zip(texts, labels))
    return hits / max(1, len(texts))
