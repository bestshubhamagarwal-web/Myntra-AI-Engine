"""Shared ingest-run lifecycle. One run per source; failures do not cross sources."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from src.db.repository import DocumentRepository, IngestRun
from src.ingest.object_store import LocalObjectStore, redact_payload
from src.models.envelope import RawEnvelope
from src.timeutil import coerce_aware, utcnow

logger = logging.getLogger(__name__)


class ConnectorBlocked(RuntimeError):
    """Source returned 403/429 after retries — disable rather than bypass."""


class ConnectorUnconfigured(RuntimeError):
    """Credentials or config missing; source is unavailable, not zero volume."""


@dataclass
class PersistResult:
    upserted: int
    watermark_after: datetime | None
    payload_warnings: list[str]
    error: BaseException | None = None


def is_block_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    code = getattr(exc, "code", None) or getattr(exc, "status", None)
    if code in {401, 403, 429, 503}:
        return True
    return any(
        token in text
        for token in ("403", "401", "429", "blocked", "forbidden", "quotaexceeded", "quota exceeded")
    )


INGEST_LOCK_TTL_SECONDS = 7200
SKIP_STATUS_NOT_SOURCE_SIGNAL = frozenset({"skipped_locked", "running"})


def concurrent_ingest(
    repo: DocumentRepository,
    source_type: str,
    *,
    exclude_id=None,
    ttl_seconds: int = INGEST_LOCK_TTL_SECONDS,
) -> bool:
    """True when another in-flight ingest for this source is still fresh (EC-IN-16)."""
    now = utcnow()
    for run in repo.list_ingest_runs(source_type):
        if exclude_id is not None and run.id == exclude_id:
            continue
        if run.status != "running":
            continue
        started = coerce_aware(run.started_at) or now
        if (now - started).total_seconds() <= ttl_seconds:
            return True
    return False


def skip_locked(repo: DocumentRepository, run: IngestRun, message: str) -> IngestRun:
    """Overlapping cron/n8n. Do not mark the source unavailable — the other run is live."""
    run.status = "skipped_locked"
    run.finished_at = utcnow()
    run.rows_fetched = None
    run.rows_upserted = None
    run.source_available = None
    run.error_message = message
    repo.finish_ingest_run(run)
    logger.warning("%s ingest skipped (lock): %s", run.source_type, message)
    return run


def record_skip_locked(
    repo: DocumentRepository,
    source_type: str,
    message: str,
) -> IngestRun:
    run = IngestRun(
        id=uuid4(),
        source_type=source_type,
        status="running",
        started_at=utcnow(),
        source_available=None,
    )
    repo.start_ingest_run(run)
    return skip_locked(repo, run, message)


def begin_ingest_run(repo: DocumentRepository, source_type: str) -> IngestRun:
    run = IngestRun(
        id=uuid4(),
        source_type=source_type,
        status="running",
        started_at=utcnow(),
        source_available=None,
    )
    if concurrent_ingest(repo, source_type):
        repo.start_ingest_run(run)
        return skip_locked(
            repo,
            run,
            f"{source_type} ingest already running; overlapping job skipped (EC-IN-16)",
        )
    repo.start_ingest_run(run)
    return run


def skip_run(
    repo: DocumentRepository,
    run: IngestRun,
    *,
    status: str,
    message: str,
) -> IngestRun:
    run.status = status
    run.finished_at = utcnow()
    run.rows_fetched = None
    run.rows_upserted = None
    run.source_available = False
    run.error_message = message
    repo.finish_ingest_run(run)
    logger.warning("%s ingest skipped: %s", run.source_type, message)
    return run


def skip_if_disabled(
    repo: DocumentRepository,
    run: IngestRun,
    *,
    enabled_db: bool,
    enabled_env: bool,
    source_type: str,
) -> IngestRun | None:
    if enabled_db and enabled_env:
        return None
    return skip_run(
        repo,
        run,
        status="skipped_disabled",
        message=f"{source_type} disabled; source unavailable — no metrics imputed",
    )


def skip_unconfigured(
    repo: DocumentRepository,
    run: IngestRun,
    message: str,
) -> IngestRun:
    return skip_run(repo, run, status="skipped_unconfigured", message=message)


def fail_run(
    repo: DocumentRepository,
    run: IngestRun,
    exc: BaseException,
    *,
    fetched: int | None = None,
    upserted: int | None = None,
    watermark: datetime | None = None,
    payload_warning: str | None = None,
    blocked: bool = False,
) -> IngestRun:
    run.status = "failed"
    run.finished_at = utcnow()
    run.rows_fetched = fetched
    run.rows_upserted = upserted
    run.source_available = False
    run.watermark_after = watermark
    prefix = "blocked: " if blocked else ""
    run.error_message = f"{prefix}{exc}"
    if payload_warning:
        run.payload_warning = payload_warning
    repo.finish_ingest_run(run)
    if blocked:
        logger.error("%s blocked: %s", run.source_type, exc)
    else:
        logger.exception("%s ingest failed", run.source_type)
    return run


def succeed_run(
    repo: DocumentRepository,
    run: IngestRun,
    *,
    fetched: int,
    upserted: int,
    watermark: datetime | None,
    payload_warning: str | None = None,
) -> IngestRun:
    run.status = "success"
    run.finished_at = utcnow()
    run.rows_fetched = fetched
    run.rows_upserted = upserted
    run.source_available = True
    run.watermark_after = watermark
    if payload_warning:
        run.payload_warning = payload_warning
    repo.finish_ingest_run(run)
    return run


def persist_envelopes(
    repo: DocumentRepository,
    object_store: LocalObjectStore,
    source_type: str,
    items: list[tuple[RawEnvelope, dict[str, Any]]],
    watermark_start: datetime | None,
    *,
    snapshots: bool = True,
) -> PersistResult:
    upserted = 0
    warnings: list[str] = []
    last_published = watermark_start
    try:
        for envelope, payload in items:
            if snapshots:
                redacted = redact_payload(payload, envelope.author_hash)
                uri = object_store.write(source_type, envelope.source_id, redacted)
                envelope.payload_uri = uri
                if uri is None:
                    warnings.append(envelope.source_id)
            repo.upsert_raw(envelope)
            upserted += 1
            if upserted % 500 == 0 and hasattr(repo, "save"):
                saver = getattr(repo, "save")
                if callable(saver):
                    saver()
            if envelope.published_at and not envelope.date_anomaly:
                published = coerce_aware(envelope.published_at)
                if published and (last_published is None or published > last_published):
                    last_published = published
        return PersistResult(upserted, last_published, warnings)
    except Exception as exc:  # noqa: BLE001 — persist is part of the ingest run
        return PersistResult(upserted, last_published, warnings, error=exc)


def payload_warning_text(warnings: list[str]) -> str | None:
    if not warnings:
        return None
    return f"snapshot write failed for {len(warnings)} records"


def date_anomaly(published: datetime | None, now: datetime) -> bool:
    return bool(published and published > now + timedelta(days=1))
