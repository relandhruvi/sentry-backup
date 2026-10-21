import pytest

from app.coercion.engine import CoercionEngine
from app.entities.extract import EntityExtractor


@pytest.fixture(scope="module")
def extractor() -> EntityExtractor:
    return EntityExtractor.load(CoercionEngine.load().lexicon.cfg)


def values(entities, kind):
    return [e.value for e in entities if e.kind == kind]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Call me on 98765 43210", ["+919876543210"]),
        ("mera number +91-91234-56789 hai", ["+919123456789"]),
        ("nau aath saat chhe paanch chaar teen do ek zero", ["+919876543210"]),
        ("nine eight double seven six five four three two one", ["+919877654321"]),
        ("नौ आठ सात छह पांच चार तीन दो एक शून्य", ["+919876543210"]),
        ("toll free 1800 425 3800", ["18004253800"]),
        ("do ghante mein ek kaam karo", []),
    ],
)
def test_phones(extractor, text, expected):
    assert values(extractor.extract(text), "phone") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("UPI ID hai verify dot rbi at ybl", ["verify.rbi@ybl"]),
        ("send to ramesh.k@okaxis, not help@gmail.com", ["ramesh.k@okaxis"]),
        ("ramesh at the rate paytm", ["ramesh@paytm"]),
        ("I will call you at nine", []),
    ],
)
def test_upi(extractor, text, expected):
    assert values(extractor.extract(text), "upi") == expected


def test_urls_and_case_ids(extractor):
    ents = extractor.extract("open www dot sbi-kyc-update dot com, your FIR number is MH 4521/2026")
    assert values(ents, "url") == ["www.sbi-kyc-update.com"]
    assert values(ents, "case_id") == ["MH-4521-2026"]
    assert values(extractor.extract("आपके खिलाफ एफआईआर नंबर ४५२१ दर्ज है"), "case_id") == ["4521"]


@pytest.mark.parametrize(
    ("text", "institution"),
    [
        ("मैं मुमबाई क्रिम ब्रान्ज से बोल रहा हूँ", "Police (Crime Branch)"),  # real STT output
        ("fraud prevention team at HTFC Bank", "HDFC Bank"),  # STT confusion
        ("KYC department of State Bank or India", "State Bank of India"),
        ("प्रवर्तन निदेशालय, दिल्ली से", "Enforcement Directorate"),
    ],
)
def test_institutions(extractor, text, institution):
    assert institution in values(extractor.extract(text), "institution")
