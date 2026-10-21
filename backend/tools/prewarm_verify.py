"""Pre-warm the verification cache with real TinyFish results for common institutions.

Runs the real institution_contact check for each institution below and the advisory check for
every scenario, writing into the app's SQLite cache. If Wi-Fi drops during the demo, /verify/manual
serves these real, sourced results labelled with their fetch time. Nothing is invented: a check
that fails here simply isn't cached.

Usage: python -m tools.prewarm_verify [--force]
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.storage import Storage
from app.verify.institutions import default_directory
from app.verify.tinyfish_client import TinyFishClient
from app.verify.verifier import Verifier, load_sources

# Directory ids (app/verify/sources.yaml) of the institutions most often impersonated.
INSTITUTIONS = [
    "sbi", "hdfc_bank", "icici_bank", "axis_bank", "punjab_national_bank", "bank_of_baroda",
    "kotak_mahindra_bank", "punjab_sind_bank", "canara_bank", "union_bank_of_india",
    "india_post", "fedex", "dtdc", "airtel", "jio", "rbi",
]  # fmt: skip


async def main_async(force: bool) -> int:
    s = get_settings()
    if s.tinyfish_api_key is None:
        print("TINYFISH_API_KEY is not set in backend/.env; nothing to pre-warm.")
        return 1
    storage = Storage(s.resolved_db_path, store_transcripts=s.store_transcripts)
    await storage.open()
    client = TinyFishClient(
        s.tinyfish_api_key.get_secret_value(),
        timeout_s=s.tinyfish_timeout_s,
        search_per_minute=s.tinyfish_search_per_minute,
        fetch_per_minute=s.tinyfish_fetch_per_minute,
        location=s.tinyfish_location,
    )
    verifier = Verifier(client, storage, 0 if force else s.verify_cache_ttl_s)
    failures = 0
    print(f"{'institution':24s} {'status':11s} {'official helpline':18s} {'ms':>6s}  source")
    directory = default_directory()
    for iid in INSTITUTIONS:
        inst = directory.get(iid)
        name = inst.name
        r = await verifier.institution_contact(inst, None)
        failures += r.status != "ok" or not r.official_contact
        src = r.sources[0].url if r.sources else "-"
        tag = " (cached)" if r.cached else ""
        print(
            f"{name:24s} {r.status:11s} {r.official_contact or '-':18s} "
            f"{r.latency_ms:6.0f}  {src}{tag}"
        )
    print()
    for sc in load_sources()["scenarios"]:
        r = await verifier.advisory(
            (sc["when"]["identity_types"] or ["other"])[0], sc["when"]["asked_to_any"]
        )
        src = r.sources[0].url if r.sources else "-"
        print(f"advisory {sc['id']:16s} {r.verdict:15s} {r.latency_ms:6.0f}  {src}")
    await client.close()
    await storage.close()
    print(f"\n{failures} institution(s) without a sourced official number")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="refetch even if cached")
    args = parser.parse_args()
    raise SystemExit(1 if asyncio.run(main_async(args.force)) else 0)


if __name__ == "__main__":
    main()
