"""Voice anti-spoofing interface. No model is wired in yet, so the only provider reports unavailable."""

from __future__ import annotations

from typing import Protocol

import numpy as np
from pydantic import BaseModel, Field

from app.events import ComponentStatus


class VoiceScore(BaseModel):
    window_start_s: float
    window_end_s: float
    p_synthetic: float = Field(ge=0.0, le=1.0)
    model: str


class AntiSpoofProvider(Protocol):
    name: str

    def status(self) -> tuple[ComponentStatus, str]: ...

    async def score(self, pcm: np.ndarray, sample_rate: int) -> VoiceScore | None:
        """Score one window of int16 mono audio. Returns None when no score can be produced."""
        ...


class UnavailableAntiSpoof:
    name = "none"

    def status(self) -> tuple[ComponentStatus, str]:
        return ComponentStatus.UNAVAILABLE, "no anti-spoof model configured"

    async def score(self, pcm: np.ndarray, sample_rate: int) -> VoiceScore | None:
        return None
