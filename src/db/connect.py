"""Pick Postgres when it is listening; otherwise the shared local file store."""

from __future__ import annotations

import logging
import os
import random
import re
import socket
import struct
import time
from pathlib import Path
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
    "Check DATABASE_URL (Neon host ending in .neon.tech with sslmode=require, or POSTGRES_URL "
    "from the Vercel Neon integration). Refusing local_store.pkl so "
    "the API cannot serve an empty corpus."
)

HOSTED_LOCAL_URL = (
    "DATABASE_URL still points at localhost on the hosted API. Set DATABASE_URL "
    "or POSTGRES_URL to the Neon (or other pgvector) URL, redeploy. "
    "Do not paste the laptop .env value."
)

HOSTED_RENDER_DNS = (
    "Render private DNS cannot resolve the short host dpg-… (Name or service not known). "
    "That name only works on the same-region private network. The API also tries "
    "dpg-….{region}-postgres.render.com which resolves on public DNS. Confirm "
    "discovery-api and discovery-db are both in Singapore."
)

HOSTED_RENDER_TLS = (
    "Render Postgres closed TLS unexpectedly. The public hostname is retried "
    "with sslnegotiation=direct (libpq 17+) plus sslmode=require and "
    "channel_binding=disable. Prefer the private dpg- host after DNS works."
)

HOSTED_INVALID_URL = (
    "DATABASE_URL is not a real Postgres URL (no hostname). On the Vercel API "
    "project set DATABASE_URL or POSTGRES_URL to the Neon connection string "
    "(host *.neon.tech, sslmode=require), then redeploy."
)

HOSTED_PLACEHOLDER_URL = (
    "DATABASE_URL is still the docs placeholder (host *.neon.tech or ep-….neon.tech). "
    "Paste the real Neon connection string from the Neon dashboard — a hostname like "
    "ep-cool-name-a1b2c3.ap-southeast-1.aws.neon.tech with sslmode=require — then redeploy."
)

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
_PUBLIC_DNS = {
    "8.8.8.8",
    "8.8.4.4",
    "1.1.1.1",
    "1.0.0.1",
    "9.9.9.9",
    "208.67.222.222",
    "208.67.220.220",
}
_PSYCOPG_ONLY = frozenset({"row_factory", "autocommit", "cursor_factory"})
HOSTED_URL_KEYS = (
    "DISCOVERY_DATABASE_URL",
    "RENDER_DATABASE_URL",
    "DATABASE_PRIVATE_URL",
    "DATABASE_PUBLIC_URL",
    "POSTGRES_URL_NON_POOLING",
    "DATABASE_URL_UNPOOLED",
    "POSTGRES_URL",
    "DATABASE_URL",
    "POSTGRES_PRISMA_URL",
)
_POSTGRES_DSN_RE = re.compile(r"postgres(?:ql)?://[^\s'\"<>]+", re.IGNORECASE)
_DPG_HOST_RE = re.compile(r"@(dpg-[A-Za-z0-9.-]+)", re.IGNORECASE)

_loopback_database_url_ignored = False
_logged_loopback_ignore = False


def is_loopback_host(host: str | None) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


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


def on_vercel() -> bool:
    from src.config import hosted_vercel

    return hosted_vercel()


def on_hosted_platform() -> bool:
    return on_render() or on_railway() or on_vercel()


def running_in_docker() -> bool:
    """True inside a Docker image (Render native Python is not this)."""
    return Path("/.dockerenv").exists()


def resolv_conf_with_private_first(text: str, extra: list[str]) -> str:
    """Prepend VPC/Docker DNS so dpg- names are not sent to 8.8.8.8 first."""
    existing: list[str] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            existing.append(parts[1].strip())
    prepend = [ns for ns in extra if ns and ns not in existing and ns not in _PUBLIC_DNS]
    if not prepend:
        return text
    header = "".join(f"nameserver {ns}\n" for ns in prepend)
    return header + (text or "")


def res_options_without_ndots(options: str) -> str:
    """ndots:0 makes dpg- a FQDN and skips Render's search list."""
    return " ".join(
        part for part in (options or "").split() if not part.lower().startswith("ndots")
    )


