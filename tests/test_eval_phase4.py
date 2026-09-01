"""Phase 4 auto evals (docs/eval.md EV-4-*). Live Groq labeling is operator CLI."""

from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from src.cli import build_parser
from src.cluster.algorithm import (
    ClusterParams,
    cluster_vectors,
    cosine,
    knn_assign,
    l2_normalize,
    match_centroids,
)
from src.cluster.eligibility import is_cluster_eligible
from src.cluster.label_schema import (
    BookmarkVsStall,
    bookmark_vs_stall_from_modes,
    is_generic_theme_name,
)
from src.cluster.pipeline import run_cluster
from src.config import Settings
from src.db.memory import MemoryRepository
from src.db.repository import (
    ChunkRecord,
    ClusterRun,
    DocumentTheme,
    ExtractionRecord,
    NormalizedRecord,
    ThemeRecord,
)
from src.extract.groq_client import GroqJsonResult
from src.metrics.formulas import (
    DENOMINATOR_DEFINITION,
    blocking_severity,
    confidence_band,
    data_confidence,
    impact_score,
    independent_source_density,
    share_of_voice,
    trend_direction,
)
from src.metrics.pipeline import run_metrics
from src.models.envelope import SourceType
from src.normalize.hashing import content_hash
from src.timeutil import utcnow

from tests.conftest import make_envelope

REPO = Path(__file__).resolve().parents[1]
DIM = 8


def _settings(**overrides) -> Settings:
    payload = dict(
        author_hmac_secret="phase4-test-hmac",
        groq_api_key="test-key",
        groq_json_retries=2,
        groq_max_retries=3,
        groq_min_interval_seconds=0.0,
        groq_max_tpm=1_000_000,
        groq_backoff_base_seconds=0.01,
        c_max=200,
        s_max=4,
        cluster_min_cluster_size=3,
        cluster_min_samples=1,
        cluster_knn_k=3,
        cluster_knn_min_similarity=0.55,
        cluster_centroid_match_min_similarity=0.70,
        cluster_recluster_new_docs=40,
        cluster_kmeans_max_k=8,
        cluster_kmeans_noise_similarity=0.40,
    )
    payload.update(overrides)
    return Settings(**payload)


def _unit(index: int, dim: int = DIM) -> list[float]:
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


def _near(center: list[float], scale: float = 0.01, salt: int = 0) -> list[float]:
    vec = [c + scale * (((salt + i * 17) % 11) - 5) / 5.0 for i, c in enumerate(center)]
    return l2_normalize(vec)


def _quotes(span: str) -> list[dict]:
    return [{"span": span, "start_char": 0, "end_char": len(span)}]


def add_doc(
    repo: MemoryRepository,
    text: str,
    embedding: list[float],
    *,
    source_type: SourceType = SourceType.play_store,
    product_category: str = "unknown",
    author_hash: str | None = "ab" * 32,
    extra_chunks: int = 0,
    review_date=None,
    **extraction_kw,
) -> object:
    env = make_envelope(
        source_type=source_type,
        raw_text=text,
        author_hash=author_hash,
        published_at=review_date or utcnow(),
    )
    repo.upsert_raw(env)
    rec = NormalizedRecord(
        id=uuid4(),
        raw_id=env.id,
        text_original=text,
        text_en=None,
        language="en",
        product_category=product_category,
        gender_segment="unknown",
        price_tier="unknown",
        platform_used="unknown",
        occasion="unknown",
        star_rating=3,
        review_date=review_date or utcnow(),
        quality_score=0.8,
        content_hash=content_hash(text),
        duplicate_of=None,
        eligible=True,
        pii_scrubbed_at=utcnow(),
        normalize_run_id=None,
        intent_mode=extraction_kw.get("intent_mode"),
    )
    repo.upsert_normalized(rec)

    status = extraction_kw.pop("extraction_status", "ok")
    extraction = ExtractionRecord(
        document_id=rec.id,
        content_hash=rec.content_hash,
        prompt_version="extract.v1",
        extraction_status=status,
        intent_tag=extraction_kw.get("intent_tag", "unknown"),
        intent_mode=extraction_kw.get("intent_mode", "near_term_purchase"),
        friction_tags=extraction_kw.get("friction_tags", ["fit_uncertainty"]),
        sentiment_primary=extraction_kw.get("sentiment_primary", "frustration"),
        sentiment_severity=extraction_kw.get("sentiment_severity", 0.8),
        verbatim_quotes=extraction_kw.get("verbatim_quotes", _quotes("runs small on Myntra")),
        extraction_confidence=extraction_kw.get("extraction_confidence", 0.7),
        maps_to_questions=extraction_kw.get("maps_to_questions", ["Q3", "Q6"]),
    )
    repo.upsert_extraction(extraction)
    chunks = [
        ChunkRecord(
            id=uuid4(),
            document_id=rec.id,
            ordinal=i,
            text=text,
            embedding=list(embedding),
            embedding_model="BAAI/bge-m3",
            embedding_dim=len(embedding),
            extraction_status=status,
        )
        for i in range(1 + extra_chunks)
    ]
    repo.replace_chunks(rec.id, chunks)
    return rec


