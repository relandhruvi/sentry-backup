"""Per-call text analysis run on every transcribed utterance: coercion, entities, risk fusion."""

from __future__ import annotations

from pydantic import BaseModel

from app.coercion.engine import CallCoercion, CoercionEngine, CoercionResult
from app.entities.extract import Entity, EntityExtractor
from app.risk.fusion import CallRisk, RiskPayload
from app.text.normalize import normalize


class EntitiesPayload(BaseModel):
    utterance_id: str
    entities: list[Entity]
    # Every distinct claim the caller has made so far in this call (used for alerts/verification).
    claims_so_far: list[Entity]


class CallAnalyzer:
    def __init__(self, engine: CoercionEngine, extractor: EntityExtractor) -> None:
        self._coercion: CallCoercion = engine.new_call()
        self._extractor = extractor
        self._claims: dict[tuple[str, str], Entity] = {}
        self.risk = CallRisk()

    @property
    def claims(self) -> list[Entity]:
        return list(self._claims.values())

    async def analyze(
        self,
        utterance_id: str,
        t_s: float,
        text: str,
        signal_status: dict[str, str] | None = None,
    ) -> tuple[CoercionResult, EntitiesPayload | None, RiskPayload]:
        """`signal_status`: live status of the other risk signals (voice, verify)."""
        normalized = normalize(text)
        coercion = await self._coercion.process(utterance_id, t_s, text, normalized)
        found = self._extractor.extract(text, normalized)
        for e in found:
            self._claims.setdefault((e.kind, e.value), e)
        entities = (
            EntitiesPayload(utterance_id=utterance_id, entities=found, claims_so_far=self.claims)
            if found
            else None
        )
        risk = self.risk.update(coercion, t_s, self.claims, signal_status or {})
        return coercion, entities, risk
