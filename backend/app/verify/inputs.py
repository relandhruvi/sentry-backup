"""Validation and normalization of what may be sent to verification: phone, UPI ID, URL."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_SEPARATORS = re.compile(r"[\s\-().]")
_UPI = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,255}@[a-zA-Z][a-zA-Z0-9]{1,63}$")
_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
# Phone-like runs in page text: digits with optional + and separators.
_PHONE_IN_TEXT = re.compile(r"(?<![\w/])\+?\d[\d \-()]{7,18}\d(?![\w/])")


def normalize_phone(raw: str) -> str | None:
    """Canonical Indian number, or None if invalid.

    - toll-free 1800/1860 + 4-7 digits -> 8-11 digits ("18002026161", "18001600")
    - mobile 6-9 + 9 digits (optionally +91 / 0 prefixed) -> 10 digits
    - landline 0 + STD + number (10 digits after the 0) -> 10 digits without the 0
    - other E.164 (+ and 8-15 digits, not +91) -> "+<digits>"
    """
    s = _SEPARATORS.sub("", raw.strip())
    if not s:
        return None
    if s.startswith("+"):
        digits = s[1:]
        if not digits.isdigit() or not 8 <= len(digits) <= 15:
            return None
        if not digits.startswith("91"):
            return "+" + digits
        s = digits[2:]
    if not s.isdigit():
        return None
    if re.fullmatch(r"18[06]0\d{4,7}", s):
        return s
    if len(s) == 12 and s.startswith("91"):
        s = s[2:]
    if len(s) == 11 and s.startswith("0"):
        s = s[1:]
    if len(s) == 10 and s[0] in "6789":
        return s
    if len(s) == 10 and s[0] in "12345":  # landline: STD code + number
        return s
    return None


def phones_in_text(text: str) -> list[tuple[str, int]]:
    """(canonical number, character offset) for every valid number in free text."""
    out = []
    for m in _PHONE_IN_TEXT.finditer(text):
        n = normalize_phone(m.group())
        if n:
            out.append((n, m.start()))
    return out


def display_phone(n: str) -> str:
    if re.fullmatch(r"18[06]0\d{7}", n):
        return f"{n[:4]} {n[4:7]} {n[7:]}"
    if re.fullmatch(r"18[06]0\d{4,6}", n):
        return f"{n[:4]} {n[4:]}"
    if len(n) == 10 and n[0] in "6789":
        return f"+91 {n[:5]} {n[5:]}"
    if len(n) == 10:
        return f"0{n}"
    return n


def normalize_upi(raw: str) -> str | None:
    s = raw.strip().lower()
    return s if _UPI.match(s) else None


def normalize_url(raw: str) -> str | None:
    """https URL with a public hostname, or None. Bare domains get https://."""
    s = raw.strip()
    if not s:
        return None
    if "://" not in s:
        s = "https://" + s
    try:
        parts = urlsplit(s)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return None
    try:
        ipaddress.ip_address(host)
        return None  # raw IPs (including private ones) are never sent
    except ValueError:
        pass
    if host == "localhost" or not _HOST.match(host):
        return None
    return f"{parts.scheme}://{host}{parts.path or ''}"