def apply_resolver_workarounds() -> None:
    """Fix Docker mDNS; do not rewrite native Render DNS (that broke dpg-)."""
    if not on_render():
        return
    docker = running_in_docker()
    # Native Python already resolves dpg- via Render's search list. ndots:0 and
    # fake nameservers (127.0.0.11 / .internal) made that lookup fail.
    cleaned = res_options_without_ndots(os.environ.get("RES_OPTIONS") or "")
    if cleaned:
        os.environ["RES_OPTIONS"] = cleaned
    else:
        os.environ.pop("RES_OPTIONS", None)
    if not docker:
        log.info(
            "Render native resolver nameservers=%s RES_OPTIONS=%s",
            _resolv_conf_nameservers(),
            os.environ.get("RES_OPTIONS", ""),
        )
        return
    path = Path("/etc/nsswitch.conf")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        log.info(
            "Render Docker resolver nameservers=%s RES_OPTIONS=%s",
            _resolv_conf_nameservers(),
            os.environ.get("RES_OPTIONS", ""),
        )
        return
    hosts_line = next((line for line in text.splitlines() if line.strip().startswith("hosts:")), "")
    if not (hosts_line and "mdns" not in hosts_line and re.search(r"\bdns\b", hosts_line)):
        new = re.sub(r"^hosts:.*$", "hosts: files dns", text, flags=re.M)
        if new == text:
            if not re.search(r"^hosts:", text, flags=re.M):
                new = text.rstrip() + "\nhosts: files dns\n"
            else:
                new = text
        if new != text:
            try:
                path.write_text(new, encoding="utf-8")
                log.info("Updated /etc/nsswitch.conf so Render dpg- hostnames use DNS, not mDNS.")
            except OSError as exc:
                log.warning("Could not update /etc/nsswitch.conf (%s).", exc)
    log.info(
        "Render Docker resolver nameservers=%s RES_OPTIONS=%s",
        _resolv_conf_nameservers(),
        os.environ.get("RES_OPTIONS", ""),
    )


def render_private_service_names() -> list[str]:
    names: list[str] = []
    extra = (os.environ.get("RENDER_POSTGRES_NAME") or "").strip().lower()
    if extra:
        names.append(extra)
    if "discovery-db" not in names:
        names.append("discovery-db")
    return names


def env_lookup(*keys: str) -> str:
    """Read an env var, ignoring key case (Render/Linux are case-sensitive)."""
    wanted = {key.upper() for key in keys if key}
    for name, value in os.environ.items():
        if name.upper() in wanted and (value or "").strip():
            return (value or "").strip()
    return ""


def _hostname(database_url: str) -> str:
    host, _port, _user, _password, _path, _query = _split_postgres_url(database_url)
    host = (host or "").strip().lower()
    if host and not is_loopback_host(host):
        return host
    match = _DPG_HOST_RE.search(database_url or "")
    if match:
        return match.group(1).strip().lower()
    return host


