"""Compare trained runs on val and print the best run name.

Best = highest micro-F1 on the STT-noised val rows (live input is always Whisper output), with
overall macro-F1 as the tie-breaker. Clean-only runs are listed but never picked.

Usage: python -m training.coercion.compare [--best]
"""

from __future__ import annotations

import argparse
import json

from training.coercion.build_dataset import TACTICS
from training.coercion.train import RUNS


def load_runs() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(RUNS.glob("*/run.json"))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--best", action="store_true", help="print only the best run name")
    args = parser.parse_args()
    runs = load_runs()
    if not runs:
        raise SystemExit("no runs in models/coercion/runs; train first")
    best = max(
        (r for r in runs if not r["run"].endswith("-clean")),
        key=lambda r: (
            round(r["val_metrics"]["by_source"]["stt_noisy"]["micro"]["f1"], 2),
            r["val_metrics"]["macro_f1"],
        ),
    )
    if args.best:
        print(best["run"])
        return
    head = f"{'run':18s} {'macroF1':>7s} {'microF1':>7s} " + " ".join(
        f"{t[:5]:>6s}" for t in TACTICS
    )
    print(head + f" {'FA-hn':>6s} {'FA-chat':>7s} {'sttF1':>6s}  epoch")
    for r in runs:
        m = r["val_metrics"]
        per = " ".join(f"{m['per_tactic'][t]['f1']:6.3f}" for t in TACTICS)
        fa = m["false_alarm_rate"]
        print(
            f"{r['run']:18s} {m['macro_f1']:7.3f} {m['micro']['f1']:7.3f} {per} "
            f"{fa.get('hard_negative', 0):6.3f} {fa.get('chat', 0):7.3f} "
            f"{m['by_source']['stt_noisy']['micro']['f1']:6.3f}  {r['best_epoch']}"
        )
    print(f"\nbest: {best['run']}")


if __name__ == "__main__":
    main()
