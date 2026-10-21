"""Fuzzy lexicon matcher: finds weighted tactic cues in normalized utterance text.

Patterns and transcripts both go through app.text.normalize, then each pattern is compared with
token windows of similar length using rapidfuzz. Short patterns (otp, cbi) must match exactly;
longer ones tolerate STT errors, with thresholds from weights.yaml. Every hit keeps the span of the
ORIGINAL text it matched, which is what users see as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from app.text.normalize import NormalizedText, normalize, normalize_key

LEXICON_PATH = Path(__file__).with_name("lexicon.yaml")
PROTECTIVE = "PROTECTIVE"


@dataclass(frozen=True)
class MatchConfig:
    exact_max_chars: int
    min_ratio_short: float
    min_ratio_medium: float
    min_ratio_long: float
    min_token_ratio: float
    optional_tokens: frozenset[str]
    confidence_floor: float

    @classmethod
    def from_dict(cls, d: dict) -> MatchConfig:
        values = {k: d[k] for k in cls.__dataclass_fields__}
        values["optional_tokens"] = frozenset(normalize_key(t) for t in d["optional_tokens"])
        return cls(**values)

    def min_ratio(self, key_len: int) -> float | None:
        """None means exact match required."""
        if key_len <= self.exact_max_chars:
            return None
        if key_len <= 6:
            return self.min_ratio_short
        if key_len <= 12:
            return self.min_ratio_medium
        return self.min_ratio_long


@dataclass(frozen=True)
class Pattern:
    id: str
    category: str  # tactic name or PROTECTIVE
    text: str  # as written in the lexicon
    tokens: tuple[str, ...]  # normalized
    weight: float

    @property
    def key(self) -> str:
        return " ".join(self.tokens)


@dataclass(frozen=True)
class Hit:
    category: str
    pattern_id: str
    pattern: str
    weight: float
    confidence: float
    first_token: int
    last_token: int
    span: str  # original text, for display
    negated: bool = False
    source: str = "lexicon"  # lexicon | model | llm

    @property
    def score(self) -> float:
        return 0.0 if self.negated else self.weight * self.confidence


@dataclass(frozen=True)
class Negation:
    tokens: frozenset[str]
    window_before: int
    window_after: int
    applies_to: frozenset[str]


class Lexicon:
    def __init__(self, patterns: list[Pattern], negation: Negation, cfg: MatchConfig) -> None:
        self.patterns = patterns
        self.negation = negation
        self.cfg = cfg

    @classmethod
    def load(cls, cfg: MatchConfig, path: Path = LEXICON_PATH) -> Lexicon:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        patterns: list[Pattern] = []
        sections = [(t, spec["patterns"]) for t, spec in data["tactics"].items()]
        sections.append((PROTECTIVE, data.get("protective", [])))
        for category, entries in sections:
            for i, entry in enumerate(entries):
                tokens = tuple(normalize(entry["p"]).norms)
                if not tokens:
                    raise ValueError(f"{category} pattern {entry['p']!r} normalizes to nothing")
                patterns.append(
                    Pattern(
                        f"{category.lower()}.{i}", category, entry["p"], tokens, float(entry["w"])
                    )
                )
        neg = data.get("negation", {})
        negation = Negation(
            tokens=frozenset(normalize_key(t) for t in neg.get("tokens", [])),
            window_before=int(neg.get("window_before", 0)),
            window_after=int(neg.get("window_after", 0)),
            applies_to=frozenset(neg.get("applies_to", [])),
        )
        return cls(patterns, negation, cfg)

    @property
    def tactics(self) -> list[str]:
        return sorted({p.category for p in self.patterns if p.category != PROTECTIVE})

    def match(self, text: NormalizedText) -> list[Hit]:
        norms = text.norms
        hits = [h for p in self.patterns if (h := self._best_window(p, text, norms)) is not None]
        return self._dedupe(hits)

    def _best_window(self, p: Pattern, text: NormalizedText, norms: list[str]) -> Hit | None:
        n = len(p.tokens)
        key = p.key
        min_ratio = self.cfg.min_ratio(len(key))
        best: tuple[float, int, int] | None = None  # (ratio, first, last)
        sizes = {n} if min_ratio is None or n == 1 else {n - 1, n, n + 1}
        for size in sorted(s for s in sizes if s >= 1):
            for i in range(0, len(norms) - size + 1):
                window = " ".join(norms[i : i + size])
                if min_ratio is None:
                    ratio = 100.0 if window == key else 0.0
                else:
                    if abs(len(window) - len(key)) > max(2, len(key) * 0.35):
                        continue
                    ratio = fuzz.ratio(key, window)
                    if ratio >= min_ratio and not self._tokens_covered(
                        p.tokens, norms[i : i + size]
                    ):
                        continue
                threshold = 100.0 if min_ratio is None else min_ratio
                if ratio >= threshold and (best is None or ratio > best[0]):
                    best = (ratio, i, i + size - 1)
        if best is None:
            return None
        ratio, first, last = best
        if min_ratio is None or min_ratio >= 100:
            confidence = 1.0
        else:
            floor = self.cfg.confidence_floor
            confidence = floor + (1 - floor) * (ratio - min_ratio) / (100 - min_ratio)
        return Hit(
            category=p.category,
            pattern_id=p.id,
            pattern=p.text,
            weight=p.weight,
            confidence=round(confidence, 3),
            first_token=first,
            last_token=last,
            span=text.span(first, last),
            negated=self._negated(p.category, norms, first, last),
        )

    def _tokens_covered(self, pattern: tuple[str, ...], window: list[str]) -> bool:
        """Each pattern token aligns, in order, to a distinct window token (fuzzy, or exact for
        negation words). Function words may be skipped in patterns of 3+ tokens."""
        can_skip = len(pattern) >= 3
        pos = 0
        for tok in pattern:
            exact = tok in self.negation.tokens
            for j in range(pos, len(window)):
                if window[j] == tok or (
                    not exact and fuzz.ratio(tok, window[j]) >= self.cfg.min_token_ratio
                ):
                    pos = j + 1
                    break
            else:
                if not (can_skip and tok in self.cfg.optional_tokens):
                    return False
        return True

    def _negated(self, category: str, norms: list[str], first: int, last: int) -> bool:
        neg = self.negation
        if category not in neg.applies_to:
            return False
        before = norms[max(0, first - neg.window_before) : first]
        after = norms[last + 1 : last + 1 + neg.window_after]
        return any(t in neg.tokens for t in (*before, *after))

    @staticmethod
    def _dedupe(hits: list[Hit]) -> list[Hit]:
        """Within a category, overlapping hits keep only the strongest (e.g. "transfer" inside
        "transfer karna hoga"). Different categories may share a span."""
        kept: list[Hit] = []
        for hit in sorted(hits, key=lambda h: (-h.weight * h.confidence, h.first_token)):
            overlaps = any(
                k.category == hit.category
                and k.first_token <= hit.last_token
                and hit.first_token <= k.last_token
                for k in kept
            )
            if not overlaps:
                kept.append(hit)
        return sorted(kept, key=lambda h: (h.first_token, h.category))
