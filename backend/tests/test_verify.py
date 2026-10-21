"""B6-lite: TinyFish client, the three verification checks, caching, privacy and /verify/manual.

TinyFish is mocked with httpx.MockTransport using the documented response shapes. One live smoke
test runs only when TINYFISH_API_KEY is configured.
"""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.verify.inputs import normalize_phone, normalize_upi, normalize_url
from app.verify.institutions import default_directory
from app.verify.tinyfish_client import TinyFishClient, TinyFishUnavailable
from app.verify.verifier import Verifier

HDFC_OFFICIAL = "18002026161"


def inst(institution_id):
    return default_directory().get(institution_id)


class MemoryCache:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[dict, datetime]] = {}

    async def cache_get(self, key):
        return self.rows.get(key)

    async def cache_put(self, key, payload):
        self.rows[key] = (payload, datetime.now(UTC))


class FakeTinyFish:
    """Routes documented Search/Fetch requests to canned results and records every request."""

    def __init__(self, search=None, fetch=None, fail_status=None):
        self.requests: list[httpx.Request] = []
        self.search_fn = search or (lambda q, domains: [])
        self.fetch_fn = fetch or (lambda urls: ([], []))
        self.fail_status = fail_status

    def bodies(self) -> str:
        return " ".join(str(r.url) + r.content.decode() for r in self.requests)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["X-API-Key"] == "test-key"
        if self.fail_status:
            return httpx.Response(self.fail_status, json={"error": {"code": "X", "message": "x"}})
        if request.url.host == "api.search.tinyfish.ai":
            qs = parse_qs(urlsplit(str(request.url)).query)
            domains = (
                qs.get("include_domains", [""])[0].split(",") if "include_domains" in qs else []
            )
            results = self.search_fn(qs["query"][0], domains)
            return httpx.Response(200, json={"query": qs["query"][0], "results": results,
                                             "total_results": len(results), "page": 0})  # fmt: skip
        if request.url.host == "api.fetch.tinyfish.ai":
            body = json.loads(request.content)
            results, errors = self.fetch_fn(body["urls"])
            return httpx.Response(200, json={"results": results, "errors": errors})
        return httpx.Response(404)


def result(url, title, snippet, pos=1):
    return {"position": pos, "site_name": urlsplit(url).hostname, "title": title,
            "snippet": snippet, "url": url}  # fmt: skip


def hdfc_search(query, domains):
    if "hdfc.bank.in" in domains:
        return [
            result("https://www.hdfc.bank.in/personal/need-help/contact-us", "Contact Us | HDFC Bank",
                   "24x7 PhoneBanking: call our toll free number 1800 202 6161 or 1860 267 6161."),
            result("https://evil.example.com/hdfc", "HDFC customer care", "helpline 9123456789"),
        ]  # fmt: skip
    if "cybercrime.gov.in" in domains:
        return [result("https://cybercrime.gov.in/advisory", "Beware of digital arrest",
                       "Police never conduct digital arrest over video calls.")]  # fmt: skip
    return []


def make_verifier(fake, **kw):
    client = TinyFishClient("test-key", transport=httpx.MockTransport(fake), **kw)
    return Verifier(client, MemoryCache())


# ---- inputs -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("98765 43210", "9876543210"),
        ("+91-98765-43210", "9876543210"),
        ("09876543210", "9876543210"),
        ("1800 202 6161", "18002026161"),
        ("1800 1600", "18001600"),
        ("022 6160 6161", "2261606161"),
        ("+44 20 7946 0958", "+442079460958"),
        ("12345", None),
        ("phone me", None),
    ],
)
def test_normalize_phone(raw, norm):
    assert normalize_phone(raw) == norm


def test_normalize_upi_and_url():
    assert normalize_upi("Verify.RBI@ybl") == "verify.rbi@ybl"
    assert normalize_upi("not a upi") is None
    assert normalize_url("sbi-kyc-update.com/login") == "https://sbi-kyc-update.com/login"
    for bad in ("http://192.168.1.1/x", "http://localhost:8000", "ftp://x.com", "nodot"):
        assert normalize_url(bad) is None


# ---- client -----------------------------------------------------------------------------------


async def test_client_retries_once_on_503_then_succeeds():
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"code": "BACKEND_UNAVAILABLE"}})
        return httpx.Response(200, json={"results": [result("https://rbi.org.in", "RBI", "x")]})

    client = TinyFishClient("test-key", transport=httpx.MockTransport(handler))
    assert len(await client.search("RBI")) == 1 and len(calls) == 2


