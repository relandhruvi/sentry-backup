"""Fine-tune a multilingual encoder as a multi-label coercion-tactic classifier.

Data: data/dataset.jsonl (generated) + data/stt_noisy.jsonl (TTS -> Whisper copies, if present).
Split: by prompt id (VAL_PROMPTS); a noisy row follows its source row's prompt id, so no prompt's
text appears on both sides. Input is the caller utterance alone (the live pipeline only hears the
caller), either the original text (`--input raw`) or `normalize(text).text` (`--input norm`).

Model: encoder -> mean pooling -> dropout -> linear, one sigmoid per tactic. BENIGN is not an output;
an utterance is benign when no tactic clears its threshold. The best epoch is picked by val macro-F1
at 0.5, then per-tactic thresholds are tuned on val (max F1). Because both are chosen on val, val
numbers are optimistic; the held-out human set is the real test.

Writes models/coercion/runs/<run>/: model.pt, tokenizer/, run.json (config, thresholds, metrics).

Usage: python -m training.coercion.train --encoder e5-small --input raw
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from transformers import AutoModel, AutoTokenizer

from training.coercion.build_dataset import DATA, TACTICS

BACKEND_DIR = Path(__file__).resolve().parents[2]
RUNS = BACKEND_DIR / "models" / "coercion" / "runs"

# Verified on huggingface.co (model card license + pinned commit).
ENCODERS = {
    "e5-small": {
        "id": "intfloat/multilingual-e5-small",
        "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "license": "mit",
        "prefix": "query: ",
        "lr": 5e-5,
    },
    "minilm-l12": {
        "id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        "license": "apache-2.0",
        "prefix": "",
        "lr": 5e-5,
    },
    "muril-base": {
        "id": "google/muril-base-cased",
        "revision": "afd9f36c7923d54e97903922ff1b260d091d202f",
        "license": "apache-2.0",
        "prefix": "",
        # 3e-5 with a 1e-3 head collapsed to "no tactic" (loss flat at 0.30 for 4 epochs).
        "lr": 2e-5,
        "head_lr": 5e-4,
    },
}
# One round of batches (one prompt per language) every five rounds -> ~18% of prompts.
VAL_PROMPTS = {"p07", "p08", "p09", "p22", "p23", "p24"}
MAX_LEN = 64
HEAD_LR = 1e-3
GRID = np.round(np.arange(0.10, 0.91, 0.025), 3)


class Classifier(nn.Module):
    def __init__(self, encoder_id: str, revision: str | None, n_labels: int) -> None:
        super().__init__()
        self.encoder = AutoModel.from_pretrained(encoder_id, revision=revision)
        self.dropout = nn.Dropout(0.1)
        self.head = nn.Linear(self.encoder.config.hidden_size, n_labels)

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        hidden = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        mask = attention_mask.unsqueeze(-1).to(hidden.dtype)
        pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-6)
        return self.head(self.dropout(pooled))


def load_rows(with_noisy: bool = True) -> list[dict]:
    rows = [json.loads(line) for line in (DATA / "dataset.jsonl").open(encoding="utf-8")]
    noisy = DATA / "stt_noisy.jsonl"
    if with_noisy and noisy.exists():
        rows += [
            r
            for r in (json.loads(line) for line in noisy.open(encoding="utf-8"))
            if r["text"].strip()
        ]
    return rows


def model_input(row: dict, mode: str, prefix: str) -> str:
    return prefix + (row["text_norm"] if mode == "norm" else row["text"])


def targets(rows: list[dict]) -> np.ndarray:
    return np.array([[t in r["labels"] for t in TACTICS] for r in rows], dtype=np.float32)


def prf(y: np.ndarray, p: np.ndarray) -> dict:
    tp = float((y & p).sum())
    fp = float((~y & p).sum())
    fn = float((y & ~p).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "support": int(y.sum()),
    }


def tune_thresholds(y: np.ndarray, probs: np.ndarray) -> dict[str, float]:
    out = {}
    for k, tactic in enumerate(TACTICS):
        best = max(GRID, key=lambda th: (prf(y[:, k] > 0, probs[:, k] >= th)["f1"], th))
        out[tactic] = float(best)
    return out


def evaluate(rows: list[dict], probs: np.ndarray, thresholds: dict[str, float]) -> dict:
    y = targets(rows) > 0
    th = np.array([thresholds[t] for t in TACTICS])
    pred = probs >= th
    per = {t: prf(y[:, k], pred[:, k]) for k, t in enumerate(TACTICS)}
    benign_y, benign_p = ~y.any(1), ~pred.any(1)

    def subset(mask: np.ndarray) -> dict:
        return {
            "rows": int(mask.sum()),
            "macro_f1": round(
                float(
                    np.mean(
                        [prf(y[mask][:, k], pred[mask][:, k])["f1"] for k in range(len(TACTICS))]
                    )
                ),
                4,
            ),
            "micro": prf(y[mask].ravel(), pred[mask].ravel()),
            "any_tactic": prf(y[mask].any(1), pred[mask].any(1)),
        }

    langs = sorted({r["lang"] for r in rows})
    sources = sorted({r["source"] for r in rows})
    cats = np.array([r["category"] for r in rows])
    return {
        "per_tactic": per,
        "macro_f1": round(float(np.mean([m["f1"] for m in per.values()])), 4),
        "micro": prf(y.ravel(), pred.ravel()),
        "benign_derived": prf(benign_y, benign_p),
        "exact_match": round(float((y == pred).all(1).mean()), 4),
        "by_lang": {lang: subset(np.array([r["lang"] == lang for r in rows])) for lang in langs},
        "by_source": {s: subset(np.array([r["source"] == s for r in rows])) for s in sources},
        "false_alarm_rate": {
            c: round(float(pred[cats == c].any(1).mean()), 4)
            for c in ("hard_negative", "chat")
            if (cats == c).any()
        },
    }


@torch.no_grad()
def predict(model, tok, texts: list[str], device: str, batch: int = 64) -> np.ndarray:
    model.eval()
    out = []
    for i in range(0, len(texts), batch):
        enc = tok(
            texts[i : i + batch],
            padding=True,
            truncation=True,
            max_length=MAX_LEN,
            return_tensors="pt",
        ).to(device)
        out.append(torch.sigmoid(model(enc["input_ids"], enc["attention_mask"])).float().cpu())
    return torch.cat(out).numpy()


def rescore(run_name: str) -> dict:
    """Recompute val metrics of a finished run with the current `evaluate` (same thresholds)."""
    run_dir = RUNS / run_name
    run = json.loads((run_dir / "run.json").read_text())
    rows = [r for r in load_rows() if r["prompt_id"] in set(run["val_prompts"])]
    probs_path = run_dir / "val_probs.npy"
    if probs_path.exists():
        probs = np.load(probs_path)
    else:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        enc = run["encoder"]
        tok = AutoTokenizer.from_pretrained(run_dir / "tokenizer")
        model = Classifier(enc["id"], enc["revision"], len(TACTICS)).to(device)
        model.load_state_dict(torch.load(run_dir / "model.pt", map_location=device))
        probs = predict(
            model, tok, [model_input(r, run["input"], enc["prefix"]) for r in rows], device
        )
        np.save(probs_path, probs)
    run["val_metrics"] = evaluate(rows, probs, run["thresholds"])
    (run_dir / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n")
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--encoder", choices=sorted(ENCODERS), required=True)
    parser.add_argument("--input", choices=["raw", "norm"], default="raw")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument(
        "--clean-only", action="store_true", help="train without the STT-noised rows"
    )
    parser.add_argument("--threads", type=int, default=0, help="torch CPU threads (0 = default)")
    args = parser.parse_args()

    enc_cfg = ENCODERS[args.encoder]
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    if args.threads:
        torch.set_num_threads(args.threads)
    # Val always includes the noisy rows, so clean-only and clean+noisy runs are comparable.
    rows = load_rows()
    if args.clean_only:
        rows = [r for r in rows if r["source"] == "generated" or r["prompt_id"] in VAL_PROMPTS]
    train = [r for r in rows if r["prompt_id"] not in VAL_PROMPTS]
    val = [r for r in rows if r["prompt_id"] in VAL_PROMPTS]
    print(f"train={len(train)} val={len(val)} device={device}", flush=True)

    tok = AutoTokenizer.from_pretrained(enc_cfg["id"], revision=enc_cfg["revision"])
    model = Classifier(enc_cfg["id"], enc_cfg["revision"], len(TACTICS)).to(device)
    x_train = [model_input(r, args.input, enc_cfg["prefix"]) for r in train]
    x_val = [model_input(r, args.input, enc_cfg["prefix"]) for r in val]
    y_train = torch.tensor(targets(train))
    y_val = targets(val)

    steps = args.epochs * ((len(train) + args.batch - 1) // args.batch)
    # The freshly initialised head needs a much larger step than the pretrained encoder.
    opt = torch.optim.AdamW(
        [
            {"params": model.encoder.parameters(), "lr": enc_cfg["lr"]},
            {"params": model.head.parameters(), "lr": enc_cfg.get("head_lr", HEAD_LR)},
        ],
        weight_decay=0.01,
    )
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt,
        lambda s: (
            min(1.0, s / max(1, int(0.1 * steps)))
            * max(0.0, (steps - s) / max(1, steps - int(0.1 * steps)))
        ),
    )
    loss_fn = nn.BCEWithLogitsLoss()

    run_name = f"{args.encoder}-{args.input}" + ("-clean" if args.clean_only else "")
    run_dir = RUNS / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    best, history = None, []
    t0 = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        model.train()
        order = np.random.permutation(len(train))
        total = 0.0
        for i in range(0, len(order), args.batch):
            idx = order[i : i + args.batch]
            enc = tok(
                [x_train[j] for j in idx],
                padding=True,
                truncation=True,
                max_length=MAX_LEN,
                return_tensors="pt",
            ).to(device)
            logits = model(enc["input_ids"], enc["attention_mask"])
            loss = loss_fn(logits, y_train[idx].to(device))
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            total += loss.item() * len(idx)
        probs = predict(model, tok, x_val, device)
        default = evaluate(val, probs, dict.fromkeys(TACTICS, 0.5))
        history.append(
            {
                "epoch": epoch,
                "train_loss": round(total / len(train), 4),
                "val_macro_f1_at_0.5": default["macro_f1"],
            }
        )
        print(f"epoch {epoch}: {history[-1]}  {time.perf_counter() - t0:.0f}s", flush=True)
        if best is None or default["macro_f1"] > best["macro_f1"]:
            best = {"epoch": epoch, "macro_f1": default["macro_f1"], "probs": probs}
            torch.save(model.state_dict(), run_dir / "model.pt")

    np.save(run_dir / "val_probs.npy", best["probs"])
    thresholds = tune_thresholds(y_val, best["probs"])
    metrics = evaluate(val, best["probs"], thresholds)
    tok.save_pretrained(run_dir / "tokenizer")
    run = {
        "run": run_name,
        "encoder": {k: enc_cfg[k] for k in ("id", "revision", "license", "prefix")},
        "input": args.input,
        "max_len": MAX_LEN,
        "labels": TACTICS,
        "val_prompts": sorted(VAL_PROMPTS),
        "rows": {
            "train": len(train),
            "val": len(val),
            "train_stt_noisy": sum(r["source"] == "stt_noisy" for r in train),
            "val_stt_noisy": sum(r["source"] == "stt_noisy" for r in val),
        },
        "hyper": {
            "head_lr": enc_cfg.get("head_lr", HEAD_LR),
            "epochs": args.epochs,
            "batch": args.batch,
            "lr": enc_cfg["lr"],
            "seed": args.seed,
        },
        "best_epoch": best["epoch"],
        "history": history,
        "thresholds": thresholds,
        "val_metrics": metrics,
        "train_seconds": round(time.perf_counter() - t0),
    }
    (run_dir / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"best_epoch": best["epoch"], "thresholds": thresholds, **metrics}, indent=2))


if __name__ == "__main__":
    main()
