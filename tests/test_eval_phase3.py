"""Phase 3 auto evals (docs/eval.md EV-3-*). Live source APIs are operator CLI."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from src.config import Settings
from src.db.memory import MemoryRepository
from src.extract.pipeline import run_extract
from src.ingest.allowlist import MYNTRA_APP_STORE_APP_ID, MYNTRA_PLAY_STORE_APP_ID
from src.ingest.app_store import run_app_store_ingest
from src.ingest.object_store import LocalObjectStore
from src.ingest.play_store import run_play_store_ingest
from src.ingest.reddit import run_reddit_ingest
from src.ingest.registry import run_unimplemented_ingest
from src.ingest.review_dump import write_review_dump
from src.ingest.x import run_x_ingest
from src.ingest.youtube import run_youtube_ingest
from src.models.envelope import ARCHITECTURE_SOURCE_TYPES, SourceType
from src.normalize.hashing import content_hash
from src.normalize.pipeline import run_normalize
from src.normalize.relevance import classify_relevance
from src.timeutil import utcnow

from tests.conftest import ingest, make_envelope, make_review
from tests.test_eval_phase2 import add_normalized, groq_result, valid_payload

REPO = Path(__file__).resolve().parents[1]


def _app_entry(**overrides) -> dict:
    now = utcnow()
    entry = {
        "id": {"label": "ios-100"},
        "author": {"name": {"label": "Patel"}},
        "updated": {"label": (now - timedelta(days=1)).isoformat()},
        "title": {"label": "Size chart"},
        "content": {"label": "Kurta runs small so I left it on my wishlist."},
        "im:rating": {"label": "3"},
        "im:version": {"label": "5.1"},
    }
    entry.update(overrides)
    return entry


def _reddit_post(**overrides) -> dict:
    row = {
        "id": "abc123",
        "kind": "submission",
        "title": "Myntra sizing is wild",
        "selftext": "Put the kurta in my wishlist until I check the size chart.",
        "created_utc": utcnow().timestamp(),
        "subreddit": "IndianFashionAddicts",
        "permalink": "/r/IndianFashionAddicts/comments/abc123/myntra/",
        "author": "user1",
        "query": "Myntra sizing",
        "thread_title": "Myntra sizing is wild",
    }
    row.update(overrides)
    return row


def _youtube_comment(**overrides) -> dict:
    row = {
        "id": "ytc-1",
        "video_id": "vid1",
        "video_title": "Myntra haul + size guide",
        "channel_title": "Hauls",
        "query": "Myntra haul",
        "text": "Runs small, still sitting in my cart.",
        "published_at": utcnow().isoformat(),
        "author": "ytuser",
    }
    row.update(overrides)
    return row


def test_ev_3_02_composite_unique_key(repo, settings):
    play = make_envelope(source_id="123", source_type=SourceType.play_store)
    reddit = make_envelope(
        source_id="123",
        source_type=SourceType.reddit,
        url="https://www.reddit.com/r/india/comments/123",
        platform="reddit",
        raw_text="Myntra returns took forever so it stays in the wishlist.",
    )
    repo.upsert_raw(play)
    repo.upsert_raw(reddit)
    assert repo.count_raw() == 2
    keys = {(e.source_type.value, e.source_id) for e in repo.raw.values()}
    assert keys == {("play_store", "123"), ("reddit", "123")}


def test_ev_3_03_source_status_covers_architecture_types(repo):
    rows = {row.source_type: row for row in repo.list_source_status()}
    missing = [name for name in ARCHITECTURE_SOURCE_TYPES if name not in rows]
    assert missing == []
    for row in rows.values():
        assert row.status in {"live", "failed", "unavailable"}


def test_ev_3_04_instagram_facebook_unavailable(repo):
    rows = {row.source_type: row for row in repo.list_source_status()}
    assert rows["instagram"].status == "unavailable"
    assert rows["facebook"].status == "unavailable"
    assert rows["instagram"].enabled is False
    assert rows["facebook"].enabled is False
    insta = run_unimplemented_ingest(repo, "instagram")
    assert insta.status == "skipped_unconfigured"
    assert insta.source_available is False
    rows = {row.source_type: row for row in repo.list_source_status()}
    assert rows["instagram"].status == "unavailable"


def test_ev_3_05_myntra_only_app_ids():
    assert MYNTRA_PLAY_STORE_APP_ID == "com.myntra.android"
    assert MYNTRA_APP_STORE_APP_ID == "907394059"
    with pytest.raises(Exception):
        Settings(play_store_app_id="com.ril.ajio", author_hmac_secret="phase3-hmac")
    with pytest.raises(Exception):
        Settings(app_store_app_id="123456", author_hmac_secret="phase3-hmac")
    forbidden = (
        "com.ril.ajio",
        "com.nykaa.fashion",
        "com.flipkart.android",
        "com.meesho.android",
    )
    for path in (REPO / "src" / "ingest").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} in {path}"


def test_ev_3_08_youtube_off_topic_rejected(repo):
    env = make_envelope(
        source_type=SourceType.youtube,
        source_id="shein-1",
        url="https://www.youtube.com/watch?v=abc",
        platform="youtube",
        raw_text="Size is small, love this haul.",
        raw_title="Shein winter haul try-on",
        parent_context={"video_title": "Shein winter haul try-on", "video_id": "abc"},
    )
    repo.upsert_raw(env)
    result = run_normalize(repo)
    stored = next(iter(repo.raw.values()))
    assert result.accepted == 0
    assert stored.myntra_relevance.value == "reject"
    assert stored.reject_reason == "off_topic"
    assert repo.count_normalized() == 0


def test_ev_3_09_reddit_deleted_removed(repo):
    env = make_envelope(
        source_type=SourceType.reddit,
        source_id="gone-1",
        url="https://www.reddit.com/r/india/comments/gone",
        platform="reddit",
        raw_text="[deleted]",
        raw_title="Myntra wishlist thread",
        parent_context={"thread_title": "Myntra wishlist thread", "subreddit": "india"},
    )
    repo.upsert_raw(env)
    result = run_normalize(repo)
    stored = next(iter(repo.raw.values()))
    assert result.accepted == 0
    assert stored.reject_reason == "removed"
    assert repo.count_normalized() == 0


def test_reddit_empty_body_removed(repo):
    env = make_envelope(
        source_type=SourceType.reddit,
        source_id="empty-1",
        raw_text="   ",
        raw_title="",
        platform="reddit",
    )
    repo.upsert_raw(env)
    run_normalize(repo)
    stored = next(iter(repo.raw.values()))
    assert stored.reject_reason == "removed"


def test_ev_3_07_youtube_parent_context_has_video_title(repo, settings):
    run = run_youtube_ingest(
        repo,
        settings,
        fetch_items=lambda *_a, **_k: [_youtube_comment()],
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    assert run.status == "success"
    env = next(iter(repo.raw.values()))
    assert env.source_type == SourceType.youtube
    assert env.parent_context.get("video_title")
    assert "Myntra" in env.parent_context["video_title"]


def test_app_store_and_reddit_and_x_ingest_then_normalize(repo, settings):
    app_run = run_app_store_ingest(
        repo,
        settings,
        fetch_page=lambda _id, _cc, page: [_app_entry()] if page == 1 else [],
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    reddit_run = run_reddit_ingest(
        repo,
        settings,
        fetch_items=lambda *_a, **_k: [_reddit_post()],
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    repo.set_enabled("x", True)
    x_run = run_x_ingest(
        repo,
        settings,
        fetch_items=lambda *_a, **_k: [
            {
                "id": "tw-1",
                "text": "Myntra returns are slow so the dress sits in my wishlist.",
                "created_at": utcnow().isoformat(),
                "author_id": "999",
                "conversation_id": "tw-1",
                "query": "Myntra returns",
            }
        ],
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    play_run = ingest(
        repo,
        settings,
        [[make_review(reviewId="play-mix")]],
    )
    assert {app_run.status, reddit_run.status, x_run.status, play_run.status} == {"success"}
    yt_run = run_youtube_ingest(
        repo,
        settings,
        fetch_items=lambda *_a, **_k: [_youtube_comment()],
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    assert yt_run.status == "success"
    result = run_normalize(repo)
    assert result.accepted >= 4
    types = set()
    for rec in repo.normalized.values():
        if not rec.eligible:
            continue
        env = repo.raw[rec.raw_id]
        types.add(env.source_type.value)
    assert types >= {"play_store", "app_store", "reddit", "youtube"}
    assert repo.count_normalized("reddit") >= 1
    sample_text = " ".join(r.text_original.lower() for r in repo.normalized.values())
    assert any(token in sample_text for token in ("wishlist", "sizing", "size", "return"))


def test_review_dump_writes_scrubbed_phase3_files(repo, settings, tmp_path):
    ingest(repo, settings, [[make_review(reviewId="play-dump", userName="Alice Sharma")]])
    run_app_store_ingest(
        repo,
        settings,
        fetch_page=lambda _id, _cc, page: [_app_entry()] if page == 1 else [],
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    run_reddit_ingest(
        repo,
        settings,
        fetch_items=lambda *_a, **_k: [_reddit_post()],
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    run_youtube_ingest(
        repo,
        settings,
        fetch_items=lambda *_a, **_k: [_youtube_comment()],
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    repo.upsert_raw(
        make_envelope(
            source_type=SourceType.youtube,
            source_id="shein-dump",
            url="https://www.youtube.com/watch?v=abc",
            platform="youtube",
            raw_text="Size is small, love this haul.",
            raw_title="Shein winter haul try-on",
            parent_context={"video_title": "Shein winter haul try-on", "video_id": "abc"},
        )
    )
    run_normalize(repo)
    dest = tmp_path / "phase3"
    result = write_review_dump(repo, dest)
    blob = "\n".join(path.read_text(encoding="utf-8") for path in dest.iterdir() if path.is_file())
    assert "Alice Sharma" not in blob
    assert "Patel" not in blob
    assert "user1" not in blob
    assert "ytuser" not in blob
    assert (dest / "play_store.jsonl").exists()
    assert (dest / "app_store.jsonl").exists()
    assert (dest / "reddit.jsonl").exists()
    assert (dest / "youtube.jsonl").exists()
    assert (dest / "sample.md").exists()
    assert (dest / "all.csv").exists()
    status = (dest / "source_status.md").read_text(encoding="utf-8")
    assert "instagram" in status
    assert "unavailable" in status
    sample = (dest / "sample.md").read_text(encoding="utf-8").lower()
    assert any(token in sample for token in ("wishlist", "size", "return"))
    youtube = (dest / "youtube.jsonl").read_text(encoding="utf-8")
    assert "Myntra haul" in youtube
    assert "off_topic" in youtube
    assert "play_store" in result.live_source_types
    assert result.source_counts["play_store"]["normalized"] >= 1


def test_ev_3_12_fifth_source_or_unavailable(repo, settings, monkeypatch):
    rows = {row.source_type: row for row in repo.list_source_status()}
    assert rows["x"].status == "unavailable"
    assert rows["quora"].status == "unavailable"
    repo.set_enabled("x", True)

    def no_public(*_a, **_k):
        from src.ingest.x import PublicSourceUnavailable

        raise PublicSourceUnavailable(
            "X public RSS hosts did not respond and bearer token is missing; "
            "source unavailable — no metrics imputed"
        )

    monkeypatch.setattr("src.ingest.x.public_fetch", no_public)
    run = run_x_ingest(
        repo,
        settings,
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    assert run.status == "skipped_unconfigured"
    assert run.source_available is False
    rows = {row.source_type: row for row in repo.list_source_status()}
    assert rows["x"].status == "unavailable"
    assert "unavailable" in (run.error_message or "").lower()


def test_x_status_rss_parser():
    from src.ingest.x import parse_status_rss

    xml = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>User: Myntra wishlist is full of kurtas that run small</title>
        <link>https://nitter.example/user/status/1234567890</link>
        <description>Myntra wishlist is full of kurtas that run small</description>
        <pubDate>Mon, 01 Sep 2026 10:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Unrelated tweet</title>
        <link>https://nitter.example/user/status/1</link>
        <description>weather is nice</description>
      </item>
    </channel></rss>
    """
    rows = parse_status_rss(xml, "Myntra wishlist")
    assert len(rows) == 1
    assert rows[0]["id"] == "1234567890"
    assert "wishlist" in rows[0]["text"].lower()


