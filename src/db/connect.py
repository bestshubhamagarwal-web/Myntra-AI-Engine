"""Pick Postgres when it is listening; otherwise the shared local file store."""

from __future__ import annotations

import logging
import socket
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


def connect_store(cfg: Settings) -> DocumentRepository:
    """Postgres when reachable; otherwise `local_store_path` so ingest and the API share data."""
    if postgres_tcp_open(cfg.database_url):
        try:
            with psycopg.connect(cfg.database_url, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return PostgresRepository(cfg.database_url)
        except Exception as exc:
            log.warning("Postgres handshake failed (%s). Using local file store.", exc)
    else:
        log.warning(
            "Postgres not listening on DATABASE_URL. Using local file store at %s.",
            cfg.local_store_path,
        )
    cfg.local_store_path.parent.mkdir(parents=True, exist_ok=True)
    return PersistentMemoryRepository(cfg.local_store_path)
