"""Phase 2 live smokes. Skip unless GROQ_API_KEY / RUN_LIVE_BGE are set."""

from __future__ import annotations

import os

import pytest

from src.cli import main
from src.config import load_settings
from src.db.postgres import PostgresRepository
from src.embed.bge import encode_query, load_bge_model
from src.extract.eval_report import build_extract_eval_report

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="GROQ_API_KEY not set")
@pytest.mark.skipif(os.getenv("RUN_LIVE_EXTRACT") != "1", reason="RUN_LIVE_EXTRACT=1 not set")
def test_ev_2_01_batch_extract_sample():
    assert main(["extract", "--limit", "50"]) == 0
    report = build_extract_eval_report(PostgresRepository(load_settings().database_url), limit=50)
    assert report.total >= 1
    assert report.ok_rate >= 0.8
    assert report.quote_span_ok == report.quote_span_checked
    assert report.intent_mode_distinct is True


@pytest.mark.skipif(os.getenv("RUN_LIVE_BGE") != "1", reason="RUN_LIVE_BGE=1 not set")
def test_ev_2_10_and_12_embed_and_sizing_nn():
    assert main(["embed", "--limit", "50"]) == 0
    settings = load_settings()
    repo = PostgresRepository(settings.database_url)
    chunks = [c for c in repo.list_chunks() if c.embedding is not None]
    assert chunks
    assert all(len(c.embedding) == 1024 for c in chunks)
    assert all(c.embedding_model and "bge-m3" in c.embedding_model.lower() for c in chunks)
    model = load_bge_model(settings)
    vector = encode_query(
        model,
        "Myntra size too small / runs small",
        model_id=settings.bge_model_id,
    )
    assert "Represent this sentence for searching" not in "Myntra size too small / runs small"
    hits = repo.nearest_chunks(vector, k=8)
    assert hits
    blob = " ".join(h.text.lower() for h in hits)
    assert any(token in blob for token in ("size", "small", "fit", "sizing"))
