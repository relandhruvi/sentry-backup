"""Claim verification against official sources (TinyFish Search + Fetch).

Three checks, each returning a `CheckResult`
`{check, input, verdict, official_contact, sources[{url,title,snippet,fetched_at}], latency_ms}`:

  institution_contact  MATCH | MISMATCH | UNKNOWN (+ the verified callback number)
      Finds the institution's helpline on its own official domain (sources.yaml) and compares
      the number the caller gave. Only the institution name goes to TinyFish; the caller's number
      is compared locally.
  advisory             ADVISORY_FOUND | UNKNOWN
      Official advisories (cybercrime.gov.in, RBI, PIB, ...) for the claimed scenario. The query
      is a fixed scenario template; nothing the user typed is sent.
  public_reports       REPORTS_FOUND (low confidence) | NO_REPORTS_FOUND
      Web search for a phone / UPI ID / URL with "fraud OR scam". No reports is NOT proof of safety.

Any check that can't reach TinyFish returns verdict UNAVAILABLE, never a guess. Network results are
cached for 24 h in SQLite; when TinyFish is unreachable an older cached result is served and
labelled with its fetch time.

Only extracted entities (institution, phone, UPI, URL) ever cross this boundary: never audio,
transcripts, message text, personal names or the elder's details.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel

from app.events import ComponentStatus
from app.risk.fusion import load_reasons
from app.verify.inputs import display_phone, phones_in_text
from app.verify.institutions import Institution
from app.verify.tinyfish_client import TinyFishClient, TinyFishUnavailable

SOURCES_PATH = Path(__file__).with_name("sources.yaml")
CONTEXT_CHARS = 120  # text around a number that must contain a contact keyword

CheckName = Literal["institution_contact", "advisory", "public_reports"]


@lru_cache(maxsize=2)
def load_sources(path: Path = SOURCES_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class Source(BaseModel):
    url: str
    title: str
    snippet: str
    fetched_at: str


class CheckResult(BaseModel):
    check: CheckName
    input: dict[str, str]
    verdict: str
    status: Literal["ok", "unavailable", "skipped"]
    official_contact: str | None = None
    other_official_contacts: list[str] = []  # further numbers found on the official domain
    sources: list[Source] = []
    latency_ms: float
    cached: bool = False
    fetched_at: str | None = None  # when the network result was obtained (cache label)
    confidence: Literal["low", "medium"] | None = None
    note: dict[str, str]  # en, hi


class Cache(Protocol):
    async def cache_get(self, key: str) -> tuple[dict, datetime] | None: ...
    async def cache_put(self, key: str, payload: dict) -> None: ...


def _host_in(url: str, domains: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Verifier:
    name = "tinyfish"

    def __init__(self, client: TinyFishClient, cache: Cache, ttl_s: float = 86400) -> None:
        self.client = client
        self.cache = cache
        self.ttl = timedelta(seconds=ttl_s)
        self.sources = load_sources()
        self.texts = load_reasons()["verify"]

    def status(self) -> tuple[ComponentStatus, str]:
        return self.client.status()

    # ---- caching around a network lookup ---------------------------------------------------

    async def _cached(self, key: str, lookup) -> tuple[dict | None, bool, str | None]:
        """(payload, from_cache, fetched_at). Fresh cache first, then live, then stale cache."""
        hit = await self.cache.cache_get(key)
        if hit and datetime.now(UTC) - hit[1] < self.ttl:
            return hit[0], True, hit[1].isoformat(timespec="seconds")
        try:
            payload = await lookup()
        except TinyFishUnavailable:
            if hit:  # offline: an older real result, clearly labelled
                return hit[0], True, hit[1].isoformat(timespec="seconds")
            return None, False, None
        await self.cache.cache_put(key, payload)
        return payload, False, payload.get("fetched_at")

    # ---- a) institution_contact ------------------------------------------------------------

    async def _official_numbers(self, inst: Institution) -> dict:
        cfg = self.sources
        name, domains = inst.name, list(inst.domains)
        keywords = [k.lower() for k in cfg["contact_keywords"]]
        strong = [k.lower() for k in cfg["contact_strong_keywords"]]
        product = [k.lower() for k in cfg["contact_deprioritize"]]
        results = await self.client.search(
            cfg["contact_query"].format(name=name),
            include_domains=domains,
            purpose=f"Find the official customer care phone number published by {name}",
        )
        official = [r for r in results if _host_in(r.url, domains)]
        fetched_at = _now()
        scores: Counter[str] = Counter()
        evidence: dict[str, Source] = {}
        best_score: dict[str, int] = {}

        def scan(text: str, url: str, title: str) -> None:
            low = text.lower()
            for number, pos in phones_in_text(text):
                ctx = low[max(0, pos - CONTEXT_CHARS) : pos + CONTEXT_CHARS]
                if any(k in ctx for k in keywords):
                    where = f"{url} {title}".lower()
                    score = (
                        (2 if number.startswith("18") else 1)
                        + (1 if any(k in ctx for k in strong) else 0)
                        - (2 if any(p in where for p in product) else 0)
                    )
                    scores[number] += score
                    if number not in evidence or score > best_score[number]:  # cite the best page
                        best_score[number] = score
                        snippet = text[max(0, pos - 80) : pos + 60].replace("\n", " ").strip()
                        evidence[number] = Source(
                            url=url, title=title, snippet=snippet, fetched_at=fetched_at
                        )

        for r in official:
            scan(f"{r.title}. {r.snippet}", r.url, r.title)
        if not scores and official:
            pages, _ = await self.client.fetch(
                [r.url for r in official[:2]],
                purpose=f"Read {name}'s official contact page for its helpline number",
                max_age_s=int(self.ttl.total_seconds()),
            )
            for p in pages:
                if _host_in(p.final_url or p.url, domains):
                    scan(p.text, p.final_url or p.url, p.title)
        # A number seen only on product, press, tender or notice pages (score <= 0) is not a
        # helpline: e.g. a district office phone in a press release.
        ranked = [n for n, score in scores.most_common() if score > 0]
        sources: dict[str, Source] = {}
        for n in ranked:
            sources.setdefault(evidence[n].url, evidence[n])  # one entry per page
        return {
            "numbers": ranked,
            "sources": [src.model_dump() for src in list(sources.values())[:3]],
            "fetched_at": fetched_at,
        }

    async def institution_contact(
        self, inst: Institution | None, given_phone: str | None, typed_name: str = ""
    ) -> CheckResult:
        """`inst` is a resolved directory entry; anything else is never searched."""
        t0 = time.perf_counter()
        texts = self.texts["contact"]
        name = inst.name if inst else typed_name
        inp = {"institution": name, "phone": given_phone or ""}
        if inst is None:
            return self._result("institution_contact", inp, "UNKNOWN", "skipped", t0,
                                texts["NOT_LISTED"], name=typed_name or "?")  # fmt: skip
        if not inst.checkable:
            return self._result("institution_contact", inp, "UNKNOWN", "skipped", t0,
                                texts["UNCONFIRMED"], name=inst.name)  # fmt: skip
        payload, cached, fetched_at = await self._cached(
            f"contact:{inst.id}", lambda: self._official_numbers(inst)
        )
        if payload is None:
            return self._unavailable("institution_contact", inp, t0)
        numbers = payload["numbers"]
        official = display_phone(numbers[0]) if numbers else None
        if not numbers:
            verdict, note = "UNKNOWN", texts["UNKNOWN"]
        elif not given_phone:
            verdict, note = "UNKNOWN", texts["OFFICIAL_ONLY"]
        elif given_phone in numbers:
            verdict, note = "MATCH", texts["MATCH"]
        else:
            verdict, note = "MISMATCH", texts["MISMATCH"]
        return self._result(
            "institution_contact", inp, verdict, "ok", t0, note,
            name=name, official=official or "",
            official_contact=official,
            other_official_contacts=[display_phone(n) for n in numbers[1:4]],
            sources=[Source(**s) for s in payload["sources"]],
            cached=cached, fetched_at=fetched_at,
        )  # fmt: skip

    # ---- b) advisory -----------------------------------------------------------------------

    def scenario(self, identity_type: str, asked_to: list[str]) -> dict:
        for s in self.sources["scenarios"]:
            types, asks = s["when"]["identity_types"], s["when"]["asked_to_any"]
            if (not types or identity_type in types) and (
                not asks or any(a in asks for a in asked_to)
            ):
                return s
        return self.sources["scenarios"][-1]

    async def _advisories(self, query: str) -> dict:
        domains = self.sources["advisory_domains"]
        results = await self.client.search(
            query,
            include_domains=domains,
            purpose="Find official Indian government or RBI advisories warning about this scam",
        )
        fetched_at = _now()
        return {
            "sources": [
                Source(
                    url=r.url, title=r.title, snippet=r.snippet, fetched_at=fetched_at
                ).model_dump()
                for r in results
                if _host_in(r.url, domains)
            ][:3],
            "fetched_at": fetched_at,
        }

    async def advisory(self, identity_type: str, asked_to: list[str]) -> CheckResult:
        t0 = time.perf_counter()
        sc = self.scenario(identity_type, asked_to)
        inp = {"scenario": sc["id"]}
        payload, cached, fetched_at = await self._cached(
            f"advisory:{sc['id']}", lambda: self._advisories(sc["query"])
        )
        if payload is None:
            return self._unavailable("advisory", inp, t0)
        found = bool(payload["sources"])
        verdict = "ADVISORY_FOUND" if found else "UNKNOWN"
        return self._result(
            "advisory", inp, verdict, "ok", t0, self.texts["advisory"][verdict],
            sources=[Source(**s) for s in payload["sources"]],
            cached=cached, fetched_at=fetched_at,
        )  # fmt: skip

    # ---- c) public_reports -----------------------------------------------------------------

    async def _reports(self, kind: str, value: str) -> dict:
        cfg = self.sources
        query_value = value if kind != "url" else (urlsplit(value).hostname or value)
        results = await self.client.search(
            cfg["report_query"].format(value=query_value),
            purpose="Find public reports or complaints about this phone number, UPI ID or website",
        )
        words = [w.lower() for w in cfg["report_words"]]
        needle = query_value.lower()
        digits = "".join(c for c in needle if c.isdigit()) if kind == "phone" else None
        fetched_at = _now()
        hits = []
        for r in results:
            blob = f"{r.title} {r.snippet}".lower()
            mentions = (digits and digits in "".join(c for c in blob if c.isdigit())) or (
                needle in blob
            )
            if mentions and any(w in blob for w in words):
                hits.append(
                    Source(url=r.url, title=r.title, snippet=r.snippet, fetched_at=fetched_at)
                )
        return {"sources": [h.model_dump() for h in hits[:3]], "fetched_at": fetched_at}

    async def public_reports(self, kind: str, value: str) -> CheckResult:
        t0 = time.perf_counter()
        inp = {kind: value}
        payload, cached, fetched_at = await self._cached(
            f"reports:{kind}:{value}", lambda: self._reports(kind, value)
        )
        if payload is None:
            return self._unavailable("public_reports", inp, t0)
        found = bool(payload["sources"])
        verdict = "REPORTS_FOUND" if found else "NO_REPORTS_FOUND"
        kinds = self.texts["kinds"][kind]
        note = {k: v.format(kind=kinds[k]) for k, v in self.texts["reports"][verdict].items()}
        return self._result(
            "public_reports", inp, verdict, "ok", t0, note,
            sources=[Source(**s) for s in payload["sources"]],
            cached=cached, fetched_at=fetched_at,
            confidence="low" if found else None,
        )  # fmt: skip

    # ---- helpers ---------------------------------------------------------------------------

    def _unavailable(self, check: CheckName, inp: dict, t0: float) -> CheckResult:
        return self._result(check, inp, "UNAVAILABLE", "unavailable", t0, self.texts["UNAVAILABLE"])

    def _result(
        self,
        check: CheckName,
        inp: dict,
        verdict: str,
        status: str,
        t0: float,
        note: dict[str, str],
        name: str = "",
        official: str = "",
        **fields,
    ) -> CheckResult:
        return CheckResult(
            check=check,
            input=inp,
            verdict=verdict,
            status=status,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            note={k: v.format(name=name, official=official) for k, v in note.items()},
            **fields,
        )

    async def gather(self, *checks) -> list[CheckResult]:
        return list(await asyncio.gather(*checks))
