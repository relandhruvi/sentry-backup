"""B3 acceptance: coercion engine on fixture conversations, plus the hard risk rules."""

import pytest
import yaml

from app.analysis import CallAnalyzer
from app.coercion.engine import CoercionEngine
from app.entities.extract import EntityExtractor
from app.risk.levels import Level
from tools.coercion_report import FIXTURES, ORDER, load_live

CONVERSATIONS = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))["conversations"]


@pytest.fixture(scope="module")
def engine() -> CoercionEngine:
    return CoercionEngine.load()


@pytest.fixture(scope="module")
def extractor(engine) -> EntityExtractor:
    return EntityExtractor.load(engine.lexicon.cfg)


async def run_call(engine, texts: list[str]):
    call = engine.new_call()
    return [await call.process(f"u{i + 1}", float(i), t) for i, t in enumerate(texts)]


def rank(level: Level) -> int:
    return ORDER.index(level)


def test_fixture_coverage():
    by_cat = {
        c: [x for x in CONVERSATIONS if x["category"] == c] for c in ("scam", "benign_urgent")
    }
    assert len(by_cat["scam"]) >= 10 and len(by_cat["benign_urgent"]) >= 10
    for cat in by_cat.values():
        assert {"en", "hi", "hinglish"} <= {c["lang"] for c in cat}


@pytest.mark.parametrize("conv", CONVERSATIONS, ids=[c["id"] for c in CONVERSATIONS])
async def test_fixture_conversation(engine, conv):
    texts = [t["text"] for t in conv["turns"] if t["speaker"] == "caller"]
    results = await run_call(engine, texts)
    levels = [r.level for r in results]
    if conv["category"] == "scam":
        assert rank(levels[-1]) >= rank(Level.HIGH), levels
        assert rank(levels[0]) < rank(levels[-1]), "scam should escalate over the call"
    else:
        assert max(rank(lv) for lv in levels) <= rank(Level.MEDIUM), levels


async def test_single_utterance_never_reaches_high(engine):
    # Every tactic in ONE utterance: still capped below HIGH.
    text = (
        "I am from the CBI, you are under digital arrest, do not tell anyone, "
        "do not disconnect, transfer the money to the safe account immediately and tell me the OTP"
    )
    (r,) = await run_call(engine, [text])
    assert r.level == Level.MEDIUM
    assert "single_tactic_or_utterance" in r.caps_applied
    assert r.raw_score >= 0.55  # the cap, not weak scoring, kept it below HIGH


async def test_single_tactic_repeated_never_reaches_high(engine):
    results = await run_call(engine, ["digital arrest"] * 5)
    assert rank(results[-1].level) < rank(Level.HIGH)


async def test_critical_requires_payment_or_credential_plus_threat_or_secrecy(engine):
    # Strong authority + threat + isolation + urgency, but no payment/credential demand.
    results = await run_call(
        engine,
        [
            "This is the crime branch, Inspector Sharma speaking.",
            "There is an arrest warrant against you, you will be sent to jail today.",
            "Do not disconnect this call and do not talk to anyone.",
            "This is a digital arrest, you have no time, cooperate immediately.",
        ],
    )
    assert results[-1].level != Level.CRITICAL
    assert results[-1].raw_score >= 0.8 and "critical_gate" in results[-1].caps_applied


async def test_verification_mismatch_satisfies_critical_gate(engine):
    call = engine.new_call()
    await call.process("u1", 0, "I am calling from the KYC department of your bank.")
    await call.process("u2", 1, "Do not go to the branch and stay on the call.")
    await call.process("u3", 2, "Read out the OTP and transfer the money to the safe account now.")
    without = await call.process("u4", 3, "Tell me the OTP right now.")
    assert without.level != Level.CRITICAL
    call2 = engine.new_call()
    for i, t in enumerate(
        [
            "I am calling from the KYC department of your bank.",
            "Do not go to the branch and stay on the call.",
            "Read out the OTP and transfer the money to the safe account now.",
        ]
    ):
        await call2.process(f"u{i}", i, t)
    with_mismatch = await call2.process(
        "u4", 3, "Tell me the OTP right now.", verification_mismatch=True
    )
    assert with_mismatch.level == Level.CRITICAL


