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
    "DATABASE_URL still points at localhost on Railway. Open the pgvector "
    "service → Variables, copy DATABASE_URL (starts with postgresql:// and "
    "contains railway.internal or rlwy.net), paste it on the API service, redeploy. "
    "Do not paste the laptop .env value."
)

RAILWAY_INVALID_URL = (
    "DATABASE_URL is not a real Postgres URL (no hostname). On the API service "
    "delete the current value, open the database/pgvector service → Variables, "
    "copy DATABASE_URL (postgresql://postgres:…@….railway.internal:5432/railway "
    "or ….rlwy.net:…/railway?sslmode=require), paste that exact string, redeploy. "
    "A typed ${{Postgres.DATABASE_URL}} is ignored unless Railway expands it to postgresql://."
)

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
RAILWAY_URL_KEYS = (
    "DATABASE_PRIVATE_URL",
    "DATABASE_PUBLIC_URL",
    "POSTGRES_PRISMA_URL",
    "POSTGRES_URL",
    "DATABASE_URL",
)


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


def is_remote_postgres_url(url: str) -> bool:
    """True when conninfo has a non-loopback host (not ${{…}} placeholders)."""
    cleaned = normalize_database_url(url)
    if not cleaned or "${{" in cleaned or "{{" in cleaned:
        return False
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"postgres", "postgresql"}:
        return False
    host = (parsed.hostname or "").strip()
    return bool(host) and not is_loopback_host(host)


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
            cleaned = _set_query_param(cleaned, "sslmode", "disable")
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


def _discover_railway_database_url(skip: str = "") -> str:
    """Last resort: any env value that looks like a Railway Postgres URL."""
    skip_n = normalize_database_url(skip)
    for key, value in os.environ.items():
        if key.upper().startswith("NIXPACKS") or key.upper() in {"PATH", "PYTHONPATH"}:
            continue
        alt = normalize_database_url(value or "")
        if alt and alt != skip_n and is_remote_postgres_url(alt):
            host = _hostname(alt)
            if host.endswith(".railway.internal") or host.endswith(".rlwy.net") or host.endswith(".railway.app"):
                log.info("Using Postgres URL from %s because DATABASE_URL is not connectable.", key)
                return alt
    return ""


def rewrite_to_ipv6_literal(url: str) -> str:
    """Railway private DNS is often IPv6-only; libpq may try a dead IPv4 first."""
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 5432
    if not host or host.startswith("["):
        return ""
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return ""
    if not infos:
        return ""
    ip = infos[0][4][0]
    user = parsed.username or ""
    password = parsed.password
    auth = quote_plus(user, safe="")
    if password is not None:
        auth += ":" + quote_plus(password, safe="")
    path = parsed.path or "/railway"
    rebuilt = f"postgresql://{auth}@[{ip}]:{port}{path}"
    if parsed.query:
        rebuilt += f"?{parsed.query}"
    return rebuilt


def iter_database_urls(explicit: str) -> list[str]:
    """Private Railway URL first, then public proxy, then other injected URLs."""
    seen: list[str] = []
    out: list[str] = []

    def add(raw: str) -> None:
        url = normalize_database_url(raw or "")
        if not url or url in seen or "${{" in url:
            return
        host = _hostname(url)
        if not host:
            return
        seen.append(url)
        out.append(url)

    add(explicit)
    add(resolve_database_url(explicit))
    for key in (
        "DATABASE_PUBLIC_URL",
        "DATABASE_PRIVATE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
    ):
        add(os.environ.get(key) or "")
    add(url_from_pg_env())
    add(_discover_railway_database_url(skip=explicit))
    return out


def resolve_database_url(explicit: str) -> str:
    """Prefer a reachable Railway URL when DATABASE_URL is local, empty, or a ${{…}} stub."""
    raw = normalize_database_url(explicit)
    if is_remote_postgres_url(raw):
        return raw
    for key in RAILWAY_URL_KEYS:
        alt = normalize_database_url(os.environ.get(key) or "")
        if alt != raw and is_remote_postgres_url(alt):
            log.info("Using %s because DATABASE_URL is local, empty, or not a postgres URL.", key)
            return alt
    pg_url = url_from_pg_env()
    if pg_url:
        log.info("Using PGHOST/PGUSER environment for Postgres.")
        return pg_url
    discovered = _discover_railway_database_url(skip=raw)
    if discovered:
        return discovered
    return raw


