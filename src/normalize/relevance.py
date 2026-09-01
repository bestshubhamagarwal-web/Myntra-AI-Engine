from __future__ import annotations

import re
from typing import Any

SHOPPING_EXPLICIT = re.compile(
    r"\b(wishlist|wish[\s\-]?list|add(?:ed)? to (?:bag|cart|wishlist)|"
    r"cart|shopping bag|size(?:s| chart| guide)?|sizing|runs? (?:small|large)|"
    r"return(?:s|ed|ing)?|exchange|refund|kurta|kurti|lehenga|saree|sari|"
    r"ethnic|footwear|sneaker|sandal|try[\s\-]?on|haul|fit issue|"
    r"cash on delivery|\bcod\b)\b",
    re.IGNORECASE,
)

SHOPPING_INFERRED = re.compile(
    r"\b(dress|jeans|shoe|top|shirt|fabric|material|quality|delivery|"
    r"product|price|sale|discount|coupon|fashion|wear|cloth|apparel|"
    r"brand|order placed|unbox)\b",
    re.IGNORECASE,
)

APP_QUALITY = re.compile(
    r"\b(crash(?:es|ed|ing)?|force close|keeps stopping|cannot open|"
    r"black screen|freeze|frozen|laggy|too slow|login|otp|otp not|"
    r"update (?:ruined|broke)|after (?:the )?update|permission|"
    r"notification|battery drain|won't load|not opening)\b",
    re.IGNORECASE,
)

OFF_TOPIC_PERSON = re.compile(
    r"\b(my friend myntra|named myntra|person (?:called|named) myntra|"
    r"myntra (?:is|was) (?:a )?(?:girl|boy|person|friend))\b",
    re.IGNORECASE,
)

BOILERPLATE = re.compile(
    r"^\s*(i used this app to|this app is (?:good|nice|ok|best|great)\.?|"
    r"nice app\.?|good app\.?|best app\.?)\s*$",
    re.IGNORECASE,
)

MYNTRA = re.compile(r"\bmyntra\b", re.IGNORECASE)
REMOVED_BODY = re.compile(
    r"^\s*(\[deleted\]|\[removed\]|deleted|removed)\s*$",
    re.IGNORECASE,
)
APP_STORE_SOURCES = {"play_store", "app_store"}
SOCIAL_SOURCES = {"reddit", "youtube", "x", "quora", "forum"}
PARENT_TITLE_KEYS = ("video_title", "thread_title", "title", "submission_title")


def parent_title_text(parent_context: dict[str, Any] | None) -> str:
    if not parent_context:
        return ""
    parts = []
    for key in PARENT_TITLE_KEYS:
        value = parent_context.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def is_removed_body(text: str | None) -> bool:
    return bool(REMOVED_BODY.match((text or "").strip()))


def classify_relevance(
    text: str | None,
    *,
    source_type: str = "play_store",
    parent_context: dict[str, Any] | None = None,
) -> tuple[str, str | None]:
    """
    Returns (myntra_relevance, reject_reason).
    reject_reason is set only when relevance == reject.
    """
    body = (text or "").strip()
    parent_title = parent_title_text(parent_context)
    combined = f"{parent_title}\n{body}".strip()

    if source_type == "reddit" and (
        is_removed_body(body) or is_removed_body((text or "").strip())
    ):
        return "reject", "removed"

    if OFF_TOPIC_PERSON.search(body) or OFF_TOPIC_PERSON.search(combined):
        return "reject", "off_topic"

    if source_type in SOCIAL_SOURCES:
        if not MYNTRA.search(combined):
            return "reject", "off_topic"
        # Video/thread about a competitor only — even if our seed query mentioned Myntra.
        if parent_title and not MYNTRA.search(parent_title) and not MYNTRA.search(body):
            return "reject", "off_topic"

    shopping_explicit = bool(SHOPPING_EXPLICIT.search(body) or SHOPPING_EXPLICIT.search(combined))
    shopping_inferred = bool(SHOPPING_INFERRED.search(body) or SHOPPING_INFERRED.search(combined))
    app_quality = bool(APP_QUALITY.search(body))

    if shopping_explicit:
        return "explicit", None
    if shopping_inferred and app_quality:
        return "inferred", None
    if shopping_inferred:
        return "inferred", None
    if app_quality:
        return "reject", "app_quality"
    if BOILERPLATE.match(body):
        return "reject", "boilerplate"
    # Play / App Store reviews are still Myntra-app speech; keep weak signal rather than emptying the corpus.
    if source_type in APP_STORE_SOURCES and len(body) >= 20:
        return "inferred", None
    if source_type in APP_STORE_SOURCES:
        return "reject", "no_signal"
    if source_type in SOCIAL_SOURCES and MYNTRA.search(body) and len(body) >= 40:
        return "inferred", None
    if source_type in SOCIAL_SOURCES and MYNTRA.search(parent_title) and len(body) >= 40:
        return "inferred", None
    return "reject", "off_topic"
