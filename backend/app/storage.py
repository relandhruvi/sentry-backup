"""SQLite persistence for sessions, events and the alert log. Audio is never stored."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.events import Event

# Payload keys that carry spoken words; removed before writing when STORE_TRANSCRIPTS=false.
TRANSCRIPT_KEYS = frozenset({"text", "span", "spans", "evidence_text"})
REDACTED = "[redacted]"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    ended_at    TEXT,
    end_reason  TEXT
);
CREATE TABLE IF NOT EXISTS events (
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    seq         INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    ts          TEXT NOT NULL,
    level       TEXT NOT NULL,
    delivered   INTEGER NOT NULL,
    latency_ms  REAL,
    error       TEXT
);
CREATE TABLE IF NOT EXISTS verify_cache (
    key         TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    phone_e164  TEXT NOT NULL,
    opted_in    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    revoked_at  TEXT
);
"""


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if k in TRANSCRIPT_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class Storage:
    def __init__(self, path: Path, store_transcripts: bool) -> None:
        self.path = path
        self.store_transcripts = store_transcripts
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def available(self) -> bool:
        return self._db is not None

    async def create_session(self, session_id: str, created_at: datetime) -> None:
        if self._db is None:
            return
        await self._db.execute(
            "INSERT INTO sessions (id, created_at) VALUES (?, ?)",
            (session_id, created_at.isoformat()),
        )
        await self._db.commit()

    async def end_session(self, session_id: str, reason: str) -> None:
        if self._db is None:
            return
        await self._db.execute(
            "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ? AND ended_at IS NULL",
            (datetime.now(UTC).isoformat(), reason, session_id),
        )
        await self._db.commit()

    async def save_event(self, event: Event) -> None:
        if self._db is None:
            return
        payload = event.payload if self.store_transcripts else redact(event.payload)
        await self._db.execute(
            "INSERT INTO events (session_id, seq, ts, type, payload) VALUES (?, ?, ?, ?, ?)",
            (
                event.session_id,
                event.seq,
                event.ts.isoformat(),
                event.type.value,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        await self._db.commit()

    async def events(self, session_id: str) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT seq, ts, type, payload FROM events WHERE session_id = ? ORDER BY seq",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [
            {"seq": seq, "ts": ts, "type": type_, "payload": json.loads(payload)}
            for seq, ts, type_, payload in rows
        ]

    async def save_alert(
        self,
        session_id: str,
        level: str,
        delivered: bool,
        latency_ms: float,
        error: str | None,
    ) -> None:
        if self._db is None:
            return
        await self._db.execute(
            "INSERT INTO alerts (session_id, ts, level, delivered, latency_ms, error)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, datetime.now(UTC).isoformat(), level, int(delivered), latency_ms, error),
        )
        await self._db.commit()

    async def alerts(self, session_id: str) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT level, delivered, latency_ms, error FROM alerts WHERE session_id = ?",
            (session_id,),
        )
        return [
            {"level": lv, "delivered": bool(d), "latency_ms": ms, "error": e}
            for lv, d, ms, e in await cursor.fetchall()
        ]

    async def add_contact(self, name: str, phone_e164: str, opted_in: bool) -> int:
        if self._db is None:
            raise RuntimeError("storage unavailable")
        cursor = await self._db.execute(
            "INSERT INTO contacts (name, phone_e164, opted_in, created_at) VALUES (?, ?, ?, ?)",
            (name, phone_e164, int(opted_in), datetime.now(UTC).isoformat()),
        )
        await self._db.commit()
        return int(cursor.lastrowid)

    async def revoke_contact(self, contact_id: int) -> None:
        if self._db is None:
            return
        await self._db.execute(
            "UPDATE contacts SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (datetime.now(UTC).isoformat(), contact_id),
        )
        await self._db.commit()

    async def active_contacts(self) -> list:
        """Opted-in, non-revoked contacts (alert recipients)."""
        from app.alerts.dispatcher import Contact

        if self._db is None:
            return []
        cursor = await self._db.execute(
            "SELECT id, name, phone_e164 FROM contacts WHERE opted_in = 1 AND revoked_at IS NULL"
            " ORDER BY id"
        )
        return [Contact(i, n, p) for i, n, p in await cursor.fetchall()]

    async def cache_get(self, key: str) -> tuple[dict, datetime] | None:
        """Verification cache (official sources only; no transcripts)."""
        if self._db is None:
            return None
        cursor = await self._db.execute(
            "SELECT payload, fetched_at FROM verify_cache WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row[0]), datetime.fromisoformat(row[1])

    async def cache_put(self, key: str, payload: dict) -> None:
        if self._db is None:
            return
        await self._db.execute(
            "INSERT OR REPLACE INTO verify_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps(payload, ensure_ascii=False), datetime.now(UTC).isoformat()),
        )
        await self._db.commit()
