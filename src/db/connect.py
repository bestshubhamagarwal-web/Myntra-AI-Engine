"""Pick Postgres when it is listening; otherwise the shared local file store."""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlencode, urlparse

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
    "Check DATABASE_URL (Render internal URL, or External Database URL for a laptop) "
    "and retry. Refusing local_store.pkl so the API cannot serve an empty corpus."
)

HOSTED_LOCAL_URL = (
    "DATABASE_URL still points at localhost on the hosted API. Open the Postgres "
    "service → Connect, copy the Internal Database URL (postgresql://…@dpg-…), "
    "paste it on the API service, redeploy. Do not paste the laptop .env value."
)

HOSTED_RENDER_DNS = (
    "Render private DNS cannot resolve the short host dpg-… (Name or service not known). "
    "That name only works on the same-region private network. The API also tries "
    "dpg-….{region}-postgres.render.com which resolves on public DNS. Confirm "
    "discovery-api and discovery-db are both in Singapore."
)

HOSTED_RENDER_TLS = (
    "Render Postgres closed TLS unexpectedly. Retry uses sslmode=require and "
    "channel_binding=disable on dpg-….{region}-postgres.render.com. Confirm the "
    "API and database share a region (Singapore)."
)

HOSTED_INVALID_URL = (
    "DATABASE_URL is not a real Postgres URL (no hostname). On the API service "
    "delete the current value and let the Blueprint inject it from discovery-db "
    "(postgresql://…@dpg-…/discovery), or paste the Internal Database URL from "
    "the Postgres Connect menu, then redeploy."
)

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
HOSTED_URL_KEYS = (
    "RENDER_DATABASE_URL",
    "DATABASE_PRIVATE_URL",
    "DATABASE_PUBLIC_URL",
    "POSTGRES_PRISMA_URL",
    "POSTGRES_URL",
    "DATABASE_URL",
)


class PostgresRequiredError(RuntimeError):
    """Hosted production must not fall back to the laptop pickle store."""


def on_render() -> bool:
    return bool(
        (os.environ.get("RENDER") or "").strip()
        or (os.environ.get("RENDER_SERVICE_ID") or "").strip()
    )


