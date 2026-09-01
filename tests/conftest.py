from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from src.config import Settings
from src.db.memory import MemoryRepository
from src.ingest.object_store import LocalObjectStore
from src.ingest.play_store import run_play_store_ingest
from src.models.envelope import RawEnvelope, SourceType
from src.timeutil import utcnow


@pytest.fixture
def repo() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        author_hmac_secret="phase1-test-hmac-secret",
        raw_store_path=tmp_path,
        play_store_page_sleep_seconds=0.0,
        play_store_max_reviews=200,
        play_store_enabled=True,
        local_store_path=tmp_path / "local_store.pkl",
    )


def make_review(**overrides) -> dict:
    now = utcnow()
    row = {
        "reviewId": str(uuid4()),
        "userName": "Alice Sharma",
        "content": "Kurta runs small, added to wishlist until I check the size chart.",
        "score": 3,
        "at": now - timedelta(days=1),
        "thumbsUpCount": 1,
        "appVersion": "4.0.0",
    }
    row.update(overrides)
    return row


def page_fetcher(pages: list[list[dict]]):
    state = {"i": 0}

    def fetch(_app_id: str, _lang: str, _country: str, _count: int, _token):
        i = state["i"]
        state["i"] += 1
        if i >= len(pages):
            return [], None
        nxt = "cont" if i + 1 < len(pages) else None
        return pages[i], nxt

    return fetch


def ingest(
    repo: MemoryRepository,
    settings: Settings,
    pages: list[list[dict]],
    **kwargs,
):
    store = LocalObjectStore(Path(settings.raw_store_path))
    return run_play_store_ingest(
        repo,
        settings,
        fetch_page=page_fetcher(pages),
        object_store=store,
        env_enabled=True,
        retries=1,
        **kwargs,
    )


def make_envelope(**overrides) -> RawEnvelope:
    now = utcnow()
    payload = dict(
        source_type=SourceType.play_store,
        source_id=str(uuid4()),
        url="https://play.google.com/store/apps/details?id=com.myntra.android",
        fetched_at=now,
        published_at=now,
        platform="android",
        raw_text="Size chart is wrong so I left it in the wishlist.",
        raw_title=None,
        star_rating=3,
        parent_context={},
        author_hash="ab" * 32,
        payload_uri=None,
    )
    payload.update(overrides)
    return RawEnvelope.model_validate(payload)
