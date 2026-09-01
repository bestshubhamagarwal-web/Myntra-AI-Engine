"""Frozen metric formulas (Architecture §8.4–8.6).

Phase 5 Query API and Copilot must use these (or the matching SQL functions
in migrations/005_phase4.sql). The UI never recomputes SoV / impact / confidence.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable, Mapping, Sequence

DENOMINATOR_DEFINITION = "eligible_normalized_after_relevance_and_quality"

NEGATIVE_SENTIMENT = frozenset({"frustration", "doubt"})
MIXED_SENTIMENT = frozenset({"mixed"})

CONFIDENCE_ANSWER = 0.60
CONFIDENCE_CAVEAT = 0.35

TREND_UP = 1.15
TREND_DOWN = 0.85


def clip01(value: float | None) -> float:
    if value is None or isinstance(value, bool):
        number = 0.0 if value is None else float(value)
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return number


def share_of_voice(mention_count: int, eligible_corpus_count: int) -> float:
    """SoV = mention_count / eligible corpus for the same slice. Never impute."""
    if eligible_corpus_count is None or eligible_corpus_count <= 0:
        return 0.0
    return float(mention_count) / float(eligible_corpus_count)


def data_confidence(
    mention_count: int,
    source_diversity: int,
    mean_extraction_confidence: float | None,
    *,
    c_max: int = 200,
    s_max: int = 4,
) -> float:
    """Architecture §8.5. Prototype constants C_max=200, S_max=4."""
    cap = max(int(c_max or 1), 1)
    s_cap = max(int(s_max or 1), 1)
    mean_c = clip01(mean_extraction_confidence)
    value = (
        0.4 * math.log1p(max(int(mention_count or 0), 0)) / math.log1p(cap)
        + 0.3 * min(max(int(source_diversity or 0), 0) / s_cap, 1.0)
        + 0.3 * mean_c
    )
    return clip01(value)


def impact_score(
    sov: float | None,
    sentiment_severity: float | None,
    segment_breadth: float | None,
    confidence: float | None,
) -> float:
    """SoV × blocking severity × segment breadth × data_confidence. Zero is valid."""
    return (
        clip01(sov)
        * clip01(sentiment_severity)
        * clip01(segment_breadth)
        * clip01(confidence)
    )


def blocking_severity(primary: str | None, severity: float | None) -> float:
    """Impact uses *blocking* severity (EC-Q-10). Delight/trust do not rank as drop-off."""
    sev = clip01(severity)
    tag = (primary or "").strip().lower()
    if tag in NEGATIVE_SENTIMENT:
        return sev
    if tag in MIXED_SENTIMENT:
        return 0.5 * sev
    return 0.0


def quality_weighted_mean(pairs: Iterable[tuple[float, float | None]]) -> float:
    """Quality-weighted mean of values. Missing quality → weight 1. Empty → 0."""
    num = 0.0
    den = 0.0
    for value, quality in pairs:
        weight = 1.0 if quality is None else max(float(quality), 0.0)
        num += float(value) * weight
        den += weight
    if den <= 0:
        return 0.0
    return num / den


def segment_concentration(counts: Mapping[str, int]) -> float:
    """Top-segment share, including `unknown`. 0 if no mentions."""
    total = sum(max(int(n), 0) for n in counts.values())
    if total <= 0:
        return 0.0
    return max(max(int(n), 0) for n in counts.values()) / float(total)


def segment_breadth(counts: Mapping[str, int]) -> float:
    """1 - max segment share. Concentrated themes have lower breadth."""
    return clip01(1.0 - segment_concentration(counts))


def independent_source_density(
    author_hashes: Iterable[str | None],
    source_types: Iterable[str | None],
) -> int:
    """Distinct hashed authors (skip missing) + distinct source_types.

    Do not invent author_hash='unknown' as a mega-user (EC-Q-06).
    """
    authors = {item for item in author_hashes if item}
    sources = {item for item in source_types if item}
    return len(authors) + len(sources)


def iso_week_bucket(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def trend_direction(bucket_counts: Sequence[tuple[str, int]]) -> str | None:
    """rising | flat | declining. None when fewer than 2 time buckets (not 'flat')."""
    series = [(key, int(count)) for key, count in bucket_counts if key and key != "unknown"]
    if len(series) < 2:
        return None
    prev = series[-2][1]
    last = series[-1][1]
    if prev == 0 and last == 0:
        return None
    if prev == 0:
        return "rising" if last > 0 else None
    ratio = last / prev
    if ratio >= TREND_UP:
        return "rising"
    if ratio <= TREND_DOWN:
        return "declining"
    return "flat"


def confidence_band(confidence: float | None) -> str:
    """EV-4-20/21/22 — Copilot policy preview (Phase 5 enforces on the API)."""
    value = clip01(confidence)
    if value >= CONFIDENCE_ANSWER:
        return "answer"
    if value >= CONFIDENCE_CAVEAT:
        return "caveat"
    return "decline"


# Play/App Store are the only connectors the dashboard treats as an outage.
# Catalog platforms (Instagram, Quora, …) and unconfigured APIs stay off Overview.
DASHBOARD_OUTAGE_SOURCES = frozenset({"play_store", "app_store"})


def unavailable_source_types(
    statuses: Iterable[object], *, dashboard: bool = False
) -> list[str]:
    """Sources that are not currently live. Never imputed as zero volume.

    When dashboard=True, only Play/App Store outages are returned so the UI
    does not list ToS-out-of-scope or unconfigured catalogs as 'unavailable'.
    """
    out: list[str] = []
    for row in statuses:
        source_type = getattr(row, "source_type", None)
        status = getattr(row, "status", None)
        if not source_type:
            continue
        if status != "live":
            name = str(source_type)
            if dashboard and name not in DASHBOARD_OUTAGE_SOURCES:
                continue
            out.append(name)
    return sorted(set(out))
