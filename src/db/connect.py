"""Pick Postgres when it is listening; otherwise the shared local file store."""

from __future__ import annotations

import logging
import socket
import time
from urllib.parse import urlparse

import psycopg

from src.config import Settings
from src.db.local import PersistentMemoryRepository
from src.db.postgres import PostgresRepository
from src.db.repository import DocumentRepository

log = logging.getLogger(__name__)

POSTGRES_UNREACHABLE = (
    "Postgres unreachable. Start it with docker compose up -d, "
    "then python -m src.cli migrate and python -m src.cli serve."
)

POSTGRES_REQUIRED = (
    "Postgres is required (REQUIRE_POSTGRES=true) but was not reachable. "
    "Check DATABASE_URL (Railway private URL + pgvector template) and retry. "
    "Refusing local_store.pkl so the API cannot serve an empty corpus."
)


class PostgresRequiredError(RuntimeError):
    """Production/Railway must not fall back to the laptop pickle store."""


def postgres_tcp_open(database_url: str, timeout: float = 1.0) -> bool:
    """Fail fast when nothing is listening (Windows localhost can hang in psycopg)."""
    parsed = urlparse(database_url)
    host = parsed.hostname or "127.0.0.1"
    if host in {"localhost", "::1"}:
        host = "127.0.0.1"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def try_postgres(cfg: Settings, *, tcp_timeout: float = 1.0, connect_timeout: int = 2) -> PostgresRepository | None:
    """Return a repo when TCP + handshake succeed; otherwise None."""
    if not postgres_tcp_open(cfg.database_url, timeout=tcp_timeout):
        return None
    try:
        with psycopg.connect(cfg.database_url, connect_timeout=connect_timeout) as conn:
            conn.execute("SELECT 1")
        return PostgresRepository(cfg.database_url)
    except Exception as exc:
        log.warning("Postgres handshake failed (%s).", exc)
        return None


def wait_for_postgres(cfg: Settings, *, total_seconds: float | None = None) -> PostgresRepository:
    """Retry until Postgres accepts connections or the wait budget is spent."""
    budget = cfg.postgres_wait_seconds if total_seconds is None else total_seconds
    deadline = time.monotonic() + max(0.0, float(budget))
    tcp_timeout = 3.0 if cfg.require_postgres else 1.0
    connect_timeout = 8 if cfg.require_postgres else 2
    last_note = "not attempted"
    while True:
        repo = try_postgres(cfg, tcp_timeout=tcp_timeout, connect_timeout=connect_timeout)
        if repo is not None:
            return repo
        last_note = "not listening or handshake failed"
        if time.monotonic() >= deadline:
            break
        time.sleep(2.0)
    raise PostgresRequiredError(f"{POSTGRES_REQUIRED} Last check: {last_note}.")


def connect_store(cfg: Settings) -> DocumentRepository:
    """Postgres when reachable; otherwise `local_store_path` so ingest and the API share data.

    When `require_postgres` is true (Railway), wait then fail hard — never pickle.
    """
    if cfg.require_postgres:
        return wait_for_postgres(cfg)

    repo = try_postgres(cfg)
    if repo is not None:
        return repo
    log.warning(
        "Postgres not reachable on DATABASE_URL. Using local file store at %s.",
        cfg.local_store_path,
    )
    cfg.local_store_path.parent.mkdir(parents=True, exist_ok=True)
    return PersistentMemoryRepository(cfg.local_store_path)
