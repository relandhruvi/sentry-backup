"""Export a trained run to the deployable classifier in models/coercion/.

1. ONNX export of encoder + pooling + head (dynamic batch and sequence length).
2. Optional dynamic int8 quantization (onnxruntime); `--precision fp32` keeps full precision.
3. Re-score val with the exported model through the same code path the app uses
   (app.coercion.model_tagger.TacticModel), re-tune per-tactic thresholds on those scores,
   and record val metrics and per-utterance CPU latency of the artifact that actually ships.

Writes models/coercion/: model.onnx, tokenizer.json, model.json (labels, thresholds, encoder,
input mode), metrics.json.

Usage: python -m training.coercion.export --run e5-small-raw
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic

from app.coercion.model_tagger import MODEL_DIR, TacticModel
from training.coercion.build_dataset import DATA
from training.coercion.train import (
    RUNS,
    Classifier,
    evaluate,
    load_rows,
    targets,
    tune_thresholds,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_onnx(run: dict, run_dir: Path, out: Path, precision: str) -> None:
    enc = run["encoder"]
    model = Classifier(enc["id"], enc["revision"], len(run["labels"]))
    model.load_state_dict(torch.load(run_dir / "model.pt", map_location="cpu"))
    model.eval()
    ids = torch.ones(1, 16, dtype=torch.long)
    mask = torch.ones(1, 16, dtype=torch.long)
    fp32 = out.with_suffix(".fp32.onnx")
    torch.onnx.export(
        model,
        (ids, mask),
        fp32,
        input_names=["input_ids", "attention_mask"],
        output_names=["logits"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "seq"},
            "attention_mask": {0: "batch", 1: "seq"},
            "logits": {0: "batch"},
        },
        opset_version=17,
        dynamo=False,
    )
    if precision == "int8":
        quantize_dynamic(fp32, out, weight_type=QuantType.QInt8)
        fp32.unlink()
    else:
        fp32.replace(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--precision", choices=["int8", "fp32"], default="int8")
    args = parser.parse_args()

    run_dir = RUNS / args.run
    run = json.loads((run_dir / "run.json").read_text())
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    export_onnx(run, run_dir, MODEL_DIR / "model.onnx", args.precision)
    shutil.copy(run_dir / "tokenizer" / "tokenizer.json", MODEL_DIR / "tokenizer.json")

    meta = {
        "labels": run["labels"],
        "thresholds": run["thresholds"],  # replaced below by thresholds tuned on the int8 model
        "encoder": run["encoder"],
        "input": run["input"],
        "max_len": run["max_len"],
        "run": run["run"],
        "precision": args.precision,
    }
    (MODEL_DIR / "model.json").write_text(json.dumps(meta, indent=2) + "\n")

    rows = load_rows()
    val = [r for r in rows if r["prompt_id"] in set(run["val_prompts"])]
    tm = TacticModel.load(MODEL_DIR, threads=args.threads)
    latencies, probs = [], []
    for r in val:
        t0 = time.perf_counter()
        probs.append(tm.probs(r["text"]))
        latencies.append((time.perf_counter() - t0) * 1000)
    probs = np.array(probs)
    thresholds = tune_thresholds(targets(val), probs)
    metrics = evaluate(val, probs, thresholds)
    lat = sorted(latencies)
    latency = {
        "threads": args.threads,
        "n": len(lat),
        "median_ms": round(statistics.median(lat), 2),
        "p95_ms": round(lat[int(0.95 * (len(lat) - 1))], 2),
        "max_ms": round(lat[-1], 2),
        "note": "single utterance, CPU, includes normalization + tokenization",
    }
    meta["thresholds"] = thresholds
    meta["created"] = datetime.now(UTC).isoformat(timespec="seconds")
    meta["dataset_sha256"] = sha256(DATA / "dataset.jsonl")
    noisy = DATA / "stt_noisy.jsonl"
    meta["stt_noisy_sha256"] = sha256(noisy) if noisy.exists() else None
    meta["model_sha256"] = sha256(MODEL_DIR / "model.onnx")
    (MODEL_DIR / "model.json").write_text(json.dumps(meta, indent=2) + "\n")
    report = {
        "run": run["run"],
        "torch_val_metrics": run["val_metrics"],
        "torch_thresholds": run["thresholds"],
        "precision": args.precision,
        "onnx_val_metrics": metrics,
        "onnx_thresholds": thresholds,
        "latency": latency,
        "rows": run["rows"],
        "best_epoch": run["best_epoch"],
        "history": run["history"],
    }
    (MODEL_DIR / "metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "precision": args.precision,
                "macro_f1": metrics["macro_f1"],
                "stt_noisy_micro_f1": metrics["by_source"]["stt_noisy"]["micro"]["f1"],
                "latency": latency,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
