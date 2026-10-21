"""Render the per-batch generator prompts (training/coercion/prompts/pNN.txt).

Each batch is one language and the standard mix (45 scam, 30 hard negatives, 25 chat); batches
rotate the scam types, hard-negative domains, personas and regions so that no two prompts ask for
the same thing. The prompt id (file name + content hash) is recorded on every generated row, and
train/val are split by prompt id.

Usage: python -m training.coercion.make_prompts
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTRUCTIONS = HERE / "generator_instructions.md"
PROMPTS = HERE / "prompts"

LANGS = ["en", "hi", "hinglish"]
BATCHES_PER_LANG = 11
MIX = {"scam": 45, "hard_negative": 30, "chat": 25}

SCAMS = [
    "digital arrest by fake CBI/ED officers over a money-laundering case",
    "fake police: parcel with drugs/fake passports in your name (courier + narcotics)",
    "bank KYC expiry / account block, asks for OTP or link",
    "relative in trouble: grandson/son in accident or arrested, voice-clone style plea",
    "electricity bill unpaid, connection cut tonight",
    "TRAI / telecom: SIM to be disconnected, Aadhaar misuse",
    "fake customs: gift parcel from abroad held, pay duty",
    "income tax refund / pension (EPFO, PF) verification",
    "tech support: computer infected, install AnyDesk, refund by mistake",
    "lottery / KBC prize / lucky draw, pay processing fee",
    "investment or stock tip group, guaranteed returns",
    "sextortion: obscene video call recorded, pay or it gets leaked",
    "fake army officer buying furniture/OLX, 'send 1 rupee to receive payment'",
    "insurance policy bonus / lapsed policy maturity",
    "wrong UPI transfer: 'I sent you money by mistake, please return it'",
    "credit card reward points expiring / limit increase",
]
HARD_NEGATIVES = [
    "real bank fraud alert that warns against sharing OTP",
    "hospital calling family about an emergency admission",
    "real police: passport or tenant verification visit",
    "delivery agent at the door asking for the delivery OTP",
    "school calling a grandparent about a child",
    "genuine family emergency (relative actually ill, needs help, no secrecy)",
    "electricity board announcing scheduled maintenance outage",
    "bank relationship manager reminding about genuine FD renewal",
    "pharmacy / doctor clinic appointment reminder, take medicine now",
    "society secretary: urgent water tank cleaning, pay maintenance at office",
    "real insurance agent: policy renewal due next month",
    "grandchild warning grandparent about scam calls",
    "family lawyer reminding about a property paperwork deadline",
    "gas cylinder booking / DTH recharge reminder",
    "cousin asking to transfer money for a shared family function, openly",
    "temple / NGO donation request, polite, no pressure",
]
CHATS = [
    "weather, health and family news",
    "cricket, TV serials, festival plans",
    "recipes, shopping, neighbours",
    "travel plans, train tickets, weddings",
    "grandchildren's studies, pets, garden",
]
PERSONAS = [
    "retired teacher in Lucknow",
    "widowed homemaker in Pune",
    "retired bank clerk in Chennai who speaks Hindi as a second language",
    "ex-army man in Dehradun",
    "pensioner couple in Jaipur",
    "retired government officer in Bhopal",
    "shopkeeper's mother in Patna",
    "retired doctor in Kolkata",
    "grandmother in Ahmedabad living alone",
    "retired professor in Delhi",
    "farmer's father in a village near Ludhiana",
]
STAGES = [
    "openings and trust building",
    "accusations and pressure",
    "isolation and secrecy instructions",
    "the money or credential ask and follow-ups after payment",
]


def batch_specs() -> list[dict]:
    scams = itertools.cycle(SCAMS)
    negs = itertools.cycle(HARD_NEGATIVES)
    chats = itertools.cycle(CHATS)
    specs = []
    n = 0
    for i in range(BATCHES_PER_LANG):
        for lang in LANGS:
            n += 1
            specs.append(
                {
                    "prompt_id": f"p{n:02d}",
                    "lang": lang,
                    "scams": [next(scams) for _ in range(3)],
                    "hard_negatives": [next(negs) for _ in range(3)],
                    "chat": next(chats),
                    "persona": PERSONAS[(i + LANGS.index(lang) * 4) % len(PERSONAS)],
                    "stage_focus": STAGES[(i + LANGS.index(lang)) % len(STAGES)],
                }
            )
    return specs


def render(spec: dict) -> str:
    lines = [
        f"BATCH {spec['prompt_id']}: write exactly 100 rows, all with lang={spec['lang']}.",
        f"- {MIX['scam']} rows category=scam, mostly about: " + "; ".join(spec["scams"]) + ".",
        f"  Give extra weight to the stage: {spec['stage_focus']}, but cover all stages.",
        f"- {MIX['hard_negative']} rows category=hard_negative (all BENIGN), mostly about: "
        + "; ".join(spec["hard_negatives"])
        + ".",
        f"- {MIX['chat']} rows category=chat (all BENIGN): {spec['chat']}.",
        f"- The listener is often (not always) a {spec['persona']}.",
        "- About a third of rows have spoken_noise=true.",
    ]
    return "\n".join(lines) + "\n"


def prompt_hash(text: str) -> str:
    instructions = INSTRUCTIONS.read_text(encoding="utf-8")
    return hashlib.sha256((instructions + text).encode()).hexdigest()[:12]


def main() -> None:
    PROMPTS.mkdir(exist_ok=True)
    for spec in batch_specs():
        text = render(spec)
        (PROMPTS / f"{spec['prompt_id']}.txt").write_text(text, encoding="utf-8")
    print(f"wrote {len(batch_specs())} prompts to {PROMPTS}")


if __name__ == "__main__":
    main()
