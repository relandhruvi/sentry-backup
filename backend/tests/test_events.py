from app.events import EventBus, EventType, StatusPayload
from app.storage import REDACTED, Storage


def _payload(detail: str) -> StatusPayload:
    return StatusPayload(component="stt", status="ok", detail=detail)


async def test_bus_fans_out_in_order_and_replays_to_late_subscribers():
    bus = EventBus("s1", queue_size=10, replay_size=10)
    early = bus.subscribe()
    await bus.publish(EventType.STATUS, _payload("a"))
    await bus.publish(EventType.STATUS, _payload("b"))
    late = bus.subscribe(replay=True)
    bus.close()

    early_seen = [(e.seq, e.payload["detail"]) async for e in early]
    late_seen = [(e.seq, e.payload["detail"]) async for e in late]
    assert early_seen == late_seen == [(1, "a"), (2, "b")]


async def test_slow_subscriber_drops_oldest_not_newest():
    bus = EventBus("s1", queue_size=2, replay_size=0)
    sub = bus.subscribe()
    for d in "abc":
        await bus.publish(EventType.STATUS, _payload(d))
    bus.close()

    seen = [e.payload["detail"] async for e in sub]
    assert seen == ["b", "c"]
    assert sub.dropped == 1


async def test_storage_redacts_transcript_text_when_disabled(tmp_path):
    from datetime import UTC, datetime

    storage = Storage(tmp_path / "db.sqlite", store_transcripts=False)
    await storage.open()
    await storage.create_session("s1", datetime.now(UTC))
    bus = EventBus("s1", queue_size=10, replay_size=10, sink=storage.save_event)

    class Utt(StatusPayload):
        text: str

    await bus.publish(EventType.UTTERANCE, Utt(component="x", status="ok", text="secret words"))
    rows = await storage.events("s1")
    await storage.close()

    assert rows[0]["payload"]["text"] == REDACTED