def test_ev_3_13_failure_does_not_mark_other_sources(repo, settings):
    play = ingest(repo, settings, [[make_review(reviewId="ok-play")]])
    assert play.status == "success"

    def boom(*_a, **_k):
        raise RuntimeError("youtube 500")

    yt = run_youtube_ingest(
        repo,
        settings,
        fetch_items=boom,
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    assert yt.status == "failed"
    assert yt.source_available is False
    rows = {row.source_type: row for row in repo.list_source_status()}
    assert rows["play_store"].status == "live"
    assert rows["youtube"].status == "failed"
    assert rows["play_store"].last_run_id == play.id


def test_youtube_unconfigured_without_key(repo, settings, monkeypatch):
    monkeypatch.setattr("src.ingest.youtube.get_json_soft", lambda *_a, **_k: None)
    monkeypatch.setattr("src.ingest.youtube.post_json_soft", lambda *_a, **_k: None)
    run = run_youtube_ingest(
        repo,
        settings,
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    assert run.status == "skipped_unconfigured"
    assert run.source_available is False


def test_youtube_public_fallback_without_api_key(repo, settings):
    assert not (settings.youtube_api_key or "").strip()
    run = run_youtube_ingest(
        repo,
        settings,
        fetch_items=lambda *_a, **_k: [_youtube_comment()],
        object_store=LocalObjectStore(Path(settings.raw_store_path)),
        env_enabled=True,
    )
    assert run.status == "success"
    assert repo.count_raw("youtube") == 1


def test_youtube_public_parsers():
    from src.ingest.youtube import parse_invidious_comments, parse_invidious_search, parse_piped_search

    videos = parse_invidious_search(
        [
            {
                "type": "video",
                "videoId": "abc",
                "title": "Myntra haul size guide",
                "author": "Hauls",
                "description": "Wishlist picks",
            }
        ]
    )
    assert videos[0]["video_id"] == "abc"
    piped = parse_piped_search(
        {
            "items": [
                {
                    "type": "stream",
                    "url": "/watch?v=xyz",
                    "title": "Myntra unboxing",
                    "uploaderName": "Chan",
                }
            ]
        }
    )
    assert piped[0]["video_id"] == "xyz"
    comments = parse_invidious_comments(
        {
            "comments": [
                {
                    "commentId": "c1",
                    "content": "Runs small, still in my cart.",
                    "published": 1710000000,
                    "author": "ytuser",
                }
            ]
        },
        {"video_id": "abc", "video_title": "Myntra haul", "channel_title": "Hauls", "query": "Myntra haul"},
    )
    assert comments[0]["id"] == "c1"
    assert "cart" in comments[0]["text"]


def test_ev_3_10_extract_skips_unchanged_hash(repo):
    first = add_normalized(repo, "Myntra size chart is wrong, left in wishlist.")
    second = add_normalized(repo, "New reddit thread: Myntra returns after sizing fail.")
    calls = {"n": 0}

    def complete(messages, **_kwargs):
        calls["n"] += 1
        return groq_result(valid_payload())

    settings = Settings(
        author_hmac_secret="phase3-hmac",
        groq_api_key="test-key",
        groq_min_interval_seconds=0.0,
        groq_json_retries=1,
    )
    first_run = run_extract(repo, settings, complete_fn=complete, sleep=lambda _s: None)
    assert first_run.ok == 2
    assert calls["n"] == 2
    second_run = run_extract(repo, settings, complete_fn=complete, sleep=lambda _s: None)
    assert second_run.ok == 0
    assert calls["n"] == 2
    assert repo.get_extraction(first.id).content_hash == content_hash(first.text_original)
    assert repo.get_extraction(second.id) is not None


def test_ajio_is_query_not_app_crawl():
    sql = (REPO / "migrations" / "002_seed.sql").read_text(encoding="utf-8")
    assert "Myntra vs AJIO" in sql
    play = (REPO / "src" / "ingest" / "play_store.py").read_text(encoding="utf-8")
    assert "ajio" not in play.lower()
    app = (REPO / "src" / "ingest" / "app_store.py").read_text(encoding="utf-8")
    assert "ajio" not in app.lower()


def test_source_status_does_not_impute_zero_as_live(repo, settings):
    ingest(repo, settings, [[]])
    rows = {row.source_type: row for row in repo.list_source_status()}
    assert rows["play_store"].status == "live"
    assert rows["play_store"].last_rows_fetched == 0
    assert rows["youtube"].status == "unavailable"
    assert rows["youtube"].raw_count == 0
    assert rows["youtube"].normalized_count == 0
