"""Render/Vercel deploy helpers (docs/deployment-plan.md)."""

import os
from pathlib import Path

import pytest

from src.api.app import pending_store_detail
from src.config import Settings, load_settings, resolve_listen_port
from src.db.connect import (
    PostgresRequiredError,
    apply_hosted_database_env,
    connect_store,
    conninfo_candidates,
    normalize_database_url,
    postgres_urls_in_text,
    resolve_database_url,
    rewrite_render_external_to_internal,
    wait_for_postgres,
    _hostname,
)
from src.db.local import PersistentMemoryRepository


def test_render_blueprint_injects_postgres_url():
    from pathlib import Path

    text = Path(__file__).resolve().parents[1].joinpath("render.yaml").read_text(encoding="utf-8")
    assert "fromDatabase" in text
    assert "RENDER_DATABASE_URL" in text
    assert "DISCOVERY_DATABASE_URL" in text
    assert "connectionString" in text
    assert "PGHOST" in text
    assert "RENDER_POSTGRES_REGION" in text
    assert "RENDER_POSTGRES_NAME" in text
    assert "runtime: python" in text
    assert "requirements-api.txt" in text
    assert "python -m src.api" in text
    assert "PYTHON_VERSION" in text
    assert "runtime: docker" not in text
    assert "ndots:0" not in text


def test_resolve_listen_port_prefers_cli_over_platform_port(monkeypatch):
    monkeypatch.setenv("PORT", "9999")
    settings = Settings(api_port=8000, author_hmac_secret="deploy-hmac")
    assert resolve_listen_port(8080, settings) == 8080
    assert resolve_listen_port(None, settings) == 9999


