"""Resolve ingest_queries rows for a connector (Architecture §6.3)."""

from __future__ import annotations

from src.db.repository import DocumentRepository

DEFAULT_QUERIES: tuple[str, ...] = (
    "Myntra wishlist",
    "Myntra cart",
    "Myntra sizing",
    "Myntra returns",
    "Myntra vs AJIO",
)

YOUTUBE_DEFAULT_QUERIES: tuple[str, ...] = (
    "Myntra haul",
    "Myntra try-on",
    "Myntra size guide",
    "Myntra unboxing",
    "Myntra vs AJIO",
    "Myntra wishlist",
    "Myntra returns",
)


def queries_for_source(repo: DocumentRepository, source_type: str) -> list[str]:
    """Rows with matching source_type plus global (NULL) seeds. Deduped, stable order."""
    seen: set[str] = set()
    out: list[str] = []
    for row in repo.list_ingest_queries():
        if row.get("active") is False:
            continue
        row_source = row.get("source_type")
        if row_source not in (None, "", source_type):
            continue
        text = (row.get("query_text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    if not out:
        if source_type == "youtube":
            return list(YOUTUBE_DEFAULT_QUERIES)
        return list(DEFAULT_QUERIES)
    if source_type == "youtube":
        for extra in YOUTUBE_DEFAULT_QUERIES:
            if extra not in seen:
                out.append(extra)
                seen.add(extra)
    return out
