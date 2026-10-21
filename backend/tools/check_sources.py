"""Re-check the bank entries in app/verify/sources.yaml against RBI's official bank list.

Every bank we list must appear on RBI's "Banks in India" page with exactly the confirmed domain we
use. Banks RBI lists outside our scope (foreign, regional rural, local area) are counted, not
failed. Run before a demo or when RBI updates its list.

Usage: python -m tools.check_sources
"""

from __future__ import annotations

import html
import re
import sys

import httpx

from app.verify.verifier import load_sources

RBI_BANKS = "https://www.rbi.org.in/commonperson/English/Scripts/BanksInIndia.aspx"


def rbi_links() -> dict[str, str]:
    page = httpx.get(RBI_BANKS, timeout=30, headers={"User-Agent": "ScamShield source check"}).text
    out = {}
    for m in re.finditer(
        r'<a [^>]*href="(https?://[^"]+\.bank\.in[^"]*)"[^>]*>(.*?)</a>', page, re.S
    ):
        name = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", "", m.group(2)))).strip()
        out[re.sub(r"^https?://(www\.)?", "", m.group(1)).split("/")[0]] = name
    return out


def main() -> None:
    listed = rbi_links()
    problems = 0
    ours = set()
    for inst in load_sources()["institutions"]:
        if inst["kind"] != "bank":
            continue
        for d in inst["domains"]:
            if not d["confirmed"]:
                continue
            ours.add(d["domain"])
            if d["domain"] not in listed:
                problems += 1
                print(f"NOT ON RBI LIST  {inst['name']:32s} {d['domain']}")
    others = sorted(name for dom, name in listed.items() if dom not in ours)
    print(f"{len(ours)} confirmed bank domains checked: {problems} not on RBI's list")
    print(
        f"RBI also lists {len(others)} banks outside our scope (foreign, regional rural, local "
        f"area banks), e.g. {', '.join(others[:4])}"
    )
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