def groq_labeler(messages, **_kwargs) -> GroqJsonResult:
    user = messages[-1]["content"]
    if "fit_uncertainty" in user:
        name = "Kurta size chart vs delivered fit"
        mode = "stall"
    elif "price_sensitivity" in user:
        name = "Wishlist used as price-drop parking"
        mode = "bookmark"
    else:
        name = "Return policy trust after wishlisting"
        mode = "unclear"
    body = {
        "name": name,
        "description": "Users state this friction in their own words.",
        "hypothesis_flag": True,
        "bookmark_vs_stall": mode,
    }
    return GroqJsonResult(content=json.dumps(body), prompt_tokens=5, completion_tokens=9)


# --- formulas (EV-4-08, EV-4-10, EV-4-20–22) ---


def test_ev_4_08_sov_identity():
    assert share_of_voice(10, 40) == pytest.approx(0.25)
    assert share_of_voice(0, 10) == 0.0
    assert share_of_voice(5, 0) == 0.0


def test_ev_4_10_impact_four_factors():
    sov, sev, breadth, conf = 0.2, 0.5, 0.4, 0.8
    expected = sov * sev * breadth * conf
    assert impact_score(sov, sev, breadth, conf) == pytest.approx(expected)
    assert impact_score(0.2, 0.0, 1.0, 1.0) == 0.0
    assert impact_score(None, 0.5, 0.5, 0.5) == 0.0


def test_ev_4_10_delight_not_blocking():
    assert blocking_severity("delight", 0.9) == 0.0
    assert blocking_severity("frustration", 0.9) == pytest.approx(0.9)
    assert blocking_severity("mixed", 0.8) == pytest.approx(0.4)


def test_ev_4_20_22_confidence_bands():
    assert confidence_band(0.60) == "answer"
    assert confidence_band(0.35) == "caveat"
    assert confidence_band(0.34) == "decline"


def test_ev_4_15_trend_needs_two_buckets():
    assert trend_direction([("2026-W35", 4)]) is None
    assert trend_direction([("2026-W35", 4), ("2026-W36", 8)]) == "rising"
    assert trend_direction([("2026-W35", 8), ("2026-W36", 4)]) == "declining"


def test_data_confidence_matches_architecture():
    value = data_confidence(10, 2, 0.5, c_max=200, s_max=4)
    expected = (
        0.4 * math.log1p(10) / math.log1p(200)
        + 0.3 * min(2 / 4, 1)
        + 0.3 * 0.5
    )
    assert value == pytest.approx(expected)


def test_independent_source_density_skips_missing_authors():
    assert independent_source_density([None, None], ["reddit", "reddit"]) == 1
    assert independent_source_density(["aa", "aa", "bb"], ["reddit", "youtube"]) == 4


# --- eligibility / algorithm ---


def test_ev_4_02_not_applicable_and_empty_excluded():
    ok = ExtractionRecord(
        document_id=uuid4(),
        content_hash="h",
        prompt_version="v",
        extraction_status="ok",
        intent_tag="price_watch",
        friction_tags=[],
    )
    assert is_cluster_eligible(ok) is True
    na = ExtractionRecord(
        document_id=uuid4(),
        content_hash="h",
        prompt_version="v",
        extraction_status="ok",
        intent_tag="not_applicable",
        friction_tags=["fit_uncertainty"],
    )
    assert is_cluster_eligible(na) is False
    empty = ExtractionRecord(
        document_id=uuid4(),
        content_hash="h",
        prompt_version="v",
        extraction_status="ok",
        intent_tag="unknown",
        friction_tags=[],
    )
    assert is_cluster_eligible(empty) is False
    failed = ExtractionRecord(
        document_id=uuid4(),
        content_hash="h",
        prompt_version="v",
        extraction_status="failed",
        intent_tag="price_watch",
        friction_tags=["fit_uncertainty"],
    )
    assert is_cluster_eligible(failed) is False


