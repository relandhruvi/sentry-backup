"""Validate, tag and dedupe the generated batches into training/coercion/data/dataset.jsonl.

- Validates every row: known labels, BENIGN never combined, language/script rules (hi = Devanagari
  only, hinglish = Latin only), 0-3 context lines. Invalid rows are dropped and counted.
- Tags each row with id, prompt_id, prompt_hash, generator and source=generated.
- Drops near-duplicates: normalized-text similarity > DEDUPE_THRESHOLD (rapidfuzz ratio on
  `normalize_key`), keeping the first by prompt order. Label disagreements among dropped pairs
  are counted.
- Writes dataset.jsonl and counts.json, and prints counts by label, language and category.

Usage: python -m training.coercion.build_dataset
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz
from rapidfuzz.process import cdist

from app.text.normalize import normalize, normalize_key
from training.coercion.make_prompts import PROMPTS, prompt_hash

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
DATA = HERE / "data"
GENERATOR = "claude-opus-5-5 (Claude Code general-purpose subagent, isolated)"

TACTICS = ["AUTHORITY", "THREAT", "URGENCY", "SECRECY", "ISOLATION", "PAYMENT", "CREDENTIAL"]
LABELS = [*TACTICS, "BENIGN"]
LANGS = {"en", "hi", "hinglish"}
CATEGORIES = {"scam", "hard_negative", "chat"}
DEDUPE_THRESHOLD = 0.75

_LATIN = re.compile(r"[A-Za-z]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def problem(row: dict) -> str | None:
    labels = row.get("labels")
    if not isinstance(labels, list) or not labels or set(labels) - set(LABELS):
        return "bad_labels"
    if len(set(labels)) != len(labels):
        return "bad_labels"
    if "BENIGN" in labels and len(labels) > 1:
        return "benign_combined"
    if row.get("lang") not in LANGS or row.get("category") not in CATEGORIES:
        return "bad_lang_or_category"
    if row["category"] != "scam" and labels != ["BENIGN"]:
        return "negative_with_tactic"
    text = row.get("text")
    if not isinstance(text, str) or not text.strip():
        return "empty_text"
    context = row.get("context", [])
    if not isinstance(context, list) or len(context) > 3:
        return "bad_context"
    lines = [text, *context]
    if row["lang"] == "hi" and any(
        _LATIN.search(re.sub(r"^(caller|elder): ", "", s)) for s in lines
    ):
        return "latin_in_hi"
    if row["lang"] != "hi" and any(_DEVANAGARI.search(s) for s in lines):
        return "devanagari_in_" + row["lang"]
    return None


def load_generated() -> tuple[list[dict], Counter]:
    rows, dropped = [], Counter()
    for path in sorted(GENERATED.glob("p*.jsonl")):
        pid = path.stem
        phash = prompt_hash((PROMPTS / f"{pid}.txt").read_text(encoding="utf-8"))
        for i, line in enumerate(path.open(encoding="utf-8")):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                dropped["invalid_json"] += 1
                continue
            why = problem(row)
            if why:
                dropped[why] += 1
                continue
            rows.append(
                {
                    "id": f"{pid}-{i:03d}",
                    "prompt_id": pid,
                    "prompt_hash": phash,
                    "generator": GENERATOR,
                    "source": "generated",
                    "lang": row["lang"],
                    "category": row["category"],
                    "scenario": str(row.get("scenario", "")),
                    "context": row.get("context", []),
                    "text": row["text"].strip(),
                    "text_norm": normalize(row["text"]).text,
                    "labels": sorted(row["labels"], key=LABELS.index),
                    "spoken_noise": bool(row.get("spoken_noise", False)),
                }
            )
    return rows, dropped


def dedupe(rows: list[dict]) -> tuple[list[dict], dict]:
    keys = [normalize_key(r["text"]) for r in rows]
    sims = cdist(keys, keys, scorer=fuzz.ratio, dtype=np.uint8, workers=-1)
    keep, removed, conflicts = [], 0, 0
    kept_idx: list[int] = []
    for i in range(len(rows)):
        dup = next((j for j in kept_idx if sims[i, j] > DEDUPE_THRESHOLD * 100), None)
        if dup is None:
            kept_idx.append(i)
            keep.append(rows[i])
        else:
            removed += 1
            conflicts += rows[i]["labels"] != rows[dup]["labels"]
    return keep, {"removed": removed, "label_conflicts_among_removed": conflicts}


def counts(rows: list[dict]) -> dict:
    by_label = Counter(lab for r in rows for lab in r["labels"])
    return {
        "rows": len(rows),
        "by_lang": dict(Counter(r["lang"] for r in rows)),
        "by_category": dict(Counter(r["category"] for r in rows)),
        "by_label": {lab: by_label.get(lab, 0) for lab in LABELS},
        "by_label_lang": {
            lab: dict(Counter(r["lang"] for r in rows if lab in r["labels"])) for lab in LABELS
        },
        "multi_label_rows": sum(len(r["labels"]) > 1 for r in rows),
        "spoken_noise_rows": sum(r["spoken_noise"] for r in rows),
        "prompts": len({r["prompt_id"] for r in rows}),
    }


def main() -> None:
    rows, dropped = load_generated()
    deduped, dd = dedupe(rows)
    DATA.mkdir(exist_ok=True)
    with (DATA / "dataset.jsonl").open("w", encoding="utf-8") as f:
        for r in deduped:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report = {
        "generated_valid": len(rows),
        "dropped_invalid": dict(dropped),
        "dedupe": {"threshold": DEDUPE_THRESHOLD, **dd},
        "final": counts(deduped),
    }
    (DATA / "counts.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
