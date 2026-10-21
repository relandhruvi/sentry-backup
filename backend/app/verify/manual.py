"""Manual "Check this caller": what the person reports + online verification -> risk + next steps.

Risk uses the same machinery as live calls: each reported request ("asked for an OTP") is one
piece of tactic evidence in a CallCoercion (caps, chain and CRITICAL gate apply), an optional
pasted message runs through the coercion classifier (experimental: it was trained on call
speech), then verification verdicts are combined by `apply_verification` (verification alone can
never reach HIGH).

Privacy: TinyFish only ever receives the institution (and only when it resolves to an entry in
sources.yaml), the phone, the UPI ID and the URL. Personal names, the message text and anything
about the elder are never sent.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.coercion.engine import CoercionEngine
from app.coercion.lexicon import PROTECTIVE, Hit
from app.entities.extract import Entity, EntityExtractor
from app.risk.fusion import (
    CallRisk,
    FusionConfig,
    Reason,
    apply_verification,
    load_reasons,
)
from app.risk.levels import Level, load_weights
from app.text.normalize import normalize
from app.verify.inputs import normalize_phone, normalize_upi, normalize_url
from app.verify.institutions import Institution, InstitutionOut, Resolution, default_directory
from app.verify.verifier import CheckResult, Verifier

IdentityType = Literal[
    "bank", "police", "government", "courier", "telecom", "company", "relative", "other"
]
AskedTo = Literal["otp", "money", "video_call", "secrecy", "install_app", "go_alone"]
SEARCHABLE_TYPES = {"bank", "police", "government", "courier", "telecom", "company"}


class ManualCheckRequest(BaseModel):
    claimed_identity_type: IdentityType
    claimed_identity_name: str = Field("", max_length=80)
    # From the dropdown: a directory id, or "other". Takes precedence over the typed name.
    institution_id: str | None = Field(None, max_length=64)
    phone: str | None = Field(None, max_length=24)
    upi: str | None = Field(None, max_length=320)
    url: str | None = Field(None, max_length=2048)
    asked_to: list[AskedTo] = []
    message_text: str | None = Field(None, max_length=2000)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        n = normalize_phone(v)
        if n is None:
            raise ValueError(
                "phone must be a 10-digit Indian number, toll-free 1800/1860, or E.164"
            )
        return n

    @field_validator("upi")
    @classmethod
    def _upi(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        n = normalize_upi(v)
        if n is None:
            raise ValueError("upi must look like name@bank")
        return n

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        n = normalize_url(v)
        if n is None:
            raise ValueError("url must be a public http(s) web address")
        return n


class UnknownInstitution(ValueError):
    pass


class TextAnalysis(BaseModel):
    label: dict[str, str]  # "experimental: model trained on call speech"
    mode: str  # model | lexicon_fallback
    tactics: list[str]
    protective: list[str]  # protective phrases found (lower the risk)
    model_probs: dict[str, float] | None


class ManualCheckResponse(BaseModel):
    level: Level
    score: float
    tactics: list[str]
    top_reasons: list[Reason]
    contributions: list
    verification_capped: bool
    coercion_score: float  # tactic evidence alone, after the identity-only cap
    protective_strength: float
    caps_applied: list[str]
    institution: str | None
    institution_match: Resolution
    # Shown when the claimed organisation can't be checked online (Other / unknown / ambiguous).
    institution_note: dict[str, str] | None
    checks: list[CheckResult]
    text_analysis: TextAnalysis | None
    next_steps: list[dict[str, str]]
    verification_status: str


def _form_hit(uid: str, tactic: str, weights: dict) -> Hit:
    return Hit(
        category=tactic,
        pattern_id=f"form:{uid}",
        pattern=f"reported by user: {uid}",
        weight=float(weights["form_weight"]),
        confidence=1.0,
        first_token=0,
        last_token=0,
        span=uid,
        source="form",
    )


async def _message_evidence(
    engine: CoercionEngine, text: str
) -> tuple[list[Hit], str, dict[str, float] | None]:
    """Tactic + protective hits for a pasted message, by the same rules as a live utterance
    (classifier primary, lexicon fallback, protective cues in both), without touching call state."""
    norm = normalize(text)
    lexicon_hits = engine.lexicon.match(norm)
    protective = [h for h in lexicon_hits if h.category == PROTECTIVE]
    if engine.model is not None:
        try:
            probs = await asyncio.wait_for(engine.model.ascores(text, norm), engine.model_timeout_s)
            return (
                [*engine.model.fuse(probs, lexicon_hits, text, norm), *protective],
                "model",
                probs,
            )
        except Exception:  # noqa: BLE001 - any classifier failure falls back to the lexicon
            pass
    return lexicon_hits, "lexicon_fallback", None


async def run_manual_check(
    req: ManualCheckRequest,
    engine: CoercionEngine,
    extractor: EntityExtractor,
    verifier: Verifier | None,
    budget_s: float,
) -> ManualCheckResponse:
    weights = load_weights()["manual_check"]
    texts = load_reasons()
    cfg = FusionConfig.load()

    # Resolve the claimed organisation to a trusted-directory entry. Only a resolved entry is ever
    # searched, by its canonical name; the typed text never leaves the server.
    directory = default_directory()
    typed = req.claimed_identity_name.strip()
    inst: Institution | None = None
    if req.institution_id == "other" or req.claimed_identity_type not in SEARCHABLE_TYPES:
        resolution = Resolution(status="other")
    elif req.institution_id:
        inst = directory.get(req.institution_id)
        if inst is None:
            raise UnknownInstitution(req.institution_id)
        resolution = Resolution(status="resolved", institution=InstitutionOut.of(inst))
    elif typed:
        resolution = directory.resolve(typed, req.claimed_identity_type)
        if resolution.status == "resolved":
            inst = directory.get(resolution.institution.id)
    else:
        resolution = Resolution(status="none")
    institution = inst.name if inst else None
    claims = (
        [Entity(kind="institution", value=inst.name, raw=inst.name, detail=inst.kind)]
        if inst
        else []
    )
    note_key = {"other": "other", "unknown": "unknown", "ambiguous": "ask", "suggest": "ask"}.get(
        resolution.status
    )
    if inst is not None and not inst.checkable:
        note_key = "unconfirmed"
    institution_note = (
        {
            k: v.format(name=inst.name if inst else typed)
            for k, v in texts["institution_notes"][note_key].items()
        }
        if note_key
        else None
    )

    # Verification (bounded). The phone's public-report search waits for the contact check: a
    # number that matches the institution's official helpline is not searched for "fraud"
    # (pages that publish official helplines next to fraud warnings are not reports).
    checks: list[CheckResult] = []
    if verifier is not None:

        async def contact_then_reports() -> list[CheckResult]:
            out: list[CheckResult] = []
            if inst is not None or resolution.status == "unknown":
                out.append(await verifier.institution_contact(inst, req.phone, typed))
            matched = any(c.verdict == "MATCH" for c in out)
            if req.phone and not matched:
                out.append(await verifier.public_reports("phone", req.phone))
            return out

        jobs = [
            contact_then_reports(),
            verifier.advisory(req.claimed_identity_type, list(req.asked_to)),
        ]
        for kind, value in (("upi", req.upi), ("url", req.url)):
            if value:
                jobs.append(verifier.public_reports(kind, value))
        try:
            done = await asyncio.wait_for(asyncio.gather(*jobs), budget_s)
            checks = [*done[0], *done[1:]]
        except TimeoutError:
            checks = []
    verdicts = [c.verdict for c in checks if c.status == "ok"]
    contact = next((c for c in checks if c.check == "institution_contact"), None)
    mismatch = contact if contact is not None and contact.verdict == "MISMATCH" else None
    match = contact if contact is not None and contact.verdict == "MATCH" else None

    # Tactic evidence: the reported requests, the claimed authority, and optionally the message.
    call = engine.new_call()
    text_analysis = None
    evidence_hits: list[tuple[str, list[Hit]]] = []
    if req.message_text and req.message_text.strip():
        hits, mode, probs = await _message_evidence(engine, req.message_text)
        mw = float(weights["message_weight"])
        scaled = [h if h.category == PROTECTIVE else replace(h, weight=h.weight * mw) for h in hits]
        evidence_hits.append(("message", scaled))
        text_analysis = TextAnalysis(
            label={
                "en": "Experimental: this model was trained on call speech, not written messages. "
                "Its findings count at reduced weight.",
                "hi": "प्रायोगिक: यह मॉडल कॉल की बातचीत पर सिखाया गया है, लिखे संदेशों पर नहीं। "
                "इसके नतीजों का असर कम रखा गया है।",
            },
            mode=mode,
            tactics=sorted({h.category for h in hits if h.category != PROTECTIVE and h.score > 0}),
            protective=sorted({h.span for h in hits if h.category == PROTECTIVE and h.score > 0}),
            model_probs=probs,
        )
    if req.claimed_identity_type in weights["authority_identity_types"]:
        evidence_hits.append(("form:identity", [_form_hit("identity", "AUTHORITY", weights)]))
    for a in req.asked_to:
        evidence_hits.append((f"form:asked:{a}", [_form_hit(f"asked:{a}", weights["asked_to"][a], weights)]))  # fmt: skip
    result = await call.add_evidence("form:none", 0.0, [], mismatch is not None)
    for i, (uid, hits) in enumerate(evidence_hits):
        result = await call.add_evidence(uid, float(i), hits, mismatch is not None)

    risk = CallRisk(cfg).update(result, 0.0, claims, {"verify": "manual"})
    caps = list(result.caps_applied)
    coercion_score = risk.score
    tactics = set(result.tactic_strengths)
    if tactics <= {"AUTHORITY"} and coercion_score >= cfg.levels.medium:
        # A claimed identity on its own ("it's the bank") is not a warning sign.
        coercion_score = round(cfg.levels.medium - cfg.cap_margin, 4)
        caps.append("identity_only")
    score, verify_contribs, capped = apply_verification(coercion_score, len(tactics), verdicts, cfg)
    if capped:
        caps.append("verification_alone")
    level = cfg.levels.of(score)

    reasons = list(risk.top_reasons)
    vt = texts["verify"]
    name = institution or typed
    if mismatch is not None:
        reasons.insert(0, Reason(code="VERIFY_MISMATCH", weight=1.0,
                                 **{k: v.format(name=name) for k, v in vt["reason_mismatch"].items()}))  # fmt: skip
    if match is not None:
        reasons.append(Reason(code="VERIFY_MATCH", weight=0.0,
                              **{k: v.format(name=name) for k, v in vt["reason_match"].items()}))  # fmt: skip
    if result.protective_strength > 0:
        reasons.append(Reason(code="PROTECTIVE", weight=0.0, **vt["reason_protective"]))
    for c in checks:
        if c.check == "public_reports" and c.verdict == "REPORTS_FOUND":
            kind = next(iter(c.input))
            reasons.append(Reason(code="VERIFY_REPORTS", weight=0.2, **{
                k: v.format(kind=vt["kinds"][kind][k]) for k, v in vt["reason_reports"].items()}))  # fmt: skip

    ns = texts["next_steps"]
    steps: list[dict[str, str]] = []
    if mismatch is not None and mismatch.official_contact:
        name = institution or typed
        steps.append({k: v.format(name=name, official=mismatch.official_contact)
                      for k, v in ns["mismatch"].items()})  # fmt: skip
    asked = set(req.asked_to)
    if asked & {"otp"}:
        steps.append(dict(ns["otp"]))
    if asked & {"money"}:
        steps.append(dict(ns["money"]))
    if "video_call" in asked and req.claimed_identity_type in {"police", "government"}:
        steps.append(dict(ns["video_call"]))
    if asked & {"secrecy", "go_alone"}:
        steps.append(dict(ns["alone"]))
    if "install_app" in asked:
        steps.append(dict(ns["install_app"]))
    if match is not None and match.official_contact:
        steps.append({k: v.format(official=match.official_contact) for k, v in ns["match"].items()})
    if not steps:
        steps.append(dict(ns["general"]))
    steps.append(dict(ns["report"]))

    if verifier is None:
        vstatus = "unavailable"
    elif not checks:
        vstatus = "timeout"
    elif all(c.status == "unavailable" for c in checks if c.status != "skipped"):
        vstatus = "unavailable"
    else:
        vstatus = "ok"
    return ManualCheckResponse(
        level=level,
        score=score,
        tactics=sorted(tactics),
        top_reasons=reasons[: cfg.max_reasons + 2],
        contributions=[*risk.contributions, *verify_contribs],
        coercion_score=coercion_score,
        protective_strength=result.protective_strength,
        caps_applied=caps,
        verification_capped=capped,
        institution=institution,
        institution_match=resolution,
        institution_note=institution_note,
        checks=checks,
        text_analysis=text_analysis,
        next_steps=steps,
        verification_status=vstatus,
    )