def looks_like_hosted_postgres_dsn(url: str) -> bool:
    text = (url or "").lower()
    return (
        "dpg-" in text
        or "postgres.render.com" in text
        or ".railway.internal" in text
        or ".rlwy.net" in text
        or ".railway.app" in text
        or ".neon.tech" in text
        or ".neon.build" in text
        or ".supabase.co" in text
        or ".supabase.com" in text
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
    at = rest.rfind("@")
    search_from = at + 1 if at != -1 else 0
    qpos = rest.find("?", search_from)
    if qpos != -1:
        rest, query = rest[:qpos], rest[qpos + 1 :]
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
    if (not host or is_loopback_host(host)) and "dpg-" in cleaned.lower():
        match = _DPG_HOST_RE.search(cleaned)
        if match:
            host = match.group(1)
            if ":" in host and not host.startswith("["):
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
    h = (host or "").strip().lower().rstrip(".")
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
    """Private short host first on native Render; public hostname first in Docker."""
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

    public = f"{pg_id}.{region}-postgres.render.com"
    # Render internal host is dpg-xxxxx-a (no .internal suffix — that NXDOMAINs).
    # Blueprint name discovery-db is not a Postgres DNS name.
    if on_render() and running_in_docker():
        add_host(public)
        add_host(pg_id)
    elif on_render():
        add_host(pg_id)
        add_host(public)
    else:
        add_host(public)
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


def host_resolves_privately(host: str, port: str | int = 5432) -> bool:
    """True only when DNS returns RFC1918/CGNAT IPv4 — not a public AWS IP."""
    ips = resolved_ips_for_host(host, port)
    v4 = [ip for ip in ips if ip and ":" not in ip]
    return bool(v4) and all(is_private_ip(ip) for ip in v4)


def resolved_ips_for_host(host: str, port: str | int = 5432) -> list[str]:
    host = (host or "").strip().rstrip(".")
    if not host or is_loopback_host(host):
        return []
    try:
        port_n = int(port or 5432)
    except (TypeError, ValueError):
        port_n = 5432
    try:
        infos = socket.getaddrinfo(host, port_n, type=socket.SOCK_STREAM)
    except OSError:
        return []
    found: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip and ip not in found:
            found.append(ip)
    return found


def postgres_host_resolves(host: str, port: str | int = 5432) -> bool:
    if not host or is_loopback_host(host):
        return True
    return bool(resolved_ips_for_host(host, port))


def is_private_ip(ip: str) -> bool:
    """True for RFC1918, link-local, and CGNAT (100.64/10) addresses."""
    try:
        packed = socket.inet_aton(ip)
    except OSError:
        return False
    n = int.from_bytes(packed, "big")
    return (
        (n >> 24) == 10
        or (n >> 20) == 0xAC1
        or (n >> 16) == 0xC0A8
        or (n >> 16) == 0xA9FE
        or (n >> 22) == 0x191  # 100.64.0.0/10
    )


def _default_gateway() -> str:
    try:
        lines = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "00000000":
            try:
                return socket.inet_ntoa(bytes.fromhex(parts[2])[::-1])
            except (ValueError, OSError):
                return ""
    return ""


def docker_dns_servers() -> list[str]:
    """Nameservers that can answer Render private names (not 8.8.8.8)."""
    servers: list[str] = []
    for ns in _resolv_conf_nameservers():
        if ns not in _PUBLIC_DNS and ns not in servers:
            servers.append(ns)
    for ns in ("127.0.0.11", "169.254.169.254"):
        if ns not in servers:
            servers.append(ns)
    gw = _default_gateway()
    if gw and gw not in servers and gw not in _PUBLIC_DNS:
        servers.append(gw)
    return servers


def _resolv_conf_nameservers() -> list[str]:
    servers: list[str] = []
    try:
        text = Path("/etc/resolv.conf").read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            ip = parts[1].strip()
            if ip and ip not in servers:
                servers.append(ip)
    return servers


def dns_lookup_a(
    host: str,
    timeout: float = 0.8,
    nameservers: list[str] | None = None,
) -> list[str]:
    """A records via UDP, bypassing nsswitch mDNS."""
    host = (host or "").strip().rstrip(".").lower()
    if not host or is_loopback_host(host) or ":" in host:
        return []
    qname = bytearray()
    try:
        for label in host.split("."):
            raw = label.encode("ascii")
            if not raw or len(raw) > 63:
                return []
            qname.append(len(raw))
            qname.extend(raw)
    except UnicodeEncodeError:
        return []
    qname.append(0)
    txid = random.randint(0, 65535)
    req = struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0) + bytes(qname) + struct.pack("!HH", 1, 1)

    def skip_name(buf: bytes, offset: int) -> int:
        jumps = 0
        while offset < len(buf) and jumps < 16:
            length = buf[offset]
            if length == 0:
                return offset + 1
            if length & 0xC0 == 0xC0:
                return offset + 2
            offset += 1 + (length & 0x3F)
            jumps += 1
        return offset

    found: list[str] = []
    servers = list(nameservers) if nameservers is not None else _resolv_conf_nameservers()
    if nameservers is None and "." not in host:
        servers = [ns for ns in servers if ns not in _PUBLIC_DNS]
        if not servers:
            return []
    for ns in servers:
        family = socket.AF_INET6 if ":" in ns else socket.AF_INET
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.sendto(req, (ns, 53))
            data, _addr = sock.recvfrom(2048)
        except OSError:
            continue
        finally:
            if sock is not None:
                sock.close()
        if len(data) < 12:
            continue
        r_id, _flags, qdcount, ancount, _nscount, _arcount = struct.unpack("!HHHHHH", data[:12])
        if r_id != txid or ancount == 0:
            continue
        offset = 12
        try:
            for _ in range(qdcount):
                offset = skip_name(data, offset) + 4
            for _ in range(ancount):
                offset = skip_name(data, offset)
                if offset + 10 > len(data):
                    break
                rtype, _rclass, _ttl, rdlen = struct.unpack("!HHIH", data[offset : offset + 10])
                offset += 10
                if rtype == 1 and rdlen == 4 and offset + 4 <= len(data):
                    ip = socket.inet_ntoa(data[offset : offset + 4])
                    if ip not in found:
                        found.append(ip)
                offset += rdlen
        except (struct.error, OSError, IndexError):
            continue
        if found:
            break
    return found


