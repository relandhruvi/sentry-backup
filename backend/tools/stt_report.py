"""B2 acceptance run: stream every synthetic test WAV through the live server and score it.

Requires a running server (`make run`). Each file goes through the real path:
/ws/audio -> ring buffer -> VAD -> STT worker -> utterance events on /ws/events.
Prints every utterance, then WER/CER per language against the script text and STT latency.

Usage: python -m tools.stt_report [--url ...] [--dir ../samples/synthetic_tts] [--speed 1.0]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import numpy as np

from tools.make_test_audio import DEFAULT_OUT
from tools.stream_wav import format_event, stream_file
from tools.wer import Report, format_table, score


async def run(files: list[Path], url: str, speed: float) -> None:
    run_dir = files[0].parent / "runs"
    run_dir.mkdir(exist_ok=True)
    run_file = run_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    total = Report()
    latencies, inference = [], []
    statuses: dict[str, int] = {}
    for path in files:
        manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        print(f"\n### {path.name}  ({manifest['duration_s']:.1f}s, {manifest['category']})")
        _, events = await stream_file(path, url, speed)
        with run_file.open("a", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps({"file": path.name, **e}, ensure_ascii=False) + "\n")
        utts = [e["payload"] for e in events if e["type"] == "utterance"]
        for e in events:
            if e["type"] == "utterance":
                print(format_event(e))
        for u in utts:
            statuses[u["stt_status"]] = statuses.get(u["stt_status"], 0) + 1
        ok = [u for u in utts if u["stt_status"] == "ok"]
        latencies += [u["stt_latency_ms"] for u in ok]
        inference += [u["inference_ms"] for u in ok]
        total.extend(score(manifest, [(u["t_start_s"], u["t_end_s"], u["text"]) for u in ok]))

    print(f"\nevents saved to {run_file}")
    print("\n=== WER/CER vs script text (synthetic TTS audio) ===")
    print(format_table(total.by_lang()))
    print(f"utterances not overlapping any script turn: {total.unmatched_utterances}")
    print(f"stt_status counts: {statuses}")
    if latencies:
        lat, inf = np.array(latencies), np.array(inference)
        print(
            f"stt_latency_ms (utterance end -> transcript): median {np.median(lat):.0f}, "
            f"p95 {np.percentile(lat, 95):.0f}, max {lat.max():.0f}"
        )
        print(f"inference_ms: median {np.median(inf):.0f}, p95 {np.percentile(inf, 95):.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()
    files = sorted(args.dir.glob("synthetic_*.wav"))
    if not files:
        raise SystemExit(f"no synthetic_*.wav in {args.dir}; run `make test-audio` first")
    asyncio.run(run(files, args.url, args.speed))


if __name__ == "__main__":
    main()