async def test_client_does_not_retry_auth_errors_and_reports_unavailable():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(401, json={"error": {"code": "INVALID_API_KEY", "message": "bad"}})

    client = TinyFishClient("test-key", transport=httpx.MockTransport(handler))
    with pytest.raises(TinyFishUnavailable):
        await client.search("RBI")
    assert len(calls) == 1
    assert client.status()[0].value == "unavailable"


async def test_client_timeout_is_unavailable_after_one_retry():
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("slow", request=req)

    client = TinyFishClient("test-key", transport=httpx.MockTransport(handler))
    with pytest.raises(TinyFishUnavailable):
        await client.search("RBI")
    assert len(calls) == 2


def test_client_without_key_is_unconfigured():
    assert TinyFishClient(None).status()[0].value == "unconfigured"


# ---- checks -----------------------------------------------------------------------------------


async def test_institution_contact_mismatch_with_official_number_and_source():
    fake = FakeTinyFish(search=hdfc_search)
    r = await make_verifier(fake).institution_contact(inst("hdfc_bank"), "9876543210")
    assert r.verdict == "MISMATCH" and r.status == "ok"
    assert r.official_contact == "1800 202 6161"
    assert r.sources and "hdfc.bank.in" in r.sources[0].url  # never the non-official result
    assert "1800 202 6161" in r.note["en"] and "1800 202 6161" in r.note["hi"]
    assert "9876543210" not in fake.bodies()  # the caller's number is compared locally


async def test_institution_contact_match():
    r = await make_verifier(FakeTinyFish(search=hdfc_search)).institution_contact(
        inst("hdfc_bank"), HDFC_OFFICIAL
    )
    assert r.verdict == "MATCH"


async def test_institution_contact_falls_back_to_fetch_when_snippets_have_no_number():
    def search(q, domains):
        return [result("https://www.icici.bank.in/contact-us", "Contact us", "Reach us any time")]

    def fetch(urls):
        page = {"url": urls[0], "final_url": urls[0], "title": "Contact us",
                "text": "Customer care (toll free): 1800 1080\\n Our customer care number 1860 120 7777"}  # fmt: skip
        return [page], []

    fake = FakeTinyFish(search=search, fetch=fetch)
    r = await make_verifier(fake).institution_contact(inst("icici_bank"), "9876543210")
    assert r.verdict == "MISMATCH" and r.official_contact == "1800 1080"  # short toll-free
    assert r.other_official_contacts == ["1860 120 7777"]
    assert any(req.url.host == "api.fetch.tinyfish.ai" for req in fake.requests)


async def test_general_helpline_ranks_above_product_helplines():
    def search(q, domains):
        return [
            result("https://homeloans.hdfc.bank.in/helpline", "Home Loan Customer Care",
                   "Helpline Numbers. Our Toll Free Helpline Number : 1800 210 0018"),
            result("https://www.hdfc.bank.in/contact-us", "Contact us",
                   "PhoneBanking customer care: 1800 202 6161 / 1860 267 6161"),
        ]  # fmt: skip

    r = await make_verifier(FakeTinyFish(search=search)).institution_contact(
        inst("hdfc_bank"), "18002100018"
    )
    assert r.official_contact == "1800 202 6161"
    assert r.verdict == "MATCH"  # the home-loan line is still an official number


async def test_unlisted_institution_is_never_searched():
    fake = FakeTinyFish(search=hdfc_search)
    r = await make_verifier(fake).institution_contact(
        None, "9876543210", "Ramesh Kumar Enterprises"
    )
    assert r.verdict == "UNKNOWN" and r.status == "skipped" and fake.requests == []


async def test_public_reports_found_is_low_confidence_and_not_found_says_not_proof():
    def search(q, domains):
        if "9123456789" in q:
            return [result("https://forum.example.in/t/1", "9123456789 fraud call",
                           "Got a scam call from 9123456789 claiming to be CBI")]  # fmt: skip
        return [result("https://example.in/x", "Unrelated", "nothing here")]

    v = make_verifier(FakeTinyFish(search=search))
    found = await v.public_reports("phone", "9123456789")
    assert found.verdict == "REPORTS_FOUND" and found.confidence == "low" and found.sources
    clean = await v.public_reports("phone", "9988776655")
    assert clean.verdict == "NO_REPORTS_FOUND"
    assert "NOT proof" in clean.note["en"] and "सबूत नहीं" in clean.note["hi"]


