from __future__ import annotations

import re

from src.normalize.pii import contains_unscrubbed_pii

# EV-1-05 rubric: leftover @, 10-digit phones, order-id-like tokens, plaintext handles.
PHONE_LEFTOVER = re.compile(r"(?:(?:\+91[\s\-]?)|0)?[6-9]\d{9}\b|\b\d{10,13}\b")
ORDER_LEFTOVER = re.compile(
    r"\b(?:order\s*(?:id|no\.?|number|#)?\s*[:#\-]?\s*|ord(?:er)?\s*#\s*)[A-Z0-9\-]{6,}\b"
    r"|\b(?:MYN|MYNTRA)[\-_][A-Z0-9]{4,}\b",
    re.IGNORECASE,
)
HANDLE_LEFTOVER = re.compile(r"\bu/[A-Za-z0-9_\-]{2,}\b", re.IGNORECASE)

USERNAME_FIELDS = frozenset(
    {"username", "user_name", "userName", "author", "display_name", "handle"}
)


def spotcheck_text_failures(text: str | None) -> list[str]:
    """Return rubric violations for a stored analysis string (should already be scrubbed)."""
    body = text or ""
    failures: list[str] = []
    if "@" in body:
        failures.append("at_sign")
    if contains_unscrubbed_pii(body):
        failures.append("unscrubbed_pii")
    if PHONE_LEFTOVER.search(body):
        failures.append("phone")
    if ORDER_LEFTOVER.search(body):
        failures.append("order_id")
    if HANDLE_LEFTOVER.search(body):
        failures.append("handle")
    return failures


def analysis_record_username_fields(record: object) -> list[str]:
    names: list[str] = []
    if hasattr(record, "__dataclass_fields__"):
        names.extend(record.__dataclass_fields__.keys())
    elif hasattr(record, "__dict__"):
        names.extend(getattr(record, "__dict__", {}).keys())
    return [n for n in names if n in USERNAME_FIELDS]
