"""Phase 0 auto evals (docs/eval.md EV-0-*, EV-X-01). Live Groq/BGE are opt-in."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.cli import build_parser, main
from src.config import BGE_M3_DIM, GROQ_DEFAULT_BASE_URL, Settings
from src.embed.bge import EmbeddingDimensionError, ZeroEmbeddingError, assert_embedding_dim, encode_texts
from src.extract.groq_client import GroqConfigError, build_groq_client
from src.models.envelope import FROZEN_RAW_ENVELOPE_FIELDS, RawEnvelope
from src.smoke import FOUNDATION_TABLES, REQUIRED_QUERY_SEEDS, check_postgres_foundation

REPO = Path(__file__).resolve().parents[1]


def test_ev_0_01_layout_paths_exist():
    required = [
        "src/ingest",
        "src/normalize",
        "src/extract",
        "src/embed",
        "src/cluster",
        "src/metrics",
        "src/api",
        "prompts",
        "web",
        "prompts/extract.json",
        "prompts/copilot_system.md",
        "prompts/theme_label.md",
        "web/package.json",
        "web/app/page.tsx",
        "api/index.py",
        "vercel.json",
        "requirements.txt",
        "Dockerfile",
        "railway.toml",
        "render.yaml",
        "docs/deployment-plan.md",
        "web/vercel.json",
    ]
    missing = [rel for rel in required if not (REPO / rel).exists()]
    assert missing == []


def test_ev_0_03_unique_constraint_in_migration():
    sql = (REPO / "migrations" / "001_init.sql").read_text(encoding="utf-8")
    assert "UNIQUE (source_type, source_id)" in sql
    assert "CREATE TABLE IF NOT EXISTS raw_documents" in sql
    assert "CREATE TABLE IF NOT EXISTS normalized_documents" in sql
    assert "CREATE TABLE IF NOT EXISTS ingest_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS ingest_queries" in sql


def test_ev_0_04_pgvector_1024_reserved():
    sql = (REPO / "migrations" / "001_init.sql").read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "embedding vector(1024)" in sql
    settings = Settings(
        groq_api_key="unused",
        author_hmac_secret="phase0-hmac",
        embedding_dim=1024,
    )
    assert settings.embedding_dim == BGE_M3_DIM
    with pytest.raises(Exception):
        Settings(embedding_dim=384, author_hmac_secret="phase0-hmac")


def test_ev_0_05_env_example_groq_bge_only():
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    for key in (
        "GROQ_API_KEY",
        "GROQ_BASE_URL",
        "GROQ_MODEL",
        "GROQ_MODEL_LIGHT",
        "BGE_MODEL_ID",
        "EMBEDDING_DIM",
        "HF_HOME",
        "AUTHOR_HMAC_SECRET",
        "C_MAX",
        "S_MAX",
    ):
        assert key in example
    assert "https://api.groq.com/openai/v1" in example
    assert "BAAI/bge-m3" in example
    assert "EMBEDDING_DIM=1024" in example
    assigned = [
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    ]
    assert "OPENAI_API_KEY" not in assigned
    assert "api.openai.com" not in example


def test_ev_0_06_ingest_query_seeds():
    sql = (REPO / "migrations" / "002_seed.sql").read_text(encoding="utf-8")
    for seed in REQUIRED_QUERY_SEEDS:
        assert seed in sql
    assert "('ajio'" not in sql.lower()
    assert "ajio_play" not in sql.lower()


def test_ev_0_09_raw_envelope_frozen_fields():
    for name in FROZEN_RAW_ENVELOPE_FIELDS:
        assert name in RawEnvelope.model_fields
    required = {
        "source_type",
        "source_id",
        "url",
        "raw_text",
        "author_hash",
        "payload_uri",
        "myntra_relevance",
        "fetched_at",
        "published_at",
        "platform",
        "raw_title",
        "star_rating",
        "parent_context",
    }
    assert required.issubset(RawEnvelope.model_fields)


def test_ev_0_10_readme_documents_first_run():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "docker compose up" in readme
    assert "python -m src.cli migrate" in readme
    assert "python -m src.cli smoke" in readme
    assert "HF_HOME" in readme or "./data/models" in readme
    assert "BGE" in readme


def test_ev_x_01_no_openai_host_defaults():
    assert GROQ_DEFAULT_BASE_URL == "https://api.groq.com/openai/v1"
    with pytest.raises(Exception):
        Settings(
            groq_base_url="https://api.openai.com/v1",
            author_hmac_secret="phase0-hmac",
        )
    for path in (REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "https://api.openai.com/v1/embeddings" not in text
        assert "https://api.openai.com/v1/chat" not in text


def test_cli_migrate_and_smoke_exist():
    parser = build_parser()
    smoke = parser.parse_args(["smoke", "--skip-bge", "--skip-groq"])
    assert smoke.skip_bge and smoke.skip_groq
    migrate = parser.parse_args(["migrate"])
    assert migrate.func.__name__ == "cmd_migrate"
    assert main(["smoke", "--skip-db", "--skip-groq", "--skip-bge"]) == 0


def test_ec_em_01_dim_mismatch_fails_fast():
    with pytest.raises(EmbeddingDimensionError):
        assert_embedding_dim([0.0] * 384, expected=1024)

    class WrongDim:
        def encode(self, texts, **_kwargs):
            return [[0.1] * 384 for _ in texts]

    with pytest.raises(EmbeddingDimensionError):
        encode_texts(WrongDim(), ["hello"], expected_dim=1024)


def test_encode_accepts_1024_and_does_not_pad():
    class OkDim:
        def encode(self, texts, **_kwargs):
            row = [0.0] * 1024
            row[0] = 1.0
            return [row for _ in texts]

    rows = encode_texts(OkDim(), ["Myntra wishlist sizing is confusing."], expected_dim=1024)
    assert len(rows) == 1
    assert len(rows[0]) == 1024
    assert abs(sum(x * x for x in rows[0]) ** 0.5 - 1.0) < 1e-6


def test_zero_vector_is_rejected():
    class ZeroDim:
        def encode(self, texts, **_kwargs):
            return [[0.0] * 1024 for _ in texts]

    with pytest.raises(ZeroEmbeddingError):
        encode_texts(ZeroDim(), ["hello"], expected_dim=1024)


def test_groq_client_requires_key_and_groq_host():
    with pytest.raises(GroqConfigError):
        build_groq_client(Settings(groq_api_key="", author_hmac_secret="phase0-hmac"))


def test_ev_0_02_migrate_against_postgres():
    url = os.environ.get(
        "DATABASE_URL", "postgresql://discovery:discovery@localhost:5432/discovery"
    )
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    assert main(["migrate"]) == 0
    check = check_postgres_foundation(url)
    for table in FOUNDATION_TABLES:
        assert table in check.counts
    assert "source_type" in check.unique_constraint
    assert "source_id" in check.unique_constraint
    assert check.embedding_type == "vector(1024)"
    for seed in REQUIRED_QUERY_SEEDS:
        assert seed in check.ingest_queries
