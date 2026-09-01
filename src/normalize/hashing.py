from __future__ import annotations

import hashlib
import re


def whitespace_normalized(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip().lower()


def content_hash(text: str | None) -> str:
    payload = whitespace_normalized(text).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