def test_ev_4_04_tiny_corpus_no_forced_k():
    params = ClusterParams(min_cluster_size=5, min_samples=5, kmeans_max_k=8)
    vectors = [_near(_unit(0), salt=i) for i in range(4)]
    fit = cluster_vectors(vectors, params)
    assert fit.algorithm == "none"
    assert fit.n_clusters == 0
    assert fit.n_noise == 4
    assert all(label == -1 for label in fit.labels)


def test_ev_4_03_noise_not_a_cluster():
    params = ClusterParams(min_cluster_size=3, min_samples=1, kmeans_max_k=8)
    tight = [_near(_unit(0), scale=0.002, salt=i) for i in range(6)]
    noise = [_unit(7), _unit(6)]
    fit = cluster_vectors(tight + noise, params)
    assert fit.n_clusters >= 1
    assert fit.n_clusters < 10
    assert -1 not in fit.centroids


def test_ev_4_kmeans_fallback_does_not_force_10():
    params = ClusterParams(min_cluster_size=3, min_samples=1, kmeans_max_k=8)
    vectors = [_near(_unit(i % 3), salt=i) for i in range(12)]
    fit = cluster_vectors(vectors, params, force_algorithm="kmeans")
    assert fit.algorithm == "kmeans"
    assert fit.n_clusters <= 8
    assert fit.n_clusters != 10


def test_ev_4_16_centroid_match_keeps_theme_id():
    theme_a = uuid4()
    theme_b = uuid4()
    old = [(theme_a, _unit(0)), (theme_b, _unit(1))]
    # labels swapped relative to last week
    new = {7: _unit(1), 3: _unit(0)}
    matched = match_centroids(new, old, min_similarity=0.7)
    assert matched.label_to_theme_id[3] == theme_a
    assert matched.label_to_theme_id[7] == theme_b


def test_ev_4_17_knn_assign():
    theme = uuid4()
    labeled = [(theme, _unit(0)), (uuid4(), _unit(1))]
    winner, sim = knn_assign(_near(_unit(0)), labeled, k=1, min_similarity=0.5)
    assert winner == theme
    assert sim > 0.9
    none, _ = knn_assign(_unit(7), labeled, k=1, min_similarity=0.9)
    assert none is None


def test_generic_theme_names_rejected():
    assert is_generic_theme_name("Customer issues")
    assert is_generic_theme_name("Issues")
    assert not is_generic_theme_name("Kurta size chart vs delivered fit")


def test_bookmark_vs_stall_from_modes():
    assert bookmark_vs_stall_from_modes(["passive_bookmark", "passive_bookmark"]) == BookmarkVsStall.bookmark
    assert bookmark_vs_stall_from_modes(["near_term_purchase"]) == BookmarkVsStall.stall
    assert bookmark_vs_stall_from_modes(["passive_bookmark", "near_term_purchase"]) == BookmarkVsStall.both


# --- SQL contract in migration ---


def test_ev_4_sql_views_and_functions_exist():
    sql = (REPO / "migrations" / "005_phase4.sql").read_text(encoding="utf-8")
    for needle in (
        "CREATE TABLE IF NOT EXISTS cluster_runs",
        "CREATE TABLE IF NOT EXISTS themes",
        "CREATE TABLE IF NOT EXISTS document_themes",
        "CREATE TABLE IF NOT EXISTS theme_metrics",
        "metric_share_of_voice",
        "metric_data_confidence",
        "metric_impact_score",
        "v_eligible_corpus",
        "v_theme_metrics_formula",
        "v_ranked_themes",
        "v_theme_evidence",
        "denominator_definition",
        "unavailable_sources",
        "bookmark_vs_stall",
        "hypothesis_flag",
        "assignment_method",
    ):
        assert needle in sql
    assert DENOMINATOR_DEFINITION == "eligible_normalized_after_relevance_and_quality"


def test_ev_4_cli_commands_registered():
    parser = build_parser()
    command_action = next(a for a in parser._actions if a.dest == "command")
    for name in ("cluster", "metrics", "themes"):
        assert name in (command_action.choices or {})


