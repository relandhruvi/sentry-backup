"""STT-noised copy of the training text: TTS -> our real Whisper STT -> normalize.

A stratified sample of the generated utterances is spoken with macOS `say`, transcribed with the
same FasterWhisperProvider and settings the live pipeline uses, and written as extra rows with
`source=stt_noisy`. Each noisy row keeps the labels and prompt id of the row it came from, so the
train/val split by prompt id still holds. Audio exists only in a temporary directory while a row
is rendered and is never kept.

Resumable: rows already in the output file are skipped.

Usage: python -m training.coercion.make_noisy [--n 1000] [--seed 13]
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from app.audio.stt import FasterWhisperProvider
from app.config import get_settings
from app.text.normalize import normalize
from tools.make_test_audio import installed_voices, render_turn, require, resolve_voice

HERE = Path(__file__).resolve().parent
DATASET = HERE / "data" / "dataset.jsonl"
OUT = HERE / "data" / "stt_noisy.jsonl"
VOICES = {"hi": ["Lekha"], "en": ["Rishi", "Aman", "Tara"], "hinglish": ["Rishi", "Aman", "Tara"]}
RATES = [165, 180, 195]


def stratified_sample(rows: list[dict], n: int, seed: int) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["lang"], r["category"])].append(r)
    rng = random.Random(seed)
    picked = []
    for key in sorted(groups):
        g = groups[key]
        rng.shuffle(g)
        picked += g[: round(n * len(g) / len(rows))]
    return sorted(picked, key=lambda r: r["id"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=13)
    args = parser.parse_args()

    say = require("say", "This tool needs macOS.")
    ffmpeg = require("ffmpeg", "Install it with: brew install ffmpeg")
    installed = installed_voices(say)
    voices = {
        lang: [v for v in (resolve_voice(n, installed) for n in names) if v]
        for lang, names in VOICES.items()
    }
    missing = [lang for lang, v in voices.items() if not v]
    if missing:
        raise SystemExit(f"no installed voice for {missing}; install voices in System Settings")

    rows = [json.loads(line) for line in DATASET.open(encoding="utf-8")]
    sample = stratified_sample(rows, args.n, args.seed)
    done = set()
    if OUT.exists():
        done = {json.loads(line)["noisy_of"] for line in OUT.open(encoding="utf-8")}
    todo = [r for r in sample if r["id"] not in done]
    print(f"sample={len(sample)} done={len(done)} todo={len(todo)}", flush=True)

    settings = get_settings()
    stt = FasterWhisperProvider.from_settings(settings)
    rng = random.Random(args.seed)
    t0 = time.perf_counter()
    with OUT.open("a", encoding="utf-8") as out, tempfile.TemporaryDirectory() as tmp:
        for i, r in enumerate(todo, 1):
            voice = rng.choice(voices[r["lang"]])
            rate = rng.choice(RATES)
            pcm = render_turn(say, ffmpeg, voice, rate, r["text"], Path(tmp))
            tr = stt.transcribe_blocking(pcm)
            text = (tr.text or "").strip()
            row = {
                **{k: r[k] for k in ("prompt_id", "lang", "category", "scenario", "labels")},
                "id": f"{r['id']}~stt",
                "noisy_of": r["id"],
                "source": "stt_noisy",
                "generator": r["generator"],
                "prompt_hash": r["prompt_hash"],
                "context": r["context"],
                "text": text,
                "text_norm": normalize(text).text,
                "spoken_noise": r["spoken_noise"],
                "tts_voice": voice,
                "tts_rate": rate,
                "stt_model": settings.stt_model,
                "stt_language": tr.detected_language,
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            if i % 25 == 0 or i == len(todo):
                rate_s = (time.perf_counter() - t0) / i
                print(
                    f"{i}/{len(todo)}  {rate_s:.1f}s/row  eta {rate_s * (len(todo) - i) / 60:.0f} min",
                    flush=True,
                )


if __name__ == "__main__":
    main()
