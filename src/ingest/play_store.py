from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from src.config import Settings
from src.db.repository import DocumentRepository, IngestRun
from src.ingest.allowlist import require_myntra_play_app_id
from src.ingest.common import (
    ConnectorBlocked,
    PersistResult,
    begin_ingest_run,
    fail_run,
    is_block_error,
    payload_warning_text,
    persist_envelopes,
    skip_if_disabled,
    succeed_run,
)
from src.ingest.object_store import LocalObjectStore
from src.models.envelope import RawEnvelope, SourceType
from src.normalize.hashing import content_hash
from src.normalize.pii import hash_author
from src.timeutil import coerce_aware, utcnow

logger = logging.getLogger(__name__)

PLAY_STORE_SOURCE = SourceType.play_store.value

ReviewPage = tuple[list[dict[str, Any]], Any]
FetchPage = Callable[[str, str, str, int, Any], ReviewPage]


def collect_reviews(
    fetch_page: FetchPage,
    app_id: str,
    lang: str,
    country: str,
    max_reviews: int,
    watermark: datetime | None,
    sleep_seconds: float,
    retries: int = 4,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    token: Any = None
    delay = max(sleep_seconds, 0.0)
    seen_ids: set[str] = set()

    while len(collected) < max_reviews:
        batch_size = min(200, max_reviews - len(collected))
        last_error: BaseException | None = None
        batch: list[dict[str, Any]] = []
        next_token: Any = None
        for attempt in range(retries):
            try:
                batch, next_token = fetch_page(app_id, lang, country, batch_size, token)
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 — connector surface
                last_error = exc
                if attempt < retries - 1:
                    time.sleep(min(2**attempt, 30))
                    continue
                if is_block_error(exc):
                    raise ConnectorBlocked(str(exc)) from exc
                raise
        if last_error:
            raise last_error
        if not batch:
            break
        stop = False
        new_in_batch = 0
        for review in batch:
            review_id = str(review.get("reviewId") or review.get("review_id") or "")
            if review_id and review_id in seen_ids:
                continue
            if review_id:
                seen_ids.add(review_id)
            published = coerce_aware(review.get("at"))
            if watermark is not None and published is not None and published <= watermark:
                stop = True
                break
            collected.append(review)
            new_in_batch += 1
            if len(collected) >= max_reviews:
                break
        if len(collected) and len(collected) % 1000 == 0:
            logger.info("play_store collected %s unique reviews", len(collected))
        if stop or not next_token or new_in_batch == 0:
            break
        token = next_token
        if delay:
            time.sleep(delay)
    return collected


def default_fetch_page(
    app_id: str, lang: str, country: str, count: int, token: Any
) -> ReviewPage:
    from google_play_scraper import Sort, reviews

    require_myntra_play_app_id(app_id)
    result, continuation = reviews(
        app_id,
        lang=lang,
        country=country,
        sort=Sort.NEWEST,
        count=count,
        continuation_token=token,
    )
    return result, continuation


def review_to_envelope(
    review: dict[str, Any],
    *,
    app_id: str,
    hmac_secret: str,
    ingest_run_id,
    fetched_at: datetime,
    now: datetime,
) -> RawEnvelope:
    source_id = str(review.get("reviewId") or review.get("review_id") or "")
    if not source_id:
        raise ValueError("Play Store review missing reviewId")
    published = coerce_aware(review.get("at"))
    date_anomaly = bool(published and published > now + timedelta(days=1))
    username = review.get("userName") or review.get("user_name")
    author = hash_author(username, hmac_secret)
    text = review.get("content") or review.get("text") or ""
    title = review.get("title")
    score = review.get("score")
    parent_context = {
        "app_id": app_id,
        "app_version": review.get("appVersion") or review.get("reviewCreatedVersion"),
        "thumbs_up": review.get("thumbsUpCount"),
    }
    url = f"https://play.google.com/store/apps/details?id={app_id}&reviewId={source_id}"
    body_for_hash = "\n".join(p for p in (str(title).strip() if title else "", str(text).strip()) if p)
    return RawEnvelope(
        source_type=SourceType.play_store,
        source_id=source_id,
        url=url,
        fetched_at=fetched_at,
        published_at=published,
        platform="android",
        raw_text=text,
        raw_title=title,
        star_rating=int(score) if score is not None else None,
        parent_context=parent_context,
        author_hash=author,
        content_hash=content_hash(body_for_hash),
        ingest_run_id=ingest_run_id,
        date_anomaly=date_anomaly,
    )


def run_play_store_ingest(
    repo: DocumentRepository,
    settings: Settings,
    *,
    fetch_page: FetchPage | None = None,
    object_store: LocalObjectStore | None = None,
    max_reviews: int | None = None,
    max_items: int | None = None,
    force_full: bool = False,
    env_enabled: bool | None = None,
    retries: int = 4,
) -> IngestRun:
    fetch_page = fetch_page or default_fetch_page
    object_store = object_store or LocalObjectStore(settings.raw_store_path)
    run = begin_ingest_run(repo, PLAY_STORE_SOURCE)
    if run.status != "running":
        return run

    enabled_db = repo.is_enabled(PLAY_STORE_SOURCE)
    enabled_env = settings.play_store_enabled if env_enabled is None else env_enabled
    skipped = skip_if_disabled(
        repo,
        run,
        enabled_db=enabled_db,
        enabled_env=enabled_env,
        source_type=PLAY_STORE_SOURCE,
    )
    if skipped:
        return skipped

    require_myntra_play_app_id(settings.play_store_app_id)
    watermark = None if force_full else repo.get_watermark(PLAY_STORE_SOURCE)
    run.watermark_before = watermark
    limit = max_items if max_items is not None else max_reviews
    if limit is None:
        limit = settings.play_store_max_reviews

    try:
        reviews = collect_reviews(
            fetch_page,
            settings.play_store_app_id,
            settings.play_store_lang,
            settings.play_store_country,
            limit,
            watermark,
            settings.play_store_page_sleep_seconds,
            retries=retries,
        )
    except ConnectorBlocked as exc:
        return fail_run(repo, run, exc, blocked=True)
    except Exception as exc:  # noqa: BLE001
        return fail_run(repo, run, exc)

    if not reviews:
        return succeed_run(repo, run, fetched=0, upserted=0, watermark=watermark)

    now = utcnow()
    fetched_at = utcnow()
    secret = settings.require_hmac_secret()
    items: list[tuple[RawEnvelope, dict[str, Any]]] = []
    for review in reviews:
        envelope = review_to_envelope(
            review,
            app_id=settings.play_store_app_id,
            hmac_secret=secret,
            ingest_run_id=run.id,
            fetched_at=fetched_at,
            now=now,
        )
        items.append((envelope, review))

    persisted: PersistResult = persist_envelopes(
        repo,
        object_store,
        PLAY_STORE_SOURCE,
        items,
        watermark,
        snapshots=len(items) <= 400,
    )
    warning = payload_warning_text(persisted.payload_warnings)
    if persisted.error:
        return fail_run(
            repo,
            run,
            persisted.error,
            fetched=len(reviews),
            upserted=persisted.upserted,
            watermark=persisted.watermark_after,
            payload_warning=warning,
        )
    return succeed_run(
        repo,
        run,
        fetched=len(reviews),
        upserted=persisted.upserted,
        watermark=persisted.watermark_after,
        payload_warning=warning,
    )