def test_env_example_has_cluster_params():
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "CLUSTER_MIN_CLUSTER_SIZE" in example
    assert "C_MAX" in example
    assert "GROQ_MODEL_LIGHT" in example


# --- full pipeline + metrics ---


def _fit_blob_corpus(repo: MemoryRepository, extra_price: int = 0) -> None:
    center_fit = _unit(0)
    center_price = _unit(1)
    for i in range(6):
        add_doc(
            repo,
            f"Kurta runs small so it sits in my wishlist {i}",
            _near(center_fit, salt=i),
            product_category="ethnic" if i < 4 else "unknown",
            source_type=SourceType.play_store if i < 4 else SourceType.reddit,
            intent_tag="save_for_later",
            intent_mode="near_term_purchase",
            friction_tags=["fit_uncertainty"],
            sentiment_primary="frustration",
            sentiment_severity=0.8,
            verbatim_quotes=_quotes("Kurta runs small so it sits in my wishlist"),
            extraction_confidence=0.8,
            review_date=utcnow() - timedelta(days=i),
        )
    for i in range(6 + extra_price):
        add_doc(
            repo,
            f"Waiting for coupon on wishlisted sneakers {i}",
            _near(center_price, salt=i),
            product_category="footwear",
            source_type=SourceType.youtube if i % 2 else SourceType.app_store,
            intent_tag="price_watch",
            intent_mode="passive_bookmark",
            friction_tags=["price_sensitivity"],
            sentiment_primary="doubt",
            sentiment_severity=0.6,
            verbatim_quotes=_quotes("Waiting for coupon on wishlisted sneakers"),
            extraction_confidence=0.6,
            review_date=utcnow() - timedelta(days=20 + i),
        )


def test_ev_4_01_03_05_07_cluster_and_metrics():
    repo = MemoryRepository()
    _fit_blob_corpus(repo)
    result = run_cluster(
        repo,
        _settings(),
        mode="recluster",
        label=True,
        complete_fn=groq_labeler,
        sleep=lambda _s: None,
    )
    assert result.status == "success"
    assert result.algorithm in {"hdbscan", "kmeans"}
    assert result.n_themes >= 1
    assert result.n_themes < 10
    run = repo.get_cluster_run(result.run_id)
    assert run is not None
    assert "min_cluster_size" in (run.params or {})
    themes = [t for t in repo.list_themes(result.run_id) if t.published]
    assert themes
    for theme in themes:
        assert theme.name
        assert theme.description is not None
        assert theme.bookmark_vs_stall in {"bookmark", "stall", "both", "unclear"}
        assert isinstance(theme.hypothesis_flag, bool)
        assert is_generic_theme_name(theme.name) is False
        members = repo.list_document_themes(cluster_run_id=result.run_id, theme_id=theme.id)
        assert members
        quotes = 0
        for row in members:
            ext = repo.get_extraction(row.document_id)
            assert ext is not None
            quotes += sum(1 for q in ext.verbatim_quotes if q.get("span"))
        assert quotes >= 1
    ranked = repo.list_theme_metrics(
        cluster_run_id=result.run_id, slice_kind="global", published_only=True
    )
    assert ranked
    for row in ranked:
        assert row.mention_count >= 1
        assert row.share_of_voice == pytest.approx(
            share_of_voice(row.mention_count, row.eligible_corpus_count)
        )
        assert row.impact_score == pytest.approx(
            impact_score(
                row.share_of_voice,
                row.sentiment_severity,
                row.segment_breadth,
                row.data_confidence,
            )
        )
        assert row.unavailable_sources
        assert "instagram" in row.unavailable_sources
        assert row.denominator_definition == DENOMINATOR_DEFINITION