def conninfo_candidates(database_url: str) -> list[str]:
    """Try TLS modes that Railway public vs private hosts actually accept."""
    url = normalize_database_url(database_url)
    if not url:
        return []
    seen: list[str] = []
    out: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.append(candidate)
            out.append(candidate)

    host = _hostname(url)
    if host.endswith(".railway.internal"):
        disabled = _set_query_param(url, "sslmode", "disable")
        add(disabled)
        ipv6 = rewrite_to_ipv6_literal(disabled)
        add(ipv6)
        add(_set_query_param(url, "sslmode", "prefer"))
        add(_set_query_param(url, "sslmode", "require"))
    else:
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


def try_postgres(
    cfg: Settings,
    *,
    tcp_timeout: float = 1.0,
    connect_timeout: int = 2,
    errors: list[str] | None = None,
) -> PostgresRepository | None:
    """Return a repo when handshake succeed; otherwise None."""
    last_exc: Exception | None = None
    for url in iter_database_urls(cfg.database_url):
        host = _hostname(url)
        if not host or "${{" in url:
            continue
        # TCP pre-check is only for loopback: Windows psycopg can hang on localhost.
        # Railway private DNS is IPv6; a Python TCP probe there false-negatives.
        if is_loopback_host(host) and not postgres_tcp_open(url, timeout=tcp_timeout):
            continue
        try:
            working = handshake_database_url(url, connect_timeout=connect_timeout)
            cfg.database_url = working
            return PostgresRepository(working)
        except Exception as exc:  # noqa: BLE001 — try public URL / next host
            last_exc = exc
            note = f"{host}: {exc}"
            if errors is not None:
                errors.append(note)
            log.warning("Postgres handshake failed host=%s (%s).", host, exc)
    if last_exc is not None:
        cfg.database_url = resolve_database_url(cfg.database_url) or cfg.database_url
        log.warning("Postgres handshake failed (%s).", last_exc)
    return None


def wait_for_postgres(cfg: Settings, *, total_seconds: float | None = None) -> PostgresRepository:
    """Retry until Postgres accepts connections or the wait budget is spent."""
    cfg.database_url = resolve_database_url(cfg.database_url)
    host = _hostname(cfg.database_url)
    urls = iter_database_urls(cfg.database_url)
    if cfg.require_postgres and on_railway():
        if not urls:
            if not host or "${{" in (cfg.database_url or ""):
                raise PostgresRequiredError(RAILWAY_INVALID_URL)
            if is_loopback_host(host):
                raise PostgresRequiredError(RAILWAY_LOCAL_URL)
        elif all(is_loopback_host(_hostname(item)) for item in urls):
            raise PostgresRequiredError(RAILWAY_LOCAL_URL)
    budget = cfg.postgres_wait_seconds if total_seconds is None else total_seconds
    deadline = time.monotonic() + max(0.0, float(budget))
    tcp_timeout = 3.0 if cfg.require_postgres else 1.0
    connect_timeout = 8 if cfg.require_postgres else 2
    last_note = "not attempted"
    last_errors: list[str] = []
    while True:
        last_errors = []
        repo = try_postgres(
            cfg,
            tcp_timeout=tcp_timeout,
            connect_timeout=connect_timeout,
            errors=last_errors,
        )
        if repo is not None:
            cfg.database_url = repo.database_url
            return repo
        host_now = _hostname(cfg.database_url) or "unknown"
        last_note = f"not listening or handshake failed (host={host_now})"
        if last_errors:
            last_note += " " + last_errors[-1]
        if time.monotonic() >= deadline:
            break
        time.sleep(2.0)
    extra = ""
    if host.endswith(".railway.internal") or any(
        _hostname(item).endswith(".railway.internal") for item in urls
    ):
        extra = (
            " Private host failed. Paste DATABASE_PUBLIC_URL from the database "
            "service (host ends with .rlwy.net) onto the API, with sslmode=require."
        )
    raise PostgresRequiredError(f"{POSTGRES_REQUIRED} Last check: {last_note}.{extra}")


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