def private_ips_for_host(host: str) -> list[str]:
    """Split-horizon: ask Docker/VPC DNS for a private A record."""
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        return []
    found: list[str] = []
    for ns in docker_dns_servers():
        for ip in dns_lookup_a(host, timeout=0.6, nameservers=[ns]):
            if ip not in found:
                found.append(ip)
    return [ip for ip in found if is_private_ip(ip)]


def _conninfo_with_hostaddrs(conninfo: str) -> list[str]:
    """Prefer concrete IPs when DNS is dual-stack (Vercel often cannot bind IPv6)."""
    host = _hostname(conninfo)
    if not host:
        return []
    out: list[str] = []
    if is_render_postgres_host(host):
        names = [host]
        for url in expand_render_postgres_url(conninfo):
            extra = _hostname(url)
            if extra and extra not in names:
                names.append(extra)
        ips: list[str] = []
        for name in names:
            for ip in private_ips_for_host(name):
                if ip not in ips:
                    ips.append(ip)
        for ip in ips:
            out.append(
                _with_query_params(
                    conninfo,
                    hostaddr=ip,
                    sslmode="disable",
                    gssencmode="disable",
                    channel_binding="disable",
                )
            )
        return out
    if is_neon_postgres_host(host) or is_supabase_postgres_host(host):
        # Keep hostname for TLS SNI; pin IPv4 via hostaddr so libpq does not try IPv6 first.
        for ip in resolved_ips_for_host(host):
            if not ip or ":" in ip:
                continue
            out.append(
                _with_query_params(
                    conninfo,
                    hostaddr=ip,
                    sslmode="require",
                    channel_binding="disable",
                )
            )
    return out


def conninfo_to_kwargs(url: str) -> dict[str, str]:
    host, port, user, password, path, query = _split_postgres_url(url)
    dbname = unquote((path or "/discovery").lstrip("/").split("/")[0] or "discovery")
    params: dict[str, str] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key:
            params[key] = value
    if host:
        params["host"] = host
    params["port"] = port or "5432"
    if user:
        params["user"] = unquote(user)
    if password:
        params["password"] = unquote(password)
    params["dbname"] = dbname
    return params


def open_psycopg(conninfo: str, **kwargs):
    """Connect with libpq kwargs so SNI/password encoding stay correct."""
    timeout = int(kwargs.pop("connect_timeout", None) or 8)
    psyco = {key: kwargs.pop(key) for key in list(kwargs) if key in _PSYCOPG_ONLY}
    params = conninfo_to_kwargs(conninfo)
    try:
        return psycopg.connect(connect_timeout=timeout, **params, **psyco)
    except TypeError:
        params.pop("sslnegotiation", None)
        params.pop("sslrootcert", None)
        return psycopg.connect(connect_timeout=timeout, **params, **psyco)


def is_render_postgres_host(host: str) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if h.endswith(".render.com") or h.startswith("dpg-"):
        return True
    return bool(on_render() and h in set(render_private_service_names()))


def is_railway_postgres_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h.endswith(".railway.internal") or h.endswith(".rlwy.net") or h.endswith(".railway.app")


def is_neon_postgres_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h.endswith(".neon.tech") or h.endswith(".neon.build")


def is_supabase_postgres_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h.endswith(".supabase.co") or h.endswith(".supabase.com")


def is_hosted_postgres_host(host: str) -> bool:
    return (
        is_render_postgres_host(host)
        or is_railway_postgres_host(host)
        or is_neon_postgres_host(host)
        or is_supabase_postgres_host(host)
    )


def is_render_internal_host(host: str) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if not h or is_loopback_host(h) or "postgres.render.com" in h:
        return False
    if h.startswith("dpg-") or h.endswith(".internal"):
        return True
    return h in set(render_private_service_names())