async def test_otp_advice_is_negated_and_protective(engine):
    (r,) = await run_call(
        engine, ["The bank will never ask for your OTP or PIN, never share them."]
    )
    creds = [h for h in r.hits if h.tactic == "CREDENTIAL"]
    assert creds and all(h.negated and h.score == 0 for h in creds)
    assert r.protective_strength > 0


async def test_negated_protective_cue_is_not_protective(engine):
    (r,) = await run_call(engine, ["You must not discuss it with your family."])
    assert r.protective_strength == 0
    assert any(h.tactic == "SECRECY" for h in r.hits)


async def test_english_paraphrase_of_hindi_scam(engine):
    # Whisper sometimes translates Hindi speech; the engine must still see the tactics.
    results = await run_call(
        engine,
        [
            "I am speaking from the police station, your son has been arrested.",
            "Don't tell your family about this, otherwise he will go to jail.",
            "Send the money right now on Google Pay, it will be refunded later.",
        ],
    )
    tactics = {h.tactic for r in results for h in r.hits}
    assert {"AUTHORITY", "THREAT", "SECRECY", "PAYMENT"} <= tactics
    assert rank(results[-1].level) >= rank(Level.HIGH)


async def test_evidence_spans_are_original_text_and_contributions_cite_utterances(engine):
    texts = ["आपके खिलाफ गिरफ्तारी वारंट जारी हुआ है।", "किसी को मत बताइए, अभी सेफ अकाउंट में पैसे भेजिए।"]
    results = await run_call(engine, texts)
    for text, r in zip(texts, results, strict=True):
        for h in r.hits:
            assert h.span in text
    contribs = [
        c
        for c in results[-1].contributions
        if c.signal.startswith("coercion.") and c.signal.count(".") == 1
    ]
    tactic_contribs = [c for c in contribs if c.signal.split(".")[1].isupper()]
    assert tactic_contribs and all(c.evidence_utterance_id in {"u1", "u2"} for c in tactic_contribs)


async def test_chain_records_stage_order(engine):
    results = await run_call(
        engine,
        [
            "Main crime branch se inspector bol raha hoon.",
            "Aapke khilaf arrest warrant hai.",
            "Kisi ko mat batana.",
            "Abhi safe account mein paise bhejne honge.",
        ],
    )
    last = results[-1]
    assert [c.stage for c in last.chain] == [1, 2, 3, 4]
    assert last.ordered


def test_lexicon_patterns_are_well_formed(engine):
    lex = engine.lexicon
    assert set(lex.tactics) == {
        "AUTHORITY", "THREAT", "URGENCY", "SECRECY", "ISOLATION", "PAYMENT", "CREDENTIAL",
    }  # fmt: skip
    for p in lex.patterns:
        assert 0 < p.weight <= 1 and p.tokens, p


@pytest.mark.parametrize("category", ["scam", "benign"])
async def test_real_stt_output_of_synthetic_calls(engine, extractor, category):
    """Noisy check: the latest local live run (Whisper output of synthetic TTS calls).
    Skipped when no run exists (runs are local, gitignored)."""
    from tools.make_test_audio import DEFAULT_OUT

    calls = [
        c
        for c in load_live(DEFAULT_OUT / "runs")
        if (c["category"] == "scam") == (category == "scam")
    ]
    if not calls:
        pytest.skip("no live STT run found; run `make stt-report` with the server up")
    for call in calls:
        analyzer = CallAnalyzer(engine, extractor)
        levels = [
            (await analyzer.analyze(f"u{i}", float(i), t))[0].level
            for i, t in enumerate(call["utterances"])
        ]
        if category == "scam":
            assert rank(levels[-1]) >= rank(Level.HIGH), (call["id"], levels)
        else:
            assert max(rank(lv) for lv in levels) <= rank(Level.MEDIUM), (call["id"], levels)
