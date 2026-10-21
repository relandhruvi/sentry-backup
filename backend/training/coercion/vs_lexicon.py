"""Per-utterance tactic detection on val: lexicon vs deployed classifier vs both (union).

The lexicon predicts a tactic when it has a non-negated hit for it; the classifier when the
probability clears its tuned threshold (models/coercion/model.json). Same val rows and metrics as
train.py. The lexicon was never tuned on this data, the classifier's thresholds were tuned on it,
so the comparison flatters the classifier somewhat.

Usage: python -m training.coercion.vs_lexicon
"""

from __future__ import annotations

import json

import numpy as np

from app.coercion.engine import CoercionEngine
from app.coercion.model_tagger import MODEL_DIR, TacticModel
from app.text.normalize import normalize
from training.coercion.build_dataset import TACTICS
from training.coercion.train import VAL_PROMPTS, evaluate, load_rows


def main() -> None:
    val = [r for r in load_rows() if r["prompt_id"] in VAL_PROMPTS]
    lexicon = CoercionEngine.load().lexicon
    model = TacticModel.load(MODEL_DIR)
    lex = np.zeros((len(val), len(TACTICS)))
    mod = np.zeros((len(val), len(TACTICS)))
    for i, r in enumerate(val):
        norm = normalize(r["text"])
        for h in lexicon.match(norm):
            if h.category in TACTICS and h.score > 0:
                lex[i, TACTICS.index(h.category)] = 1.0
        p = model.probs(r["text"], norm)
        mod[i] = [p[k] >= model.thresholds[t] for k, t in enumerate(TACTICS)]
    half = dict.fromkeys(TACTICS, 0.5)
    out = {
        "lexicon": evaluate(val, lex, half),
        "classifier": evaluate(val, mod, half),
        "lexicon+classifier": evaluate(val, np.maximum(lex, mod), half),
    }
    (MODEL_DIR / "vs_lexicon.json").write_text(json.dumps(out, indent=2) + "\n")
    print(
        f"{'detector':20s} {'macro':>6s} {'sttMac':>6s} "
        + " ".join(f"{t[:5]:>6s}" for t in TACTICS)
        + "  FA-hn FA-chat"
    )
    for name, m in out.items():
        per = " ".join(f"{m['per_tactic'][t]['f1']:6.3f}" for t in TACTICS)
        fa = m["false_alarm_rate"]
        print(
            f"{name:20s} {m['macro_f1']:6.3f} {m['by_source']['stt_noisy']['macro_f1']:6.3f} {per}"
            f"  {fa['hard_negative']:.3f} {fa['chat']:.3f}"
        )


if __name__ == "__main__":
    main()
