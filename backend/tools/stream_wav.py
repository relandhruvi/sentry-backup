"""Stream an audio file to a running Scam Shield server in real time and print events live.

The file is decoded in memory to 16 kHz mono int16 (ffmpeg for anything that isn't already that),
sent as 20 ms binary frames to /ws/audio/{id}, and every event from /ws/events/{id} is printed.

Usage: python -m tools.stream_wav FILE [--url http://127.0.0.1:8000] [--speed 1.0] [--jsonl OUT]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
import wave
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np
import websockets

from app.audio.buffer import FRAME_BYTES, SAMPLE_RATE

FRAME_S = 0.020


def load_pcm16k(path: Path) -> bytes:
    try:
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (SAMPLE_RATE, 1, 2):
                return wav.readframes(wav.getnframes())
    except wave.Error:
        pass
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        sys.exit("error: ffmpeg is needed to convert this file (brew install ffmpeg)")
    return subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout


def format_event(event: dict) -> str:
    p = event["payload"]
    kind = event["type"]
    if kind == "utterance":
        span = f"[{p['t_start_s']:6.1f}–{p['t_end_s']:6.1f}s]"
        if p["stt_status"] != "ok":
            return f"{span} ({p['stt_status']}: {p.get('detail') or 'no transcript'})"
        probs = " ".join(f"{k}={v:.2f}" for k, v in p.get("lang_probs", {}).items())
        return (
            f"{span} {p['lang']:5s} lat={p['stt_latency_ms']:6.0f}ms "
            f"inf={p['inference_ms']:6.0f}ms ({probs})  {p['text']}"
        )
    if kind == "coercion":
        hits = ", ".join(
            f'{h["tactic"]}{"(neg)" if h["negated"] else ""} "{h["span"]}"' for h in p["hits"]
        )
        caps = f" caps={p['caps_applied']}" if p["caps_applied"] else ""
        return f"      ↳ coercion {p['level']:8s} {p['score']:.2f}{caps}  {hits}"
    if kind == "entities":
        return "      ↳ entities " + ", ".join(f"{e['kind']}={e['value']}" for e in p["entities"])
    if kind == "status":
        detail = f" ({p['detail']})" if p.get("detail") else ""
        return f"  · {p['component']}: {p['status']}{detail}"
    return f"  {kind}: {json.dumps(p, ensure_ascii=False)}"


async def stream_file(
    path: Path,
    url: str,
    speed: float = 1.0,
    on_event: Callable[[dict], None] | None = None,
) -> tuple[str, list[dict]]:
    pcm = load_pcm16k(path)
    pcm = pcm[: len(pcm) - len(pcm) % FRAME_BYTES]
    ws_url = url.replace("http", "ws", 1)
    events: list[dict] = []

    async with httpx.AsyncClient(base_url=url, timeout=10) as http:
        session_id = (await http.post("/sessions")).raise_for_status().json()["session_id"]

        async def consume() -> None:
            async with websockets.connect(f"{ws_url}/ws/events/{session_id}") as ws:
                async for message in ws:
                    event = json.loads(message)
                    events.append(event)
                    if on_event:
                        on_event(event)

        consumer = asyncio.create_task(consume())
        async with websockets.connect(f"{ws_url}/ws/audio/{session_id}") as ws:
            t0 = time.monotonic()
            for i in range(0, len(pcm), FRAME_BYTES):
                delay = t0 + (i // FRAME_BYTES) * FRAME_S / speed - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                await ws.send(pcm[i : i + FRAME_BYTES])
        # Ending drains in-flight transcriptions before the server closes the event stream.
        await http.post(f"/sessions/{session_id}/end", timeout=120)
        await asyncio.wait_for(consumer, timeout=30)
    return session_id, events


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("file", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--speed", type=float, default=1.0, help="1.0 = real time")
    parser.add_argument("--jsonl", type=Path, help="also write every event to this file")
    args = parser.parse_args()

    seconds = len(load_pcm16k(args.file)) / 2 / SAMPLE_RATE
    print(f"streaming {args.file.name} ({seconds:.1f}s audio) to {args.url} at {args.speed}x")
    session_id, events = asyncio.run(
        stream_file(args.file, args.url, args.speed, on_event=lambda e: print(format_event(e)))
    )
    if args.jsonl:
        args.jsonl.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))

    utts = [e["payload"] for e in events if e["type"] == "utterance"]
    lat = [u["stt_latency_ms"] for u in utts if u["stt_status"] == "ok"]
    if lat:
        print(
            f"\nsession {session_id}: {len(utts)} utterances, stt_latency_ms "
            f"median={np.median(lat):.0f} p95={np.percentile(lat, 95):.0f} max={max(lat):.0f}"
        )


if __name__ == "__main__":
    main()