def test_ev_4_02_13_exclusions_not_clustered():
    repo = MemoryRepository()
    _fit_blob_corpus(repo)
    add_doc(
        repo,
        "Weather is nice today",
        _near(_unit(0), salt=99),
        intent_tag="not_applicable",
        friction_tags=["fit_uncertainty"],
        verbatim_quotes=_quotes("Weather is nice today"),
    )
    add_doc(
        repo,
        "Just shopping vibes",
        _near(_unit(0), salt=98),
        intent_tag="unknown",
        friction_tags=[],
        verbatim_quotes=_quotes("Just shopping vibes"),
    )
    add_doc(
        repo,
        "Failed JSON leftover embedding",
        _near(_unit(0), salt=97),
        extraction_status="failed",
        intent_tag="save_for_later",
        friction_tags=["fit_uncertainty"],
        verbatim_quotes=_quotes("Failed JSON leftover embedding"),
    )
    result = run_cluster(
        repo, _settings(), mode="recluster", label=False, sleep=lambda _s: None
    )
    assigned = {row.document_id for row in repo.list_document_themes(cluster_run_id=result.run_id)}
    texts = []
    for doc_id in assigned:
        rec = repo.get_normalized(doc_id)
        texts.append(rec.text_original if rec else "")
    assert all("Weather is nice" not in t for t in texts)
    assert all("Just shopping vibes" not in t for t in texts)
    assert all("Failed JSON" not in t for t in texts)


def test_ev_4_11_monetary_theme_not_filtered():
    repo = MemoryRepository()
    _fit_blob_corpus(repo)
    result = run_cluster(
        repo,
        _settings(),
        mode="recluster",
        label=True,
        complete_fn=groq_labeler,
        sleep=lambda _s: None,
    )
    names = " ".join(t.name.lower() for t in repo.list_themes(result.run_id) if t.published)
    tags = []
    for row in repo.list_document_themes(cluster_run_id=result.run_id):
        ext = repo.get_extraction(row.document_id)
        tags.extend(ext.friction_tags if ext else [])
    assert "price_sensitivity" in tags
    assert "coupon" in names or "price" in names or "price_sensitivity" in tags


def test_ev_4_12_unknown_segment_slice_present():
    repo = MemoryRepository()
    _fit_blob_corpus(repo)
    result = run_cluster(
        repo, _settings(), mode="recluster", label=False, sleep=lambda _s: None
    )
    slices = repo.list_theme_metrics(cluster_run_id=result.run_id, slice_kind="product_category")
    unknown = [row for row in slices if row.slice.get("product_category") == "unknown"]
    assert unknown, "unknown category must appear in slice metrics"


def test_ev_4_09_unavailable_play_not_imputed():
    repo = MemoryRepository()
    center = _unit(0)
    for i in range(6):
        add_doc(
            repo,
            f"Reddit sizing thread {i}",
            _near(center, salt=i),
            source_type=SourceType.reddit,
            product_category="ethnic",
            verbatim_quotes=_quotes("size chart is wrong"),
        )
    repo.set_enabled("play_store", False)
    result = run_cluster(
        repo, _settings(), mode="recluster", label=False, sleep=lambda _s: None
    )
    ranked = repo.list_theme_metrics(
        cluster_run_id=result.run_id, slice_kind="global", published_only=True
    )
    assert ranked
    for row in ranked:
        assert "play_store" in row.unavailable_sources
        assert row.eligible_corpus_count == 6
        source_slices = [
            s
            for s in repo.list_theme_metrics(cluster_run_id=result.run_id, slice_kind="source_type")
            if s.theme_id == row.theme_id
        ]
        play_slices = [s for s in source_slices if s.slice.get("source_type") == "play_store"]
        assert play_slices == []


def test_ev_4_14_mention_count_distinct_document_id():
    repo = MemoryRepository()
    center = _unit(0)
    rec = add_doc(
        repo,
        "Two chunks same review about sizing",
        _near(center),
        extra_chunks=1,
        verbatim_quotes=_quotes("Two chunks same review about sizing"),
    )
    for i in range(5):
        add_doc(
            repo,
            f"More sizing {i}",
            _near(center, salt=i + 1),
            verbatim_quotes=_quotes("More sizing"),
        )
    result = run_cluster(
        repo, _settings(), mode="recluster", label=False, sleep=lambda _s: None
    )
    rows = repo.list_document_themes(cluster_run_id=result.run_id)
    theme_for_rec = [r.theme_id for r in rows if r.document_id == rec.id]
    if theme_for_rec:
        members = [r.document_id for r in rows if r.theme_id == theme_for_rec[0]]
        assert members.count(rec.id) == 1
        snap = next(
            m
            for m in repo.list_theme_metrics(cluster_run_id=result.run_id, slice_kind="global")
            if m.theme_id == theme_for_rec[0]
        )
        assert snap.mention_count == len(set(members))