def test_resolve_listen_port_falls_back_to_api_port(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    settings = Settings(api_port=8000, author_hmac_secret="deploy-hmac")
    assert resolve_listen_port(None, settings) == 8000


def test_resolve_listen_port_rejects_non_integer_platform_port(monkeypatch):
    monkeypatch.setenv("PORT", "not-a-port")
    with pytest.raises(ValueError, match="PORT must be an integer"):
        resolve_listen_port(None, Settings(author_hmac_secret="deploy-hmac"))


def test_normalize_database_url_rewrites_scheme_and_hosted_ssl():
    assert normalize_database_url("postgres://u:p@h:5432/db").startswith("postgresql://")
    render_ext = normalize_database_url(
        "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com/discovery"
    )
    assert "sslmode=require" in render_ext
    assert "channel_binding=disable" in render_ext
    assert "gssencmode=disable" in render_ext
    assert "dpg-abc123-a.singapore-postgres.render.com:5432" in render_ext
    render_int = normalize_database_url("postgresql://u:p@dpg-abc123-a:5432/discovery")
    assert "sslmode=require" in render_int
    public = normalize_database_url("postgresql://u:p@turn.proxy.rlwy.net:1234/railway")
    assert "sslmode=require" in public
    internal = normalize_database_url("postgresql://u:p@postgres.railway.internal:5432/railway")
    assert "sslmode=disable" in internal
    quoted = normalize_database_url('  "postgresql://u:p@h:5432/db"  ')
    assert quoted == "postgresql://u:p@h:5432/db"


def test_normalize_database_url_keeps_host_when_password_has_at():
    url = normalize_database_url(
        "postgresql://u:p@ss:word@dpg-abc123-a.singapore-postgres.render.com/discovery"
    )
    assert _hostname(url) == "dpg-abc123-a.singapore-postgres.render.com"
    assert ":5432/" in url
    assert "localhost" not in url


def test_rewrite_render_external_to_internal_host():
    src = "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com/discovery"
    out = rewrite_render_external_to_internal(src)
    assert _hostname(out) == "dpg-abc123-a"
    assert "postgres.render.com" not in out


def test_resolve_database_url_prefers_private_when_local(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://u:p@dpg-abc123-a:5432/discovery",
    )
    url = resolve_database_url("postgresql://discovery:discovery@localhost:5432/discovery")
    assert "dpg-abc123-a" in url
    assert "sslmode=require" in url


def test_resolve_database_url_ignores_uninterpolated_reference(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://u:p@dpg-abc123-a:5432/discovery",
    )
    url = resolve_database_url("${{Postgres.DATABASE_URL}}")
    assert "dpg-abc123-a" in url
    url = resolve_database_url("")
    assert "dpg-abc123-a" in url


def test_resolve_database_url_prefers_render_url_over_localhost(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://discovery:discovery@localhost:5432/discovery",
    )
    monkeypatch.setenv(
        "RENDER_DATABASE_URL",
        "postgresql://u:p@dpg-abc123-a:5432/discovery",
    )
    url = resolve_database_url("postgresql://discovery:discovery@localhost:5432/discovery")
    assert "dpg-abc123-a" in url
    assert "localhost" not in url


def test_apply_hosted_database_env_replaces_localhost(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://discovery:discovery@localhost:5432/discovery",
    )
    monkeypatch.setenv(
        "DISCOVERY_DATABASE_URL",
        "postgresql://u:p@dpg-abc123-a:5432/discovery",
    )
    pinned = apply_hosted_database_env()
    assert "dpg-abc123-a" in pinned
    assert "localhost" not in os.environ.get("DATABASE_URL", "")
    assert "localhost" not in pinned


def test_load_settings_on_render_does_not_keep_localhost(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://discovery:discovery@localhost:5432/discovery",
    )
    monkeypatch.setenv(
        "RENDER_DATABASE_URL",
        "postgresql://u:p@dpg-abc123-a:5432/discovery",
    )
    settings = load_settings()
    assert "dpg-abc123-a" in settings.database_url
    assert "localhost" not in settings.database_url


def test_postgres_urls_in_text_extracts_dpg_dsn_from_blob():
    blob = (
        "Internal Database URL\n"
        "postgresql://u:p@dpg-abc123-a/discovery\n"
        "External Database URL\n"
        "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com/discovery\n"
    )
    urls = postgres_urls_in_text(blob)
    hosts = {_hostname(item) for item in urls}
    assert "dpg-abc123-a" in hosts


def test_split_keeps_render_host_when_password_has_question_mark():
    url = "postgresql://u:p?ss@dpg-abc123-a:5432/discovery"
    assert _hostname(url) == "dpg-abc123-a"


def test_wait_for_postgres_uses_pghost_when_database_url_is_localhost(monkeypatch, tmp_path):
    from src.db import connect as connect_mod

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://discovery:discovery@localhost:5432/discovery",
    )
    monkeypatch.setenv("PGHOST", "dpg-abc123-a")
    monkeypatch.setenv("PGUSER", "discovery")
    monkeypatch.setenv("PGPASSWORD", "secret")
    monkeypatch.setenv("PGDATABASE", "discovery")
    monkeypatch.setenv("PGPORT", "5432")
    for key in (
        "RENDER_DATABASE_URL",
        "DISCOVERY_DATABASE_URL",
        "DATABASE_PRIVATE_URL",
        "DATABASE_PUBLIC_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(connect_mod, "handshake_database_url", lambda url, connect_timeout=8: url)
    settings = Settings(
        database_url="postgresql://discovery:discovery@localhost:5432/discovery",
        require_postgres=True,
        postgres_wait_seconds=0,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
    )
    repo = wait_for_postgres(settings)
    assert "dpg-abc123-a" in repo.database_url
    assert "localhost" not in repo.database_url


def test_resolve_keeps_public_render_hostname_for_dns(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    for key in (
        "RENDER_DATABASE_URL",
        "DISCOVERY_DATABASE_URL",
        "DATABASE_URL",
        "DATABASE_PRIVATE_URL",
        "DATABASE_PUBLIC_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "PGHOST",
        "PG_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    url = resolve_database_url(
        "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com/discovery"
    )
    assert _hostname(url) == "dpg-abc123-a.singapore-postgres.render.com"
    assert ":5432" in url


def test_resolve_prefers_render_internal_over_external_env(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com/discovery",
    )
    monkeypatch.setenv(
        "RENDER_DATABASE_URL",
        "postgresql://u:p@dpg-abc123-a:5432/discovery",
    )
    url = resolve_database_url(
        "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com/discovery"
    )
    assert _hostname(url) == "dpg-abc123-a"
    assert "postgres.render.com" not in url


def test_conninfo_candidates_render_tries_internal_without_prefer(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    cands = conninfo_candidates(
        "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com/discovery"
    )
    assert cands
    hosts = [_hostname(item) for item in cands]
    assert hosts[0] == "dpg-abc123-a"
    assert "postgres.render.com" in " ".join(hosts)
    assert ":5432" in cands[0]
    blob = " ".join(cands)
    assert "sslmode=prefer" not in blob
    assert "sslmode=disable" in cands[0]
    public = [item for item in cands if "postgres.render.com" in item]
    assert public and "sslnegotiation=direct" in public[0]
    internal = [item for item in cands if _hostname(item) == "dpg-abc123-a"]
    assert internal and "sslmode=disable" in internal[0]
    assert "dpg-abc123-a.internal" not in blob
    assert "discovery-db" not in blob


def test_conninfo_candidates_docker_tries_public_host_first(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    from src.db import connect as connect_mod

    monkeypatch.setattr(connect_mod, "running_in_docker", lambda: True)
    cands = conninfo_candidates(
        "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com/discovery"
    )
    assert _hostname(cands[0]) == "dpg-abc123-a.singapore-postgres.render.com"
    assert "sslnegotiation=direct" in cands[0]
    assert "sslmode=require" in cands[0]


def test_conninfo_candidates_rebuilds_public_host_from_internal(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_POSTGRES_REGION", "singapore")
    cands = conninfo_candidates("postgresql://u:p@dpg-abc123-a:5432/discovery")
    blob = " ".join(cands)
    assert "dpg-abc123-a.singapore-postgres.render.com" in blob
    assert "sslmode=prefer" not in blob
    assert _hostname(cands[0]) == "dpg-abc123-a"
    assert "sslmode=disable" in cands[0]


def test_expand_render_postgres_url_keeps_region_from_hostname(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    from src.db.connect import expand_render_postgres_url

    urls = expand_render_postgres_url(
        "postgresql://u:p@dpg-abc123-a.ohio-postgres.render.com/discovery"
    )
    hosts = [_hostname(item) for item in urls]
    assert hosts[0] == "dpg-abc123-a"
    assert "dpg-abc123-a.ohio-postgres.render.com" in hosts
    assert "dpg-abc123-a.internal" not in hosts
    assert "discovery-db" not in hosts


def test_dns_lookup_a_returns_empty_when_resolv_conf_missing(monkeypatch):
    from src.db import connect as connect_mod

    monkeypatch.setattr(connect_mod, "_resolv_conf_nameservers", lambda: [])
    assert connect_mod.dns_lookup_a("dpg-abc123-a") == []


def test_dns_lookup_a_skips_public_resolvers_for_single_label(monkeypatch):
    from src.db import connect as connect_mod

    monkeypatch.setattr(connect_mod, "_resolv_conf_nameservers", lambda: ["8.8.8.8", "1.1.1.1"])
    assert connect_mod.dns_lookup_a("dpg-abc123-a") == []


def test_is_private_ip_rfc1918():
    from src.db.connect import is_private_ip

    assert is_private_ip("10.1.2.3")
    assert is_private_ip("192.168.1.1")
    assert is_private_ip("172.16.0.1")
    assert is_private_ip("100.64.1.2")
    assert not is_private_ip("3.0.216.9")
    assert not is_private_ip("8.8.8.8")


def test_conninfo_hostaddr_uses_private_ip_without_tls(monkeypatch):
    from src.db import connect as connect_mod

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(connect_mod, "private_ips_for_host", lambda host: ["10.9.8.7"])
    alts = connect_mod._conninfo_with_hostaddrs(
        "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com:5432/discovery"
    )
    assert alts
    assert "hostaddr=10.9.8.7" in alts[0]
    assert "sslmode=disable" in alts[0]


def test_res_options_without_ndots():
    from src.db.connect import res_options_without_ndots

    assert res_options_without_ndots("ndots:0 timeout:2 attempts:2") == "timeout:2 attempts:2"
    assert res_options_without_ndots("") == ""


def test_resolv_conf_with_private_first_prepends_gateway():
    from src.db.connect import resolv_conf_with_private_first

    original = "nameserver 8.8.8.8\nnameserver 8.8.4.4\n"
    out = resolv_conf_with_private_first(original, ["10.0.0.1", "127.0.0.11", "8.8.8.8"])
    assert out.startswith("nameserver 10.0.0.1\nnameserver 127.0.0.11\n")
    assert "nameserver 8.8.8.8" in out
    assert out.count("nameserver 8.8.8.8") == 1


def test_dockerfile_skips_torch_and_serves_api_module():
    text = Path(__file__).resolve().parents[1].joinpath("Dockerfile").read_text(encoding="utf-8")
    assert "pip install torch" not in text.lower()
    assert "sentence-transformers" not in text.lower()
    assert "hosts: files dns" in text
    assert "requirements-api.txt" in text
    assert '"src.api"' in text
    assert "--no-deps" in text


def test_api_serve_parser_accepts_migrate():
    from src.api.serve import main

    with pytest.raises(SystemExit):
        main(["--help"])


def test_apply_resolver_workarounds_native_clears_ndots_zero(monkeypatch):
    from src.db.connect import apply_resolver_workarounds

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RES_OPTIONS", "ndots:0 timeout:2 attempts:2")
    apply_resolver_workarounds()
    assert "ndots" not in os.environ.get("RES_OPTIONS", "").lower()


def test_wait_for_postgres_rejects_localhost_on_render(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://discovery:discovery@localhost:5432/discovery",
    )
    for key in (
        "RENDER_DATABASE_URL",
        "DATABASE_PRIVATE_URL",
        "DATABASE_PUBLIC_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "PGHOST",
        "PG_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(
        database_url="postgresql://discovery:discovery@localhost:5432/discovery",
        require_postgres=True,
        postgres_wait_seconds=0,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
    )
    with pytest.raises(PostgresRequiredError, match="localhost"):
        wait_for_postgres(settings)


def test_wait_for_postgres_rejects_placeholder_on_render(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER", "true")
    for key in (
        "RENDER_DATABASE_URL",
        "DATABASE_URL",
        "DATABASE_PRIVATE_URL",
        "DATABASE_PUBLIC_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
        "PGHOST",
        "PG_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(
        database_url="${{Postgres.DATABASE_URL}}",
        require_postgres=True,
        postgres_wait_seconds=0,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
    )
    with pytest.raises(PostgresRequiredError, match="not a real Postgres URL"):
        wait_for_postgres(settings)


def test_try_postgres_does_not_tcp_probe_remote_hosts(monkeypatch, tmp_path):
    from src.db import connect as connect_mod

    probed: list[bool] = []
    monkeypatch.setattr(
        connect_mod,
        "postgres_tcp_open",
        lambda *a, **k: probed.append(True) or False,
    )
    monkeypatch.setattr(
        connect_mod,
        "handshake_database_url",
        lambda url, connect_timeout=8: url,
    )
    settings = Settings(
        database_url="postgresql://u:p@dpg-abc123-a:5432/discovery",
        require_postgres=True,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
    )
    repo = connect_mod.try_postgres(settings)
    assert repo is not None
    assert probed == []
    assert "dpg-abc123-a" in repo.database_url


def test_try_postgres_falls_back_to_public_url(monkeypatch, tmp_path):
    from src.db import connect as connect_mod

    def fake_handshake(url, connect_timeout=8):
        if "postgres.render.com" in url:
            return url
        raise RuntimeError("private network failed")

    monkeypatch.setenv(
        "DATABASE_PUBLIC_URL",
        "postgresql://u:p@dpg-abc123-a.singapore-postgres.render.com/discovery",
    )
    monkeypatch.setattr(connect_mod, "handshake_database_url", fake_handshake)
    settings = Settings(
        database_url="postgresql://u:p@dpg-abc123-a:5432/discovery",
        require_postgres=True,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
    )
    repo = connect_mod.try_postgres(settings)
    assert repo is not None
    assert "postgres.render.com" in repo.database_url


def test_require_postgres_does_not_fall_back_to_pickle(tmp_path):
    settings = Settings(
        database_url="postgresql://discovery:discovery@127.0.0.1:1/discovery",
        require_postgres=True,
        postgres_wait_seconds=0,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
    )
    with pytest.raises(PostgresRequiredError, match="REQUIRE_POSTGRES"):
        connect_store(settings)
    assert not (tmp_path / "local_store.pkl").exists()


def test_local_dev_still_falls_back_to_pickle_when_postgres_down(tmp_path):
    settings = Settings(
        database_url="postgresql://discovery:discovery@127.0.0.1:1/discovery",
        require_postgres=False,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
    )
    store = connect_store(settings)
    assert isinstance(store, PersistentMemoryRepository)


def test_pending_store_detail_explains_pgvector_and_localhost():
    generic = pending_store_detail(None)
    assert "connecting to Postgres" in generic
    vector = pending_store_detail('extension "vector" is not available')
    assert "pgvector" in vector.lower()
    reachability = pending_store_detail(
        "Postgres is required (REQUIRE_POSTGRES=true) but was not reachable. "
        "Check DATABASE_URL (Render internal URL, or External Database URL for a laptop) "
        "and retry. Last check: not listening or handshake failed (host=unknown)."
    )
    assert "Migrations need pgvector" not in reachability
    assert "not a real postgres" in reachability.lower() or "Internal Database URL" in reachability
    local = pending_store_detail("could not connect to server at localhost")
    assert "localhost" in local.lower()
    private = pending_store_detail("handshake failed (host=dpg-abc123-a)")
    assert "dpg-" in private
    ssl = pending_store_detail(
        "connection to server at \"13.214.97.86\", port 5432 failed: "
        "SSL connection has been closed unexpectedly"
    )
    assert "sslmode=require" in ssl or "Singapore" in ssl
    assert "sslnegotiation=direct" in ssl
    dns = pending_store_detail(
        "failed to resolve host 'dpg-xxxxx-a': [Errno -2] Name or service not known"
    )
    assert "singapore-postgres.render.com" in dns
    assert "Singapore" in dns


def test_require_postgres_health_listens_before_db(tmp_path):
    from fastapi.testclient import TestClient

    from src.api.app import create_app

    settings = Settings(
        database_url="postgresql://discovery:discovery@127.0.0.1:1/discovery",
        require_postgres=True,
        postgres_wait_seconds=0,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
        api_shared_secret="deploy-secret",
    )
    app = create_app(settings=settings)
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code in {200, 503}
    body = health.json()
    assert body["store"] in {"pending", "postgres"}
    assert body["status"] in {"starting", "ok"}
    overview = client.get("/metrics/overview", headers={"X-API-Key": "deploy-secret"})
    if body["store"] == "pending":
        assert health.status_code == 200
        assert overview.status_code == 503
        assert "Postgres" in overview.json()["detail"]


def test_pending_metrics_surface_boot_error(tmp_path):
    from fastapi.testclient import TestClient

    from src.api.app import create_app

    settings = Settings(
        database_url="postgresql://discovery:discovery@127.0.0.1:1/discovery",
        require_postgres=True,
        postgres_wait_seconds=0,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
        api_shared_secret="deploy-secret",
    )
    app = create_app(settings=settings)
    app.state.boot_error = 'extension "vector" is not available'
    client = TestClient(app)
    overview = client.get("/metrics/overview", headers={"X-API-Key": "deploy-secret"})
    assert overview.status_code == 503
    assert "pgvector" in overview.json()["detail"].lower()
