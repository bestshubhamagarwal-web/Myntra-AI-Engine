from __future__ import annotations

import re

LETTER_RE = re.compile(r"[A-Za-z\u0900-\u097F]")


def is_empty_or_emoji_only(text: str | None) -> bool:
    if not text or not text.strip():
        return True
    return LETTER_RE.search(text) is None


def quality_score(text: str, relevance: str, reject_reason: str | None) -> float:
    if reject_reason:
        return 0.0
    length = len(text.strip())
    if length < 40:
        return 0.45
    if relevance == "inferred":
        return 0.7
    return 1.0