def is_placeholder_postgres_host(host: str) -> bool:
    """Docs samples like *.neon.tech or ep-….neon.tech are not real DNS names."""
    h = (host or "").strip().lower()
    if not h:
        return False
    if "*" in h or "\u2026" in h or h.startswith("<") or h.endswith(">") or "<" in h:
        return True
    if "..." in h:
        return True
    if h in {"neon.tech", "example.com", "host.neon.tech"}:
        return True
    first = h.split(".")[0]
    if first.startswith("ep-") and not re.search(r"ep-[a-z0-9]", first):
        return True
    return False


def is_placeholder_postgres_url(url: str) -> bool:
    """True when the DSN is a docs paste (ellipsis, *.neon.tech), not a live endpoint."""
    text = (url or "").strip()
    if not text:
        return False
    if "\u2026" in text:
        return True
    return is_placeholder_postgres_host(_hostname(text))


def is_remote_postgres_url(url: str) -> bool:
    """True when conninfo has a non-loopback host (not ${{…}} placeholders)."""
    cleaned = normalize_database_url(url)
    if not cleaned or "${{" in cleaned or "{{" in cleaned:
        return False
    if is_placeholder_postgres_url(cleaned):
        return False
    host = _hostname(cleaned)
    if not host or is_placeholder_postgres_host(host) or is_loopback_host(host):
        return False
    if looks_like_hosted_postgres_dsn(cleaned):
        return True
    if not cleaned.lower().startswith(("postgres://", "postgresql://")):
        return False
    return True


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
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k.lower() not in {"pgbouncer"}]
    keys = {key.lower() for key, _ in pairs}

    def set_default(key: str, value: str) -> None:
        nonlocal pairs, keys
        if key.lower() in keys:
            return
        pairs.append((key, value))
        keys.add(key.lower())

    def force_param(key: str, value: str) -> None:
        """Overwrite query params Neon may set to values that break serverless (e.g. channel_binding=require)."""
        nonlocal pairs, keys
        pairs = [(k, v) for k, v in pairs if k.lower() != key.lower()]
        pairs.append((key, value))
        keys.add(key.lower())

    host_l = host.lower()
    if is_render_postgres_host(host_l):
        set_default("sslmode", "require")
        set_default("gssencmode", "disable")
        force_param("channel_binding", "disable")
    elif (
        host_l.endswith(".rlwy.net")
        or host_l.endswith(".railway.app")
        or is_neon_postgres_host(host_l)
        or is_supabase_postgres_host(host_l)
    ):
        force_param("sslmode", "require")
        force_param("channel_binding", "disable")
    elif host_l.endswith(".railway.internal"):
        set_default("sslmode", "disable")
    return _rebuild_postgres_url(host, port, user, password, path, urlencode(pairs))


