"""Train the LOCAL_CHAT / WEB_SEARCH / ONLINE_LLM classifier and write its weights.

  python scripts/train_classifier.py                  # base data -> winmeh/core/router_model.json
  python scripts/train_classifier.py --with-feedback  # + this user's Yes/No choices -> user model in the data dir

Prints accuracy on the hand-written held-out set (data/router_eval.tsv) and per-class recall.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from winmeh.core import classifier as C  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-feedback", action="store_true", help="also learn from this user's logged choices")
    ap.add_argument("--min-accuracy", type=float, default=0.90)
    a = ap.parse_args()

    root = ROOT / "data"
    texts, labels = C.load_training(root)
    t0 = time.perf_counter()
    if a.with_feedback:
        model, msg = C.retrain_for_user()
        if model is None:
            print("not retrained:", msg)
            return 1
        print(msg, "->", C.data_dir() / C.USER_MODEL_FILE)
    else:
        model = C.train(texts, labels)
        model.save(C.MODEL_FILE)
        print(f"wrote {C.MODEL_FILE.relative_to(ROOT)} ({C.MODEL_FILE.stat().st_size // 1024} KB)")
    print(f"trained on {len(texts)} examples in {time.perf_counter() - t0:.1f}s")

    et, el = C.load_eval(root)
    acc = C.accuracy(model, et, el)
    print(f"held-out accuracy: {acc:.1%} on {len(et)} hand-written queries")
    for lab in C.LABELS:
        idx = [i for i, l in enumerate(el) if l == lab]
        ok = sum(C.LABELS[int(model.probs(et[i]).argmax())] == lab for i in idx)
        print(f"  {lab:<11} recall {ok}/{len(idx)}")
    wrong = [(el[i], C.LABELS[int(model.probs(t).argmax())], t[:70]) for i, t in enumerate(et)
             if C.LABELS[int(model.probs(t).argmax())] != el[i]]
    for want, got, t in wrong:
        print(f"  miss: want {want:<11} got {got:<11} {t}")
    t0 = time.perf_counter()
    for t in et:
        model.decide(t)
    print(f"inference: {(time.perf_counter() - t0) * 1000 / len(et):.2f} ms per query")
    return 0 if acc >= a.min_accuracy else 2


if __name__ == "__main__":
    sys.exit(main())
