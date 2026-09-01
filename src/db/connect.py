"""Pick Postgres when it is listening; otherwise the shared local file store."""

from __future__ import annotations

import logging
import os
import socket
import time
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

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

RAILWAY_LOCAL_URL = (
    "DATABASE_URL still points at localhost on Railway. In the API service "
    "Variables set DATABASE_URL to ${{Postgres.DATABASE_URL}} from the pgvector "
    "service (not the laptop .env, and not DATABASE_PUBLIC_URL unless you also "
    "append sslmode=require)."
)

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class PostgresRequiredError(RuntimeError):
    """Production/Railway must not fall back to the laptop pickle store."""


def on_railway() -> bool:
    return bool(
        (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip()
        or (os.environ.get("RAILWAY_PROJECT_ID") or "").strip()
    )


def _hostname(database_url: str) -> str:
    return (urlparse(database_url).hostname or "").strip().lower()


def is_loopback_host(host: str | None) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


def _set_query_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() != key.lower()]
    pairs.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(pairs)))


def normalize_database_url(url: str) -> str:
    """postgres:// → postgresql://, strip quotes, default sslmode for Railway hosts."""
    cleaned = (url or "").strip().strip('"').strip("'")
    if not cleaned:
        return ""
    if cleaned.startswith("postgres://"):
        cleaned = "postgresql://" + cleaned[len("postgres://") :]
    parsed = urlparse(cleaned)
    has_ssl = any(k.lower() == "sslmode" for k, _ in parse_qsl(parsed.query, keep_blank_values=True))
    host = (parsed.hostname or "").lower()
    if not has_ssl:
        if host.endswith(".rlwy.net") or host.endswith(".railway.app"):
            cleaned = _set_query_param(cleaned, "sslmode", "require")
        elif host.endswith(".railway.internal"):
            cleaned = _set_query_param(cleaned, "sslmode", "prefer")
    return cleaned


