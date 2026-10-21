"""Per-call risk fusion: signals -> risk score, level (with hysteresis) and plain-language reasons.

B4-lite has one live signal, coercion (classifier-primary, see app/coercion/engine.py), whose score
already carries time decay, the escalation chain and the hard caps. Voice anti-spoof (B5) and
verification (B6) are reported with their real status ("unavailable" until built) and contribute
nothing until they exist. Level changes use hysteresis: a level rises as soon as the score crosses
its threshold, but only drops once the score is `hysteresis` below that level's threshold.

All numbers come from weights.yaml; all reason texts from reasons.yaml (English + Hindi).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from app.coercion.engine import CoercionResult
from app.coercion.sequence import Contribution
from app.entities.extract import Entity
from app.risk.levels import Level, Levels, load_weights

REASONS_PATH = Path(__file__).with_name("reasons.yaml")
ORDER = [Level.LOW, Level.MEDIUM, Level.HIGH, Level.CRITICAL]


@lru_cache(maxsize=2)
def load_reasons(path: Path = REASONS_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class Reason(BaseModel):
    code: str  # tactic name or CHAIN
    en: str
    hi: str
    evidence_text: str | None = None  # original transcript words behind the reason
    utterance_id: str | None = None
    weight: float  # weight x strength, for ordering


class RiskPayload(BaseModel):
    utterance_id: str
    t_s: float
    score: float
    level: Level
    previous_level: Level
    level_changed: bool
    contributions: list[Contribution]
    top_reasons: list[Reason]
    # Live status of every risk signal: coercion mode (model | lexicon_fallback), voice, verify.
    signals: dict[str, str]


@dataclass(frozen=True)
class FusionConfig:
    levels: Levels
    hysteresis: float
    signal_weights: dict[str, float]
    tactic_weights: dict[str, float]
    stages: dict[str, int]
    max_reasons: int
    chain_reason_min_stages: int
    verification_weights: dict[str, float]
    cap_margin: float
    min_tactics_for_high: int

    @classmethod
    def load(cls) -> FusionConfig:
        w = load_weights()
        f = w["fusion"]
        return cls(
            levels=Levels.from_weights(w),
            hysteresis=float(w["levels"]["hysteresis"]),
            signal_weights={k: float(v) for k, v in f["signal_weights"].items()},
            tactic_weights=dict(w["coercion"]["tactic_weights"]),
            stages=dict(w["coercion"]["stages"]),
            max_reasons=int(f["max_reasons"]),
            chain_reason_min_stages=int(f["chain_reason_min_stages"]),
            verification_weights={k: float(v) for k, v in f["verification"].items()},
            cap_margin=float(w["coercion"]["cap_margin"]),
            min_tactics_for_high=int(w["coercion"]["min_tactics_for_high"]),
        )

    def lower(self, level: Level) -> float:
        return {
            Level.LOW: 0.0,
            Level.MEDIUM: self.levels.medium,
            Level.HIGH: self.levels.high,
            Level.CRITICAL: self.levels.critical,
        }[level]


class CallRisk:
    """Risk state for one call."""

    def __init__(self, cfg: FusionConfig | None = None, reasons: dict | None = None) -> None:
        self.cfg = cfg or FusionConfig.load()
        self.texts = reasons or load_reasons()
        self.level = Level.LOW
        # tactic -> (score, original words, utterance_id) of the strongest counted evidence
        self._evidence: dict[str, tuple[float, str, str]] = {}

    def _with_hysteresis(self, score: float) -> Level:
        target = self.cfg.levels.of(score)
        if ORDER.index(target) >= ORDER.index(self.level):
            return target
        level = self.level
        while level != Level.LOW and score < self.cfg.lower(level) - self.cfg.hysteresis:
            level = ORDER[ORDER.index(level) - 1]
        return level

    def _reasons(self, coercion: CoercionResult, claims: list[Entity]) -> list[Reason]:
        t = self.texts["tactics"]
        institutions = [e.value for e in claims if e.kind == "institution"]
        ranked = sorted(
            coercion.tactic_strengths.items(),
            key=lambda kv: -self.cfg.tactic_weights[kv[0]] * kv[1],
        )
        out: list[Reason] = []
        for tactic, strength in ranked:
            if strength <= 0:
                continue
            key, fill = tactic, {}
            if tactic == "AUTHORITY" and institutions:
                key, fill = "AUTHORITY_INSTITUTION", {"institution": institutions[0]}
            _, words, utt = self._evidence.get(tactic, (0.0, None, None))
            out.append(
                Reason(
                    code=tactic,
                    en=t[key]["en"].format(**fill),
                    hi=t[key]["hi"].format(**fill),
                    evidence_text=words,
                    utterance_id=utt,
                    weight=round(self.cfg.tactic_weights[tactic] * strength, 4),
                )
            )
        stages = coercion.stages_reached
        if coercion.ordered and len(stages) >= self.cfg.chain_reason_min_stages:
            names = self.texts["stage_names"]
            chain = self.texts["chain"]
            out.insert(
                0,
                Reason(
                    code="CHAIN",
                    en=chain["en"].format(stages=" → ".join(names[s]["en"] for s in stages)),
                    hi=chain["hi"].format(stages=" → ".join(names[s]["hi"] for s in stages)),
                    weight=1.0,
                ),
            )
        return out[: self.cfg.max_reasons]

    def update(
        self,
        coercion: CoercionResult,
        t_s: float,
        claims: list[Entity],
        signal_status: dict[str, str],
    ) -> RiskPayload:
        for h in coercion.hits:
            if h.counted and h.score > 0:
                best = self._evidence.get(h.tactic)
                if best is None or h.score > best[0]:
                    self._evidence[h.tactic] = (h.score, h.span, coercion.utterance_id)

        w = self.cfg.signal_weights["coercion"]
        score = round(min(1.0, w * coercion.score), 4)
        previous = self.level
        self.level = self._with_hysteresis(score)
        top = max(coercion.contributions, key=lambda c: c.value * c.weight, default=None)
        contributions = [
            Contribution(
                signal="risk.coercion",
                value=coercion.score,
                weight=w,
                evidence_utterance_id=top.evidence_utterance_id if top else None,
            ),
            *coercion.contributions,
        ]
        return RiskPayload(
            utterance_id=coercion.utterance_id,
            t_s=t_s,
            score=score,
            level=self.level,
            previous_level=previous,
            level_changed=self.level != previous,
            contributions=contributions,
            top_reasons=self._reasons(coercion, claims),
            signals={"coercion": coercion.mode, **signal_status},
        )


def apply_verification(
    score: float, tactic_count: int, verdicts: list[str], cfg: FusionConfig
) -> tuple[float, list[Contribution], bool]:
    """Combine a coercion score with verification verdicts (noisy-OR).

    Returns (score, contributions, capped). Verification can never be what reaches HIGH: unless the
    tactic evidence on its own could reach HIGH (at least `min_tactics_for_high` tactics), the
    result is capped just below the HIGH threshold.
    """
    weights = [cfg.verification_weights[v] for v in verdicts if v in cfg.verification_weights]
    contributions = [
        Contribution(signal=f"verify.{v}", value=1.0, weight=cfg.verification_weights[v])
        for v in sorted(set(verdicts))
        if v in cfg.verification_weights
    ]
    combined = score
    for w in weights:
        combined = 1 - (1 - combined) * (1 - w)
    capped = False
    if tactic_count < cfg.min_tactics_for_high and combined >= cfg.levels.high:
        combined, capped = cfg.levels.high - cfg.cap_margin, True
    return round(min(1.0, combined), 4), contributions, capped
