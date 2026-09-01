"""Phase 7 auto evals (docs/eval.md EV-7-*). Live Groq gold scoring is `python -m src.cli eval`."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from src.api.copilot import CopilotService
from src.api.filters import filters_from_params
from src.api.grounding import numbers_subset_of_tools
from src.api.query import QueryService
from src.cli import main
from src.config import (
    FROZEN_C_MAX,
    FROZEN_S_MAX,
    Settings,
    frozen_snapshot,
    require_frozen_constants,
)
from src.cluster.algorithm import match_centroids
from src.db.memory import MemoryRepository
from src.db.repository import IngestRun
from src.evals.dod import static_dod
from src.evals.gold import gold_coverage_errors, load_gold
from src.evals.runner import run_check, run_gold_suite, write_score
from src.evals.scorer import score_row, score_suite
from src.ingest.common import begin_ingest_run
from src.ingest.lock import ExclusiveFileLock
from src.models.envelope import SourceType
from src.timeutil import utcnow

from tests.conftest import ingest, make_review
from tests.test_eval_phase4 import _unit
from tests.test_eval_phase5 import (
    _settings,
    grounded_complete,
    seed_serving_corpus,
)

REPO = Path(__file__).resolve().parents[1]


def test_ev_7_gold_coverage():
    rows = load_gold()
    assert len(rows) >= 18
    errors = gold_coverage_errors(rows)
    assert errors == [], errors
    by_q = {}
    for row in rows:
        by_q.setdefault(row.question_id, []).append(row)
    for qid in [f"Q{i}" for i in range(1, 10)]:
        assert len(by_q[qid]) >= 2, qid
    assert any(r.expected_behavior == "refuse_solution" for r in rows)
    assert any(r.expected_behavior == "refuse_ooscope" for r in rows)
    assert any(r.id == "R3" for r in rows)


def test_ev_7_scorer_bits_and_suite_fail_on_s2():
    gold_ok = load_gold()[0]
    gold_ok.require_citation = True
    gold_ok.require_metrics_match = True
    gold_ok.expected_behavior = "answer"
    gold_ok.acceptable_behaviors = ["answer"]
    doc = str(uuid4())
    api = {
        "themes": {
            "themes": [{"share_of_voice": 0.25, "mention_count": 12, "data_confidence": 0.7}]
        }
    }
    turn = {
        "status": "ok",
        "intent": "qualitative",
        "confidence_band": "answer",
        "answer": "Share of voice is 0.25 with mention_count 12.",
        "citations": [{"document_id": doc, "quote": "runs small"}],
    }
    row = score_row(gold_ok, turn, api)
    assert row.passed
    assert row.s1_citation == 1
    assert row.s2_metrics == 1

    liar = dict(turn)
    liar["answer"] = "Share of voice is 90% of all shoppers."
    bad = score_row(gold_ok, liar, api)
    assert bad.s2_metrics == 0
    assert bad.passed is False
    suite = score_suite([row, bad])
    assert suite.s2_pass is False
    assert suite.suite_pass is False


def test_ev_7_refuse_and_injection_bits():
    rows = {r.id: r for r in load_gold()}
    r1 = rows["R1"]
    turn = {
        "status": "refused",
        "intent": "refuse_solution",
        "confidence_band": "decline",
        "answer": "Product solution design is out of scope — I will not recommend features.",
        "citations": [],
    }
    assert score_row(r1, turn, {}).passed

    r4 = rows["R4"]
    injected = {
        "status": "ok",
        "intent": "quantitative",
        "confidence_band": "answer",
        "answer": "Share of voice is 90%.",
        "citations": [],
    }
    scored = score_row(r4, injected, {"themes": {"themes": [{"share_of_voice": 0.12}]}})
    assert scored.s6_no_inject == 0
    assert scored.passed is False


def test_ev_7_q7_split():
    from src.evals.scorer import has_bookmark_stall_split

    assert has_bookmark_stall_split(
        "Near-term purchase intent shows up in sizing rants; bookmarking is a separate passive pile."
    )
    assert not has_bookmark_stall_split(
        "Users wishlist because they are unsure of fit therefore they bookmark."
    )


def test_ev_7_01_scorer_writes_score_json(tmp_path):
    repo = MemoryRepository()
    settings = _settings(tmp_path)
    seed_serving_corpus(repo, settings)
    service = CopilotService(
        repo,
        settings,
        complete_tools=grounded_complete,
        embed_query=lambda _t: _unit(0),
    )
    query = QueryService(repo, settings)
    gold = [row for row in load_gold() if row.question_id in {"R1", "R2", "R3", "Q7"}]

    def copilot_fn(prompt, filters):
        return service.query_turn(prompt, filters)

    def metrics_fn(filters):
        return {
            "overview": query.overview(filters),
            "themes": query.themes(filters),
            "evidence": query.evidence(filters),
        }

    turns, suite = run_gold_suite(gold, copilot_fn, metrics_fn, groq_available=True)
    dest = tmp_path / "runs" / "7"
    path = write_score(
        dest,
        settings=settings,
        suite=suite,
        turns=turns,
        cluster_run_id="test-cluster",
        embedding_revision="testrev",
        extra={"mode": "test"},
        repo_root=REPO,
    )
    assert path.is_file()
    payload = path.read_text(encoding="utf-8")
    assert "C_max" in payload
    assert "GROQ_MODEL" in payload
    assert '"S_max": 4' in payload or '"S_max":4' in payload
    refuse = [s for s in suite.rows if s.question_id in {"R1", "R2", "R3"}]
    assert refuse and all(row.passed for row in refuse)
    assert suite.refuse_pass is True


def test_ev_7_02_pause_source_unavailable_on_overview_and_copilot(tmp_path):
    repo = MemoryRepository()
    settings = _settings(tmp_path)
    seed_serving_corpus(repo, settings)
    now = utcnow()
    run = IngestRun(
        id=uuid4(),
        source_type=SourceType.play_store.value,
        status="success",
        started_at=now,
        finished_at=now,
        source_available=True,
    )
    repo.start_ingest_run(run)
    repo.finish_ingest_run(run)
    query = QueryService(repo, settings)
    live = query.overview(filters_from_params())
    assert "play_store" not in live["unavailable_sources"]
    repo.set_enabled(SourceType.play_store.value, False)
    overview = query.overview(filters_from_params())
    assert "play_store" in overview["unavailable_sources"]
    play = next(s for s in overview["counts_by_source"] if s["source_type"] == "play_store")
    assert play["status"] == "unavailable"
    assert play["volume_is_current"] is False
    service = CopilotService(
        repo,
        settings,
        complete_tools=grounded_complete,
        embed_query=lambda _t: _unit(0),
    )
    turn = service.query_turn(
        "Why do users add items to the Myntra wishlist?",
        filters_from_params(),
    )
    assert "play_store" in turn["unavailable_sources"]
    themes = query.themes(filters_from_params())
    for card in themes["themes"]:
        if card.get("share_of_voice") is not None:
            assert numbers_subset_of_tools(
                f"{card['share_of_voice']} {card['mention_count']}",
                {"themes": themes},
            )


def test_ev_7_03_recluster_or_refreshed_note(tmp_path):
    theme_a = uuid4()
    matched = match_centroids({3: _unit(0)}, [(theme_a, _unit(0))], min_similarity=0.7)
    assert matched.label_to_theme_id[3] == theme_a
    repo = MemoryRepository()
    settings = _settings(tmp_path)
    seed_serving_corpus(repo, settings)
    themes = QueryService(repo, settings).themes(filters_from_params())
    assert themes["themes_refreshed_at"]
    bar = (REPO / "web" / "components" / "FilterBar.tsx").read_text(encoding="utf-8")
    page = (REPO / "web" / "app" / "(shell)" / "themes" / "page.tsx").read_text(encoding="utf-8")
    assert "themes refreshed" in bar
    assert "themes refreshed" in page


def test_ev_7_04_runbook_covers_section_18():
    text = (REPO / "docs" / "Runbook.md").read_text(encoding="utf-8").lower()
    for needle in (
        "quota",
        "429",
        "invalid json",
        "empty cluster",
        "bge",
        "recluster",
        "unavailable",
        "c_max",
    ):
        assert needle in text, needle
    assert (REPO / "ops" / "n8n" / "discovery-pipeline.json").is_file()
    assert (REPO / "ops" / "windows" / "pipeline.ps1").is_file()
    assert (REPO / "ops" / "cron" / "discovery.crontab").is_file()


def test_ev_7_05_config_freeze():
    settings = Settings(author_hmac_secret="phase7-hmac")
    snap = frozen_snapshot(settings)
    assert snap["actual"]["C_max"] == FROZEN_C_MAX == 200
    assert snap["actual"]["S_max"] == FROZEN_S_MAX == 4
    assert snap["matches_frozen"] is True
    require_frozen_constants(settings)
    drifted = Settings(author_hmac_secret="phase7-hmac", c_max=50)
    assert frozen_snapshot(drifted)["matches_frozen"] is False


def test_ev_7_06_ingest_lock(tmp_path, repo, settings):
    begin_ingest_run(repo, "play_store")
    second = ingest(repo, settings, [[make_review(reviewId="lock-1")]])
    assert second.status == "skipped_locked"
    assert repo.count_raw("play_store") == 0
    lock_path = tmp_path / "pipeline.lock"
    held = ExclusiveFileLock(lock_path, stale_seconds=7200)
    assert held.acquire()
    other = ExclusiveFileLock(lock_path, stale_seconds=7200)
    assert other.acquire() is True
    assert other.reentrant is True
    other.release()
    held.release()
    stale = tmp_path / "stale.lock"
    stale.write_text("1\n", encoding="utf-8")
    import os

    os.utime(stale, (0, 0))
    stolen = ExclusiveFileLock(stale, stale_seconds=60)
    assert stolen.acquire() is True
    stolen.release()


def test_ev_7_06_file_lock_blocks_other_handle(tmp_path):
    path = tmp_path / "exclusive.lock"
    first = ExclusiveFileLock(path)
    assert first.acquire()
    # Simulate another process: fake pid in a second lock object by writing a different pid
    # after first acquired — the file exists so a fresh lock (not same pid owner path)
    # still sees FileExistsError. Same-PID reentry is allowed; different path content
    # with existing file and current pid still reenters. Use a non-owner pid file:
    first.release()
    path.write_text("999999\n", encoding="utf-8")
    blocked = ExclusiveFileLock(path, stale_seconds=10_000)
    assert blocked.acquire() is False


def test_ev_7_07_readme_ops_notes():
    text = (REPO / "README.md").read_text(encoding="utf-8").lower()
    assert "unavailable" in text
    assert "tpm" in text
    assert "bge" in text
    assert "eval" in text
    assert "runbook" in text


def test_ev_7_08_project_dod():
    dod = static_dod(REPO)
    assert dod["ingest_4_5_sources"] is True
    assert dod["raw_structured_tables"] is True
    assert dod["copilot_api"] is True
    assert dod["dashboard_views"] is True
    assert dod["gold_file"] is True
    assert dod["runbook"] is True
    assert dod["passed"] is True


def test_ev_7_check_cli(tmp_path):
    dest = tmp_path / "check"
    payload = run_check(Settings(author_hmac_secret="phase7-hmac"), dest=dest, repo_root=REPO)
    assert payload["result"] == "pass"
    assert (dest / "check.json").is_file()
    assert main(["eval", "--check", "--out", str(dest)]) == 0
