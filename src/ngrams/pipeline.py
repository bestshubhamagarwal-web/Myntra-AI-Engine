"""1–3 gram precompute from scrubbed evidence text (en/hi stopwords)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from uuid import UUID, uuid4

from src.config import Settings, load_settings
from src.db.repository import DocumentRepository, NgramRow
from src.ngrams.stopwords import STOPWORDS
from src.normalize.pii import scrub_pii
from src.timeutil import utcnow

TOKEN_RE = re.compile(r"[A-Za-z]{2,}|[\u0900-\u097F]{2,}")


@dataclass
class NgramJobResult:
    cluster_run_id: UUID
    n_rows: int
    status: str = "success"


def tokenize(text: str) -> list[str]:
    cleaned = scrub_pii(text or "")
    return [tok.lower() for tok in TOKEN_RE.findall(cleaned)]


def iter_ngrams(tokens: list[str], n: int) -> list[str]:
    if n < 1 or len(tokens) < n:
        return []
    grams: list[str] = []
    for i in range(len(tokens) - n + 1):
        window = tokens[i : i + n]
        if all(tok in STOPWORDS for tok in window):
            continue
        if n == 1 and window[0] in STOPWORDS:
            continue
        grams.append(" ".join(window))
    return grams


def run_ngrams(
    repo: DocumentRepository,
    settings: Settings | None = None,
    *,
    cluster_run_id: UUID | None = None,
    min_count: int = 1,
    max_rows: int | None = None,
) -> NgramJobResult:
    _cfg = settings or load_settings()
    run = repo.get_cluster_run(cluster_run_id) if cluster_run_id else repo.latest_cluster_run()
    if run is None:
        raise ValueError("no cluster_run for n-grams; run cluster first")
    assignments = repo.list_document_themes(cluster_run_id=run.id)
    theme_of: dict[UUID, UUID] = {}
    for row in assignments:
        theme_of[row.document_id] = row.theme_id

    store = getattr(repo, "normalized", None)
    ext_store = getattr(repo, "extractions", None)
    if store is not None:
        docs = [d for d in store.values() if d.eligible and d.duplicate_of is None]
    else:
        docs = [r for r in repo.list_normalized(limit=None, eligible_only=True) if r.duplicate_of is None]

    counts: Counter[tuple[str, int, UUID | None, str | None, str | None]] = Counter()
    for rec in docs:
        extraction = ext_store.get(rec.id) if ext_store is not None else repo.get_extraction(rec.id)
        text = rec.text_original or ""
        tokens = tokenize(text)
        sentiment = extraction.sentiment_primary if extraction else None
        category = rec.product_category or "unknown"
        theme_id = theme_of.get(rec.id)
        for n in (1, 2, 3):
            for gram in iter_ngrams(tokens, n):
                counts[(gram, n, theme_id, category, sentiment)] += 1
                counts[(gram, n, None, category, sentiment)] += 1
                if theme_id is not None:
                    counts[(gram, n, theme_id, None, sentiment)] += 1

    now = utcnow()
    ranked = [
        (count, gram, n, theme_id, category, sentiment)
        for (gram, n, theme_id, category, sentiment), count in counts.items()
        if count >= max(1, min_count)
    ]
    ranked.sort(key=lambda item: item[0], reverse=True)
    if max_rows is not None:
        ranked = ranked[:max_rows]
    rows = [
        NgramRow(
            id=uuid4(),
            gram=gram,
            n=n,
            count=count,
            cluster_run_id=run.id,
            theme_id=theme_id,
            category=category,
            sentiment=sentiment,
            computed_at=now,
        )
        for count, gram, n, theme_id, category, sentiment in ranked
    ]
    repo.replace_ngrams(run.id, rows)
    return NgramJobResult(cluster_run_id=run.id, n_rows=len(rows))
