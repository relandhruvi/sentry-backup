"""Alert dispatcher: fires once per call, on the first transition to HIGH or CRITICAL.

Content is built from fusion output only: level, title, top reasons, the caller's claims
(institution, numbers, UPI IDs, case numbers), the verified official number when verification
provides one, and a "Call <name> now" action. Every channel send has a timeout; the outcome and
latency are logged and stored in the `alerts` table. A failed channel never blocks the others.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from pydantic import BaseModel

from app.alerts.channels import AlertChannel
from app.entities.extract import Entity
from app.risk.fusion import Reason, RiskPayload, load_reasons
from app.risk.levels import Level
from app.storage import Storage

log = logging.getLogger(__name__)

ALERT_LEVELS = (Level.HIGH, Level.CRITICAL)


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    phone_e164: str


class AlertPayload(BaseModel):
    alert_id: str
    utterance_id: str
    level: Level
    score: float
    title: dict[str, str]  # en, hi
    top_reasons: list[Reason]
    claims: list[Entity]
    verified_official_number: str | None
    verification_status: str
    action: dict[str, str]  # en, hi
    contacts: list[str]  # names of opted-in contacts reached by per-contact channels


class CallAlerts:
    """Alert state for one call."""

    def __init__(
        self,
        session_id: str,
        channels: list[AlertChannel],
        storage: Storage,
        elder_name: str,
        send_timeout_s: float,
    ) -> None:
        self.session_id = session_id
        self._channels = channels
        self._storage = storage
        self._elder_name = elder_name
        self._timeout = send_timeout_s
        self.fired = False

    def _build(
        self,
        risk: RiskPayload,
        claims: list[Entity],
        contacts: list[Contact],
        verification_status: str,
    ) -> AlertPayload:
        texts = load_reasons()["alert"]
        if self._elder_name:
            action = {k: v.format(name=self._elder_name) for k, v in texts["action"].items()}
        else:
            action = dict(texts["action_default"])
        return AlertPayload(
            alert_id=uuid.uuid4().hex,
            utterance_id=risk.utterance_id,
            level=risk.level,
            score=risk.score,
            title=dict(texts["title"][risk.level.value]),
            top_reasons=risk.top_reasons,
            claims=claims,
            verified_official_number=None,  # filled by verification (B6) when it confirms one
            verification_status=verification_status,
            action=action,
            contacts=[c.name for c in contacts],
        )

    async def on_risk(
        self, risk: RiskPayload, claims: list[Entity], verification_status: str
    ) -> AlertPayload | None:
        if self.fired or risk.level not in ALERT_LEVELS:
            return None
        self.fired = True
        t0 = time.perf_counter()
        contacts = await self._storage.active_contacts()
        alert = self._build(risk, claims, contacts, verification_status)
        delivered, errors = [], []
        for channel in self._channels:
            targets: list[Contact | None] = contacts if channel.per_contact else [None]
            for contact in targets:
                try:
                    await asyncio.wait_for(channel.send(alert, contact), self._timeout)
                    delivered.append(channel.name)
                except Exception as exc:  # includes TimeoutError
                    log.warning("alert channel %s failed: %r", channel.name, exc)
                    errors.append(f"{channel.name}: {exc!r}")
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        log.info(
            "alert %s %s level=%s delivered=%s errors=%d latency=%.1fms",
            self.session_id[:8],
            alert.alert_id[:8],
            alert.level,
            delivered,
            len(errors),
            latency_ms,
        )
        await self._storage.save_alert(
            self.session_id,
            alert.level.value,
            bool(delivered),
            latency_ms,
            "; ".join(errors) or None,
        )
        return alert
