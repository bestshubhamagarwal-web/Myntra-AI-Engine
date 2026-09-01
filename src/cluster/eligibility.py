"""Who may enter HDBSCAN (Architecture §8.3, EC-CL-02, EC-CL-11)."""

from __future__ import annotations

from src.db.repository import ExtractionRecord, NormalizedRecord

EMPTY_INTENT = frozenset({"", "unknown"})
NOT_APPLICABLE = "not_applicable"


def is_cluster_eligible(
    extraction: ExtractionRecord | None,
    normalized: NormalizedRecord | None = None,
) -> bool:
    """Exclude failed JSON, not_applicable, and empty friction+intent."""
    if extraction is None:
        return False
    if normalized is not None and not normalized.eligible:
        return False
    if extraction.extraction_status != "ok" or not extraction.metrics_eligible:
        return False
    intent = (extraction.intent_tag or "").strip().lower()
    frictions = [tag for tag in (extraction.friction_tags or []) if str(tag).strip()]
    if intent == NOT_APPLICABLE:
        return False
    if not frictions and intent in EMPTY_INTENT:
        return False
    return True


def quote_spans(extraction: ExtractionRecord | None) -> list[str]:
    if extraction is None:
        return []
    spans: list[str] = []
    for item in extraction.verbatim_quotes or []:
        if not isinstance(item, dict):
            continue
        span = str(item.get("span") or item.get("text") or "").strip()
        if span:
            spans.append(span)
    return spans
