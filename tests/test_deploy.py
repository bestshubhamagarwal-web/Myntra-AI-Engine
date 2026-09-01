"""Railway/Vercel deploy helpers (docs/deployment-plan.md)."""

from __future__ import annotations

import pytest

from src.config import Settings, resolve_listen_port
from src.db.connect import PostgresRequiredError, connect_store
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
    assert health.status_code == 200
    body = health.json()
    assert body["store"] in {"pending", "postgres"}
    assert body["status"] in {"starting", "ok"}
