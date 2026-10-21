"""Alert channels. The dispatcher builds one alert per call and hands it to every channel.

- `dashboard`: emits an `alert` event on the call's event stream (/ws/events/{id}); the family
  dashboard and /demo show it instantly. One per call, not per contact.
- SMS / WhatsApp (B9) are per-contact channels: one message per opted-in contact per call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.events import EventBus, EventType

if TYPE_CHECKING:
    from app.alerts.dispatcher import AlertPayload, Contact


class AlertChannel(Protocol):
    name: str
    per_contact: bool

    async def send(self, alert: AlertPayload, contact: Contact | None) -> None: ...


class DashboardChannel:
    name = "dashboard"
    per_contact = False

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def send(self, alert: AlertPayload, contact: Contact | None) -> None:
        await self._bus.publish(EventType.ALERT, alert)
