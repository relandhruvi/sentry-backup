"""Trained tactic classifier behind CoercionEngine: hits, statuses, caps, degradation."""

import asyncio
import statistics
import time

import pytest
from fastapi.testclient import TestClient

from app.coercion.engine import CoercionEngine
from app.coercion.model_tagger import MODEL_DIR, REQUIRED_FILES, ModelTagger, TacticModel
from app.main import create_app
from app.risk.levels import Level

TACTICS = ["AUTHORITY", "THREAT", "URGENCY", "SECRECY", "ISOLATION", "PAYMENT", "CREDENTIAL"]


class FakeModel:
    """Stands in for TacticModel: fixed probabilities per call."""

    def __init__(self, probs: dict[str, float], threshold: float = 0.5) -> None:
        self.labels = TACTICS
        self.thresholds = dict.fromkeys(TACTICS, threshold)
        self.run = "fake"
        self._probs = probs

    def probs(self, text, normalized=None):
        return [self._probs.get(t, 0.0) for t in TACTICS]


class SlowModel(ModelTagger):
    async def ascores(self, text, normalized):
        await asyncio.sleep(5)


class BrokenModel(ModelTagger):
    async def ascores(self, text, normalized):
        raise RuntimeError("boom")


def engine_with(probs: dict[str, float], boost: float = 0.0, gate: float | None = None):
    return CoercionEngine.load(model=ModelTagger(FakeModel(probs), 0.8, boost, gate))


WARRANT = "There is an arrest warrant against you."


def test_hits_above_threshold_only():
    tagger = ModelTagger(FakeModel({"THREAT": 0.9, "PAYMENT": 0.4}), weight=0.8)
    (hit,) = tagger.hits("aapke khilaf warrant hai")
    assert hit.category == "THREAT" and hit.source == "model"
    assert hit.score == pytest.approx(0.8 * 0.9)


async def test_classifier_is_primary_lexicon_hits_are_evidence_only():
    r = await engine_with({}).new_call().process("u1", 0.0, WARRANT)
    assert r.mode == "model" and r.model_status == "ok"
    lex = [h for h in r.hits if h.source == "lexicon"]
    assert lex and not any(h.counted for h in lex)  # shown, but the classifier said no
    assert r.score == 0 and r.tactic_strengths == {}


async def test_model_hit_uses_lexicon_span_as_evidence():
    text = "Sir please listen. " + WARRANT
    r = await engine_with({"THREAT": 0.9}).new_call().process("u1", 0.0, text)
    (hit,) = [h for h in r.hits if h.counted]
    assert hit.source == "model" and hit.tactic == "THREAT"
    assert hit.span != text and hit.span in text  # the lexicon's matched phrase


@pytest.mark.parametrize(
    ("prob", "gate", "counted"),
    [(0.45, 0.4, True), (0.35, 0.4, False), (0.45, None, False)],
)
async def test_lexicon_boost_only_above_gate(prob, gate, counted):
    r = (
        await engine_with({"THREAT": prob}, boost=0.1, gate=gate)
        .new_call()
        .process("u1", 0.0, WARRANT)
    )
    assert any(h.counted and h.tactic == "THREAT" for h in r.hits) == counted


async def test_boost_needs_a_lexicon_hit_for_the_same_tactic():
    r = (
        await engine_with({"PAYMENT": 0.45}, boost=0.1, gate=0.4)
        .new_call()
        .process("u1", 0.0, WARRANT)
    )
    assert not any(h.counted for h in r.hits)


async def test_source_contributions():
    r = (
        await engine_with({"SECRECY": 0.95})
        .new_call()
        .process("u1", 0.0, "this stays between the two of us, okay ji")
    )
    signals = {c.signal: c for c in r.contributions}
    assert signals["coercion.SECRECY.model"].evidence_utterance_id == "u1"
    assert not any(s.endswith(".lexicon") for s in signals)


async def test_model_hits_respect_single_utterance_cap():
    engine = CoercionEngine.load(
        model=ModelTagger(FakeModel(dict.fromkeys(TACTICS, 1.0)), weight=1.0)
    )
    r = await engine.new_call().process("u1", 0.0, "anything at all")
    assert r.level == Level.MEDIUM and "single_tactic_or_utterance" in r.caps_applied


@pytest.mark.parametrize(("cls", "status"), [(SlowModel, "timeout"), (BrokenModel, "error")])
async def test_model_failure_falls_back_to_lexicon(cls, status):
    engine = CoercionEngine.load(model=cls(FakeModel({}), 0.8), model_timeout_s=0.05)
    r = await engine.new_call().process("u1", 0.0, WARRANT)
    assert r.model_status == status and r.mode == "lexicon_fallback"
    assert any(h.tactic == "THREAT" and h.source == "lexicon" and h.counted for h in r.hits)


async def test_no_model_runs_lexicon_fallback():
    r = await CoercionEngine.load().new_call().process("u1", 0.0, WARRANT)
    assert r.model_status == "disabled" and r.mode == "lexicon_fallback"
    assert r.tactic_strengths.get("THREAT", 0) > 0


def test_health_reports_missing_model_as_unavailable_and_degraded(settings, tmp_path):
    settings.coercion_model_enabled = True
    settings.coercion_model_dir = tmp_path / "no-model"
    with TestClient(create_app(settings)) as c:
        body = c.get("/health").json()
    comp = body["components"]["classifier"]
    assert comp["status"] == "unavailable"
    assert "make train-coercion" in comp["detail"] and "lexicon fallback" in comp["detail"]
    assert body["status"] == "degraded"


def test_health_reports_disabled(client):
    assert client.get("/health").json()["components"]["classifier"]["status"] == "disabled"


real_model = pytest.mark.skipif(
    not all((MODEL_DIR / f).exists() for f in REQUIRED_FILES),
    reason="no trained model in models/coercion (run `make train-coercion`)",
)


@real_model
def test_real_model_latency_under_budget():
    model = TacticModel.load(MODEL_DIR, threads=1)
    texts = [
        "Sir, I am calling from the cyber crime branch, your Aadhaar is linked to a money laundering case.",
        "आप किसी को कुछ मत बताइए, यह गोपनीय जांच है।",
        "Beta aaj shaam ko khana kya bana rahe ho, main aa rahi hoon.",
    ] * 20
    model.probs(texts[0])
    lat = []
    for t in texts:
        t0 = time.perf_counter()
        model.probs(t)
        lat.append((time.perf_counter() - t0) * 1000)
    assert statistics.median(lat) < 150 and max(lat) < 300, lat


async def test_protective_cues_count_in_model_mode():
    r = await engine_with({}).new_call().process("u1", 0.0, "Please visit your branch tomorrow.")
    assert r.mode == "model" and r.protective_strength > 0
    assert any(h.tactic == "PROTECTIVE" and h.counted for h in r.hits)
