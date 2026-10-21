"""Entity extraction: Indian phone numbers, UPI IDs, URLs, case/FIR numbers and institutions.

Callers often SAY these ("nine eight seven…", "verify dot rbi at the rate ybl"), so spoken digits
(English, romanized Hindi, Devanagari) and spoken "at the rate"/"dot" are converted first.
Only these extracted values (kind + value) are ever sent to verification, never the transcript.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from app.coercion.lexicon import Lexicon, MatchConfig, Negation, Pattern
from app.text.normalize import NormalizedText, normalize

INSTITUTIONS_PATH = Path(__file__).with_name("institutions.yaml")

EntityKind = Literal["phone", "upi", "url", "case_id", "institution"]


class Entity(BaseModel):
    kind: EntityKind
    value: str  # canonical form (E.164 phone, lowercase UPI/URL, institution name)
    raw: str  # as it appeared in the transcript
    detail: str = ""  # e.g. institution kind


_DIGIT_WORDS = {
    # English
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Romanized Hindi
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4", "paanch": "5",
    "panch": "5", "chhah": "6", "chhe": "6", "che": "6", "saat": "7", "sat": "7", "aath": "8",
    "nau": "9",
    # Devanagari
    "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5",
    "छह": "6", "छः": "6", "छे": "6", "सात": "7", "आठ": "8", "नौ": "9",
}  # fmt: skip
_REPEAT = {"double": 2, "triple": 3, "डबल": 2, "ट्रिपल": 3}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_WORD = re.compile(r"[^\s,.;:!?।()\"']+")
# Ambiguous words ("do" = two/give, "ek" = one/a) only count inside a longer run of digits.
_MIN_SPOKEN_RUN = 6

_MOBILE = re.compile(r"(?:91|0)?([6-9]\d{9})")
_TOLL_FREE = re.compile(r"(1800\d{6,7})")

_SPOKEN_AT = re.compile(r"\s+(?:at the rate(?: of)?|at|एट द रेट|एट)\s+", re.IGNORECASE)
_SPOKEN_DOT = re.compile(r"\s+(?:dot|डॉट)\s+", re.IGNORECASE)
_UPI = re.compile(r"(?<![\w.@-])([a-z0-9][a-z0-9._-]{1,255})@([a-z][a-z0-9]{1,63})(?![\w.@])")
_URL = re.compile(
    r"(?<![\w@])((?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*"
    r"\.(?:com|in|org|net|co|info|xyz|link|online|site|app|top|live|gov\.in|co\.in)(?:/[^\s]*)?)(?![\w.])"
)
_CASE = re.compile(
    r"(?:\b(?:fir|f\.i\.r\.?|case|complaint|reference|ref)|एफआईआर|केस|शिकायत)\s*"
    r"(?:number|no\.?|num|id|नंबर|संख्या)?\s*(?:is|hai|है)?\s*[:#-]?\s*"
    r"([a-z]{0,4}[\s/-]?\d[\d/-]{2,}|\d[\d\s/-]{2,}\d)",
    re.IGNORECASE,
)


def _digit_runs(text: str) -> list[tuple[str, str]]:
    """(digits, raw) for each run of written or spoken digits."""
    runs, digits, raw, spoken = [], [], [], 0
    pending_repeat = 1

    def close() -> None:
        nonlocal digits, raw, spoken
        if digits and (spoken < len("".join(digits)) or len("".join(digits)) >= _MIN_SPOKEN_RUN):
            runs.append(("".join(digits), " ".join(raw)))
        digits, raw, spoken = [], [], 0

    for m in _WORD.finditer(text):
        word = m.group().lower().translate(_DEVANAGARI_DIGITS).replace("+", "").replace("-", "")
        if word in _REPEAT:
            pending_repeat = _REPEAT[word]
            raw.append(m.group())
            continue
        if word.isdigit():
            digits.append(word * pending_repeat if len(word) == 1 else word)
        elif word in _DIGIT_WORDS:
            digits.append(_DIGIT_WORDS[word] * pending_repeat)
            spoken += pending_repeat
        else:
            close()
            pending_repeat = 1
            continue
        raw.append(m.group())
        pending_repeat = 1
    close()
    return runs


def extract_phones(text: str) -> list[Entity]:
    found: dict[str, Entity] = {}
    for digits, raw in _digit_runs(text):
        if m := _TOLL_FREE.fullmatch(digits):
            value = m.group(1)
            found.setdefault(value, Entity(kind="phone", value=value, raw=raw, detail="toll_free"))
            continue
        # A run may hold several numbers read back-to-back; take non-overlapping mobiles.
        for m in _MOBILE.finditer(digits):
            value = f"+91{m.group(1)}"
            found.setdefault(value, Entity(kind="phone", value=value, raw=raw, detail="mobile"))
    return list(found.values())


def _spoken_to_written(text: str) -> str:
    text = _SPOKEN_DOT.sub(".", text)
    return _SPOKEN_AT.sub("@", text)


def extract_upi(text: str, handles: frozenset[str]) -> list[Entity]:
    found: dict[str, Entity] = {}
    candidates = [(text, False), (_spoken_to_written(text), True)]
    for candidate, spoken in candidates:
        for m in _UPI.finditer(candidate.lower()):
            handle = m.group(2)
            # Spoken "at" is ambiguous ("call at nine"): require a known UPI handle.
            if spoken and handle not in handles:
                continue
            value = f"{m.group(1)}@{handle}"
            found.setdefault(value, Entity(kind="upi", value=value, raw=m.group(0)))
    return list(found.values())


def extract_urls(text: str) -> list[Entity]:
    found: dict[str, Entity] = {}
    for candidate in (text, _SPOKEN_DOT.sub(".", text)):
        for m in _URL.finditer(candidate.lower()):
            value = m.group(1).rstrip("/.")
            if "@" in value:
                continue
            found.setdefault(value, Entity(kind="url", value=value, raw=m.group(1)))
    return list(found.values())


def extract_case_ids(text: str) -> list[Entity]:
    found: dict[str, Entity] = {}
    for m in _CASE.finditer(text.translate(_DEVANAGARI_DIGITS)):
        value = re.sub(r"[\s/-]+", "-", m.group(1).strip()).upper().strip("-")
        if sum(c.isdigit() for c in value) >= 3:
            found.setdefault(value, Entity(kind="case_id", value=value, raw=m.group(0).strip()))
    return list(found.values())


class EntityExtractor:
    def __init__(self, institutions: Lexicon, upi_handles: frozenset[str]) -> None:
        self._institutions = institutions
        self._upi_handles = upi_handles
        self._kind = {p.category: p.id.split(":", 1)[0] for p in institutions.patterns}

    @classmethod
    def load(cls, cfg: MatchConfig, path: Path = INSTITUTIONS_PATH) -> EntityExtractor:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        patterns = [
            Pattern(f"{inst['kind']}:{i}", inst["name"], alias, tuple(normalize(alias).norms), 1.0)
            for i, inst in enumerate(data["institutions"])
            for alias in inst["aliases"]
        ]
        no_negation = Negation(frozenset(), 0, 0, frozenset())
        handles = frozenset(str(h).lower() for h in data.get("upi_handles", []))
        return cls(Lexicon(patterns, no_negation, cfg), handles)

    def extract(self, text: str, normalized: NormalizedText | None = None) -> list[Entity]:
        normalized = normalized or normalize(text)
        entities: list[Entity] = []
        entities += extract_phones(text)
        entities += extract_upi(text, self._upi_handles)
        entities += extract_urls(text)
        entities += extract_case_ids(text)
        seen: set[str] = set()
        for hit in self._institutions.match(normalized):
            if hit.category in seen:
                continue
            seen.add(hit.category)
            entities.append(
                Entity(
                    kind="institution",
                    value=hit.category,
                    raw=hit.span,
                    detail=self._kind[hit.category],
                )
            )
        return entities