async def test_advisory_found_only_on_official_domains():
    v = make_verifier(FakeTinyFish(search=hdfc_search))
    r = await v.advisory("police", ["video_call"])
    assert r.verdict == "ADVISORY_FOUND" and r.input == {"scenario": "digital_arrest"}
    assert all("cybercrime.gov.in" in s.url for s in r.sources)


async def test_cache_hit_and_stale_cache_when_offline():
    fake = FakeTinyFish(search=hdfc_search)
    v = make_verifier(fake)
    await v.institution_contact(inst("hdfc_bank"), "9876543210")
    n = len(fake.requests)
    again = await v.institution_contact(inst("hdfc_bank"), "9876543210")
    assert again.cached and len(fake.requests) == n and again.fetched_at
    # Expire the entry and take TinyFish down: the older real result is served, labelled.
    key = next(iter(v.cache.rows))
    payload, _ = v.cache.rows[key]
    v.cache.rows[key] = (payload, datetime.now(UTC) - timedelta(days=3))
    fake.fail_status = 503
    offline = await v.institution_contact(inst("hdfc_bank"), "9876543210")
    assert offline.cached and offline.verdict == "MISMATCH"


async def test_unavailable_never_guesses():
    v = make_verifier(FakeTinyFish(fail_status=503))
    r = await v.institution_contact(inst("hdfc_bank"), "9876543210")
    assert r.verdict == "UNAVAILABLE" and r.status == "unavailable" and r.official_contact is None


# ---- /verify/manual ---------------------------------------------------------------------------


@pytest.fixture
def verify_client(tmp_path):
    fake = FakeTinyFish(search=hdfc_search)
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "v.db",
        stt_load_on_startup=False,
        coercion_model_enabled=False,
        tinyfish_api_key="test-key",
    )
    with TestClient(create_app(settings, tinyfish_transport=httpx.MockTransport(fake))) as c:
        yield c, fake


def test_manual_hdfc_random_number_mismatch_and_critical(verify_client):
    client, fake = verify_client
    body = client.post(
        "/verify/manual",
        json={"claimed_identity_type": "bank", "claimed_identity_name": "HDFC Bank",
              "phone": "98765 43210", "asked_to": ["otp", "money", "secrecy"]},
    ).json()  # fmt: skip
    contact = next(c for c in body["checks"] if c["check"] == "institution_contact")
    assert contact["verdict"] == "MISMATCH" and contact["official_contact"] == "1800 202 6161"
    assert contact["sources"][0]["url"].startswith("https://www.hdfc.bank.in")
    assert body["level"] == "CRITICAL"
    assert body["top_reasons"][0]["code"] == "VERIFY_MISMATCH"
    assert body["next_steps"][0]["en"].startswith(
        "Hang up. Call HDFC Bank yourself on 1800 202 6161"
    )
    assert body["next_steps"][-1]["en"] == "Report at cybercrime.gov.in or call 1930."


def test_verification_alone_never_reaches_high(verify_client):
    client, _ = verify_client
    body = client.post(
        "/verify/manual",
        json={"claimed_identity_type": "bank", "claimed_identity_name": "HDFC Bank",
              "phone": "9876543210", "asked_to": []},
    ).json()  # fmt: skip
    assert body["level"] in ("LOW", "MEDIUM")


def test_privacy_personal_names_and_message_never_sent(verify_client):
    client, fake = verify_client
    secret = "Nani please send 5000 to my friend Vikram Malhotra"
    for identity_type, name in (("relative", "Rahul Sharma"), ("bank", "Rahul Sharma")):
        r = client.post(
            "/verify/manual",
            json={"claimed_identity_type": identity_type, "claimed_identity_name": name,
                  "phone": "9876543210", "asked_to": ["money"], "message_text": secret},
        )  # fmt: skip
        assert r.status_code == 200
        assert r.json()["text_analysis"]["label"]["en"].startswith("Experimental")
    sent = fake.bodies()
    assert "Rahul" not in sent and "Vikram" not in sent and "5000" not in sent


def test_invalid_inputs_rejected(verify_client):
    client, _ = verify_client
    for field, bad in (("phone", "12ab"), ("upi", "nope"), ("url", "http://10.0.0.1/a")):
        r = client.post("/verify/manual", json={"claimed_identity_type": "other", field: bad})
        assert r.status_code == 422, field


