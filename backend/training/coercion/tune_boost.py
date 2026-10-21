"""Tune the gate for the lexicon boost on val and store it in models/coercion/model.json.

Fusion: the classifier is primary; a lexicon hit adds `lexicon_boost` (weights.yaml) to the
probability of the same tactic only when the classifier already scores it >= gate. For each gate
on a grid we score val exactly as the app would (ModelTagger.boosted + tuned thresholds). The gate
with the best macro-F1 is kept only if it beats "boost off" by MIN_GAIN and false alarms on hard negatives and
on chat do not rise; otherwise the boost stays off (gate = null).

Usage: python -m training.coercion.tune_boost
"""

from __future__ import annotations

import json

import numpy as np

from app.coercion.engine import CoercionEngine
from app.coercion.model_tagger import MODEL_DIR, ModelTagger, TacticModel
from app.text.normalize import normalize
from training.coercion.build_dataset import TACTICS
from training.coercion.train import VAL_PROMPTS, evaluate, load_rows

GATES = [round(g, 2) for g in np.arange(0.05, 0.95, 0.05)]
# Smaller gains than this are noise on ~800 val rows; the boost then stays off.
MIN_GAIN = 0.005


def main() -> None:
    val = [r for r in load_rows() if r["prompt_id"] in VAL_PROMPTS]
    lexicon = CoercionEngine.load().lexicon
    model = TacticModel.load(MODEL_DIR)
    tagger = ModelTagger.from_config(model)
    cached = []
    for r in val:
        norm = normalize(r["text"])
        tactics = {h.category for h in lexicon.match(norm) if h.score > 0}
        cached.append((tagger.scores(r["text"], norm), tactics))

    def score(gate: float | None) -> dict:
        tagger.boost_gate = gate
        probs = np.array(
            [[tagger.boosted(p, lex)[t] for t in TACTICS] for p, lex in cached], dtype=np.float32
        )
        return evaluate(val, probs, model.thresholds)

    def summary(m: dict) -> dict:
        return {
            "macro_f1": m["macro_f1"],
            "stt_noisy_macro_f1": m["by_source"]["stt_noisy"]["macro_f1"],
            "fa_hard_negative": m["false_alarm_rate"]["hard_negative"],
            "fa_chat": m["false_alarm_rate"]["chat"],
        }

    base = summary(score(None))
    table = {"off": base} | {str(g): summary(score(g)) for g in GATES}
    ok = [
        (g, s)
        for g, s in table.items()
        if g != "off"
        and s["macro_f1"] >= base["macro_f1"] + MIN_GAIN
        and s["fa_hard_negative"] <= base["fa_hard_negative"]
        and s["fa_chat"] <= base["fa_chat"]
    ]
    best = max(ok, key=lambda gs: (gs[1]["macro_f1"], float(gs[0])), default=None)
    gate = float(best[0]) if best else None

    meta = json.loads((MODEL_DIR / "model.json").read_text())
    meta["lexicon_boost_gate"] = gate
    meta["lexicon_boost"] = tagger.lexicon_boost
    (MODEL_DIR / "model.json").write_text(json.dumps(meta, indent=2) + "\n")
    metrics = json.loads((MODEL_DIR / "metrics.json").read_text())
    metrics["lexicon_boost_tuning"] = {"boost": tagger.lexicon_boost, "gate": gate, "table": table}
    (MODEL_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")

    print(
        f"boost={tagger.lexicon_boost}  {'gate':>5s} {'macro':>6s} {'sttMac':>6s} {'FA-hn':>6s} {'FA-chat':>7s}"
    )
    for g, s in table.items():
        mark = (
            " <- kept"
            if (gate is not None and g == str(gate)) or (gate is None and g == "off")
            else ""
        )
        print(
            f"{'':11s}{g:>5s} {s['macro_f1']:6.3f} {s['stt_noisy_macro_f1']:6.3f} "
            f"{s['fa_hard_negative']:6.3f} {s['fa_chat']:7.3f}{mark}"
        )


if __name__ == "__main__":
    main()
