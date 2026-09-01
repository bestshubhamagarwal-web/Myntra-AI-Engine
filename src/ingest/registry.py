"""Connector registry and unimplemented-source skip (honest unavailable)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.config import Settings
from src.db.repository import DocumentRepository, IngestRun
from src.ingest.allowlist import IMPLEMENTED_SOURCE_TYPES, UNAVAILABLE_WITHOUT_CONNECTOR
from src.ingest.app_store import run_app_store_ingest
from src.ingest.common import begin_ingest_run, record_skip_locked, skip_unconfigured
from src.ingest.lock import ExclusiveFileLock
from src.ingest.object_store import LocalObjectStore
from src.ingest.play_store import run_play_store_ingest
from src.ingest.reddit import run_reddit_ingest
from src.ingest.x import run_x_ingest
from src.ingest.youtube import run_youtube_ingest

ConnectorFn = Callable[..., IngestRun]

CONNECTORS: dict[str, ConnectorFn] = {
    "play_store": run_play_store_ingest,
    "app_store": run_app_store_ingest,
    "reddit": run_reddit_ingest,
    "youtube": run_youtube_ingest,
    "x": run_x_ingest,
}

# Default `ingest all` order. X is fifth and stays skipped without a bearer.
INGEST_ALL_SOURCES: tuple[str, ...] = IMPLEMENTED_SOURCE_TYPES


def run_unimplemented_ingest(
    repo: DocumentRepository,
    source_type: str,
) -> IngestRun:
    run = begin_ingest_run(repo, source_type)
    if run.status != "running":
        return run
    return skip_unconfigured(
        repo,
        run,
        f"{source_type} has no public ToS-compliant connector; "
        "source unavailable — no metrics imputed",
    )


def run_source_ingest(
    source_type: str,
    repo: DocumentRepository,
    settings: Settings,
    *,
    object_store: LocalObjectStore | None = None,
    max_items: int | None = None,
    force_full: bool = False,
    **kwargs: Any,
) -> IngestRun:
    lock_dir = getattr(settings, "lock_path", None)
    stale = int(getattr(settings, "lock_stale_seconds", 7200) or 7200)
    lock: ExclusiveFileLock | None = None
    if lock_dir is not None:
        lock = ExclusiveFileLock(Path(lock_dir) / f"ingest-{source_type}.lock", stale_seconds=stale)
        if not lock.acquire():
            return record_skip_locked(
                repo,
                source_type,
                f"{source_type} ingest lock held; overlapping cron/n8n skipped (EC-IN-16)",
            )
    try:
        if source_type in CONNECTORS:
            return CONNECTORS[source_type](
                repo,
                settings,
                object_store=object_store,
                max_items=max_items,
                force_full=force_full,
                **kwargs,
            )
        if source_type in UNAVAILABLE_WITHOUT_CONNECTOR:
            return run_unimplemented_ingest(repo, source_type)
        raise KeyError(source_type)
    finally:
        if lock is not None:
            lock.release()