def on_railway() -> bool:
    return bool(
        (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip()
        or (os.environ.get("RAILWAY_PROJECT_ID") or "").strip()
    )


def on_hosted_platform() -> bool:
    return on_render() or on_railway()


def env_lookup(*keys: str) -> str:
    """Read an env var, ignoring key case (Render/Linux are case-sensitive)."""
    wanted = {key.upper() for key in keys if key}
    for name, value in os.environ.items():
        if name.upper() in wanted and (value or "").strip():
            return (value or "").strip()
    return ""


def _hostname(database_url: str) -> str:
    host, _port, _user, _password, _path, _query = _split_postgres_url(database_url)
    return (host or "").strip().lower()


def looks_like_hosted_postgres_dsn(url: str) -> bool:
    text = (url or "").lower()
    return (
        "dpg-" in text
        or "postgres.render.com" in text
        or ".railway.internal" in text
        or ".rlwy.net" in text
        or ".railway.app" in text
    )


def _split_postgres_url(url: str) -> tuple[str, str, str, str, str, str]:
    """Split DSN without urlparse so passwords may contain '@' or ':'."""
    cleaned = (url or "").strip().strip('"').strip("'")
    if cleaned.startswith("postgres://"):
        cleaned = "postgresql://" + cleaned[len("postgres://") :]
    if not cleaned.lower().startswith("postgresql://"):
        return "", "", "", "", "", ""
    rest = cleaned[len("postgresql://") :]
    query = ""
    if "?" in rest:
        rest, query = rest.split("?", 1)
    user = ""
    password = ""
    if "@" in rest:
        userinfo, rest = rest.rsplit("@", 1)
        if ":" in userinfo:
            user, password = userinfo.split(":", 1)
        else:
            user = userinfo
    path = ""
    if "/" in rest:
        rest, path = rest.split("/", 1)
        path = "/" + path
    host = rest
    port = ""
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            port = host[end + 1 :].lstrip(":")
            host = host[1:end]
    elif host.count(":") == 1:
        host, port = host.split(":", 1)
    return host, port, user, password, path, query


_RENDER_EXTERNAL_HOST = re.compile(
    r"^(dpg-[a-z0-9-]+)\.([a-z0-9-]+)-postgres\.render\.com$",
    re.IGNORECASE,
)


def _rebuild_postgres_url(
    host: str,
    port: str,
    user: str,
    password: str,
    path: str,
    query: str,
) -> str:
    user_q = quote(unquote(user), safe="") if user else ""
    pass_q = quote(unquote(password), safe="") if password else ""
    auth = f"{user_q}:{pass_q}@" if (user_q or pass_q) else ""
    hostport = host
    if ":" in host and not host.startswith("["):
        hostport = f"[{host}]"
    if port:
        hostport = f"{hostport}:{port}"
    url = f"postgresql://{auth}{hostport}{path or ''}"
    if query:
        url += f"?{query}"
    return url


def rewrite_render_external_to_internal(url: str) -> str:
    """dpg-xxx.singapore-postgres.render.com → dpg-xxx (private network, no TLS hairpin)."""
    host, port, user, password, path, query = _split_postgres_url(url)
    match = _RENDER_EXTERNAL_HOST.match((host or "").strip().lower())
    if not match:
        return url
    return _rebuild_postgres_url(match.group(1), port or "5432", user, password, path, query)


def render_postgres_id(host: str) -> str:
    h = (host or "").strip().lower()
    match = _RENDER_EXTERNAL_HOST.match(h)
    if match:
        return match.group(1)
    if h.endswith(".internal"):
        h = h[: -len(".internal")]
    if h.startswith("dpg-") and "." not in h:
        return h
    return ""


def render_postgres_region(host: str = "") -> str:
    match = _RENDER_EXTERNAL_HOST.match((host or "").strip().lower())
    if match:
        return match.group(2)
    for key in ("RENDER_POSTGRES_REGION", "RENDER_REGION"):
        value = (os.environ.get(key) or "").strip().lower().replace("_", "-")
        if value:
            return value
    for raw in os.environ.values():
        nested = _RENDER_EXTERNAL_HOST.match(_hostname(raw or ""))
        if nested:
            return nested.group(2)
    return "singapore"


def expand_render_postgres_url(url: str) -> list[str]:
    """Private short host, then public dpg-….region-postgres.render.com (resolves on public DNS)."""
    cleaned = normalize_database_url(url)
    if not cleaned:
        return []
    host, port, user, password, path, query = _split_postgres_url(cleaned)
    pg_id = render_postgres_id(host)
    if not pg_id:
        return [cleaned]
    region = render_postgres_region(host)
    hosts: list[str] = []

    def add_host(candidate: str) -> None:
        if candidate and candidate not in hosts:
            hosts.append(candidate)

    if on_render():
        add_host(pg_id)
        add_host(f"{pg_id}.internal")
    add_host(f"{pg_id}.{region}-postgres.render.com")
    add_host(host)
    port = port or "5432"
    out: list[str] = []
    seen: set[str] = set()
    for item_host in hosts:
        rebuilt = normalize_database_url(
            _rebuild_postgres_url(item_host, port, user, password, path, query)
        )
        if rebuilt and rebuilt not in seen:
            seen.add(rebuilt)
            out.append(rebuilt)
    return out


def postgres_host_resolves(host: str, port: str | int = 5432) -> bool:
    if not host or is_loopback_host(host):
        return True
    try:
        port_n = int(port or 5432)
    except (TypeError, ValueError):
        port_n = 5432
    try:
        socket.getaddrinfo(host, port_n, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def is_loopback_host(host: str | None) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


def is_render_postgres_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h.endswith(".render.com") or h.startswith("dpg-")


def is_railway_postgres_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h.endswith(".railway.internal") or h.endswith(".rlwy.net") or h.endswith(".railway.app")


def is_hosted_postgres_host(host: str) -> bool:
    return is_render_postgres_host(host) or is_railway_postgres_host(host)


def is_render_internal_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h.startswith("dpg-") and "postgres.render.com" not in h


def is_remote_postgres_url(url: str) -> bool:
    """True when conninfo has a non-loopback host (not ${{…}} placeholders)."""
    cleaned = normalize_database_url(url)
    if not cleaned or "${{" in cleaned or "{{" in cleaned:
        return False
    if not cleaned.lower().startswith(("postgres://", "postgresql://")):
        return False
    host = _hostname(cleaned)
    return bool(host) and not is_loopback_host(host)


def _set_query_param(url: str, key: str, value: str) -> str:
    host, port, user, password, path, query = _split_postgres_url(url)
    if not host:
        return url
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k.lower() != key.lower()]
    pairs.append((key, value))
    return _rebuild_postgres_url(host, port or "5432", user, password, path, urlencode(pairs))


def _with_query_params(url: str, **params: str) -> str:
    result = url
    for key, value in params.items():
        result = _set_query_param(result, key, value)
    return result


def normalize_database_url(url: str) -> str:
    """postgres:// → postgresql://, strip quotes, default port 5432 and hosted TLS params."""
    cleaned = (url or "").strip().strip('"').strip("'")
    if not cleaned:
        return ""
    host, port, user, password, path, query = _split_postgres_url(cleaned)
    if not host:
        if cleaned.startswith("postgres://"):
            return "postgresql://" + cleaned[len("postgres://") :]
        return cleaned
    port = port or "5432"
    pairs = list(parse_qsl(query, keep_blank_values=True))
    keys = {key.lower() for key, _ in pairs}

    def set_default(key: str, value: str) -> None:
        nonlocal pairs, keys
        if key.lower() in keys:
            return
        pairs.append((key, value))
        keys.add(key.lower())

    host_l = host.lower()
    if is_render_postgres_host(host_l):
        set_default("sslmode", "require")
        set_default("gssencmode", "disable")
        set_default("channel_binding", "disable")
    elif host_l.endswith(".rlwy.net") or host_l.endswith(".railway.app"):
        set_default("sslmode", "require")
    elif host_l.endswith(".railway.internal"):
        set_default("sslmode", "disable")
    return _rebuild_postgres_url(host, port, user, password, path, urlencode(pairs))


def url_from_pg_env() -> str:
    host = (env_lookup("PGHOST", "PG_HOST") or "").strip()
    if not host or is_loopback_host(host):
        return ""
    user = (os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER") or "postgres").strip() or "postgres"
    password = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD") or ""
    port = (os.environ.get("PGPORT") or os.environ.get("POSTGRES_PORT") or "5432").strip() or "5432"
    dbname = (
        os.environ.get("PGDATABASE")
        or os.environ.get("POSTGRES_DB")
        or os.environ.get("POSTGRES_DATABASE")
        or "discovery"
    ).strip() or "discovery"
    hostport = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
    return normalize_database_url(
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{hostport}/{quote_plus(dbname)}"
    )


def _discover_hosted_database_url(skip: str = "") -> str:
    """Last resort: any env value that looks like a Render or Railway Postgres URL."""
    skip_n = normalize_database_url(skip)
    for key, value in os.environ.items():
        if key.upper().startswith("NIXPACKS") or key.upper() in {"PATH", "PYTHONPATH"}:
            continue
        alt = normalize_database_url(value or "")
        if alt and alt != skip_n and is_remote_postgres_url(alt):
            if is_hosted_postgres_host(_hostname(alt)):
                log.info("Using Postgres URL from %s because DATABASE_URL is not connectable.", key)
                return alt
    return ""


def rewrite_to_ipv6_literal(url: str) -> str:
    """Some private DNS is IPv6-only; libpq may try a dead IPv4 first."""
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
    path = parsed.path or "/discovery"
    rebuilt = f"postgresql://{auth}@[{ip}]:{port}{path}"
    if parsed.query:
        rebuilt += f"?{parsed.query}"
    return rebuilt


def iter_database_urls(explicit: str) -> list[str]:
    """Explicit URL first, then public/external fallbacks, then other injected URLs."""
    seen: list[str] = []
    out: list[str] = []

    def add(raw: str) -> None:
        url = normalize_database_url(raw or "")
        if not url or "${{" in url:
            return
        host = _hostname(url)
        if not host:
            return
        if on_hosted_platform() and is_loopback_host(host):
            return
        ordered: list[str] = []
        if is_render_postgres_host(host):
            ordered.extend(expand_render_postgres_url(url))
        else:
            ordered.append(url)
        for item in ordered:
            if item and item not in seen:
                seen.append(item)
                out.append(item)

    add(explicit)
    add(resolve_database_url(explicit))
    for key in HOSTED_URL_KEYS:
        add(env_lookup(key))
    add(url_from_pg_env())
    add(_discover_hosted_database_url(skip=explicit))
    return out


def resolve_database_url(explicit: str) -> str:
    """Prefer a reachable hosted URL when DATABASE_URL is local, empty, or a stub."""
    raw = normalize_database_url(explicit)
    if on_hosted_platform() and (not raw or not is_remote_postgres_url(raw)):
        if raw and is_loopback_host(_hostname(raw)):
            log.warning(
                "Ignoring loopback DATABASE_URL on hosted API (host=%s). "
                "Using Render Postgres Internal Database URL instead.",
                _hostname(raw) or "localhost",
            )
        raw = ""
    if on_render():
        render_url = normalize_database_url(env_lookup("RENDER_DATABASE_URL"))
        if is_remote_postgres_url(render_url):
            chosen = normalize_database_url(rewrite_render_external_to_internal(render_url))
            if chosen != raw:
                log.info("Using RENDER_DATABASE_URL (internal Render hostname).")
            return chosen
        if is_remote_postgres_url(raw):
            rewritten = normalize_database_url(rewrite_render_external_to_internal(raw))
            if rewritten != raw:
                log.info(
                    "Rewriting Render External Database URL %s → %s so TLS is not hairpinned.",
                    _hostname(raw),
                    _hostname(rewritten),
                )
            return rewritten
    if is_remote_postgres_url(raw):
        return raw
    for key in HOSTED_URL_KEYS:
        alt = normalize_database_url(env_lookup(key))
        if alt != raw and is_remote_postgres_url(alt):
            log.info("Using %s because DATABASE_URL is local, empty, or not a postgres URL.", key)
            return alt
    pg_url = url_from_pg_env()
    if pg_url:
        log.info("Using PGHOST/PGUSER environment for Postgres.")
        return pg_url
    discovered = _discover_hosted_database_url(skip=raw)
    if discovered:
        return discovered
    return raw


def conninfo_candidates(database_url: str) -> list[str]:
    """Try TLS modes that hosted public vs private hosts actually accept."""
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
    render_tls = {"sslmode": "require", "gssencmode": "disable", "channel_binding": "disable"}
    render_plain = {"sslmode": "disable", "gssencmode": "disable", "channel_binding": "disable"}
    if is_render_postgres_host(host):
        # Never sslmode=prefer: libpq starts TLS, Render closes, error is "SSL closed unexpectedly".
        # Short dpg-… host is private DNS only; if it does not resolve, use
        # dpg-….{region}-postgres.render.com (public DNS + TLS).
        for variant in expand_render_postgres_url(url):
            variant_host = _hostname(variant)
            if is_render_internal_host(variant_host):
                add(_with_query_params(variant, **render_plain))
                add(_with_query_params(variant, **render_tls))
            else:
                add(_with_query_params(variant, **render_tls))
        return out
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
    host, port, _user, _password, _path, _query = _split_postgres_url(database_url)
    host = host or "127.0.0.1"
    if host in {"localhost", "::1"}:
        host = "127.0.0.1"
    try:
        port_n = int(port or "5432")
    except ValueError:
        port_n = 5432
    try:
        with socket.create_connection((host, port_n), timeout=timeout):
            return True
    except OSError:
        return False


def handshake_database_url(database_url: str, *, connect_timeout: int = 8) -> str:
    """Return the conninfo that actually completed SELECT 1."""
    last_exc: Exception | None = None
    for conninfo in conninfo_candidates(database_url):
        host = _hostname(conninfo)
        _h, port, _user, _password, _path, _query = _split_postgres_url(conninfo)
        if host and not is_loopback_host(host) and not postgres_host_resolves(host, port or "5432"):
            last_exc = OSError(f"failed to resolve host '{host}': Name or service not known")
            log.warning(
                "Postgres host %s did not resolve; trying dpg-….region-postgres.render.com next.",
                host,
            )
            continue
        try:
            with psycopg.connect(conninfo, connect_timeout=connect_timeout) as conn:
                conn.execute("SELECT 1")
            return conninfo
        except Exception as exc:  # noqa: BLE001 — try the next TLS mode
            last_exc = exc
            log.warning(
                "Postgres handshake failed host=%s sslmode=%s (%s).",
                host or "?",
                dict(parse_qsl(_query)).get("sslmode", "default"),
                exc,
            )
    if last_exc is not None:
        raise last_exc
    raise PostgresRequiredError(POSTGRES_UNREACHABLE)


def postgres_connect(database_url: str, **kwargs):
    """Open a psycopg connection, retrying hosted TLS modes."""
    last_exc: Exception | None = None
    timeout = int(kwargs.get("connect_timeout") or 8)
    for conninfo in conninfo_candidates(database_url):
        host = _hostname(conninfo)
        _h, port, _user, _password, _path, _query = _split_postgres_url(conninfo)
        if host and not is_loopback_host(host) and not postgres_host_resolves(host, port or "5432"):
            last_exc = OSError(f"failed to resolve host '{host}': Name or service not known")
            log.warning("Postgres host %s did not resolve; trying public Render hostname next.", host)
            continue
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
        # Hosted private DNS can be IPv6; a Python TCP probe there false-negatives.
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
    env_url = normalize_database_url(env_lookup("RENDER_DATABASE_URL") or env_lookup("DATABASE_URL") or "")
    cfg.database_url = resolve_database_url(env_url or cfg.database_url)
    host = _hostname(cfg.database_url)
    urls = iter_database_urls(cfg.database_url)
    if cfg.require_postgres and on_hosted_platform():
        remote = [item for item in urls if not is_loopback_host(_hostname(item))]
        if not remote:
            if is_loopback_host(_hostname(env_url)):
                raise PostgresRequiredError(HOSTED_LOCAL_URL)
            raise PostgresRequiredError(HOSTED_INVALID_URL)
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
    used_internal = any(is_render_internal_host(_hostname(item)) for item in urls)
    dns_note = any(
        "not known" in (note or "").lower() or "did not resolve" in (note or "").lower() or "failed to resolve" in (note or "").lower()
        for note in last_errors
    )
    ssl_note = any("ssl" in (note or "").lower() and "closed" in (note or "").lower() for note in last_errors)
    if dns_note:
        extra = " " + HOSTED_RENDER_DNS
    elif ssl_note:
        extra = " " + HOSTED_RENDER_TLS
    elif used_internal or is_render_internal_host(host):
        extra = (
            " Internal Render host failed. Confirm discovery-api and discovery-db "
            "share a region (Singapore). The public hostname "
            "dpg-….{region}-postgres.render.com is the fallback when private DNS "
            "does not resolve."
        )
    elif host.endswith(".railway.internal") or any(
        _hostname(item).endswith(".railway.internal") for item in urls
    ):
        extra = (
            " Private host failed. Paste DATABASE_PUBLIC_URL from the database "
            "service (host ends with .rlwy.net) onto the API, with sslmode=require."
        )
    raise PostgresRequiredError(f"{POSTGRES_REQUIRED} Last check: {last_note}.{extra}")


def connect_store(cfg: Settings) -> DocumentRepository:
    """Postgres when reachable; otherwise `local_store_path` so ingest and the API share data.

    When `require_postgres` is true (Render), wait then fail hard — never pickle.
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
