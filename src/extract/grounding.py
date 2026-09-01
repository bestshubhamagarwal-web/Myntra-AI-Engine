"""Ground Groq extraction against the source text (Architecture §8.2, EC-EX-*)."""

from __future__ import annotations

import re

from src.extract.schema import (
    ComparisonBehavior,
    ExtractionPayload,
    IntentMode,
    IntentTag,
    VerbatimQuote,
)
from src.normalize.pii import scrub_pii

PRICE_CUES = re.compile(
    r"(?:₹|rs\.?|inr|\bmrp\b|\bprice\b|\bpriced\b|\bpricing\b|\bsale\b|\bdiscount\b|"
    r"\bcheaper\b|\bexpensive\b|\bcostly\b|\bcost\b|\bdeal\b|\boffer\b|\bcashback\b|"
    r"\bcoupon\b|\bcheap\b)",
    re.IGNORECASE,
)
COMPARISON_CUES = re.compile(
    r"\b(?:vs\.?|versus|compared?|comparison|better than|worse than|cheaper than|"
    r"instead of)\b|ajio|nykaa|flipkart|meesho",
    re.IGNORECASE,
)
PASSIVE_CUES = re.compile(
    r"mood\s*board|someday|maybe later|inspiration|bookmark|"
    r"not buying now|no plan to buy|saving (?:this )?for later|"
    r"wishlist as (?:a )?mood",
    re.IGNORECASE,
)
NEAR_CUES = re.compile(
    r"(?<!not )\b(?:buy(?:ing)? (?:today|now|this week)|checkout|order now|"
    r"need it (?:for|by)|wearing (?:this )?(?:saturday|tomorrow|tonight))\b",
    re.IGNORECASE,
)

KNOWN_COMPETITORS = (
    "AJIO",
    "Nykaa Fashion",
    "Nykaa",
    "Flipkart Fashion",
    "Flipkart",
    "Meesho",
    "Amazon",
)


def repair_quotes(payload: ExtractionPayload, text: str) -> list[VerbatimQuote]:
    """Keep only exact (or offset-aligned) substrings of text_original. EC-EX-07/08."""
    repaired: list[VerbatimQuote] = []
    seen: set[tuple[int, int, str]] = set()
    for quote in payload.verbatim_quotes:
        aligned = _align_quote(quote, text)
        if aligned is None:
            continue
        span = scrub_pii(aligned.span)
        if span != aligned.span:
            realigned = _align_quote(VerbatimQuote(span=span), text)
            if realigned is None:
                continue
            aligned = realigned
        key = (aligned.start_char or 0, aligned.end_char or 0, aligned.span)
        if key in seen:
            continue
        seen.add(key)
        repaired.append(aligned)
    return repaired


def _align_quote(quote: VerbatimQuote, text: str) -> VerbatimQuote | None:
    span = (quote.span or "").strip()
    start, end = quote.start_char, quote.end_char
    if start is not None and end is not None and 0 <= start < end <= len(text):
        extracted = text[start:end]
        if extracted.strip() and (
            extracted == span
            or (span and extracted.lower() == span.lower())
            or (span and extracted == span)
        ):
            return VerbatimQuote(span=extracted, start_char=start, end_char=end)

    if span:
        idx = text.find(span)
        if idx == -1:
            idx = text.lower().find(span.lower())
            if idx != -1:
                span = text[idx : idx + len(span)]
        if idx != -1:
            return VerbatimQuote(span=span, start_char=idx, end_char=idx + len(span))
    return None


def _ground_competitors(names: list[str], text: str) -> list[str]:
    lowered = text.lower()
    out: list[str] = []
    for name in names:
        token = name.strip()
        if not token:
            continue
        if token.lower() in lowered and token not in out:
            out.append(token)
    for known in KNOWN_COMPETITORS:
        if known.lower() in lowered and known not in out:
            # Only add known names that Groq already listed, or that appear in text
            # when Groq listed a close variant.
            if any(known.lower() in n.lower() or n.lower() in known.lower() for n in names):
                if known not in out:
                    out.append(known)
    return out


def ground_payload(payload: ExtractionPayload, text: str) -> ExtractionPayload:
    """Apply no-guessing guards. Does not replace text_original."""
    data = payload.model_copy(deep=True)
    data.verbatim_quotes = repair_quotes(data, text)

    if data.intent_tag == IntentTag.price_watch and not PRICE_CUES.search(text):
        data.intent_tag = IntentTag.unknown
        data.extraction_confidence = min(data.extraction_confidence, 0.35)

    if data.comparison_behavior == ComparisonBehavior.true and not COMPARISON_CUES.search(text):
        data.comparison_behavior = ComparisonBehavior.unknown

    data.entities.competitor_mentions = _ground_competitors(
        data.entities.competitor_mentions, text
    )

    passive = bool(PASSIVE_CUES.search(text))
    near = bool(NEAR_CUES.search(text))
    if passive and near:
        data.intent_mode = IntentMode.mixed
    elif passive and not near and data.intent_mode == IntentMode.near_term_purchase:
        data.intent_mode = IntentMode.passive_bookmark

    if data.intent_tag in {IntentTag.bookmark, IntentTag.mood_board} and not near:
        if data.intent_mode == IntentMode.near_term_purchase:
            data.intent_mode = IntentMode.passive_bookmark

    # Thin compliments must not become a guessed wishlist intent (EC-EX-04).
    stripped = re.sub(r"[^a-zA-Z]+", " ", text).strip().lower()
    if stripped in {"nice dress", "nice app", "good", "nice"}:
        data.intent_tag = IntentTag.not_applicable
        data.intent_mode = IntentMode.unknown
        data.extraction_confidence = min(data.extraction_confidence, 0.3)

    return data
