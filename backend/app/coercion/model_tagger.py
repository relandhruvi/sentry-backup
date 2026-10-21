"""Trained coercion-tactic classifier (ONNX, int8) as a `CoercionTagger` next to the lexicon.

The model is built by `make train-coercion` into models/coercion/ (git-ignored):
model.onnx, tokenizer.json and model.json (labels, per-tactic thresholds, lexicon boost gate, input
mode). It reads one caller utterance and outputs a probability per tactic. It is the primary
coercion signal: every tactic that clears its tuned threshold becomes a hit with `source="model"`
and score = weight × probability (weight from app/risk/weights.yaml). The lexicon only supplies
evidence spans, a small gated boost, and the fallback when the model can't run. Inference runs in
a dedicated single worker thread; the engine applies the timeout.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.coercion.lexicon import Hit
from app.config import BACKEND_DIR
from app.risk.levels import load_weights
from app.text.normalize import NormalizedText, normalize

MODEL_DIR = BACKEND_DIR / "models" / "coercion"
REQUIRED_FILES = ("model.onnx", "tokenizer.json", "model.json")


class ModelUnavailable(Exception):
    pass


@dataclass
class TacticModel:
    """Tokenizer + ONNX session + metadata. Synchronous; callers run it off the event loop."""

    session: object
    tokenizer: object
    labels: list[str]
    thresholds: dict[str, float]
    boost_gate: float | None  # lexicon boost gate tuned on val (None = boost off)
    input_mode: str  # "raw" = original text, "norm" = normalize(text).text
    prefix: str
    run: str

    @classmethod
    def load(cls, model_dir: Path = MODEL_DIR, threads: int = 1) -> TacticModel:
        missing = [f for f in REQUIRED_FILES if not (model_dir / f).exists()]
        if missing:
            raise ModelUnavailable(f"missing {', '.join(missing)} in {model_dir}")
        import onnxruntime as ort
        from tokenizers import Tokenizer

        meta = json.loads((model_dir / "model.json").read_text(encoding="utf-8"))
        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=int(meta["max_len"]))
        tokenizer.no_padding()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(model_dir / "model.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        return cls(
            session=session,
            tokenizer=tokenizer,
            labels=list(meta["labels"]),
            thresholds={k: float(v) for k, v in meta["thresholds"].items()},
            boost_gate=meta.get("lexicon_boost_gate"),
            input_mode=meta["input"],
            prefix=meta["encoder"].get("prefix", ""),
            run=meta.get("run", "?"),
        )

    def probs(self, text: str, normalized: NormalizedText | None = None) -> list[float]:
        if self.input_mode == "norm":
            text = (normalized or normalize(text)).text
        enc = self.tokenizer.encode(self.prefix + text)
        ids = np.array([enc.ids], dtype=np.int64)
        mask = np.array([enc.attention_mask], dtype=np.int64)
        (logits,) = self.session.run(["logits"], {"input_ids": ids, "attention_mask": mask})
        return (1.0 / (1.0 + np.exp(-logits[0]))).tolist()


class ModelTagger:
    """The primary coercion signal: per-tactic probabilities, fused with lexicon evidence.

    Fusion rule (per utterance and tactic):
      - the classifier decides: a tactic counts only if its probability clears the tuned threshold;
      - the lexicon may add a small boost (`lexicon_boost`, weights.yaml) to a tactic, and only when
        the classifier already scores it at or above `lexicon_boost_gate` (tuned on val, stored in
        model.json; null = boost off);
      - the lexicon supplies the evidence span shown to people when it matched the same tactic.
    """

    source = "model"

    def __init__(
        self,
        model: TacticModel,
        weight: float,
        lexicon_boost: float = 0.0,
        boost_gate: float | None = None,
    ) -> None:
        self.model = model
        self.weight = weight
        self.lexicon_boost = lexicon_boost
        self.boost_gate = boost_gate
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coercion-model")

    @classmethod
    def from_config(cls, model: TacticModel) -> ModelTagger:
        """Weight and boost size from weights.yaml; the boost gate comes with the model."""
        cfg = load_weights()["model_tagger"]
        return cls(model, float(cfg["weight"]), float(cfg["lexicon_boost"]), model.boost_gate)

    def describe(self) -> str:
        gate = "off" if self.boost_gate is None else f"gate {self.boost_gate:.2f}"
        return f"{self.model.run}, {len(self.model.labels)} tactics, lexicon boost {gate}"

    def scores(self, text: str, normalized: NormalizedText | None = None) -> dict[str, float]:
        probs = self.model.probs(text, normalized)
        return dict(zip(self.model.labels, probs, strict=True))

    async def ascores(self, text: str, normalized: NormalizedText) -> dict[str, float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.scores, text, normalized)

    def boosted(self, probs: dict[str, float], lexicon_tactics: set[str]) -> dict[str, float]:
        """Probabilities after the gated lexicon boost."""
        if self.boost_gate is None or self.lexicon_boost <= 0:
            return dict(probs)
        return {
            t: min(1.0, p + self.lexicon_boost)
            if t in lexicon_tactics and p >= self.boost_gate
            else p
            for t, p in probs.items()
        }

    def fuse(
        self,
        probs: dict[str, float],
        lexicon_hits: list[Hit],
        text: str,
        normalized: NormalizedText,
    ) -> list[Hit]:
        """Hits that count toward the call score: one per tactic the classifier detects."""
        evidence: dict[str, Hit] = {}
        for h in lexicon_hits:
            if h.score > 0 and (h.category not in evidence or h.score > evidence[h.category].score):
                evidence[h.category] = h
        final = self.boosted(probs, set(evidence))
        last = max(0, len(normalized.tokens) - 1)
        hits = []
        for tactic, p in final.items():
            threshold = self.model.thresholds[tactic]
            if p < threshold:
                continue
            lex = evidence.get(tactic)
            boost = " + lexicon boost" if p > probs[tactic] else ""
            hits.append(
                Hit(
                    category=tactic,
                    pattern_id=f"model:{tactic}",
                    pattern=f"classifier p={probs[tactic]:.2f}{boost} (threshold {threshold:.2f})",
                    weight=self.weight,
                    confidence=round(p, 4),
                    first_token=lex.first_token if lex else 0,
                    last_token=lex.last_token if lex else last,
                    span=lex.span if lex else text,
                    source=self.source,
                )
            )
        return hits

    def hits(self, text: str, normalized: NormalizedText | None = None) -> list[Hit]:
        """Classifier-only hits (no lexicon evidence); for tools and warm-up."""
        normalized = normalized or normalize(text)
        return self.fuse(self.scores(text, normalized), [], text, normalized)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
