"""Copilot grounding: assistant numbers must come from tool JSON (EC-CO-17)."""

from __future__ import annotations

import json
import re
from typing import Any

UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
ISO_WEEK_RE = re.compile(r"\b(20\d{2})-W\d{2}\b")
ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
NUMBER_RE = re.compile(r"(?<![A-Za-z])(\d+(?:\.\d+)?)(%?)")

ALLOWED_BARE = {1, 2, 3, 4, 5, 6, 7, 8, 9}  # Q1–Q9 / list counts


def collect_tool_numbers(payload: Any) -> set[float]:
    found: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, int):
            found.add(float(node))
            return
        if isinstance(node, float):
            found.add(node)
            if 0.0 <= node <= 1.0:
                found.add(round(node * 100.0, 6))
            return
        if isinstance(node, str):
            for match in NUMBER_RE.finditer(node):
                value = float(match.group(1))
                found.add(value)
                if match.group(2) == "%":
                    found.add(value / 100.0)
            return
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
            return
        if isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(payload)
    return found


def extract_answer_numbers(text: str) -> list[float]:
    if not text:
        return []
    stripped = UUID_RE.sub(" ", text)
    stripped = ISO_WEEK_RE.sub(" ", stripped)
    stripped = ISO_DATE_RE.sub(" ", stripped)
    stripped = re.sub(r"\bQ[1-9]\b", " ", stripped)
    stripped = re.sub(r"\b20[2-3]\d\b", " ", stripped)
    values: list[float] = []
    for match in NUMBER_RE.finditer(stripped):
        raw = float(match.group(1))
        if match.group(2) == "%":
            values.append(raw)
            values.append(raw / 100.0)
            continue
        if raw in ALLOWED_BARE and "." not in match.group(1):
            continue
        values.append(raw)
    return values


def numbers_subset_of_tools(answer: str, tool_payloads: Any, *, atol: float = 1e-4) -> bool:
    """True when every quantitative number in `answer` appears in tool JSON."""
    allowed = collect_tool_numbers(tool_payloads)
    if not allowed:
        return not extract_answer_numbers(answer)
    for number in extract_answer_numbers(answer):
        if any(abs(number - item) <= atol for item in allowed):
            continue
        if any(abs(number - item * 100.0) <= 0.05 for item in allowed if 0 <= item <= 1):
            continue
        if any(abs(number / 100.0 - item) <= atol for item in allowed):
            continue
        return False
    return True


def dump_tools(payload: Any) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


def compact_tool_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Shrink prefetch JSON so Groq is not sent the full evidence table."""
    compact: dict[str, Any] = {}
    overview = pack.get("overview")
    if isinstance(overview, dict):
        compact["overview"] = {
            "eligible_corpus_count": overview.get("eligible_corpus_count"),
            "normalized_count": overview.get("normalized_count"),
            "raw_count": overview.get("raw_count"),
            "included_sources": overview.get("included_sources"),
            "unavailable_sources": [
                name
                for name in (overview.get("unavailable_sources") or [])
                if name in {"play_store", "app_store"}
            ],
            "intent_tag_counts": overview.get("intent_tag_counts"),
            "intent_mode_counts": overview.get("intent_mode_counts"),
            "empty": overview.get("empty"),
            "counts_by_source": [
                {
                    "source_type": row.get("source_type"),
                    "status": row.get("status"),
                    "eligible_count": row.get("eligible_count"),
                    "raw_count": row.get("raw_count"),
                }
                for row in (overview.get("counts_by_source") or [])
                if isinstance(row, dict)
                and (
                    row.get("status") == "live"
                    or int(row.get("raw_count") or 0) > 0
                    or int(row.get("eligible_count") or 0) > 0
                )
            ],
        }
    themes = pack.get("themes")
    if isinstance(themes, dict):
        cards = themes.get("themes") or []
        compact["themes"] = {
            "empty": themes.get("empty"),
            "themes": [
                {
                    "theme_id": card.get("theme_id"),
                    "name": card.get("name"),
                    "mention_count": card.get("mention_count"),
                    "share_of_voice": card.get("share_of_voice"),
                    "data_confidence": card.get("data_confidence"),
                    "impact_score": card.get("impact_score"),
                    "sentiment_skew": card.get("sentiment_skew"),
                    "bookmark_vs_stall": card.get("bookmark_vs_stall"),
                    "hypothesis_flag": card.get("hypothesis_flag"),
                    "source_diversity": card.get("source_diversity"),
                }
                for card in cards[:12]
                if isinstance(card, dict)
            ],
        }
    segments = pack.get("segments")
    if isinstance(segments, dict):
        cells = segments.get("cells") or []
        compact["segments"] = {
            "dimension": segments.get("dimension"),
            "empty": segments.get("empty"),
            "cells": cells[:40] if isinstance(cells, list) else cells,
        }
    evidence = pack.get("evidence")
    if isinstance(evidence, dict):
        rows = evidence.get("rows") or []
        compact["evidence"] = {
            "empty": evidence.get("empty"),
            "rows": [
                {
                    "document_id": row.get("document_id"),
                    "quote": str(row.get("quote") or "")[:280],
                    "source_type": row.get("source_type"),
                    "url": row.get("url"),
                    "intent_mode": row.get("intent_mode"),
                    "friction_tags": row.get("friction_tags"),
                }
                for row in rows[:2]
                if isinstance(row, dict)
            ],
        }
    retrieval = pack.get("retrieval")
    if isinstance(retrieval, dict):
        chunks = retrieval.get("chunks") or []
        compact["retrieval"] = {
            "filters_kept": retrieval.get("filters_kept"),
            "chunks": chunks[:2] if isinstance(chunks, list) else chunks,
        }
    rows = pack.get("retrieval_rows")
    if isinstance(rows, list) and rows:
        compact["retrieval_rows"] = rows[:2]
    for key, value in pack.items():
        if key in compact or key in {"overview", "themes", "segments", "evidence", "retrieval", "retrieval_rows"}:
            continue
        compact[key] = value
    return compact
