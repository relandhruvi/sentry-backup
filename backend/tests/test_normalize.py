import pytest

from app.text.normalize import normalize, normalize_key, romanize_devanagari


@pytest.mark.parametrize(
    ("devanagari", "roman"),
    [
        ("अपने", "apne"),  # medial schwa deleted (V C _ C V)
        ("करना", "karnaa"),
        ("समझ", "samajh"),  # final schwa deleted, medial kept
        ("जनता", "jantaa"),  # first syllable keeps its vowel
        ("अकाउंट", "akaaunt"),
        ("हॉस्पिटल", "hospital"),  # candra-o
    ],
)
def test_romanize_devanagari(devanagari, roman):
    assert romanize_devanagari(devanagari) == roman


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("गिरफ्तार", "giraftaar"),
        ("ओटीपी", "OTP"),
        ("पुलिस", "police"),
        ("मत बताइए", "mat bataiye"),
        ("नहीं", "nhi"),
        ("आधार कार्ड", "aadhar card"),
        ("हॉस्पिटल", "Hospital"),
    ],
)
def test_scripts_and_spellings_meet(a, b):
    assert normalize_key(a) == normalize_key(b)


def test_safe_account_devanagari_and_latin_are_close():
    from rapidfuzz import fuzz

    assert fuzz.ratio(normalize_key("सेफ अकाउंट"), normalize_key("safe account")) >= 80


def test_context_dependent_confusion_only_near_bank_words():
    assert "branch" in normalize_key("check your card or visit your brunch")
    assert "brunch" in normalize_key("we had brunch on sunday")
    assert normalize_key("HTFC Bank") == normalize_key("HDFC bank")


def test_original_spans_are_preserved():
    text = "आपका account, कृपया OTP बताइए!"
    n = normalize(text)
    assert n.original == text
    i = n.norms.index("otp")
    assert n.span(i, i) == "OTP"
    assert n.span(0, len(n.tokens) - 1) == "आपका account, कृपया OTP बताइए"


def test_multiword_variant_maps_to_single_token_with_full_span():
    n = normalize("abhi kar do")
    assert n.norms[-1] == "karo"
    assert n.span(len(n.tokens) - 1, len(n.tokens) - 1) == "kar do"
