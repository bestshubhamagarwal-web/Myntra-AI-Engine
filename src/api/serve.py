"""Start the Query API. Used by `python -m src.api` and `python -m src.cli serve`."""

from __future__ import annotations

import argparse
import sys

from src.config import load_settings, resolve_listen_port
from src.db.connect import PostgresRequiredError, wait_for_postgres
from src.db.local import PersistentMemoryRepository
from src.db.migrate import apply_migrations
from src.db.postgres import PostgresRepository


def run_serve(*, host: str | None = None, port: int | None = None, migrate: bool = False) -> int:
    settings = load_settings()
    bind_host = host or settings.api_host
    try:
        bind_port = resolve_listen_port(port, settings)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings.api_host = bind_host
    settings.api_port = int(bind_port)
    try:
        settings.require_api_secret_if_public()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not (settings.api_shared_secret or "").strip() and bind_host in {"127.0.0.1", "localhost", "::1"}:
        print("auth: localhost with empty API_SHARED_SECRET (do not bind 0.0.0.0 without a secret)")
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required: pip install -r requirements-api.txt", file=sys.stderr)
        return 1
    from src.api.app import create_app

    settings.ensure_runtime_dirs()
    if migrate and not settings.require_postgres:
        try:
            wait_for_postgres(settings)
        except PostgresRequiredError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            applied = apply_migrations(settings.database_url)
        except Exception as exc:  # noqa: BLE001 — boot must not hang as a silent pickle API
            print(f"migrate failed: {exc}", file=sys.stderr)
            return 1
        if applied:
            print("applied:", ", ".join(applied))
        else:
            print("migrations already applied")
    try:
        app = create_app(settings=settings, migrate_on_boot=migrate and settings.require_postgres)
    except PostgresRequiredError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    store = app.state.repo
    if store is None:
        print("store: connecting in background (REQUIRE_POSTGRES)")
    elif isinstance(store, PostgresRepository):
        print(f"store: postgres  {settings.database_url}")
    elif isinstance(store, PersistentMemoryRepository):
        print(f"store: local file  {settings.local_store_path}")
    else:
        print("store: memory")
    if not (settings.api_shared_secret or "").strip() and bind_host not in {"127.0.0.1", "localhost", "::1"}:
        print("warning: API_SHARED_SECRET is empty on a public bind", file=sys.stderr)
    print(f"Query API: http://{bind_host}:{bind_port}/docs")
    print("UI:        http://localhost:3000")
    uvicorn.run(app, host=bind_host, port=int(bind_port))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.api")
    parser.add_argument("--host", default=None, help="Bind host (default API_HOST)")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: PORT env, then API_PORT=8000)",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Apply SQL migrations before serving (hosted boot)",
    )
    args = parser.parse_args(argv)
    return run_serve(host=args.host, port=args.port, migrate=bool(args.migrate))
