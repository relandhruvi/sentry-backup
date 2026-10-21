"""Risk levels and the weights file shared by coercion scoring and (B4) fusion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml

WEIGHTS_PATH = Path(__file__).with_name("weights.yaml")


class Level(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class Levels:
    medium: float
    high: float
    critical: float

    @classmethod
    def from_weights(cls, weights: dict) -> Levels:
        lv = weights["levels"]
        return cls(float(lv["medium"]), float(lv["high"]), float(lv["critical"]))

    def of(self, score: float) -> Level:
        if score >= self.critical:
            return Level.CRITICAL
        if score >= self.high:
            return Level.HIGH
        if score >= self.medium:
            return Level.MEDIUM
        return Level.LOW


@lru_cache(maxsize=4)
def load_weights(path: Path = WEIGHTS_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