def test_without_key_risk_and_steps_still_work(client):
    body = client.post(
        "/verify/manual",
        json={"claimed_identity_type": "police", "claimed_identity_name": "CBI",
              "asked_to": ["video_call", "money", "secrecy"]},
    ).json()  # fmt: skip
    assert body["verification_status"] == "unavailable" and body["checks"] == []
    assert body["level"] in ("HIGH", "CRITICAL")
    assert any("digital arrest" in s["en"] for s in body["next_steps"])
    assert body["next_steps"][-1]["en"] == "Report at cybercrime.gov.in or call 1930."


# ---- live smoke -------------------------------------------------------------------------------

_live = Settings()
live = pytest.mark.skipif(
    _live.tinyfish_api_key is None, reason="TINYFISH_API_KEY not set (live smoke test)"
)


@live
async def test_live_hdfc_official_helpline():
    client = TinyFishClient(_live.tinyfish_api_key.get_secret_value())
    r = await Verifier(client, MemoryCache()).institution_contact(inst("hdfc_bank"), "9876543210")
    await client.close()
    assert r.status == "ok", r
    assert r.verdict == "MISMATCH" and r.official_contact and r.sources
    assert r.latency_ms < 10000


# ---- regressions: official number must not look risky (training-side cases only) ---------------

SBI_OFFICIAL = "1800 112211"


def sbi_search(query, domains):
    if "sbi.bank.in" in domains:
        return [result("https://sbi.bank.in/web/customer-care/contact-centre", "Contact Centre",
                       "SBI contact centre: customer care toll free 1800 1122 11 / 1800 425 3800")]  # fmt: skip
    if "112211" in query:
        # A news page publishing the OFFICIAL helpline next to fraud warnings: not a report.
        return [result("https://news.example.in/sbi-customer-care-number",
                       "SBI customer care number: beware of fraud",
                       "SBI customer care number 1800112211. Beware of scam calls claiming to be SBI.")]  # fmt: skip
    return []


@pytest.fixture
def sbi_client(tmp_path):
    fake = FakeTinyFish(search=sbi_search)
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "s.db",
        stt_load_on_startup=False,
        coercion_model_enabled=False,
        tinyfish_api_key="test-key",
    )
    with TestClient(create_app(settings, tinyfish_transport=httpx.MockTransport(fake))) as c:
        yield c, fake


def check(client, **body):
    r = client.post("/verify/manual", json={"claimed_identity_type": "bank", **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_official_number_nothing_ticked_is_low(sbi_client):
    client, fake = sbi_client
    body = check(client, claimed_identity_name="SBI", phone=SBI_OFFICIAL)
    contact = next(c for c in body["checks"] if c["check"] == "institution_contact")
    assert contact["verdict"] == "MATCH"
    assert "matches" in contact["note"]["en"] and "fake caller ID" in contact["note"]["en"]
    assert body["level"] == "LOW", body["contributions"]
    # The matching official number was never searched for fraud reports.
    assert not any(c["check"] == "public_reports" for c in body["checks"])
    assert not any("112211" in parse_qs(urlsplit(str(r.url)).query).get("query", [""])[0]
                   for r in fake.requests)  # fmt: skip
    assert any("call 1800 112211 yourself" in s["en"] for s in body["next_steps"])


def test_official_number_and_visit_branch_message_is_low(sbi_client):
    client, _ = sbi_client
    body = check(
        client,
        claimed_identity_name="SBI",
        phone=SBI_OFFICIAL,
        message_text="told me to visit sbi branch",
    )
    assert body["level"] == "LOW"
    assert body["protective_strength"] > 0
    assert body["text_analysis"]["protective"]
    assert any(r["code"] == "PROTECTIVE" for r in body["top_reasons"])


def test_claimed_identity_alone_stays_low(sbi_client):
    client, _ = sbi_client
    for identity, name in (("bank", "SBI"), ("police", "CBI")):
        body = check(client, claimed_identity_type=identity, claimed_identity_name=name)
        assert body["level"] == "LOW", (identity, body["coercion_score"])


def test_unknown_number_otp_and_secrecy_is_high(sbi_client):
    client, _ = sbi_client
    body = check(
        client, claimed_identity_name="SBI", phone="9876543210", asked_to=["otp", "secrecy"]
    )
    assert body["level"] in ("HIGH", "CRITICAL"), body["contributions"]
    assert any(c["verdict"] == "MISMATCH" for c in body["checks"])


def test_breakdown_fields_always_present(sbi_client):
    client, _ = sbi_client
    body = check(client, claimed_identity_name="SBI", phone=SBI_OFFICIAL)
    assert {"contributions", "coercion_score", "protective_strength", "caps_applied"} <= set(body)
    assert any(c["signal"] == "risk.coercion" for c in body["contributions"])
