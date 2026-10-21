"""CoercionEngine: per-utterance tactic evidence + call-level sequence scoring.

Evidence per utterance (`mode` on every result):
  - `model`: the trained classifier (app/coercion/model_tagger.py) is the primary signal. Only its
    detections count as tactic evidence. Lexicon tactic hits are still reported (`counted=false`)
    for evidence spans and explanations, and may add a small boost to a tactic the classifier
    already scores above the tuned gate. Lexicon protective cues count in both modes.
  - `lexicon_fallback`: the classifier is missing, disabled, timed out or failed for this
    utterance, so lexicon hits (including protective cues) count instead.
An optional LLM tagger (`CoercionTagger`, off until the LLM milestone) adds counted hits.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from pydantic import BaseModel

from app.coercion.lexicon import PROTECTIVE, Hit, Lexicon, MatchConfig
from app.coercion.model_tagger import ModelTagger
from app.coercion.sequence import (
    ChainStep,
    CoercionConfig,
    Contribution,
    SequenceTracker,
)
from app.risk.levels import Level, Levels, load_weights
from app.text.normalize import NormalizedText, normalize

log = logging.getLogger(__name__)


class CoercionTagger(Protocol):
    """Adds tactic hits for an utterance (e.g. an LLM with strict JSON output)."""

    async def tag(self, text: str, normalized: NormalizedText) -> list[Hit]: ...


async def _run_tagger(
    tagger: CoercionTagger | None, text: str, normalized: NormalizedText, budget_s: float
) -> tuple[list[Hit], str]:
    """Hits and status (`disabled | ok | timeout | error`). A failure never fails the utterance."""
    if tagger is None:
        return [], "disabled"
    try:
        return await asyncio.wait_for(tagger.tag(text, normalized), budget_s), "ok"
    except TimeoutError:
        return [], "timeout"
    except Exception:
        log.exception("coercion tagger %s failed", type(tagger).__name__)
        return [], "error"


async def _run_model(
    model: ModelTagger | None, text: str, normalized: NormalizedText, budget_s: float
) -> tuple[dict[str, float] | None, str]:
    """Classifier probabilities and status (`disabled | ok | timeout | error`)."""
    if model is None:
        return None, "disabled"
    try:
        return await asyncio.wait_for(model.ascores(text, normalized), budget_s), "ok"
    except TimeoutError:
        return None, "timeout"
    except Exception:
        log.exception("coercion classifier failed")
        return None, "error"


class HitOut(BaseModel):
    tactic: str
    pattern: str
    span: str  # original transcript text
    weight: float
    confidence: float
    score: float
    negated: bool
    source: str = "lexicon"
    # False = shown as evidence only (lexicon hits while the classifier is the primary signal).
    counted: bool = True


class CoercionResult(BaseModel):
    utterance_id: str
    hits: list[HitOut]
    score: float
    raw_score: float
    level: Level
    tactic_strengths: dict[str, float]
    stages_reached: list[int]
    chain: list[ChainStep]
    ordered: bool
    protective_strength: float
    caps_applied: list[str]
    contributions: list[Contribution]
    mode: str  # model | lexicon_fallback | evidence (non-speech evidence, e.g. a form)
    model_probs: dict[str, float] | None  # classifier probabilities (after any lexicon boost)
    model_status: str
    llm_status: str


class CoercionEngine:
    def __init__(
        self,
        lexicon: Lexicon,
        cfg: CoercionConfig,
        levels: Levels,
        tagger: CoercionTagger | None = None,
        tagger_timeout_s: float = 2.0,
        model: ModelTagger | None = None,
        model_timeout_s: float = 1.0,
    ) -> None:
        self.lexicon = lexicon
        self.cfg = cfg
        self.levels = levels
        self.tagger = tagger
        self.tagger_timeout_s = tagger_timeout_s
        self.model = model
        self.model_timeout_s = model_timeout_s

    @classmethod
    def load(
        cls,
        tagger: CoercionTagger | None = None,
        model: ModelTagger | None = None,
        model_timeout_s: float = 1.0,
    ) -> CoercionEngine:
        weights = load_weights()
        return cls(
            Lexicon.load(MatchConfig.from_dict(weights["matching"])),
            CoercionConfig.from_weights(weights),
            Levels.from_weights(weights),
            tagger,
            model=model,
            model_timeout_s=model_timeout_s,
        )

    def describe(self) -> str:
        tactics = self.lexicon.tactics
        n = sum(1 for p in self.lexicon.patterns if p.category != PROTECTIVE)
        return f"{n} patterns, {len(tactics)} tactics + protective cues"

    def new_call(self) -> CallCoercion:
        return CallCoercion(self)


class CallCoercion:
    """Per-call coercion state."""

    def __init__(self, engine: CoercionEngine) -> None:
        self._engine = engine
        self._tracker = SequenceTracker(engine.cfg)

    async def process(
        self,
        utterance_id: str,
        t_s: float,
        text: str,
        normalized: NormalizedText | None = None,
        verification_mismatch: bool = False,
    ) -> CoercionResult:
        engine = self._engine
        normalized = normalized or normalize(text)
        lexicon_hits = engine.lexicon.match(normalized)
        probs, model_status = await _run_model(
            engine.model, text, normalized, engine.model_timeout_s
        )
        llm_hits, llm_status = await _run_tagger(
            engine.tagger, text, normalized, engine.tagger_timeout_s
        )
        if probs is not None:
            mode = "model"
            # Protective cues ("visit your branch", "never share your OTP") lower risk in both
            # modes, capped by coercion.protective_max_reduction. Lexicon tactic hits don't count.
            protective = [h for h in lexicon_hits if h.category == PROTECTIVE]
            counted = [*engine.model.fuse(probs, lexicon_hits, text, normalized), *protective]
            shown = [(h, True) for h in counted] + [
                (h, False) for h in lexicon_hits if h.category != PROTECTIVE
            ]
            lexicon_tactics = {h.category for h in lexicon_hits if h.score > 0}
            model_probs = {
                t: round(p, 4) for t, p in engine.model.boosted(probs, lexicon_tactics).items()
            }
        else:
            mode, counted, model_probs = "lexicon_fallback", lexicon_hits, None
            shown = [(h, True) for h in lexicon_hits]
        counted = [*counted, *llm_hits]
        shown += [(h, True) for h in llm_hits]

        return self._result(
            utterance_id, t_s, shown, counted, mode, model_probs, model_status, llm_status,
            verification_mismatch,
        )  # fmt: skip

    async def add_evidence(
        self, utterance_id: str, t_s: float, hits: list[Hit], verification_mismatch: bool = False
    ) -> CoercionResult:
        """Count evidence that didn't come from speech (e.g. what a person reports on a form)."""
        return self._result(
            utterance_id, t_s, [(h, True) for h in hits], hits, "evidence", None,
            "disabled", "disabled", verification_mismatch,
        )  # fmt: skip

    def _result(
        self,
        utterance_id: str,
        t_s: float,
        shown: list[tuple[Hit, bool]],
        counted: list[Hit],
        mode: str,
        model_probs: dict[str, float] | None,
        model_status: str,
        llm_status: str,
        verification_mismatch: bool,
    ) -> CoercionResult:
        engine = self._engine
        snap = self._tracker.update(utterance_id, t_s, counted, verification_mismatch)
        return CoercionResult(
            utterance_id=utterance_id,
            hits=[
                HitOut(
                    tactic=h.category,
                    pattern=h.pattern,
                    span=h.span,
                    weight=h.weight,
                    confidence=h.confidence,
                    score=round(h.score, 4),
                    negated=h.negated,
                    source=h.source,
                    counted=c,
                )
                for h, c in shown
            ],
            score=snap.score,
            raw_score=snap.raw_score,
            level=engine.levels.of(snap.score),
            tactic_strengths=snap.tactic_strengths,
            stages_reached=snap.stages_reached,
            chain=snap.chain,
            ordered=snap.ordered,
            protective_strength=snap.protective_strength,
            caps_applied=snap.caps_applied,
            contributions=snap.contributions,
            mode=mode,
            model_probs=model_probs,
            model_status=model_status,
            llm_status=llm_status,
        )