def url_from_pg_env() -> str:
    host = (os.environ.get("PGHOST") or os.environ.get("PG_HOST") or "").strip()
    if not host or is_loopback_host(host):
        return ""
    user = (os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER") or "postgres").strip() or "postgres"
    password = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD") or ""
    port = (os.environ.get("PGPORT") or os.environ.get("POSTGRES_PORT") or "5432").strip() or "5432"
    dbname = (
        os.environ.get("PGDATABASE") or os.environ.get("POSTGRES_DB") or os.environ.get("POSTGRES_DATABASE") or "railway"
    ).strip() or "railway"
    hostport = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
    return normalize_database_url(
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{hostport}/{quote_plus(dbname)}"
    )


def resolve_database_url(explicit: str) -> str:
    """Prefer a reachable Railway URL when DATABASE_URL is still the laptop default."""
    raw = normalize_database_url(explicit)
    host = _hostname(raw) if raw else ""
    if raw and not is_loopback_host(host):
        return raw
    for key in (
        "DATABASE_PRIVATE_URL",
        "DATABASE_PUBLIC_URL",
        "POSTGRES_PRISMA_URL",
        "POSTGRES_URL",
    ):
        alt = normalize_database_url(os.environ.get(key) or "")
        if alt and not is_loopback_host(_hostname(alt)):
            log.info("Using %s because DATABASE_URL is local or empty.", key)
            return alt
    pg_url = url_from_pg_env()
    if pg_url:
        log.info("Using PGHOST/PGUSER environment for Postgres.")
        return pg_url
    return raw


def conninfo_candidates(database_url: str) -> list[str]:
    """Try TLS modes that Railway public vs private hosts actually accept."""
    url = resolve_database_url(database_url)
    if not url:
        return []
    seen: list[str] = []
    out: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.append(candidate)
            out.append(candidate)

    add(url)
    add(_set_query_param(url, "sslmode", "require"))
    add(_set_query_param(url, "sslmode", "prefer"))
    add(_set_query_param(url, "sslmode", "disable"))
    return out


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


def handshake_database_url(database_url: str, *, connect_timeout: int = 8) -> str:
    """Return the conninfo that actually completed SELECT 1."""
    last_exc: Exception | None = None
    for conninfo in conninfo_candidates(database_url):
        try:
            with psycopg.connect(conninfo, connect_timeout=connect_timeout) as conn:
                conn.execute("SELECT 1")
            return conninfo
        except Exception as exc:  # noqa: BLE001 — try the next TLS mode
            last_exc = exc
            log.warning(
                "Postgres handshake failed host=%s sslmode=%s (%s).",
                _hostname(conninfo) or "?",
                dict(parse_qsl(urlparse(conninfo).query)).get("sslmode", "default"),
                exc,
            )
    if last_exc is not None:
        raise last_exc
    raise PostgresRequiredError(POSTGRES_UNREACHABLE)


def postgres_connect(database_url: str, **kwargs):
    """Open a psycopg connection, retrying Railway TLS modes."""
    last_exc: Exception | None = None
    timeout = int(kwargs.get("connect_timeout") or 8)
    for conninfo in conninfo_candidates(database_url):
        try:
            options = dict(kwargs)
            options.setdefault("connect_timeout", timeout)
            return psycopg.connect(conninfo, **options)
        except Exception as exc:  # noqa: BLE001 — try the next TLS mode
            last_exc = exc
            log.warning("Postgres connect failed host=%s (%s).", _hostname(conninfo) or "?", exc)
    if last_exc is not None:
        raise last_exc
    raise PostgresRequiredError(POSTGRES_UNREACHABLE)


def try_postgres(cfg: Settings, *, tcp_timeout: float = 1.0, connect_timeout: int = 2) -> PostgresRepository | None:
    """Return a repo when handshake succeed; otherwise None."""
    url = resolve_database_url(cfg.database_url)
    host = _hostname(url)
    # TCP pre-check is only for loopback: Windows psycopg can hang on localhost.
    # Railway private DNS is IPv6; a Python TCP probe there false-negatives.
    if is_loopback_host(host) and not postgres_tcp_open(url, timeout=tcp_timeout):
        return None
    try:
        working = handshake_database_url(url, connect_timeout=connect_timeout)
        cfg.database_url = working
        return PostgresRepository(working)
    except Exception as exc:  # noqa: BLE001 — caller retries or falls back
        log.warning("Postgres handshake failed (%s).", exc)
        return None


def wait_for_postgres(cfg: Settings, *, total_seconds: float | None = None) -> PostgresRepository:
    """Retry until Postgres accepts connections or the wait budget is spent."""
    cfg.database_url = resolve_database_url(cfg.database_url)
    if cfg.require_postgres and on_railway() and is_loopback_host(_hostname(cfg.database_url)):
        raise PostgresRequiredError(RAILWAY_LOCAL_URL)
    budget = cfg.postgres_wait_seconds if total_seconds is None else total_seconds
    deadline = time.monotonic() + max(0.0, float(budget))
    tcp_timeout = 3.0 if cfg.require_postgres else 1.0
    connect_timeout = 8 if cfg.require_postgres else 2
    last_note = "not attempted"
    while True:
        repo = try_postgres(cfg, tcp_timeout=tcp_timeout, connect_timeout=connect_timeout)
        if repo is not None:
            cfg.database_url = repo.database_url
            return repo
        last_note = f"not listening or handshake failed (host={_hostname(cfg.database_url) or 'unknown'})"
        if time.monotonic() >= deadline:
            break
        time.sleep(2.0)
    raise PostgresRequiredError(f"{POSTGRES_REQUIRED} Last check: {last_note}.")


def connect_store(cfg: Settings) -> DocumentRepository:
    """Postgres when reachable; otherwise `local_store_path` so ingest and the API share data.

    When `require_postgres` is true (Railway), wait then fail hard — never pickle.
    """
    cfg.database_url = resolve_database_url(cfg.database_url)
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
