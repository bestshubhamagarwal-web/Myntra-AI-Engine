"""Phase 0 smoke: Postgres foundation + Groq ping + BGE-M3 dim check."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from src.config import Settings

FOUNDATION_TABLES: tuple[str, ...] = (
    "raw_documents",
    "normalized_documents",
    "ingest_runs",
    "ingest_queries",
)

REQUIRED_QUERY_SEEDS: tuple[str, ...] = (
    "Myntra wishlist",
    "Myntra cart",
    "Myntra sizing",
    "Myntra returns",
    "Myntra vs AJIO",
)


@dataclass
class FoundationCheck:
    tables: list[str]
    unique_constraint: str
    embedding_type: str
    ingest_queries: list[str]
    counts: dict[str, int]


def check_postgres_foundation(database_url: str) -> FoundationCheck:
    """EV-0-02/03/04/06: required tables, unique key, vector(1024), query seeds."""
    with psycopg.connect(database_url, connect_timeout=8) as conn:
        ext = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        if not ext:
            raise RuntimeError("pgvector extension is not installed")

        names = [
            row[0]
            for row in conn.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY 1
                """
            )
        ]
        missing = [table for table in FOUNDATION_TABLES if table not in names]
        if missing:
            raise RuntimeError(f"missing tables after migrate: {missing}")

        unique_defs = [
            row[0]
            for row in conn.execute(
                """
                SELECT pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                WHERE c.conrelid = 'raw_documents'::regclass
                  AND c.contype IN ('u', 'p')
                """
            )
        ]
        unique = next(
            (
                definition
                for definition in unique_defs
                if "source_type" in definition and "source_id" in definition
            ),
            None,
        )
        if unique is None:
            raise RuntimeError(
                "raw_documents unique (source_type, source_id) is missing: "
                f"{unique_defs}"
            )

        emb = conn.execute(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = 'chunks'
              AND a.attname = 'embedding'
              AND a.attnum > 0
              AND NOT a.attisdropped
            """
        ).fetchone()
        embedding_type = emb[0] if emb else None
        if embedding_type != "vector(1024)":
            raise RuntimeError(
                f"chunks.embedding must be vector(1024), got {embedding_type!r}"
            )

        queries = [
            row[0]
            for row in conn.execute(
                "SELECT query_text FROM ingest_queries WHERE active IS TRUE"
            )
        ]
        missing_q = [seed for seed in REQUIRED_QUERY_SEEDS if seed not in queries]
        if missing_q:
            raise RuntimeError(f"missing ingest_queries seeds: {missing_q}")

        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in FOUNDATION_TABLES
        }

    return FoundationCheck(
        tables=names,
        unique_constraint=unique,
        embedding_type=embedding_type,
        ingest_queries=queries,
        counts=counts,
    )


def smoke_db(settings: Settings) -> FoundationCheck:
    return check_postgres_foundation(settings.database_url)
