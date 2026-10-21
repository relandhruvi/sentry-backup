"""Event models and the per-session async event bus.

Every event uses the same envelope: {v, type, session_id, seq, ts, payload}.
Payloads are pydantic models defined next to the code that produces them;
the bus stores them as JSON-ready dicts so subscribers and storage see one shape.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class EventType(StrEnum):
    UTTERANCE = "utterance"
    COERCION = "coercion"
    ENTITIES = "entities"
    RISK = "risk"
    ALERT = "alert"
    STATUS = "status"


class ComponentStatus(StrEnum):
    OK = "ok"
    LOADING = "loading"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    UNCONFIGURED = "unconfigured"


class StatusPayload(BaseModel):
    """Status of a pipeline component, or of the session itself (component="session")."""

    component: str
    status: ComponentStatus | Literal["started", "ended"]
    detail: str = ""


class Event(BaseModel):
    v: int = SCHEMA_VERSION
    type: EventType
    session_id: str
    seq: int
    ts: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


EventSink = Callable[[Event], Awaitable[None]]


class Subscription:
    """A bounded queue of events for one subscriber. Drops the oldest event when full."""

    def __init__(self, bus: EventBus, maxsize: int) -> None:
        self._bus = bus
        # Unbounded so the end-of-stream sentinel never evicts a real event; events are capped below.
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._maxsize = maxsize
        self.dropped = 0

    def _offer(self, item: Event | None) -> None:
        if item is not None and self._queue.qsize() >= self._maxsize:
            self._queue.get_nowait()
            self.dropped += 1
        self._queue.put_nowait(item)

    async def __aiter__(self) -> AsyncIterator[Event]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    def close(self) -> None:
        self._bus._unsubscribe(self)


class EventBus:
    """Fan-out of one session's events to live subscribers, with a replay window for late joiners."""

    def __init__(
        self,
        session_id: str,
        queue_size: int,
        replay_size: int,
        sink: EventSink | None = None,
    ) -> None:
        self.session_id = session_id
        self._queue_size = queue_size
        self._replay: deque[Event] = deque(maxlen=replay_size)
        self._subs: set[Subscription] = set()
        self._sink = sink
        self._seq = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def publish(self, type_: EventType, payload: BaseModel) -> Event | None:
        if self._closed:
            return None
        self._seq += 1
        event = Event(
            type=type_,
            session_id=self.session_id,
            seq=self._seq,
            ts=datetime.now(UTC),
            payload=payload.model_dump(mode="json"),
        )
        self._replay.append(event)
        for sub in list(self._subs):
            sub._offer(event)
        if self._sink is not None:
            try:
                await self._sink(event)
            except Exception:
                log.exception("event sink failed for %s #%d", self.session_id, event.seq)
        return event

    def subscribe(self, replay: bool = True) -> Subscription:
        sub = Subscription(self, self._queue_size)
        if replay:
            for event in self._replay:
                sub._offer(event)
        if self._closed:
            sub._offer(None)
        else:
            self._subs.add(sub)
        return sub

    def _unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sub in list(self._subs):
            sub._offer(None)
        self._subs.clear()
