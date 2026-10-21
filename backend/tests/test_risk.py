"""B4-lite: risk fusion (levels, hysteresis, decay, reasons) and the dashboard alert dispatcher."""

import pytest
import yaml

from app.alerts.channels import DashboardChannel
from app.alerts.dispatcher import CallAlerts
from app.analysis import CallAnalyzer
from app.coercion.engine import CoercionEngine
from app.entities.extract import EntityExtractor
from app.events import EventBus, EventType
from app.risk.fusion import ORDER, CallRisk, FusionConfig
from app.risk.levels import Level
from app.storage import Storage
from tools.coercion_report import FIXTURES

CONVERSATIONS = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))["conversations"]
SIGNALS = {"voice": "unavailable", "verify": "unavailable"}


@pytest.fixture(scope="module")
def engine() -> CoercionEngine:
    return CoercionEngine.load()  # lexicon_fallback mode: deterministic, no local model needed


@pytest.fixture(scope="module")
def extractor(engine) -> EntityExtractor:
    return EntityExtractor.load(engine.lexicon.cfg)


@pytest.fixture
async def storage(tmp_path):
    s = Storage(tmp_path / "t.db", store_transcripts=True)
    await s.open()
    await s.create_session("s1", __import__("datetime").datetime.now())
    yield s
    await s.close()


async def run_call(engine, extractor, storage, texts, t_step=5.0):
    bus = EventBus("s1", queue_size=64, replay_size=64)
    sub = bus.subscribe()
    analyzer = CallAnalyzer(engine, extractor)
    alerts = CallAlerts("s1", [DashboardChannel(bus)], storage, "Nani", 2.0)
    risks = []
    for i, text in enumerate(texts):
        _, _, risk = await analyzer.analyze(f"u{i + 1}", i * t_step, text, SIGNALS)
        risks.append(risk)
        await alerts.on_risk(risk, analyzer.claims, SIGNALS["verify"])
    events = []
    while not sub._queue.empty():
        events.append(sub._queue.get_nowait())
    return risks, [e for e in events if e.type == EventType.ALERT]


@pytest.mark.parametrize("conv", CONVERSATIONS, ids=[c["id"] for c in CONVERSATIONS])
async def test_fixture_calls_risk_and_alerts(engine, extractor, storage, conv):
    texts = [t["text"] for t in conv["turns"] if t["speaker"] == "caller"]
    risks, alerts = await run_call(engine, extractor, storage, texts)
    if conv["category"] == "scam":
        assert ORDER.index(risks[-1].level) >= ORDER.index(Level.HIGH)
        (alert,) = alerts  # exactly one alert per call
        first_high = next(r for r in risks if r.level in (Level.HIGH, Level.CRITICAL))
        assert alert.payload["utterance_id"] == first_high.utterance_id
        assert alert.payload["top_reasons"] and alert.payload["action"]["en"] == "Call Nani now"
        assert (await storage.alerts("s1"))[0]["delivered"] is True
    else:
        assert max(ORDER.index(r.level) for r in risks) <= ORDER.index(Level.MEDIUM)
        assert alerts == []


async def test_reasons_are_bilingual_with_evidence_and_institution(engine, extractor, storage):
    risks, _ = await run_call(
        engine,
        extractor,
        storage,
        [
            "This is Inspector Sharma from the CBI, Mumbai.",
            "There is an arrest warrant against you.",
            "Do not tell anyone in your family about this.",
            "Transfer the money to the safe account right now.",
        ],
    )
    reasons = risks[-1].top_reasons
    assert reasons[0].code == "CHAIN"
    for r in reasons:
        assert r.en and r.hi and r.hi != r.en
    tactic_reasons = [r for r in reasons if r.code != "CHAIN"]
    assert all(r.evidence_text and r.utterance_id for r in tactic_reasons)
    assert risks[-1].signals == {"coercion": "lexicon_fallback", **SIGNALS}
    assert any(c.signal == "risk.coercion" for c in risks[-1].contributions)


def test_hysteresis_rises_immediately_drops_late():
    cfg = FusionConfig.load()
    risk = CallRisk(cfg)
    risk.level = Level.HIGH
    assert risk._with_hysteresis(cfg.levels.high - cfg.hysteresis / 2) == Level.HIGH  # holds
    assert risk._with_hysteresis(cfg.levels.high - cfg.hysteresis * 2) == Level.MEDIUM  # drops
    assert risk._with_hysteresis(cfg.levels.critical) == Level.CRITICAL  # rises at once
    risk.level = Level.CRITICAL
    assert risk._with_hysteresis(0.0) == Level.LOW  # can fall several levels at once


async def test_evidence_decays_with_time(engine):
    call = engine.new_call()
    a = await call.process("u1", 0.0, "There is an arrest warrant against you.")
    b = await call.process("u2", 180.0, "Hello, how are you?")  # two half-lives later
    assert b.tactic_strengths["THREAT"] == pytest.approx(a.tactic_strengths["THREAT"] / 4, rel=0.01)
