"""Trusted institution directory: resolution of typed names, ambiguity, privacy, source rules."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.verify.institutions import default_directory
from app.verify.verifier import load_sources
from tests.test_verify import FakeTinyFish, result


@pytest.fixture(scope="module")
def directory():
    return default_directory()


@pytest.mark.parametrize(
    ("typed", "identity_type", "expected_id"),
    [
        ("PSB", None, "punjab_sind_bank"),
        ("psb", "bank", "punjab_sind_bank"),
        ("punjab sind", None, "punjab_sind_bank"),
        ("Punjab & Sind", None, "punjab_sind_bank"),
        ("पंजाब सिंध", None, "punjab_sind_bank"),
        ("sbi", None, "sbi"),
        ("State Bank", None, "sbi"),
        ("स्टेट बैंक", None, "sbi"),
        ("स्टेट बैंक ऑफ़ इंडिया", None, "sbi"),
        ("PNB", None, "punjab_national_bank"),
        ("BoB", None, "bank_of_baroda"),
        ("hdfc bnak", None, "hdfc_bank"),  # typo
        ("kotak mahendra", None, "kotak_mahindra_bank"),  # typo
        ("fedx", None, "fedex"),  # typo
        ("एचडीएफसी", None, "hdfc_bank"),
        ("CBI", "bank", "central_bank_of_india"),
        ("CBI", "police", "cbi"),
        ("customs", None, "cbic"),
        ("aadhar", None, "uidai"),
    ],
)
def test_resolves(directory, typed, identity_type, expected_id):
    r = directory.resolve(typed, identity_type)
    assert r.status == "resolved", (typed, r)
    assert r.institution.id == expected_id


def test_ambiguous_abbreviation_offers_candidates(directory):
    r = directory.resolve("CBI")
    assert r.status == "ambiguous"
    assert {c.id for c in r.candidates} == {"cbi", "central_bank_of_india"}


def test_single_word_suggests_instead_of_guessing(directory):
    r = directory.resolve("Punjab")
    assert r.status == "suggest"
    assert {c.id for c in r.candidates} == {"punjab_national_bank", "punjab_sind_bank"}
    assert directory.resolve("ICIC bank").status == "suggest"


@pytest.mark.parametrize(
    "typed", ["Sunshine Traders", "Rahul Sharma", "Priya Verma", "Sharma Bank"]
)
def test_unknown_names_and_personal_names_do_not_resolve(directory, typed):
    assert directory.resolve(typed, "bank").status == "unknown"


def test_every_bank_has_one_rbi_confirmed_bank_in_domain():
    for raw in load_sources()["institutions"]:
        confirmed = [d for d in raw["domains"] if d["confirmed"]]
        assert all(d["source"] for d in confirmed), raw["id"]
        if raw["kind"] == "bank":
            assert len(confirmed) == 1 and confirmed[0]["domain"].endswith(".bank.in"), raw["id"]
            assert "rbi.org.in" in confirmed[0]["source"]


def test_unconfirmed_domains_are_never_used(directory):
    hdfc = directory.get("hdfc_bank")
    assert hdfc.domains == ("hdfc.bank.in",) and "hdfcbank.com" in hdfc.unconfirmed
    assert not directory.get("blue_dart").checkable


def test_directory_covers_bank_categories_and_agencies(directory):
    banks = [i for i in directory.institutions if i.kind == "bank"]
    assert {b.category for b in banks} == {"public_sector", "private", "small_finance", "payments"}
    assert len(banks) == 50
    for iid in ("rbi", "npci", "cbi", "ed", "income_tax", "cbic", "trai", "uidai", "cybercrime",
                "dot", "india_post", "fedex", "dhl", "blue_dart", "dtdc"):  # fmt: skip
        assert directory.get(iid), iid


# ---- /verify/manual + /verify/institutions -----------------------------------------------------


def search(query, domains):
    if "punjabandsind.bank.in" in domains:
        return [result("https://punjabandsind.bank.in/contact-us", "Contact Us",
                       "Customer care toll free number 1800 419 8300")]  # fmt: skip
    return []


@pytest.fixture
def client(tmp_path):
    fake = FakeTinyFish(search=search)
    settings = Settings(_env_file=None, db_path=tmp_path / "i.db", stt_load_on_startup=False,
                        coercion_model_enabled=False, tinyfish_api_key="test-key")  # fmt: skip
    with TestClient(create_app(settings, tinyfish_transport=httpx.MockTransport(fake))) as c:
        yield c, fake


def post(client, **body):
    r = client.post("/verify/manual", json={"claimed_identity_type": "bank", **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_psb_is_resolved_and_checked_on_its_rbi_domain(client):
    c, fake = client
    body = post(c, claimed_identity_name="PSB", phone="9876543210")
    assert body["institution_match"]["status"] == "resolved"
    assert body["institution"] == "Punjab & Sind Bank"
    contact = next(ch for ch in body["checks"] if ch["check"] == "institution_contact")
    assert contact["verdict"] == "MISMATCH" and contact["official_contact"] == "1800 419 8300"
    sent = fake.bodies()
    assert "Punjab+%26+Sind+Bank" in sent or "Punjab & Sind Bank" in sent
    assert "PSB" not in sent  # the typed text never leaves the server


def test_ambiguous_input_asks_and_searches_nothing(client):
    c, fake = client
    body = post(c, claimed_identity_type="government", claimed_identity_name="central")
    assert body["institution_match"]["status"] in ("ambiguous", "suggest")
    assert body["institution_match"]["candidates"]
    assert body["institution_note"]["en"].startswith("Which one did you mean")
    assert not any(ch["check"] == "institution_contact" for ch in body["checks"])
    assert not any("include_domains" in str(r.url) and "central" in str(r.url).lower()
                   for r in fake.requests if "advisory" not in str(r.url))  # fmt: skip


def test_dropdown_id_and_unknown_id(client):
    c, _ = client
    body = post(c, institution_id="punjab_sind_bank", phone="1800 419 8300")
    contact = next(ch for ch in body["checks"] if ch["check"] == "institution_contact")
    assert contact["verdict"] == "MATCH"
    r = c.post("/verify/manual", json={"claimed_identity_type": "bank", "institution_id": "nope"})
    assert r.status_code == 422


def test_personal_name_is_never_searched(client):
    c, fake = client
    for identity_type in ("bank", "relative", "other"):
        body = post(c, claimed_identity_type=identity_type, claimed_identity_name="Rahul Sharma",
                    phone="9876543210", asked_to=["money"])  # fmt: skip
        assert body["institution"] is None
    sent = fake.bodies()
    assert "Rahul" not in sent and "Sharma" not in sent


def test_other_explains_and_still_gives_advice(client):
    c, _ = client
    body = post(c, institution_id="other", asked_to=["otp"])
    assert body["institution_match"]["status"] == "other"
    assert "never search" in body["institution_note"]["en"]
    assert body["next_steps"][-1]["en"] == "Report at cybercrime.gov.in or call 1930."


def test_unconfirmed_institution_is_explained_not_searched(client):
    c, fake = client
    body = post(c, claimed_identity_type="courier", institution_id="blue_dart")
    assert "couldn't confirm" in body["institution_note"]["en"]
    contact = next(ch for ch in body["checks"] if ch["check"] == "institution_contact")
    assert contact["status"] == "skipped"
    assert "bluedart" not in fake.bodies().lower()


def test_institutions_endpoint_lists_bilingual_names(client):
    c, _ = client
    items = c.get("/verify/institutions").json()
    psb = next(i for i in items if i["id"] == "punjab_sind_bank")
    assert psb["name_hi"] == "पंजाब एंड सिंध बैंक" and psb["checkable"]
