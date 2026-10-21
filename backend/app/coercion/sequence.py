"""Call-level escalation tracking: turns per-utterance tactic hits into a coercion score.

Per tactic, the strongest hit in each utterance is combined across utterances with noisy-OR, so
repeated pressure accumulates but saturates. Tactics are combined (noisy-OR, weighted) and the
result is scaled by how far the call has progressed along the chain

    AUTHORITY -> THREAT/URGENCY -> SECRECY/ISOLATION -> PAYMENT/CREDENTIAL

with a bonus when stages appeared in that order. Protective cues reduce the score (capped).
Evidence fades with time: each utterance's contribution is scaled by 0.5^(age / half-life), so
pressure that stopped minutes ago weighs less than pressure happening now.
Finally the hard rules cap it: no HIGH from a single tactic or single utterance, no CRITICAL
without a payment/credential demand plus threat/secrecy (or a verification mismatch).

All weights and thresholds come from app/risk/weights.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from app.coercion.lexicon import PROTECTIVE, Hit


@dataclass(frozen=True)
class CoercionConfig:
    tactic_weights: dict[str, float]
    stages: dict[str, int]
    chain_factor: dict[int, float]
    ordered_bonus: float
    protective_max_reduction: float
    min_tactics_for_high: int
    min_utterances_for_high: int
    critical_requires_any: frozenset[str]
    critical_with_any: frozenset[str]
    cap_margin: float
    decay_half_life_s: float
    high: float
    critical: float

    @classmethod
    def from_weights(cls, weights: dict) -> CoercionConfig:
        c, levels = weights["coercion"], weights["levels"]
        return cls(
            tactic_weights=dict(c["tactic_weights"]),
            stages=dict(c["stages"]),
            chain_factor={int(k): float(v) for k, v in c["chain_factor"].items()},
            ordered_bonus=float(c["ordered_bonus"]),
            protective_max_reduction=float(c["protective_max_reduction"]),
            min_tactics_for_high=int(c["min_tactics_for_high"]),
            min_utterances_for_high=int(c["min_utterances_for_high"]),
            critical_requires_any=frozenset(c["critical_requires_any"]),
            critical_with_any=frozenset(c["critical_with_any"]),
            cap_margin=float(c["cap_margin"]),
            decay_half_life_s=float(c["decay_half_life_s"]),
            high=float(levels["high"]),
            critical=float(levels["critical"]),
        )


class Contribution(BaseModel):
    signal: str
    value: float
    weight: float
    evidence_utterance_id: str | None = None


class ChainStep(BaseModel):
    tactic: str
    stage: int
    utterance_id: str
    t_s: float


class SequenceSnapshot(BaseModel):
    score: float
    raw_score: float
    tactic_strengths: dict[str, float]
    stages_reached: list[int]
    chain: list[ChainStep]
    ordered: bool
    protective_strength: float
    caps_applied: list[str]
    contributions: list[Contribution]


def _noisy_or(values: list[float]) -> float:
    out = 1.0
    for v in values:
        out *= 1.0 - max(0.0, min(1.0, v))
    return 1.0 - out


@dataclass
class SequenceTracker:
    cfg: CoercionConfig
    # tactic -> utterance_id -> strongest hit score in that utterance
    _per_utt: dict[str, dict[str, float]] = field(default_factory=dict)
    # tactic -> source (lexicon | model | llm) -> utterance_id -> strongest hit from that source
    _per_src: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    _first_seen: dict[str, ChainStep] = field(default_factory=dict)
    _protective: dict[str, float] = field(default_factory=dict)
    _t: dict[str, float] = field(default_factory=dict)  # utterance_id -> call time (s)
    _now: float = 0.0

    def update(
        self, utterance_id: str, t_s: float, hits: list[Hit], verification_mismatch: bool = False
    ) -> SequenceSnapshot:
        self._t[utterance_id] = t_s
        self._now = max(self._now, t_s)
        for hit in hits:
            if hit.score <= 0:
                continue
            if hit.category == PROTECTIVE:
                self._protective[utterance_id] = max(
                    self._protective.get(utterance_id, 0), hit.score
                )
                continue
            per = self._per_utt.setdefault(hit.category, {})
            per[utterance_id] = max(per.get(utterance_id, 0.0), hit.score)
            src = self._per_src.setdefault(hit.category, {}).setdefault(hit.source, {})
            src[utterance_id] = max(src.get(utterance_id, 0.0), hit.score)
            if hit.category not in self._first_seen:
                self._first_seen[hit.category] = ChainStep(
                    tactic=hit.category,
                    stage=self.cfg.stages[hit.category],
                    utterance_id=utterance_id,
                    t_s=t_s,
                )
        return self.snapshot(verification_mismatch)

    def _decayed(self, per: dict[str, float]) -> list[float]:
        hl = self.cfg.decay_half_life_s
        return [v * 0.5 ** (max(0.0, self._now - self._t[u]) / hl) for u, v in per.items()]

    def snapshot(self, verification_mismatch: bool = False) -> SequenceSnapshot:
        cfg = self.cfg
        strengths = {t: _noisy_or(self._decayed(u)) for t, u in self._per_utt.items()}
        weighted = [cfg.tactic_weights[t] * s for t, s in strengths.items()]
        base = _noisy_or(weighted)

        chain = sorted(self._first_seen.values(), key=lambda c: (c.t_s, c.stage))
        stages = sorted({c.stage for c in chain})
        # Ordered = the first time each stage appeared follows chain order.
        first_by_stage: dict[int, float] = {}
        for c in chain:
            first_by_stage.setdefault(c.stage, c.t_s)
        stage_order = [s for s, _ in sorted(first_by_stage.items(), key=lambda kv: kv[1])]
        ordered = len(stages) >= 2 and stage_order == sorted(stage_order)

        factor = cfg.chain_factor.get(len(stages), 0.0) if stages else 0.0
        if ordered:
            factor *= 1.0 + cfg.ordered_bonus
        protective = _noisy_or(self._decayed(self._protective))
        reduction = min(cfg.protective_max_reduction, protective)
        raw = min(1.0, base * factor * (1.0 - reduction))

        score, caps = raw, []
        active = [t for t, s in strengths.items() if s > 0]
        utterances = {u for per in self._per_utt.values() for u in per}
        if (
            len(active) < cfg.min_tactics_for_high or len(utterances) < cfg.min_utterances_for_high
        ) and score >= cfg.high:
            score, caps = cfg.high - cfg.cap_margin, [*caps, "single_tactic_or_utterance"]
        critical_ok = bool(cfg.critical_requires_any & set(active)) and (
            bool(cfg.critical_with_any & set(active)) or verification_mismatch
        )
        if not critical_ok and score >= cfg.critical:
            score, caps = cfg.critical - cfg.cap_margin, [*caps, "critical_gate"]

        contributions = []
        for t, s in sorted(strengths.items(), key=lambda kv: -cfg.tactic_weights[kv[0]] * kv[1]):
            contributions.append(
                Contribution(
                    signal=f"coercion.{t}",
                    value=round(s, 4),
                    weight=cfg.tactic_weights[t],
                    evidence_utterance_id=max(self._per_utt[t], key=self._per_utt[t].get),
                )
            )
            # Which evidence source (lexicon, model, llm) backs the tactic, and how strongly.
            for source, per in sorted(self._per_src[t].items()):
                contributions.append(
                    Contribution(
                        signal=f"coercion.{t}.{source}",
                        value=round(_noisy_or(self._decayed(per)), 4),
                        weight=cfg.tactic_weights[t],
                        evidence_utterance_id=max(per, key=per.get),
                    )
                )
        contributions.append(
            Contribution(signal="coercion.chain_factor", value=round(factor, 4), weight=1.0)
        )
        if protective > 0:
            contributions.append(
                Contribution(
                    signal="coercion.protective",
                    value=round(-reduction, 4),
                    weight=1.0,
                    evidence_utterance_id=max(self._protective, key=self._protective.get),
                )
            )
        return SequenceSnapshot(
            score=round(score, 4),
            raw_score=round(raw, 4),
            tactic_strengths={t: round(s, 4) for t, s in strengths.items()},
            stages_reached=stages,
            chain=chain,
            ordered=ordered,
            protective_strength=round(protective, 4),
            caps_applied=caps,
            contributions=contributions,
        )