def test_ev_4_16_recluster_preserves_ids():
    repo = MemoryRepository()
    _fit_blob_corpus(repo)
    first = run_cluster(
        repo, _settings(), mode="recluster", label=False, sleep=lambda _s: None
    )
    ids_first = {t.id for t in repo.list_themes(first.run_id) if t.published}
    second = run_cluster(
        repo, _settings(), mode="recluster", label=False, sleep=lambda _s: None
    )
    ids_second = {t.id for t in repo.list_themes(second.run_id) if t.published}
    assert ids_first
    assert ids_first == ids_second


def test_ev_4_17_incremental_knn_method():
    repo = MemoryRepository()
    _fit_blob_corpus(repo)
    first = run_cluster(
        repo, _settings(), mode="recluster", label=False, sleep=lambda _s: None
    )
    assert first.n_themes >= 1
    first_ids = {t.id for t in repo.list_themes(first.run_id) if t.published}
    add_doc(
        repo,
        "New kurta still runs small, parked in wishlist",
        _near(_unit(0), salt=42),
        verbatim_quotes=_quotes("New kurta still runs small"),
        friction_tags=["fit_uncertainty"],
        intent_mode="near_term_purchase",
    )
    second = run_cluster(
        repo, _settings(), mode="incremental", label=False, sleep=lambda _s: None
    )
    assert second.mode == "incremental"
    assert second.algorithm == "knn_incremental"
    methods = {
        row.assignment_method
        for row in repo.list_document_themes(cluster_run_id=second.run_id)
    }
    assert "knn_incremental" in methods
    assert second.n_incremental >= 1
    assert {t.id for t in repo.list_themes(second.run_id)} == first_ids


def test_ev_4_metrics_hand_calc_three_rows():
    """EV-4-10 without depending on HDBSCAN: three snapshot rows match the four factors."""
    repo = MemoryRepository()
    docs = []
    for i in range(10):
        docs.append(
            add_doc(
                repo,
                f"Fit issue {i}",
                _unit(0),
                product_category="ethnic" if i < 7 else "unknown",
                source_type=SourceType.play_store if i < 5 else SourceType.reddit,
                verbatim_quotes=_quotes("Fit issue"),
                extraction_confidence=0.5,
                sentiment_primary="frustration",
                sentiment_severity=0.8,
            )
        )
    run = ClusterRun(
        id=uuid4(),
        started_at=utcnow(),
        status="success",
        mode="recluster",
        algorithm="hdbscan",
        n_themes=1,
        c_max=200,
        s_max=4,
    )
    repo.start_cluster_run(run)
    repo.finish_cluster_run(run)
    theme = ThemeRecord(
        id=uuid4(),
        name="Kurta size chart vs delivered fit",
        cluster_run_id=run.id,
        description="Fit uncertainty after wishlisting.",
        hypothesis_flag=True,
        bookmark_vs_stall="stall",
        published=True,
        label_status="heuristic",
        centroid=_unit(0),
    )
    repo.upsert_theme(theme)
    repo.replace_document_themes(
        run.id,
        [
            DocumentTheme(
                document_id=doc.id,
                theme_id=theme.id,
                cluster_run_id=run.id,
                assignment_method="cluster",
                assignment_confidence=0.9,
            )
            for doc in docs[:4]
        ],
    )
    out = run_metrics(repo, _settings(), cluster_run_id=run.id)
    assert out.n_themes == 1
    global_row = next(
        r
        for r in repo.list_theme_metrics(cluster_run_id=run.id, slice_kind="global")
        if r.theme_id == theme.id
    )
    assert global_row.mention_count == 4
    assert global_row.eligible_corpus_count == 10
    assert global_row.share_of_voice == pytest.approx(0.4)
    expected_conf = data_confidence(
        4,
        global_row.source_diversity,
        global_row.mean_extraction_confidence,
        c_max=200,
        s_max=4,
    )
    assert global_row.data_confidence == pytest.approx(expected_conf)
    assert global_row.impact_score == pytest.approx(
        impact_score(
            global_row.share_of_voice,
            global_row.sentiment_severity,
            global_row.segment_breadth,
            global_row.data_confidence,
        )
    )
    unknown = [
        r
        for r in repo.list_theme_metrics(cluster_run_id=run.id, slice_kind="product_category")
        if r.slice.get("product_category") == "unknown"
    ]
    assert unknown
    assert global_row.trend_direction is None
