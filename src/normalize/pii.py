from __future__ import annotations

import hashlib
import hmac
import re

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# Indian mobiles, optional +91 / 0 prefix, and generic 10+ digit runs.
PHONE_RE = re.compile(
    r"(?:(?:\+91[\s\-]?)|0)?[6-9]\d{9}\b|\b\d{10,13}\b"
)
ORDER_RE = re.compile(
    r"\b(?:order\s*(?:id|no\.?|number|#)?\s*[:#\-]?\s*|ord(?:er)?\s*#\s*)[A-Z0-9\-]{6,}\b"
    r"|\b(?:MYN|MYNTRA)[\-_][A-Z0-9]{4,}\b"
    r"|\b[A-Z]{2,}\d{8,}\b",
    re.IGNORECASE,
)
PIN_ADDRESS_RE = re.compile(
    r"\b(?:flat|house|plot|street|nagar|road|sector|pin(?:code)?|pincode)\b.{0,40}\b\d{6}\b",
    re.IGNORECASE,
)
# After emails are stripped, leftover @mentions / Reddit handles must not reach analysis text.
HANDLE_RE = re.compile(r"\bu/[A-Za-z0-9_\-]{2,}\b|@[A-Za-z0-9_]{2,}\b", re.IGNORECASE)

PII_PLACEHOLDERS = ("[EMAIL]", "[PHONE]", "[ORDER_ID]", "[ADDRESS]", "[HANDLE]")


def hash_author(username: str | None, secret: str) -> str | None:
    if not username or not str(username).strip():
        return None
    digest = hmac.new(
        secret.encode("utf-8"),
        str(username).strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def scrub_pii(text: str | None) -> str:
    if not text:
        return ""
    out = EMAIL_RE.sub("[EMAIL]", text)
    out = ORDER_RE.sub("[ORDER_ID]", out)
    out = PIN_ADDRESS_RE.sub("[ADDRESS]", out)
    out = PHONE_RE.sub("[PHONE]", out)
    out = HANDLE_RE.sub("[HANDLE]", out)
    return out


def contains_unscrubbed_pii(text: str | None) -> bool:
    if not text:
        return False
    if EMAIL_RE.search(text):
        return True
    if ORDER_RE.search(text):
        return True
    if PIN_ADDRESS_RE.search(text):
        return True
    if PHONE_RE.search(text):
        return True
    if HANDLE_RE.search(text):
        return True
    return False
