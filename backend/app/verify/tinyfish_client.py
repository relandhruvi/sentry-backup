"""Async TinyFish client: Search + Fetch only, exactly as documented at docs.tinyfish.ai.

- Search: `GET https://api.search.tinyfish.ai` with `query`, `location`, `language`,
  `include_domains` (comma-separated), `purpose`. Response `results[]`:
  `{position, site_name, title, snippet, url, date?}`. Default limit 30 requests/minute.
- Fetch: `POST https://api.fetch.tinyfish.ai` with `{urls (max 10), format, ttl,
  per_url_timeout_ms, purpose}`. Response `results[]`: `{url, final_url, title, description,
  language, format, text}` plus `errors[]`: `{url, error, status?}` (per-URL failures come back
  with HTTP 200). Default limit 150 URLs/minute.
- Auth: `X-API-Key` header. Errors: `{"error": {"code", "message"}}`.

The official Python SDK documents only a synchronous client, so this uses httpx directly.

Every request has a timeout, is retried once on 429 (honouring `Retry-After`), 500, 503 or a
network error/timeout, and never on 400/401/402/403/404. Failures raise `TinyFishUnavailable`;
callers report "unavailable" and never invent a result. Client-side rate limiters keep us under
the documented per-minute limits.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

from app.events import ComponentStatus

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
RETRY_STATUSES = {429, 500, 503}
MAX_RETRY_AFTER_S = 5.0


class TinyFishUnavailable(Exception):
    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    site_name: str = ""
    position: int = 0


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    title: str
    text: str


@dataclass
class RateLimiter:
    """Sliding one-minute window; `acquire(n)` waits until n more units fit."""

    per_minute: int
    _events: deque[float] = field(default_factory=deque)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self, n: int = 1) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._events and now - self._events[0] >= 60:
                    self._events.popleft()
                if len(self._events) + n <= self.per_minute:
                    self._events.extend([now] * n)
                    return
                await asyncio.sleep(60 - (now - self._events[0]) + 0.01)


def _error_code(resp: httpx.Response) -> str:
    try:
        return resp.json()["error"]["code"]
    except Exception:
        return f"HTTP {resp.status_code}"


class TinyFishClient:
    def __init__(
        self,
        api_key: str | None,
        timeout_s: float = 8.0,
        search_per_minute: int = 30,
        fetch_per_minute: int = 150,
        location: str = "IN",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key
        self._timeout = timeout_s
        self._location = location
        self._search_limit = RateLimiter(search_per_minute)
        self._fetch_limit = RateLimiter(fetch_per_minute)
        self._http = httpx.AsyncClient(timeout=timeout_s, transport=transport)
        self._last_error: str | None = None
        self._last_error_t = 0.0
        self._last_ok: float | None = None

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def status(self) -> tuple[ComponentStatus, str]:
        if not self._key:
            return ComponentStatus.UNCONFIGURED, "TINYFISH_API_KEY not set"
        if self._last_error and (self._last_ok is None or self._last_error_t > self._last_ok):
            fatal = any(
                c in self._last_error for c in ("INVALID_API_KEY", "MISSING_API_KEY", "401")
            )
            return (
                ComponentStatus.UNAVAILABLE if fatal else ComponentStatus.DEGRADED,
                f"TinyFish: last request failed ({self._last_error})",
            )
        if self._last_ok is None:
            return ComponentStatus.OK, "TinyFish key configured; no request yet"
        return ComponentStatus.OK, "TinyFish search + fetch reachable"

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        if not self._key:
            raise TinyFishUnavailable("TINYFISH_API_KEY not set")
        headers = {"X-API-Key": self._key}
        for attempt in (1, 2):
            try:
                resp = await self._http.request(method, url, headers=headers, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                reason = f"{type(exc).__name__}"
                if attempt == 1:
                    continue
                raise self._fail(reason) from exc
            if resp.status_code == 200:
                self._last_ok = time.monotonic()
                return resp.json()
            reason = f"{resp.status_code} {_error_code(resp)}"
            if attempt == 1 and resp.status_code in RETRY_STATUSES:
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = min(float(retry_after), MAX_RETRY_AFTER_S) if retry_after else 0.5
                except ValueError:
                    wait = 0.5
                await asyncio.sleep(wait)
                continue
            raise self._fail(reason, resp.status_code)
        raise self._fail("retry exhausted")  # pragma: no cover

    def _fail(self, reason: str, status: int | None = None) -> TinyFishUnavailable:
        self._last_error = reason
        self._last_error_t = time.monotonic()
        log.warning("TinyFish request failed: %s", reason)
        return TinyFishUnavailable(reason, status)

    async def search(
        self,
        query: str,
        include_domains: list[str] | None = None,
        purpose: str | None = None,
    ) -> list[SearchResult]:
        params = {"query": query, "location": self._location, "language": "en"}
        if include_domains:
            params["include_domains"] = ",".join(include_domains)
        if purpose:
            params["purpose"] = purpose
        await self._search_limit.acquire()
        body = await self._request("GET", SEARCH_URL, params=params)
        return [
            SearchResult(
                title=r.get("title") or "",
                url=r.get("url") or "",
                snippet=r.get("snippet") or "",
                site_name=r.get("site_name") or "",
                position=int(r.get("position") or 0),
            )
            for r in body.get("results", [])
            if r.get("url")
        ]

    async def fetch(
        self, urls: list[str], purpose: str | None = None, max_age_s: int | None = None
    ) -> tuple[list[FetchedPage], list[dict]]:
        payload: dict = {
            "urls": urls[:10],
            "format": "markdown",
            "per_url_timeout_ms": int(self._timeout * 1000) - 500,
        }
        if max_age_s is not None:
            payload["ttl"] = max_age_s
        if purpose:
            payload["purpose"] = purpose
        await self._fetch_limit.acquire(len(payload["urls"]))
        body = await self._request("POST", FETCH_URL, json=payload)
        pages = [
            FetchedPage(
                url=r.get("url") or "",
                final_url=r.get("final_url") or r.get("url") or "",
                title=r.get("title") or "",
                text=r.get("text") if isinstance(r.get("text"), str) else "",
            )
            for r in body.get("results", [])
        ]
        return pages, list(body.get("errors", []))
