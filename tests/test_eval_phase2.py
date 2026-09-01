"""Phase 2 auto evals (docs/eval.md EV-2-*). Live Groq/BGE are opt-in."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from src.cli import build_parser
from src.config import Settings
from src.db.memory import MemoryRepository
from src.db.repository import NormalizedRecord
from src.embed.bge import (
    EN_V15_QUERY_PREFIX,
    ZeroEmbeddingError,
    encode_query,
    encode_texts,
    l2_norm,
    query_text_for_model,
)
from src.embed.chunking import chunk_text, mention_count_from_document_ids
from src.embed.pipeline import run_embed
from src.extract.groq_client import GroqAuthError, GroqJsonResult, GroqRateLimitError
from src.extract.pipeline import run_extract
from src.extract.schema import (
    FRICTION_TAGS,
    INTENT_MODES,
    INTENT_TAGS,
    QUESTION_IDS,
    ExtractionPayload,
    architecture_field_names,
)
from src.normalize.hashing import content_hash
from src.timeutil import utcnow

from tests.conftest import make_envelope

REPO = Path(__file__).resolve().parents[1]


def _settings(**overrides) -> Settings:
    payload = dict(
        author_hmac_secret="phase2-test-hmac",
        groq_api_key="test-key",
        groq_json_retries=2,
        groq_max_retries=3,
        groq_min_interval_seconds=0.0,
        groq_max_tpm=1_000_000,
        groq_backoff_base_seconds=0.01,
        embed_batch_size=4,
        chunk_max_tokens=400,
        chunk_overlap_tokens=50,
    )
    payload.update(overrides)
    return Settings(**payload)


def add_normalized(repo: MemoryRepository, text: str, **overrides) -> NormalizedRecord:
    env = make_envelope(raw_text=text)
    repo.upsert_raw(env)
    rec = NormalizedRecord(
        id=uuid4(),
        raw_id=env.id,
        text_original=text,
        text_en=None,
        language=overrides.get("language", "en"),
        product_category=overrides.get("product_category", "unknown"),
        gender_segment="unknown",
        price_tier="unknown",
        platform_used="unknown",
        occasion="unknown",
        star_rating=3,
        review_date=utcnow(),
        quality_score=0.8,
        content_hash=content_hash(text),
        duplicate_of=None,
        eligible=True,
        pii_scrubbed_at=utcnow(),
        normalize_run_id=None,
        intent_mode=None,
    )
    repo.upsert_normalized(rec)
    return rec


def groq_result(payload: dict | str, prompt_tokens: int = 11, completion_tokens: int = 7) -> GroqJsonResult:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return GroqJsonResult(content=content, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


def valid_payload(**overrides) -> dict:
    data = {
        "intent_tag": "unknown",
        "intent_mode": "unknown",
        "friction_tag": [],
        "residual_uncertainties": [],
        "comparison_behavior": "unknown",
        "off_platform_info_seeking": [],
        "entities": {
            "category": None,
            "brand": None,
            "occasion": None,
            "size_fit_mentioned": False,
            "price_mentioned": False,
            "competitor_mentions": [],
        },
        "sentiment": {"primary": "neutral", "severity": 0.1},
        "verbatim_quotes": [],
        "maps_to_questions": [],
        "extraction_confidence": 0.4,
    }
    data.update(overrides)
    return data


class FakeBGE:
    def encode(self, texts, **_kwargs):
        rows = []
        for text in texts:
            vec = [0.0] * 1024
            lowered = text.lower()
            if any(token in lowered for token in ("size", "small", "fit", "sizing")):
                vec[0] = 4.0
            else:
                vec[1] = 4.0
            rows.append(vec)
        return rows


def test_ev_2_03_pydantic_matches_architecture():
    assert architecture_field_names() <= set(ExtractionPayload.model_fields)
    empty = ExtractionPayload.model_validate({})
    assert empty.intent_tag.value == "unknown"
    assert empty.intent_mode.value == "unknown"
    assert empty.friction_tag == []
    nulls = ExtractionPayload.model_validate(
        {
            "intent_tag": None,
            "intent_mode": None,
            "friction_tag": None,
            "verbatim_quotes": None,
            "maps_to_questions": None,
            "entities": None,
        }
    )
    assert nulls.intent_mode.value == "unknown"
    for tag in INTENT_TAGS:
        ExtractionPayload.model_validate({"intent_tag": tag})
    for mode in INTENT_MODES:
        ExtractionPayload.model_validate({"intent_mode": mode})
    for tag in FRICTION_TAGS:
        parsed = ExtractionPayload.model_validate({"friction_tag": [tag]})
        assert parsed.friction_values() == [tag]
    for qid in QUESTION_IDS:
        parsed = ExtractionPayload.model_validate({"maps_to_questions": [qid]})
        assert qid in parsed.maps_to_questions
    prompt = (REPO / "prompts" / "extract.json").read_text(encoding="utf-8")
    assert "extract-v1" in prompt
    assert "intent_mode" in prompt
    assert "friction_tag" in prompt


def test_ev_2_02_invalid_json_does_not_crash_batch(repo):
    bad = add_normalized(repo, "Kurta runs small so I left it on the wishlist.")
    good = add_normalized(
        repo,
        "Returns are painful, the dress sits in my cart until I trust the policy.",
    )
    calls: list = []

    def complete(messages, document=None, **_kwargs):
        calls.append(document.id)
        if document.id == bad.id:
            return groq_result("NOT JSON {")
        return groq_result(
            valid_payload(
                intent_tag="save_for_later",
                friction_tag=["return_risk"],
                maps_to_questions=["Q2", "Q6"],
            )
        )

    result = run_extract(
        repo,
        _settings(),
        complete_fn=complete,
        sleep=lambda _s: None,
    )
    assert result.status == "success"
    assert result.failed == 1
    assert result.ok == 1
    assert repo.get_normalized(bad.id) is not None
    failed = repo.get_extraction(bad.id)
    assert failed is not None
    assert failed.extraction_status == "failed"
    assert failed.raw_response
    assert failed.metrics_eligible is False
    assert len([c for c in calls if c == bad.id]) == 2
    ok = repo.get_extraction(good.id)
    assert ok.extraction_status == "ok"
    assert "return_risk" in ok.friction_tags


def test_ev_2_04_quote_spans_repaired_or_dropped(repo):
    text = "These Myntra jeans run small and the size chart is useless."
    doc = add_normalized(repo, text)

    def complete(messages, document=None, **_kwargs):
        return groq_result(
            valid_payload(
                friction_tag=["fit_uncertainty"],
                verbatim_quotes=[
                    {"span": "run small", "start_char": 0, "end_char": 3},
                    {"span": "paraphrased sizing complaint", "start_char": 0, "end_char": 10},
                ],
            )
        )

    run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    rec = repo.get_extraction(doc.id)
    assert rec.extraction_status == "ok"
    spans = [q["span"] for q in rec.verbatim_quotes]
    assert "run small" in spans
    assert all(q["span"] in text for q in rec.verbatim_quotes)
    for quote in rec.verbatim_quotes:
        assert text[quote["start_char"] : quote["end_char"]] == quote["span"]
    assert "paraphrased sizing complaint" not in spans


def test_ev_2_05_intent_mode_not_friction(repo):
    text = (
        "Saving this lehenga to my wishlist as a mood board for a wedding someday, "
        "not buying now."
    )
    doc = add_normalized(repo, text, product_category="ethnic")

    def complete(messages, document=None, **_kwargs):
        return groq_result(
            valid_payload(
                intent_tag="mood_board",
                intent_mode="near_term_purchase",
                friction_tag=["fit_uncertainty"],
                maps_to_questions=["Q7"],
            )
        )

    run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    rec = repo.get_extraction(doc.id)
    assert rec.intent_mode == "passive_bookmark"
    assert rec.intent_mode not in rec.friction_tags
    assert rec.friction_tags == ["fit_uncertainty"]
    assert repo.get_normalized(doc.id).intent_mode == "passive_bookmark"


def test_ev_2_06_multi_friction_fit_and_returns(repo):
    text = "Kurta runs small and returns are a nightmare so it sits in my cart."
    doc = add_normalized(repo, text)

    def complete(messages, document=None, **_kwargs):
        return groq_result(
            valid_payload(
                intent_mode="near_term_purchase",
                friction_tag=["fit_uncertainty", "return_risk"],
                residual_uncertainties=["fit", "returns"],
                maps_to_questions=["Q3", "Q6"],
            )
        )

    run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    rec = repo.get_extraction(doc.id)
    assert "fit_uncertainty" in rec.friction_tags
    assert "return_risk" in rec.friction_tags


def test_ev_2_07_nice_dress_not_price_watch(repo):
    doc = add_normalized(repo, "Nice dress.")

    def complete(messages, document=None, **_kwargs):
        return groq_result(
            valid_payload(
                intent_tag="price_watch",
                intent_mode="near_term_purchase",
                extraction_confidence=0.9,
            )
        )

    run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    rec = repo.get_extraction(doc.id)
    assert rec.intent_tag in {"unknown", "not_applicable"}
    assert rec.intent_tag != "price_watch"


def test_ev_2_08_same_hash_skips_groq(repo):
    doc = add_normalized(repo, "Wishlisted the sneakers until the price drops on Myntra.")
    calls: list = []

    def complete(messages, document=None, **_kwargs):
        calls.append(document.id)
        return groq_result(
            valid_payload(intent_tag="price_watch", maps_to_questions=["Q1"])
        )

    settings = _settings()
    run_extract(repo, settings, complete_fn=complete, sleep=lambda _s: None)
    assert calls == [doc.id]
    result = run_extract(repo, settings, complete_fn=complete, sleep=lambda _s: None)
    assert calls == [doc.id]
    assert result.ok == 0
    assert repo.get_extraction(doc.id).extraction_status == "ok"


def test_ev_2_09_resume_does_not_rebill_cached(repo):
    first = add_normalized(repo, "Size chart is wrong for this kurta, still in wishlist.")
    second = add_normalized(repo, "I compare Myntra vs AJIO before I buy from the cart.")
    ordered = sorted([first, second], key=lambda r: r.id)
    calls: list = []

    def complete(messages, document=None, **_kwargs):
        calls.append(document.id)
        return groq_result(valid_payload(friction_tag=["fit_uncertainty"]))

    settings = _settings()
    run_extract(repo, settings, complete_fn=complete, sleep=lambda _s: None, limit=1)
    assert calls == [ordered[0].id]
    run_extract(repo, settings, complete_fn=complete, sleep=lambda _s: None)
    assert calls == [ordered[0].id, ordered[1].id]


def test_ev_2_11_l2_norm_unit():
    class Unnormalized:
        def encode(self, texts, **_kwargs):
            row = [0.0] * 1024
            row[0] = 3.0
            row[1] = 4.0
            return [row for _ in texts]

    rows = encode_texts(Unnormalized(), ["size runs small"], expected_dim=1024)
    assert abs(l2_norm(rows[0]) - 1.0) < 1e-3


def test_ev_2_13_no_openai_embed_in_extract_embed_path():
    for rel in ("src/extract", "src/embed"):
        for path in (REPO / rel).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "embeddings.create" not in text
            assert "https://api.openai.com/v1/embeddings" not in text
            assert "api.openai.com/v1/chat" not in text
    groq = (REPO / "src/extract/groq_client.py").read_text(encoding="utf-8")
    assert "chat.completions.create" in groq
    assert "api.groq.com" in groq or "GROQ_DEFAULT_BASE_URL" in groq


def test_ev_2_14_failed_excluded_from_metrics(repo):
    doc = add_normalized(repo, "Delivery was late so the heels stayed in my wishlist.")

    def complete(messages, document=None, **_kwargs):
        return groq_result("{{{{")

    run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    failed = repo.get_extraction(doc.id)
    assert failed.extraction_status == "failed"
    assert failed.metrics_eligible is False
    eligible = repo.list_extractions(metrics_eligible_only=True)
    assert eligible == []
    sql = (REPO / "migrations" / "003_extract_embed.sql").read_text(encoding="utf-8")
    assert "extraction_metrics_eligible" in sql
    assert "metrics_eligible" in sql
    assert "extraction_status = 'ok'" in sql


def test_ev_2_15_chunk_metadata_has_tags_after_extract(repo):
    text = "Kurta runs small and I will not checkout until returns feel safe."
    doc = add_normalized(repo, text)

    def complete(messages, document=None, **_kwargs):
        return groq_result(
            valid_payload(
                intent_mode="near_term_purchase",
                friction_tag=["fit_uncertainty", "return_risk"],
                maps_to_questions=["Q6"],
                sentiment={"primary": "frustration", "severity": 0.7},
            )
        )

    run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    run_embed(repo, _settings(), model=FakeBGE())
    chunks = repo.list_chunks(doc.id)
    assert len(chunks) == 1
    assert chunks[0].intent_mode == "near_term_purchase"
    assert "fit_uncertainty" in chunks[0].friction_tags
    assert "return_risk" in chunks[0].friction_tags
    assert chunks[0].extraction_status == "ok"
    assert chunks[0].embedding is not None
    assert len(chunks[0].embedding) == 1024
    assert chunks[0].embedding_model == "BAAI/bge-m3"


def test_ev_2_15_extract_after_embed_rewrites_metadata(repo):
    text = "Saving sneakers to my wishlist as inspiration, not buying now."
    doc = add_normalized(repo, text)
    run_embed(repo, _settings(), model=FakeBGE())
    assert repo.list_chunks(doc.id)[0].intent_mode is None

    def complete(messages, document=None, **_kwargs):
        return groq_result(
            valid_payload(intent_tag="mood_board", intent_mode="passive_bookmark")
        )

    run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    chunk = repo.list_chunks(doc.id)[0]
    assert chunk.intent_mode == "passive_bookmark"
    assert chunk.intent_tag == "mood_board"


def test_ev_2_16_long_doc_overlap_and_distinct_document_id():
    words = [f"word{i}" for i in range(600)]
    text = " ".join(words)
    pieces = chunk_text(text)
    assert len(pieces) > 1
    first = pieces[0].split()
    second = pieces[1].split()
    assert first[-50:] == second[:50]
    doc_id = uuid4()
    assert mention_count_from_document_ids([doc_id] * len(pieces)) == 1


def test_ev_2_17_severity_clipped():
    payload = ExtractionPayload.model_validate(
        {"sentiment": {"primary": "frustration", "severity": 1.7}}
    )
    assert 0.0 <= payload.sentiment.severity <= 1.0
    assert payload.sentiment.severity == 1.0


def test_empty_text_is_not_chunked_or_embedded(repo):
    doc = add_normalized(repo, "ok")
    doc.text_original = "   "
    repo.upsert_normalized(doc)
    result = run_embed(repo, _settings(), model=FakeBGE())
    assert repo.list_chunks(doc.id) == []
    assert result.encoded == 0


def test_query_embed_m3_has_no_en_v15_prefix():
    assert query_text_for_model("runs small", "BAAI/bge-m3") == "runs small"
    assert EN_V15_QUERY_PREFIX not in query_text_for_model("runs small", "BAAI/bge-m3")
    prefixed = query_text_for_model("runs small", "BAAI/bge-small-en-v1.5")
    assert prefixed.startswith(EN_V15_QUERY_PREFIX)

    captured: list[str] = []

    class Capture(FakeBGE):
        def encode(self, texts, **kwargs):
            captured.extend(texts)
            return super().encode(texts, **kwargs)

    encode_query(Capture(), "Myntra size too small", model_id="BAAI/bge-m3")
    assert captured == ["Myntra size too small"]


def test_sizing_nn_returns_fit_chunk(repo):
    size_doc = add_normalized(repo, "Myntra kurtas run small and the size chart is wrong.")
    other = add_normalized(repo, "Loved the colours on the wishlist mood board for a wedding someday.")
    run_embed(repo, _settings(), model=FakeBGE())
    query = encode_query(FakeBGE(), "Myntra size too small / runs small", model_id="BAAI/bge-m3")
    hits = repo.nearest_chunks(query, k=2)
    assert hits
    assert hits[0].document_id == size_doc.id
    assert other.id in {c.document_id for c in repo.list_chunks()}


def test_rate_limit_fails_row_not_host_switch(repo):
    doc = add_normalized(repo, "Fit is uncertain so the dress stays wishlisted.")
    calls = {"n": 0}

    def complete(messages, document=None, **_kwargs):
        calls["n"] += 1
        raise GroqRateLimitError("429 TPM")

    result = run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    assert result.failed == 1
    assert result.ok == 0
    assert repo.get_extraction(doc.id).extraction_status == "failed"
    assert calls["n"] == 3
    groq = (REPO / "src/extract/pipeline.py").read_text(encoding="utf-8")
    assert "openai.com" not in groq.lower() or "api.openai.com" not in groq


def test_auth_error_aborts_job(repo):
    add_normalized(repo, "Returns policy is unclear, sitting in my cart.")

    def complete(messages, document=None, **_kwargs):
        raise GroqAuthError("401")

    with pytest.raises(GroqAuthError):
        run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    pending = list(repo.extractions.values())
    assert pending
    assert pending[0].extraction_status == "pending"


def test_tag_filters_work(repo):
    doc = add_normalized(repo, "Size too small and I keep comparing with AJIO on Myntra.")

    def complete(messages, document=None, **_kwargs):
        return groq_result(
            valid_payload(
                intent_mode="near_term_purchase",
                friction_tag=["fit_uncertainty", "comparison_paralysis"],
                comparison_behavior="true",
                maps_to_questions=["Q4", "Q6"],
                entities={
                    "size_fit_mentioned": True,
                    "competitor_mentions": ["AJIO"],
                },
            )
        )

    run_extract(repo, _settings(), complete_fn=complete, sleep=lambda _s: None)
    by_mode = [e for e in repo.list_extractions(status="ok") if e.intent_mode == "near_term_purchase"]
    by_friction = [
        e for e in repo.list_extractions(status="ok") if "fit_uncertainty" in e.friction_tags
    ]
    by_q = [e for e in repo.list_extractions(status="ok") if "Q6" in e.maps_to_questions]
    assert doc.id in {e.document_id for e in by_mode}
    assert doc.id in {e.document_id for e in by_friction}
    assert doc.id in {e.document_id for e in by_q}


def test_hinglish_stays_in_groq_prompt():
    from src.extract.prompt import build_extract_messages, load_extract_prompt

    repo = MemoryRepository()
    text = "size chhota hai, wishlist mein daala hai for this kurta"
    doc = add_normalized(repo, text, language="hinglish")
    messages = build_extract_messages(doc, load_extract_prompt())
    user = messages[1]["content"]
    assert text in user
    assert "Do not translate quotes" in user
    assert user.count(text) >= 1


def test_cli_phase2_commands_exist():
    parser = build_parser()
    extract = parser.parse_args(["extract", "--limit", "50"])
    assert extract.func.__name__ == "cmd_extract"
    embed = parser.parse_args(["embed", "--force"])
    assert embed.force is True
    search = parser.parse_args(["search", "runs small", "-k", "5"])
    assert search.query == "runs small"


def test_zero_embedding_rejected():
    class Zeros:
        def encode(self, texts, **_kwargs):
            return [[0.0] * 1024 for _ in texts]

    with pytest.raises(ZeroEmbeddingError):
        encode_texts(Zeros(), ["x"], expected_dim=1024)


def test_migration_003_defines_extract_tables():
    sql = (REPO / "migrations" / "003_extract_embed.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS extractions" in sql
    assert "embedding vector(1024)" not in sql  # reserved in 001; 003 only adds metadata
    assert "friction_tags" in sql
    assert "intent_mode" in sql
    assert "maps_to_questions" in sql
