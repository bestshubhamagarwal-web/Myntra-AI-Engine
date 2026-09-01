"""Phase 1 auto evals (docs/eval.md EV-1-*). Live Play Store pulls are operator CLI."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from src.db.memory import MemoryRepository
from src.ingest.object_store import LocalObjectStore
from src.ingest.play_store import run_play_store_ingest
from src.normalize.pipeline import run_normalize
from src.normalize.spotcheck import analysis_record_username_fields, spotcheck_text_failures
from src.timeutil import utcnow

from tests.conftest import ingest, make_envelope, make_review


def _eligible(repo: MemoryRepository):
    return [r for r in repo.normalized.values() if r.eligible]


def test_ev_1_03_idempotent_natural_key(repo, settings):
    review = make_review(reviewId="play-abc")
    first = ingest(repo, settings, [[review]])
    second = ingest(repo, settings, [[review]], force_full=True)
    assert first.status == "success"
    assert second.status == "success"
    assert repo.count_raw("play_store") == 1
    keys = {(e.source_type.value, e.source_id) for e in repo.raw.values()}
    assert keys == {("play_store", "play-abc")}


def test_ev_1_06_pii_scrubbed_before_normalize(repo):
    env = make_envelope(
        source_id="pii-1",
        raw_text=(
            "Order MYNTRA-998877 stuck. Email me at user.name@gmail.com "
            "or 9876543210. Wishlisted the kurta."
        ),
    )
    repo.upsert_raw(env)
    result = run_normalize(repo)
    assert result.accepted == 1
    rec = next(iter(repo.normalized.values()))
    assert "gmail.com" not in rec.text_original
    assert "@" not in rec.text_original
    assert "9876543210" not in rec.text_original
    assert "MYNTRA-998877" not in rec.text_original
    assert "[EMAIL]" in rec.text_original
    assert "[PHONE]" in rec.text_original
    assert "[ORDER_ID]" in rec.text_original
    assert rec.text_en is None
    assert not spotcheck_text_failures(rec.text_original)
    assert analysis_record_username_fields(rec) == []


def test_ev_1_07_emoji_and_empty_not_eligible(repo):
    repo.upsert_raw(make_envelope(source_id="emoji", raw_text="🔥🔥🔥"))
    repo.upsert_raw(make_envelope(source_id="empty", raw_text="   "))
    result = run_normalize(repo)
    assert result.accepted == 0
    assert result.rejected == 2
    assert repo.count_normalized() == 0
    reasons = {e.reject_reason for e in repo.raw.values()}
    assert reasons == {"empty_or_emoji"}


def test_ev_1_08_exact_duplicate_one_survivor(repo):
    text = "  Kurta  runs   SMALL, wishlist mein daala.  "
    a = make_envelope(source_id="dup-a", raw_text=text)
    b = make_envelope(source_id="dup-b", raw_text="kurta runs small, wishlist mein daala.")
    repo.upsert_raw(a)
    repo.upsert_raw(b)
    result = run_normalize(repo)
    eligible = _eligible(repo)
    assert len(eligible) == 1
    assert result.accepted == 1
    assert result.duplicates == 1
    dupes = [r for r in repo.normalized.values() if r.duplicate_of is not None]
    assert len(dupes) == 1
    assert dupes[0].duplicate_of == eligible[0].id
    assert dupes[0].eligible is False


def test_ev_1_09_hinglish_keeps_original(repo):
    text = "size chhota hai, wishlist mein daala hai for this kurta"
    repo.upsert_raw(make_envelope(source_id="hi-mix", raw_text=text))
    run_normalize(repo)
    rec = next(iter(repo.normalized.values()))
    assert rec.language == "hinglish"
    assert rec.text_original == text
    assert rec.text_en is None
    assert "wishlist mein daala" in rec.text_original


def test_ev_1_10_off_topic_person_name_rejected(repo):
    repo.upsert_raw(
        make_envelope(
            source_id="person",
            raw_text="My friend Myntra is a girl in my class and she is funny.",
        )
    )
    result = run_normalize(repo)
    env = next(iter(repo.raw.values()))
    assert result.accepted == 0
    assert env.myntra_relevance.value == "reject"
    assert env.reject_reason == "off_topic"
    assert repo.count_normalized() == 0


def test_ev_1_11_app_crash_reject_does_not_empty_corpus(repo):
    repo.upsert_raw(
        make_envelope(
            source_id="crash",
            raw_text="App crashes on checkout after the update, cannot open, force close.",
        )
    )
    repo.upsert_raw(
        make_envelope(
            source_id="shop",
            raw_text="Loved the dress fabric but returns are painful so it sits in my cart.",
        )
    )
    result = run_normalize(repo)
    crash = next(e for e in repo.raw.values() if e.source_id == "crash")
    assert crash.myntra_relevance.value == "reject"
    assert crash.reject_reason == "app_quality"
    assert result.accepted == 1
    assert result.rejected == 1
    assert _eligible(repo)[0].raw_id != crash.id


def test_ev_1_12_unknown_category(repo):
    repo.upsert_raw(
        make_envelope(
            source_id="unk",
            raw_text="Delivery of my order was late and the price felt high on Myntra app.",
        )
    )
    run_normalize(repo)
    rec = next(iter(repo.normalized.values()))
    assert rec.product_category == "unknown"
    assert rec.gender_segment == "unknown"
    assert rec.price_tier == "unknown"
    assert rec.platform_used == "unknown"


def test_timeout_is_failed_run(repo, settings):
    def fetch(*_args, **_kwargs):
        raise TimeoutError("connection timed out")

    run = run_play_store_ingest(
        repo,
        settings,
        fetch_page=fetch,
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
        retries=1,
    )
    assert run.status == "failed"
    assert run.source_available is False
    assert "timed out" in (run.error_message or "").lower()


def test_ev_1_13_block_is_failed_not_success(repo, settings):
    def fetch(*_args, **_kwargs):
        raise RuntimeError("HTTP 403 Forbidden")

    run = run_play_store_ingest(
        repo,
        settings,
        fetch_page=fetch,
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
        retries=1,
    )
    assert run.status == "failed"
    assert run.source_available is False
    assert run.rows_fetched is None
    assert "403" in (run.error_message or "")
    stored = repo.ingest_runs[run.id]
    assert stored.status == "failed"


def test_ev_1_14_empty_pull_success_still_available(repo, settings):
    run = ingest(repo, settings, [[]])
    assert run.status == "success"
    assert run.rows_fetched == 0
    assert run.rows_upserted == 0
    assert run.source_available is True
    status = {s.source_type: s for s in repo.list_source_status()}
    assert status["play_store"].status == "live"
    assert status["play_store"].last_source_available is True


def test_ev_1_16_snapshot_written_and_username_redacted(repo, settings, tmp_path):
    review = make_review(reviewId="snap-1", userName="Plaintext User")
    ingest(repo, settings, [[review]])
    env = next(iter(repo.raw.values()))
    assert env.payload_uri
    path = Path(env.payload_uri)
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "userName" not in payload
    assert "user_name" not in payload
    assert payload["author_hash"] == env.author_hash
    assert env.author_hash
    assert env.author_hash != "Plaintext User"


def test_disable_source_skips_without_imputing(repo, settings):
    repo.set_enabled("play_store", False)
    run = run_play_store_ingest(
        repo,
        settings,
        fetch_page=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not fetch")),
        env_enabled=True,
        retries=1,
    )
    assert run.status == "skipped_disabled"
    assert run.source_available is False
    assert run.rows_fetched is None
    status = {s.source_type: s for s in repo.list_source_status()}
    assert status["play_store"].status == "unavailable"


def test_env_flag_disables_without_fetch(repo, settings):
    run = run_play_store_ingest(
        repo,
        settings,
        fetch_page=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not fetch")),
        env_enabled=False,
        retries=1,
    )
    assert run.status == "skipped_disabled"
    assert run.source_available is False


def test_watermark_skips_older_keeps_undated(repo, settings):
    now = utcnow()
    old_id = "old-1"
    ingest(
        repo,
        settings,
        [[make_review(reviewId=old_id, at=now - timedelta(days=5))]],
    )
    newer = make_review(reviewId="new-1", at=now - timedelta(hours=1))
    undated = make_review(reviewId="null-1", at=None)
    older = make_review(reviewId=old_id, at=now - timedelta(days=5))
    run = ingest(repo, settings, [[newer, undated, older]])
    assert run.rows_fetched == 2
    ids = {e.source_id for e in repo.raw.values()}
    assert ids == {"old-1", "new-1", "null-1"}


def test_future_published_at_does_not_starve_watermark(repo, settings):
    now = utcnow()
    future = make_review(reviewId="fut", at=now + timedelta(days=400))
    past = make_review(reviewId="past", at=now - timedelta(days=2))
    ingest(repo, settings, [[future, past]])
    env_future = next(e for e in repo.raw.values() if e.source_id == "fut")
    assert env_future.date_anomaly is True
    watermark = repo.get_watermark("play_store")
    assert watermark is not None
    assert watermark <= now
    past_env = next(e for e in repo.raw.values() if e.source_id == "past")
    assert watermark == past_env.published_at


def test_edited_review_renormalizes(repo, settings):
    rid = "edit-1"
    ingest(
        repo,
        settings,
        [[make_review(reviewId=rid, content="Nice dress, added to bag yesterday.")]],
    )
    run_normalize(repo, since_run_id=next(iter(repo.ingest_runs)))
    first = next(iter(repo.normalized.values()))
    assert "dress" in first.text_original
    ingest(
        repo,
        settings,
        [[
            make_review(
                reviewId=rid,
                content="Kurta sizing is wrong, email foo@myntra.com if you fix returns.",
                at=utcnow() + timedelta(seconds=2),
            )
        ]],
        force_full=True,
    )
    result = run_normalize(repo)
    assert result.accepted == 1
    rec = next(iter(repo.normalized.values()))
    assert rec.id == first.id
    assert "Kurta sizing" in rec.text_original
    assert "@" not in rec.text_original
    assert repo.count_raw("play_store") == 1


def test_devanagari_language_hi(repo):
    repo.upsert_raw(
        make_envelope(
            source_id="hi-1",
            raw_text="कुर्ता छोटा है साइज़ चार्ट गलत है विशलिस्ट में डाला",
        )
    )
    run_normalize(repo)
    rec = next(iter(repo.normalized.values()))
    assert rec.language == "hi"
    assert "कुर्ता" in rec.text_original


def test_boilerplate_rejected(repo):
    repo.upsert_raw(make_envelope(source_id="bp", raw_text="Nice app."))
    run_normalize(repo)
    env = next(iter(repo.raw.values()))
    assert env.reject_reason == "boilerplate"


def test_conflicting_gender_stays_unknown(repo):
    repo.upsert_raw(
        make_envelope(
            source_id="g",
            raw_text="Men's sneakers as a gift for wife, still in the cart.",
        )
    )
    run_normalize(repo)
    rec = next(iter(repo.normalized.values()))
    assert rec.gender_segment == "unknown"
    assert rec.product_category == "footwear"


def test_persistent_store_keeps_play_store_ingest(tmp_path, settings):
    from src.db.local import PersistentMemoryRepository

    path = tmp_path / "store.pkl"
    settings.local_store_path = path
    repo = PersistentMemoryRepository(path)
    run = ingest(repo, settings, [[make_review(reviewId="play-persist")]])
    assert run.status == "success"
    assert run.source_available is True
    reloaded = PersistentMemoryRepository(path)
    assert reloaded.count_raw("play_store") == 1
    statuses = {row.source_type: row.status for row in reloaded.list_source_status()}
    assert statuses["play_store"] == "live"
    assert statuses["instagram"] == "unavailable"
