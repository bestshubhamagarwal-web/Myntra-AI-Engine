"""Railway/Vercel deploy helpers (docs/deployment-plan.md)."""

from __future__ import annotations

import pytest

from src.api.app import pending_store_detail
from src.config import Settings, resolve_listen_port
from src.db.connect import (
    PostgresRequiredError,
    connect_store,
    normalize_database_url,
    resolve_database_url,
    wait_for_postgres,
)
from src.db.local import PersistentMemoryRepository


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


def test_normalize_database_url_rewrites_scheme_and_railway_ssl():
    assert normalize_database_url("postgres://u:p@h:5432/db").startswith("postgresql://")
    public = normalize_database_url("postgresql://u:p@turn.proxy.rlwy.net:1234/railway")
    assert "sslmode=require" in public
    internal = normalize_database_url("postgresql://u:p@postgres.railway.internal:5432/railway")
    assert "sslmode=disable" in internal
    quoted = normalize_database_url('  "postgresql://u:p@h:5432/db"  ')
    assert quoted == "postgresql://u:p@h:5432/db"


def test_resolve_database_url_prefers_private_when_local(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://u:p@postgres.railway.internal:5432/railway",
    )
    url = resolve_database_url("postgresql://discovery:discovery@localhost:5432/discovery")
    assert "railway.internal" in url
    assert "sslmode=disable" in url


def test_resolve_database_url_ignores_uninterpolated_reference(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_PRIVATE_URL",
        "postgresql://u:p@postgres.railway.internal:5432/railway",
    )
    url = resolve_database_url("${{Postgres.DATABASE_URL}}")
    assert "railway.internal" in url
    url = resolve_database_url("")
    assert "railway.internal" in url


def test_wait_for_postgres_rejects_localhost_on_railway(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    for key in (
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


def test_wait_for_postgres_rejects_placeholder_on_railway(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    for key in (
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
        database_url="postgresql://u:p@postgres.railway.internal:5432/railway",
        require_postgres=True,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
    )
    repo = connect_mod.try_postgres(settings)
    assert repo is not None
    assert probed == []
    assert "railway.internal" in repo.database_url


def test_try_postgres_falls_back_to_public_url(monkeypatch, tmp_path):
    from src.db import connect as connect_mod

    def fake_handshake(url, connect_timeout=8):
        if "rlwy.net" in url:
            return url
        raise RuntimeError("private network failed")

    monkeypatch.setenv(
        "DATABASE_PUBLIC_URL",
        "postgresql://u:p@turn.proxy.rlwy.net:1234/railway",
    )
    monkeypatch.setattr(connect_mod, "handshake_database_url", fake_handshake)
    settings = Settings(
        database_url="postgresql://u:p@postgres.railway.internal:5432/railway",
        require_postgres=True,
        local_store_path=tmp_path / "local_store.pkl",
        author_hmac_secret="deploy-hmac",
        raw_store_path=tmp_path,
    )
    repo = connect_mod.try_postgres(settings)
    assert repo is not None
    assert "rlwy.net" in repo.database_url


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
        "Check DATABASE_URL (Railway private URL + pgvector template) and retry. "
        "Last check: not listening or handshake failed (host=unknown)."
    )
    assert "Migrations need pgvector" not in reachability
    assert "not a real postgres" in reachability.lower() or "copy DATABASE_URL" in reachability
    local = pending_store_detail("could not connect to server at localhost")
    assert "localhost" in local.lower()
    private = pending_store_detail(
        "handshake failed (host=postgres.railway.internal)"
    )
    assert "DATABASE_PUBLIC_URL" in private or "rlwy.net" in private


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
        assert health.status_code == 503
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
