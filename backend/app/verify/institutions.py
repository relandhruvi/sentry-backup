"""Directory of trusted institutions (sources.yaml) and resolution of what a person typed.

`resolve("PSB")` -> Punjab & Sind Bank. Matching uses names, Hindi names and aliases:
  - short abbreviations (<= 5 letters, e.g. "sbi", "psb", "cbi") must match exactly;
  - longer names match fuzzily (typos, word order, "&" vs "and", Devanagari via the shared
    normalizer), on the full name and on its distinctive part ("Punjab Sind" for
    "Punjab & Sind Bank").
Outcomes: `resolved` (one clear match), `ambiguous` (several entries match about equally well:
"CBI" is both the Central Bureau of Investigation and Central Bank of India), `suggest` (one or
more plausible matches, not confident enough to search without asking), `unknown`.

Only a resolved institution is ever searched, by its canonical `name`; the text the person typed
never leaves the server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel
from rapidfuzz import fuzz

from app.text.normalize import normalize

# Words that don't tell institutions apart; removed for the "distinctive part" comparison.
GENERIC = {
    "bank", "ltd", "limited", "the", "of", "and", "india", "indian", "small", "finance",
    "payments", "payment", "department", "co", "corporation", "बैंक", "ऑफ", "ऑफ़", "एंड",
}  # fmt: skip
SHORT = 5  # aliases this short must match exactly
RESOLVE_MIN = 88
SUGGEST_MIN = 80
AMBIGUOUS_GAP = 6
WORD_SUGGEST = 84  # below RESOLVE_MIN on purpose

# Which kinds fit a claimed identity type (used to break ties such as "CBI").
KINDS_FOR_TYPE = {
    "bank": {"bank", "regulator"},
    "police": {"agency", "police"},
    "government": {"agency", "regulator", "police"},
    "courier": {"courier"},
    "telecom": {"telecom", "regulator"},
}


@dataclass(frozen=True)
class Institution:
    id: str
    name: str
    name_hi: str
    kind: str
    category: str
    aliases: tuple[str, ...]
    domains: tuple[str, ...]  # confirmed official domains only
    unconfirmed: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    @property
    def checkable(self) -> bool:
        return bool(self.domains)


class InstitutionOut(BaseModel):
    id: str
    name: str
    name_hi: str
    kind: str
    category: str
    checkable: bool
    aliases: list[str] = []  # for the dropdown's type-ahead ("PSB", "स्टेट बैंक")

    @classmethod
    def of(cls, i: Institution) -> InstitutionOut:
        return cls(id=i.id, name=i.name, name_hi=i.name_hi, kind=i.kind, category=i.category,
                   checkable=i.checkable, aliases=list(i.aliases))  # fmt: skip


class Resolution(BaseModel):
    status: Literal["resolved", "ambiguous", "suggest", "unknown", "other", "none"]
    institution: InstitutionOut | None = None
    candidates: list[InstitutionOut] = []


def _clean(text: str) -> str:
    text = text.lower().replace("&", " and ")
    # Keep Devanagari whole: its vowel signs are not \w and must not be stripped.
    return re.sub(r"[^\w\s\u0900-\u097F]", " ", text)


def _key(text: str) -> str:
    """Comparable form: lowercase, punctuation-free, Devanagari romanized (shared normalizer)."""
    return normalize(_clean(text)).text


def _core(key: str) -> str:
    generic = {normalize(g).text for g in GENERIC} | GENERIC
    return " ".join(t for t in key.split() if t not in generic)


@dataclass
class _Entry:
    inst: Institution
    exact: set[str] = field(default_factory=set)  # short aliases: exact match only
    keys: list[str] = field(default_factory=list)  # longer names: fuzzy


class InstitutionDirectory:
    def __init__(self, institutions: list[Institution]) -> None:
        self.institutions = institutions
        self.by_id = {i.id: i for i in institutions}
        self._entries = []
        for i in institutions:
            e = _Entry(i)
            for text in (i.name, i.name_hi, *i.aliases):
                k = _key(text)
                if not k:
                    continue
                if len(k.replace(" ", "")) <= SHORT and " " not in k:
                    e.exact.add(k)
                else:
                    e.keys.append(k)
            self._entries.append(e)

    @classmethod
    def from_sources(cls, sources: dict) -> InstitutionDirectory:
        out = []
        for raw in sources["institutions"]:
            doms = raw["domains"]
            out.append(
                Institution(
                    id=raw["id"],
                    name=raw["name"],
                    name_hi=raw["name_hi"],
                    kind=raw["kind"],
                    category=raw["category"],
                    aliases=tuple(raw.get("aliases", [])),
                    domains=tuple(d["domain"] for d in doms if d["confirmed"]),
                    unconfirmed=tuple(d["domain"] for d in doms if not d["confirmed"]),
                    sources=tuple(d["source"] for d in doms if d["confirmed"]),
                )
            )
        return cls(out)

    def _score(self, e: _Entry, q: str) -> float:
        if q in e.exact:
            return 100.0
        best = 0.0
        q_core = _core(q)
        for k in e.keys:
            best = max(best, fuzz.ratio(q, k))
            k_core = _core(k)
            if q_core and k_core and len(q_core) >= 3:
                # Distinctive words in any order: "sind punjab" vs "punjab and sind bank".
                best = max(best, fuzz.token_sort_ratio(q_core, k_core) - 2)
                if " " not in q_core and q_core in k_core.split():
                    # One distinctive word ("Punjab"): worth asking about, never auto-resolved.
                    best = max(best, WORD_SUGGEST)
        return best

    def resolve(self, text: str, identity_type: str | None = None) -> Resolution:
        q = _key(text)
        if not q:
            return Resolution(status="none")
        scored = sorted(((self._score(e, q), e.inst) for e in self._entries), key=lambda s: -s[0])
        scored = [(s, i) for s, i in scored if s >= SUGGEST_MIN]
        if not scored:
            return Resolution(status="unknown")
        kinds = KINDS_FOR_TYPE.get(identity_type or "")
        if kinds:
            fitting = [(s, i) for s, i in scored if i.kind in kinds]
            if fitting:
                scored = fitting
        top_score, top = scored[0]
        exact = [i for s, i in scored if s >= 100]
        if len(exact) == 1:  # an exact name/alias match wins unless another is exact too
            return Resolution(status="resolved", institution=InstitutionOut.of(exact[0]))
        if len(exact) > 1:
            return Resolution(status="ambiguous", candidates=[InstitutionOut.of(i) for i in exact])
        close = [i for s, i in scored if s >= top_score - AMBIGUOUS_GAP]
        out = [InstitutionOut.of(i) for i in close[:5]]
        if top_score >= RESOLVE_MIN and len(close) == 1:
            return Resolution(status="resolved", institution=InstitutionOut.of(top))
        if top_score >= RESOLVE_MIN:
            return Resolution(status="ambiguous", candidates=out)
        return Resolution(status="suggest", candidates=out)

    def get(self, institution_id: str) -> Institution | None:
        return self.by_id.get(institution_id)


@lru_cache(maxsize=1)
def default_directory() -> InstitutionDirectory:
    from app.verify.verifier import load_sources

    return InstitutionDirectory.from_sources(load_sources())
