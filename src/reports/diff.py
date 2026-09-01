"""Diff theme_metrics snapshots by theme_id (EC-RP-01/02/04)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.db.repository import ThemeMetricsSnapshot


def _by_theme(rows: list[ThemeMetricsSnapshot]) -> dict[UUID, ThemeMetricsSnapshot]:
    return {row.theme_id: row for row in rows if row.slice_kind == "global"}


def diff_theme_metrics(
    current: list[ThemeMetricsSnapshot],
    previous: list[ThemeMetricsSnapshot] | None,
    *,
    newly_unavailable: list[str],
) -> dict[str, Any]:
    cur = _by_theme(current)
    prev = _by_theme(previous or [])
    first_week = not prev
    rising: list[dict[str, Any]] = []
    new_themes: list[dict[str, Any]] = []
    for theme_id, snap in cur.items():
        card = {
            "theme_id": str(theme_id),
            "mention_count": snap.mention_count,
            "share_of_voice": snap.share_of_voice,
            "impact_score": snap.impact_score,
            "data_confidence": snap.data_confidence,
        }
        old = prev.get(theme_id)
        if old is None:
            new_themes.append(card)
            continue
        delta = None
        if old.share_of_voice and old.share_of_voice > 0:
            delta = (snap.share_of_voice - old.share_of_voice) / old.share_of_voice
        elif snap.share_of_voice > 0 and old.share_of_voice == 0:
            delta = None
        card = {**card, "sov_delta_ratio": delta, "previous_share_of_voice": old.share_of_voice}
        if delta is not None and delta >= 0.15:
            rising.append(card)
    ranked = sorted(cur.values(), key=lambda r: r.impact_score or 0.0, reverse=True)
    top = [
        {
            "theme_id": str(row.theme_id),
            "mention_count": row.mention_count,
            "share_of_voice": row.share_of_voice,
            "impact_score": row.impact_score,
            "data_confidence": row.data_confidence,
            "unavailable_sources": list(row.unavailable_sources),
        }
        for row in ranked[:8]
    ]
    return {
        "first_week": first_week,
        "baseline": first_week,
        "do_not_interpret_as_volume_drop": bool(newly_unavailable),
        "newly_unavailable_sources": list(newly_unavailable),
        "new_theme_ids": [row["theme_id"] for row in new_themes],
        "rising": rising,
        "top_themes": top,
        "percent_change_available": not first_week and not newly_unavailable,
    }
