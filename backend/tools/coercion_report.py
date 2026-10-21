"""Score conversations with the coercion engine and print each call's trajectory.

Sources:
  --fixtures   tests/fixtures/conversations.yaml (clean text, caller turns only)
  --live       the latest live STT run (samples/synthetic_tts/runs/*.jsonl): the real Whisper
               output of the synthetic calls, i.e. text with actual STT errors

  --model      use the trained classifier as the primary signal (models/coercion/); without it
               the engine runs in lexicon_fallback mode

Usage: python -m tools.coercion_report [--fixtures] [--live] [--model] [-v]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import yaml

from app.analysis import CallAnalyzer
from app.coercion.engine import CoercionEngine
from app.coercion.model_tagger import ModelTagger, TacticModel
from app.entities.extract import EntityExtractor
from app.risk.levels import Level
from tools.make_test_audio import DEFAULT_OUT

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "conversations.yaml"
ORDER = [Level.LOW, Level.MEDIUM, Level.HIGH, Level.CRITICAL]


def load_fixtures() -> list[dict]:
    data = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))
    return [
        {
            "id": c["id"],
            "category": c["category"],
            "lang": c["lang"],
            "utterances": [t["text"] for t in c["turns"] if t["speaker"] == "caller"],
        }
        for c in data["conversations"]
    ]


def load_live(run_dir: Path) -> list[dict]:
    runs = sorted(run_dir.glob("*.jsonl"))
    if not runs:
        return []
    calls: dict[str, dict] = {}
    for line in runs[-1].open(encoding="utf-8"):
        e = json.loads(line)
        if e["type"] != "utterance" or e["payload"]["stt_status"] != "ok":
            continue
        name = e["file"].removesuffix(".wav")
        manifest = json.loads((run_dir.parent / f"{name}.json").read_text(encoding="utf-8"))
        call = calls.setdefault(
            name, {"id": name, "category": manifest["category"], "lang": "stt", "utterances": []}
        )
        call["utterances"].append(e["payload"]["text"])
    return list(calls.values())


async def score_call(engine: CoercionEngine, extractor: EntityExtractor, call: dict) -> dict:
    analyzer = CallAnalyzer(engine, extractor)
    trajectory, last, claims = [], None, []
    for i, text in enumerate(call["utterances"]):
        coercion, entities, _ = await analyzer.analyze(f"u{i + 1}", float(i), text)
        trajectory.append(coercion)
        last = coercion
        if entities:
            claims = entities.claims_so_far
    peak = max((c.level for c in trajectory), key=ORDER.index, default=Level.LOW)
    return {**call, "trajectory": trajectory, "final": last, "peak": peak, "claims": claims}


def expected_ok(result: dict) -> bool:
    if result["category"] == "scam":
        return ORDER.index(result["final"].level) >= ORDER.index(Level.HIGH)
    return ORDER.index(result["peak"]) <= ORDER.index(Level.MEDIUM)


def print_result(r: dict, verbose: bool) -> None:
    levels = " ".join(c.level.value[0] for c in r["trajectory"])
    scores = " ".join(f"{c.score:.2f}" for c in r["trajectory"])
    mark = "ok " if expected_ok(r) else "XX "
    f = r["final"]
    print(
        f"{mark}{r['id'][:38]:38s} {r['category'][:6]:6s} {r['lang']:8s} "
        f"final={f.level.value:8s} {f.score:.2f}  [{levels}]  ({scores})"
    )
    if verbose:
        for c in r["trajectory"]:
            for h in c.hits:
                neg = " NEG" if h.negated else ""
                src = "" if h.source == "lexicon" else f" [{h.source}]"
                print(
                    f'      {c.utterance_id:4s} {h.tactic:10s} {h.score:.2f}{neg}{src}  "{h.span[:90]}"'
                )
        if f.caps_applied:
            print(f"      caps: {f.caps_applied}")
        if r["claims"]:
            print(f"      claims: {[(e.kind, e.value) for e in r['claims']]}")


async def main_async(args) -> int:
    model = None
    if args.model:
        model = ModelTagger.from_config(TacticModel.load())
        print(f"classifier: {model.describe()}")
    engine = CoercionEngine.load(model=model)
    extractor = EntityExtractor.load(engine.lexicon.cfg)
    sources = []
    if args.fixtures:
        sources.append(("fixtures (clean text)", load_fixtures()))
    if args.live:
        sources.append(("live STT run (synthetic TTS -> Whisper)", load_live(DEFAULT_OUT / "runs")))
    failures = 0
    for title, calls in sources:
        print(f"\n=== {title}: {len(calls)} calls")
        for call in calls:
            r = await score_call(engine, extractor, call)
            failures += not expected_ok(r)
            print_result(r, args.verbose)
    print(
        f"\n{failures} call(s) off expectation (scam >= HIGH at end, benign <= MEDIUM throughout)"
    )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--fixtures", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--model", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if not (args.fixtures or args.live):
        args.fixtures = args.live = True
    raise SystemExit(1 if asyncio.run(main_async(args)) else 0)


if __name__ == "__main__":
    main()