def postgres_urls_in_text(text: str) -> list[str]:
    """Pull postgresql:// DSNs out of a Connect-menu paste or a messy env value."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _POSTGRES_DSN_RE.finditer(text or ""):
        url = normalize_database_url(match.group(0).rstrip(".,);"))
        if url and url not in seen:
            seen.add(url)
            found.append(url)
    kv = url_from_libpq_kv(text or "")
    if kv and kv not in seen:
        found.append(kv)
    return found


def url_from_libpq_kv(text: str) -> str:
    blob = (text or "").strip()
    if not blob or "://" in blob:
        return ""
    if not re.search(r"(?i)\bhost\s*=", blob):
        return ""
    kv: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z_]+)\s*=\s*(\S+)", blob):
        kv[match.group(1).lower()] = match.group(2).strip("'\"")
    host = kv.get("host") or kv.get("hostaddr") or ""
    if not host or is_loopback_host(host):
        return ""
    user = kv.get("user") or kv.get("username") or "postgres"
    password = kv.get("password") or ""
    port = kv.get("port") or "5432"
    dbname = kv.get("dbname") or kv.get("database") or "discovery"
    return normalize_database_url(
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(dbname)}"
    )


def apply_hosted_database_env() -> str:
    """On Vercel/Railway/Render, replace leftover laptop DATABASE_URL=localhost with the real DSN."""
    global _loopback_database_url_ignored
    apply_resolver_workarounds()
    if not on_hosted_platform():
        return env_lookup("DATABASE_URL")
    _loopback_database_url_ignored = False
    current = normalize_database_url(env_lookup("DATABASE_URL"))
    was_loopback = bool(current) and is_loopback_host(_hostname(current))
    if current and is_remote_postgres_url(current) and not was_loopback:
        os.environ["DATABASE_URL"] = current
        return current
    if was_loopback:
        for name in list(os.environ):
            if name.upper() == "DATABASE_URL":
                os.environ.pop(name, None)
    chosen = resolve_database_url("")
    if chosen and is_remote_postgres_url(chosen):
        os.environ["DATABASE_URL"] = chosen
        _loopback_database_url_ignored = False
        log.info("Pinned DATABASE_URL to hosted Postgres host=%s", _hostname(chosen))
        return chosen
    _loopback_database_url_ignored = was_loopback
    return ""


def url_from_pg_env() -> str:
    host = (env_lookup("PGHOST", "PG_HOST", "POSTGRES_HOST", "POSTGRES_HOST_NON_POOLING") or "").strip()
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
    """Last resort: any env value that looks like a hosted Postgres URL."""
    skip_n = normalize_database_url(skip)
    for key, value in os.environ.items():
        if key.upper().startswith("NIXPACKS") or key.upper() in {"PATH", "PYTHONPATH"}:
            continue
        candidates = postgres_urls_in_text(value or "")
        if not candidates:
            normalized = normalize_database_url(value or "")
            if normalized:
                candidates = [normalized]
        for alt in candidates:
            if not alt or alt == skip_n or not is_remote_postgres_url(alt):
                continue
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
        if is_placeholder_postgres_host(host) or is_placeholder_postgres_url(url):
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
    global _logged_loopback_ignore
    raw = normalize_database_url(explicit)
    if not is_remote_postgres_url(raw):
        for item in postgres_urls_in_text(explicit or ""):
            if is_remote_postgres_url(item) and is_hosted_postgres_host(_hostname(item)):
                raw = item
                break
    if on_hosted_platform() and (not raw or not is_remote_postgres_url(raw)):
        if raw and is_loopback_host(_hostname(raw)):
            if not _logged_loopback_ignore:
                log.warning(
                    "Ignoring loopback DATABASE_URL on hosted API (host=%s).",
                    _hostname(raw) or "localhost",
                )
                _logged_loopback_ignore = True
        raw = ""
    if on_render():
        for key in ("DISCOVERY_DATABASE_URL", "RENDER_DATABASE_URL"):
            render_url = normalize_database_url(env_lookup(key))
            if is_remote_postgres_url(render_url):
                if render_url != raw:
                    log.info("Using %s (host=%s).", key, _hostname(render_url))
                return render_url
        if is_remote_postgres_url(raw):
            return raw
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


def ssl_tls_required(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "ssl/tls required" in msg or "ssl is required" in msg or "ssl required" in msg


def render_require_conninfo(url: str) -> str:
    """External Render URL with sslmode=require (SNI + TLS). Never disable against a public IP."""
    host, port, user, password, path, query = _split_postgres_url(url)
    pg_id = render_postgres_id(host)
    if pg_id:
        host = f"{pg_id}.{render_postgres_region(host)}-postgres.render.com"
    rebuilt = _rebuild_postgres_url(host, port or "5432", user, password, path, "")
    return _with_query_params(
        rebuilt,
        sslmode="require",
        gssencmode="disable",
        channel_binding="disable",
    )


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
    render_tls_direct = {**render_tls, "sslnegotiation": "direct"}
    render_plain = {"sslmode": "disable", "gssencmode": "disable", "channel_binding": "disable"}
    if is_render_postgres_host(host):
        # Public AWS IPs (18.x, 13.x, 3.x) require TLS. sslmode=disable → FATAL SSL/TLS required.
        # Short dpg- names often resolve to that same public IP, so disable is wrong there too.
        # Only disable when DNS actually returns a private address.
        add(render_require_conninfo(url))
        for variant in expand_render_postgres_url(url):
            variant_host = _hostname(variant)
            if is_render_internal_host(variant_host):
                if host_resolves_privately(variant_host):
                    add(_with_query_params(variant, **render_plain))
                    add(_with_query_params(variant, **render_tls))
                continue
            add(_with_query_params(variant, **render_tls))
            add(_with_query_params(variant, **render_tls_direct))
            add(_with_query_params(variant, sslmode="require", sslnegotiation="direct"))
            add(_with_query_params(variant, sslmode="require"))
        return out
    if host.endswith(".railway.internal"):
        disabled = _set_query_param(url, "sslmode", "disable")
        add(disabled)
        ipv6 = rewrite_to_ipv6_literal(disabled)
        add(ipv6)
        add(_set_query_param(url, "sslmode", "prefer"))
        add(_set_query_param(url, "sslmode", "require"))
    elif is_neon_postgres_host(host) or is_supabase_postgres_host(host):
        add(_with_query_params(url, sslmode="require", channel_binding="disable"))
        add(url)
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
    apply_resolver_workarounds()
    last_exc: Exception | None = None
    tried_private: set[str] = set()
    for conninfo in conninfo_candidates(database_url):
        host = _hostname(conninfo)
        if host and host not in tried_private:
            tried_private.add(host)
            for alt in _conninfo_with_hostaddrs(conninfo):
                try:
                    with open_psycopg(alt, connect_timeout=connect_timeout) as conn:
                        conn.execute("SELECT 1")
                    log.info("Postgres connected on private hostaddr host=%s", host)
                    return alt
                except Exception as exc:  # noqa: BLE001 — try the next TLS mode
                    last_exc = exc
                    log.warning("Postgres private IP handshake failed host=%s (%s).", host, exc)
        _h, port, _user, _password, _path, _query = _split_postgres_url(conninfo)
        if host and not is_loopback_host(host) and not postgres_host_resolves(host, port or "5432"):
            last_exc = OSError(f"failed to resolve host '{host}': Name or service not known")
            log.info("Postgres host %s did not resolve; trying next hostname.", host)
            continue
        try:
            with open_psycopg(conninfo, connect_timeout=connect_timeout) as conn:
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
            if ssl_tls_required(exc):
                required = render_require_conninfo(conninfo)
                if required != conninfo:
                    try:
                        with open_psycopg(required, connect_timeout=connect_timeout) as conn:
                            conn.execute("SELECT 1")
                        log.info(
                            "Postgres connected with sslmode=require host=%s",
                            _hostname(required),
                        )
                        return required
                    except Exception as retry_exc:  # noqa: BLE001
                        last_exc = retry_exc
                        log.warning(
                            "Postgres sslmode=require retry failed host=%s (%s).",
                            _hostname(required),
                            retry_exc,
                        )
    if last_exc is not None:
        raise last_exc
    raise PostgresRequiredError(POSTGRES_UNREACHABLE)


def postgres_connect(database_url: str, **kwargs):
    """Open a psycopg connection, retrying hosted TLS modes."""
    last_exc: Exception | None = None
    timeout = int(kwargs.get("connect_timeout") or 8)
    tried_private: set[str] = set()
    for conninfo in conninfo_candidates(database_url):
        host = _hostname(conninfo)
        if host and host not in tried_private:
            tried_private.add(host)
            for alt in _conninfo_with_hostaddrs(conninfo):
                try:
                    options = dict(kwargs)
                    options.setdefault("connect_timeout", timeout)
                    return open_psycopg(alt, **options)
                except Exception as exc:  # noqa: BLE001 — try the next TLS mode
                    last_exc = exc
                    log.warning("Postgres private IP connect failed host=%s (%s).", host, exc)
        _h, port, _user, _password, _path, _query = _split_postgres_url(conninfo)
        if host and not is_loopback_host(host) and not postgres_host_resolves(host, port or "5432"):
            last_exc = OSError(f"failed to resolve host '{host}': Name or service not known")
            log.info("Postgres host %s did not resolve; trying next hostname.", host)
            continue
        try:
            options = dict(kwargs)
            options.setdefault("connect_timeout", timeout)
            return open_psycopg(conninfo, **options)
        except Exception as exc:  # noqa: BLE001 — try the next TLS mode
            last_exc = exc
            log.warning("Postgres connect failed host=%s (%s).", _hostname(conninfo) or "?", exc)
            if ssl_tls_required(exc):
                required = render_require_conninfo(conninfo)
                if required != conninfo:
                    try:
                        options = dict(kwargs)
                        options.setdefault("connect_timeout", timeout)
                        return open_psycopg(required, **options)
                    except Exception as retry_exc:  # noqa: BLE001
                        last_exc = retry_exc
                        log.warning(
                            "Postgres sslmode=require retry failed host=%s (%s).",
                            _hostname(required),
                            retry_exc,
                        )
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
        if not host or "${{" in url or is_placeholder_postgres_host(host):
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
    pinned = apply_hosted_database_env()
    original = pinned or cfg.database_url or env_lookup("DATABASE_URL")
    original_placeholder = is_placeholder_postgres_url(original) or is_placeholder_postgres_host(
        _hostname(original)
    )
    cfg.database_url = resolve_database_url(pinned or cfg.database_url)
    host = _hostname(cfg.database_url)
    urls = iter_database_urls(cfg.database_url)
    remote = [item for item in urls if is_remote_postgres_url(item)]
    placeholder = original_placeholder or is_placeholder_postgres_url(cfg.database_url) or is_placeholder_postgres_host(host)
    if remote and (placeholder or not is_remote_postgres_url(cfg.database_url)):
        cfg.database_url = remote[0]
        host = _hostname(cfg.database_url)
        placeholder = False
    if cfg.require_postgres and placeholder and not remote:
        raise PostgresRequiredError(HOSTED_PLACEHOLDER_URL)
    if cfg.require_postgres and on_hosted_platform():
        if not remote:
            if (
                placeholder
                or original_placeholder
                or "*" in (original or "")
                or "\u2026" in (original or "")
                or "*" in (cfg.database_url or "")
                or "\u2026" in (cfg.database_url or "")
            ):
                raise PostgresRequiredError(HOSTED_PLACEHOLDER_URL)
            if _loopback_database_url_ignored or is_loopback_host(host):
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
        render_fail = is_render_postgres_host(host) or any(
            is_render_postgres_host(_hostname(item)) for item in urls
        )
        neon_fail = is_neon_postgres_host(host) or any(
            is_neon_postgres_host(_hostname(item)) for item in urls
        )
        if render_fail and not neon_fail:
            extra = " " + HOSTED_RENDER_DNS
        elif neon_fail:
            extra = (
                " Neon hostname did not resolve. If it contains '…' or '*', it is still "
                "the docs example. Paste the real endpoint from the Neon dashboard "
                "(ep-cool-name-a1b2c3.ap-southeast-1.aws.neon.tech), then redeploy."
            )
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
    elif any(is_neon_postgres_host(_hostname(item)) for item in urls) or is_neon_postgres_host(host):
        extra = (
            " Neon host failed. Use the direct (non-pooled) URL for migrations "
            "(POSTGRES_URL_NON_POOLING) and sslmode=require. Confirm pgvector "
            "is enabled (CREATE EXTENSION vector)."
        )
    raise PostgresRequiredError(f"{POSTGRES_REQUIRED} Last check: {last_note}.{extra}")


def connect_store(cfg: Settings) -> DocumentRepository:
    """Postgres when reachable; otherwise `local_store_path` so ingest and the API share data.

    When `require_postgres` is true (Vercel/Railway/Render), wait then fail hard — never pickle.
    """
    from src.config import path_parent_unwritable

    apply_hosted_database_env()
    cfg.database_url = resolve_database_url(cfg.database_url)
    if cfg.require_postgres or on_vercel():
        return wait_for_postgres(cfg)

    repo = try_postgres(cfg)
    if repo is not None:
        return repo
    pickle_error = (
        "Cannot create local_store.pkl "
        f"({cfg.local_store_path}). On Vercel the filesystem is "
        "read-only except /tmp. Set DATABASE_URL to a real Neon host "
        "(ep-cool-name-a1b2c3.ap-southeast-1.aws.neon.tech), not the docs "
        "placeholder *.neon.tech or ep-….neon.tech."
    )
    if path_parent_unwritable(cfg.local_store_path.parent):
        raise PostgresRequiredError(pickle_error)
    log.warning(
        "Postgres not reachable on DATABASE_URL. Using local file store at %s.",
        cfg.local_store_path,
    )
    try:
        cfg.local_store_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PostgresRequiredError(f"{pickle_error} ({exc})") from exc
    return PersistentMemoryRepository(cfg.local_store_path)
