from __future__ import annotations

from pathlib import Path

from src.db.connect import postgres_connect

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _statements(sql: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def apply_migrations(database_url: str) -> list[str]:
    applied: list[str] = []
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    try:
        with postgres_connect(database_url, autocommit=True, connect_timeout=8) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            for path in files:
                row = conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE filename = %s",
                    (path.name,),
                ).fetchone()
                if row:
                    continue
                sql = path.read_text(encoding="utf-8")
                for statement in _statements(sql):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)",
                    (path.name,),
                )
                applied.append(path.name)
    except Exception as exc:
        text = str(exc).lower()
        if "vector" in text or "extension" in text:
            raise RuntimeError(
                "CREATE EXTENSION vector failed. Use Railway's pgvector template "
                "(not default Postgres) and set DATABASE_URL to the private URL "
                f"(*.railway.internal). Original error: {exc}"
            ) from exc
        raise
    return applied
