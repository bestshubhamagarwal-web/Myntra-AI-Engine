"""Chunk normalized documents and encode with local BGE-M3 (never Groq)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from src.config import BGE_M3_DIM, Settings, load_settings
from src.db.repository import ChunkRecord, DocumentRepository, EmbedRun, ExtractionRecord, NormalizedRecord
from src.embed.bge import (
    EmbeddingCollectionError,
    encode_texts,
    embedding_revision,
    is_bge_m3,
    load_bge_model,
)
from src.embed.chunking import chunk_text, estimate_tokens
from src.timeutil import utcnow

logger = logging.getLogger(__name__)


@dataclass
class EmbedBatchResult:
    run_id: UUID
    status: str
    encoded: int
    skipped: int
    embedding_model: str
    embedding_revision: str
    error_message: str | None = None


def assert_single_bge_collection(repo: DocumentRepository, model_id: str) -> None:
    for stored in repo.distinct_embedding_models():
        if stored == model_id:
            continue
        if is_bge_m3(stored) and is_bge_m3(model_id):
            continue
        raise EmbeddingCollectionError(
            f"chunks already store embedding_model={stored!r}; refusing to mix "
            f"{model_id!r}. Full re-embed required (EC-EM-02)."
        )


def _apply_extraction(chunk: ChunkRecord, extraction: ExtractionRecord | None) -> None:
    if extraction is None:
        return
    chunk.intent_tag = extraction.intent_tag
    chunk.intent_mode = extraction.intent_mode
    chunk.friction_tags = list(extraction.friction_tags)
    chunk.sentiment = extraction.sentiment_primary
    chunk.maps_to_questions = list(extraction.maps_to_questions)
    chunk.extraction_status = extraction.extraction_status


def _new_chunks_for_document(
    document: NormalizedRecord,
    pieces: list[str],
    extraction: ExtractionRecord | None,
    *,
    source_type: str | None,
    published_at,
) -> list[ChunkRecord]:
    out: list[ChunkRecord] = []
    for ordinal, text in enumerate(pieces):
        chunk = ChunkRecord(
            id=uuid4(),
            document_id=document.id,
            ordinal=ordinal,
            text=text,
            token_count=estimate_tokens(text),
            content_hash=document.content_hash,
            source_type=source_type,
            published_at=published_at,
            product_category=document.product_category,
        )
        _apply_extraction(chunk, extraction)
        out.append(chunk)
    return out


def _flush_embeddings(
    repo: DocumentRepository,
    model: Any,
    pending: list[tuple[UUID, str]],
    *,
    settings: Settings,
    revision: str,
) -> int:
    if not pending:
        return 0
    encoded = 0

    def encode_batch(items: list[tuple[UUID, str]]) -> None:
        nonlocal encoded
        if not items:
            return
        texts = [text for _, text in items]
        try:
            vectors = encode_texts(
                model,
                texts,
                expected_dim=settings.embedding_dim,
                batch_size=len(texts),
            )
        except Exception as exc:
            if len(items) > 1:
                logger.warning("embed batch failed (%s); splitting", exc)
                mid = len(items) // 2
                encode_batch(items[:mid])
                encode_batch(items[mid:])
                return
            logger.error("embed failed for chunk %s: %s", items[0][0], exc)
            return
        for (chunk_id, _), vector in zip(items, vectors):
            repo.update_chunk_embedding(
                chunk_id,
                vector,
                embedding_model=settings.bge_model_id,
                embedding_revision=revision,
                embedding_dim=settings.embedding_dim,
            )
            encoded += 1

    encode_batch(pending)
    pending.clear()
    return encoded


def sync_document_chunks(
    repo: DocumentRepository,
    document: NormalizedRecord,
    settings: Settings,
) -> list[ChunkRecord]:
    pieces = chunk_text(
        document.text_original,
        max_tokens=settings.chunk_max_tokens,
        overlap=settings.chunk_overlap_tokens,
    )
    raw = repo.get_raw(document.raw_id)
    source_type = raw.source_type.value if raw else None
    published_at = raw.published_at if raw else document.review_date
    extraction = repo.get_extraction(document.id)

    if not pieces:
        repo.replace_chunks(document.id, [])
        return []

    existing = sorted(repo.list_chunks(document.id), key=lambda c: c.ordinal)
    if [c.text for c in existing] == pieces and all(
        c.content_hash == document.content_hash for c in existing
    ):
        if extraction is not None:
            repo.update_chunk_metadata(document.id, extraction)
        return repo.list_chunks(document.id)

    fresh = _new_chunks_for_document(
        document,
        pieces,
        extraction,
        source_type=source_type,
        published_at=published_at,
    )
    repo.replace_chunks(document.id, fresh)
    return fresh


def run_embed(
    repo: DocumentRepository,
    settings: Settings | None = None,
    *,
    model: Any | None = None,
    limit: int | None = None,
    force: bool = False,
    load_model: Callable[[Settings], Any] | None = None,
) -> EmbedBatchResult:
    cfg = settings or load_settings()
    if cfg.embedding_dim != BGE_M3_DIM:
        raise EmbeddingCollectionError(
            f"EMBEDDING_DIM is {cfg.embedding_dim}, frozen BGE-M3 dim is {BGE_M3_DIM}"
        )
    assert_single_bge_collection(repo, cfg.bge_model_id)

    loaded = model if model is not None else (load_model or load_bge_model)(cfg)
    revision = embedding_revision(loaded)

    run = EmbedRun(
        id=uuid4(),
        started_at=utcnow(),
        status="running",
        embedding_model=cfg.bge_model_id,
        embedding_revision=revision,
        embedding_dim=cfg.embedding_dim,
    )
    repo.start_embed_run(run)

    encoded = skipped = 0
    status = "success"
    error_message = None
    pending: list[tuple[UUID, str]] = []
    batch_size = max(1, cfg.embed_batch_size)

    try:
        documents = repo.list_embed_candidates(limit=limit)
        for document in documents:
            chunks = sync_document_chunks(repo, document, cfg)
            if not chunks:
                skipped += 1
                continue
            for chunk in chunks:
                if chunk.embedding is not None and not force:
                    skipped += 1
                    continue
                pending.append((chunk.id, chunk.text))
                if len(pending) >= batch_size:
                    encoded += _flush_embeddings(
                        repo, loaded, pending, settings=cfg, revision=revision
                    )
        encoded += _flush_embeddings(repo, loaded, pending, settings=cfg, revision=revision)
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
        logger.exception("embed batch failed")
        raise
    finally:
        run.finished_at = utcnow()
        run.status = status
        run.rows_encoded = encoded
        run.rows_skipped = skipped
        run.error_message = error_message
        repo.finish_embed_run(run)

    return EmbedBatchResult(
        run_id=run.id,
        status=status,
        encoded=encoded,
        skipped=skipped,
        embedding_model=cfg.bge_model_id,
        embedding_revision=revision,
        error_message=error_message,
    )


def search_chunks(
    repo: DocumentRepository,
    query_vector: Sequence[float],
    *,
    k: int = 8,
    friction_tag: str | None = None,
    intent_mode: str | None = None,
) -> list[ChunkRecord]:
    return repo.nearest_chunks(
        list(query_vector),
        k=k,
        friction_tag=friction_tag,
        intent_mode=intent_mode,
    )
