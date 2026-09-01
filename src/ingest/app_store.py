"""Apple App Store — official iTunes customer-reviews RSS (Myntra app only)."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.config import Settings
from src.db.repository import DocumentRepository, IngestRun
from src.ingest.allowlist import require_myntra_app_store_id
from src.ingest.common import (
    ConnectorBlocked,
    begin_ingest_run,
    date_anomaly,
    fail_run,
    payload_warning_text,
    persist_envelopes,
    skip_if_disabled,
    succeed_run,
)
from src.ingest.http import get_json, get_json_soft, get_text_soft, url_with_query
from src.ingest.object_store import LocalObjectStore
from src.models.envelope import RawEnvelope, SourceType
from src.normalize.hashing import content_hash
from src.normalize.pii import hash_author
from src.timeutil import parse_datetime, utcnow

logger = logging.getLogger(__name__)

APP_STORE_SOURCE = SourceType.app_store.value
MAX_RSS_PAGES = 10
APPLE_TOKEN_RE = re.compile(r"eyJhbGciOiJ[A-Za-z0-9_\-\.]+")

FetchRssPage = Callable[[str, str, int], list[dict[str, Any]]]


def _label(node: Any, default: str | None = None) -> str | None:
    if node is None:
        return default
    if isinstance(node, dict):
        value = node.get("label")
        return str(value) if value is not None else default
    return str(node)


def _as_entry_list(feed: Any) -> list[dict[str, Any]]:
    if not isinstance(feed, dict):
        return []
    inner = feed.get("feed") if "feed" in feed else feed
    if not isinstance(inner, dict):
        return []
    entry = inner.get("entry")
    if entry is None:
        return []
    if isinstance(entry, dict):
        return [entry]
    if isinstance(entry, list):
        return [item for item in entry if isinstance(item, dict)]
    return []


def is_review_entry(entry: dict[str, Any]) -> bool:
    return bool(entry.get("im:rating") or entry.get("im:version"))


def review_source_id(entry: dict[str, Any]) -> str:
    raw_id = _label(entry.get("id")) or ""
    if raw_id.isdigit():
        return raw_id
    author = entry.get("author") if isinstance(entry.get("author"), dict) else {}
    uri = _label(author.get("uri")) or ""
    if "/id" in uri:
        tail = uri.rsplit("/id", 1)[-1].strip("/")
        if tail:
            return tail
    updated = _label(entry.get("updated")) or ""
    title = _label(entry.get("title")) or ""
    content = _label(entry.get("content")) or ""
    digest = hashlib.sha256(f"{updated}|{title}|{content[:120]}".encode("utf-8")).hexdigest()[:16]
    return f"rss-{digest}"


def default_fetch_page(app_id: str, country: str, page: int, sort: str = "mostrecent") -> list[dict[str, Any]]:
    require_myntra_app_store_id(app_id)
    sortby = "mosthelpful" if sort == "mosthelpful" else "mostrecent"
    url = (
        f"https://itunes.apple.com/{country}/rss/customerreviews/"
        f"page={page}/id={app_id}/sortby={sortby}/json"
    )
    payload = get_json(url, retries=4, timeout=30)
    return [entry for entry in _as_entry_list(payload) if is_review_entry(entry)]


def apple_web_token(country: str, app_id: str) -> str | None:
    html = get_text_soft(
        f"https://apps.apple.com/{country}/app/id{app_id}",
        headers={"Accept": "text/html"},
        timeout=30,
    )
    if not html:
        return None
    match = APPLE_TOKEN_RE.search(html)
    return match.group(0) if match else None


def collect_amp_reviews(
    app_id: str,
    country: str,
    max_reviews: int,
    watermark: datetime | None,
    sleep_seconds: float,
    seen: set[str],
) -> list[dict[str, Any]]:
    token = apple_web_token(country, app_id)
    if not token:
        logger.info("app_store AMP token missing; RSS-only")
        return []
    collected: list[dict[str, Any]] = []
    offset = 0
    delay = max(sleep_seconds, 0.0)
    while len(seen) < max_reviews:
        url = url_with_query(
            f"https://amp-api.apps.apple.com/v1/catalog/{country}/apps/{app_id}/reviews",
            {
                "l": "en-GB",
                "offset": offset,
                "limit": 20,
                "platform": "web",
                "additionalPlatforms": "appletv,ipad,iphone,mac",
            },
        )
        payload = get_json_soft(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "https://apps.apple.com",
                "Accept": "application/json",
            },
            retries=2,
            timeout=30,
        )
        if not isinstance(payload, dict):
            break
        rows = payload.get("data") or []
        if not rows:
            break
        stop = False
        for item in rows:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("id") or "")
            attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
            if not ident or ident in seen:
                continue
            published = parse_datetime(attrs.get("date"))
            if watermark is not None and published is not None and published <= watermark:
                stop = True
                continue
            seen.add(ident)
            collected.append(item)
            if len(seen) >= max_reviews:
                break
        next_url = (payload.get("next") or payload.get("links") or {})
        if isinstance(next_url, dict):
            next_url = next_url.get("next")
        offset += len(rows)
        if stop or not next_url:
            if not next_url and len(rows) < 10:
                break
        if delay:
            time.sleep(delay)
        if len(rows) < 10:
            break
    return collected


def amp_to_envelope(
    item: dict[str, Any],
    *,
    app_id: str,
    country: str,
    hmac_secret: str,
    ingest_run_id,
    fetched_at: datetime,
    now: datetime,
) -> RawEnvelope:
    ident = str(item.get("id") or "")
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    title = attrs.get("title")
    text = attrs.get("review") or attrs.get("body") or ""
    rating = attrs.get("rating")
    published = parse_datetime(attrs.get("date"))
    username = attrs.get("userName") or attrs.get("reviewerNickname")
    url = (
        f"https://apps.apple.com/{country}/app/myntra-fashion-shopping-app/"
        f"id{app_id}?see-all=reviews"
    )
    body_for_hash = "\n".join(p for p in ((str(title).strip() if title else ""), str(text).strip()) if p)
    return RawEnvelope(
        source_type=SourceType.app_store,
        source_id=ident,
        url=url,
        fetched_at=fetched_at,
        published_at=published,
        platform="ios",
        raw_text=str(text),
        raw_title=str(title) if title else None,
        star_rating=int(rating) if rating is not None and str(rating).isdigit() else (
            int(rating) if isinstance(rating, int) else None
        ),
        parent_context={"app_id": app_id, "country": country, "via": "amp"},
        author_hash=hash_author(username, hmac_secret),
        content_hash=content_hash(body_for_hash),
        ingest_run_id=ingest_run_id,
        date_anomaly=date_anomaly(published, now),
    )


def collect_reviews(
    fetch_page: FetchRssPage,
    app_id: str,
    country: str,
    max_reviews: int,
    watermark: datetime | None,
    sleep_seconds: float,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    delay = max(sleep_seconds, 0.0)
    for page in range(1, MAX_RSS_PAGES + 1):
        batch = fetch_page(app_id, country, page)
        if not batch:
            break
        stop = False
        for entry in batch:
            published = parse_datetime(_label(entry.get("updated")))
            if watermark is not None and published is not None and published <= watermark:
                stop = True
                break
            collected.append(entry)
            if len(collected) >= max_reviews:
                return collected
        if stop:
            break
        if delay:
            time.sleep(delay)
    return collected


def entry_to_envelope(
    entry: dict[str, Any],
    *,
    app_id: str,
    country: str,
    hmac_secret: str,
    ingest_run_id,
    fetched_at: datetime,
    now: datetime,
) -> RawEnvelope:
    source_id = review_source_id(entry)
    published = parse_datetime(_label(entry.get("updated")))
    author_node = entry.get("author") if isinstance(entry.get("author"), dict) else {}
    username = _label(author_node.get("name"))
    rating_raw = _label(entry.get("im:rating"))
    version = _label(entry.get("im:version"))
    title = _label(entry.get("title"))
    text = _label(entry.get("content")) or ""
    url = (
        f"https://apps.apple.com/{country}/app/myntra-fashion-shopping-app/"
        f"id{app_id}?see-all=reviews"
    )
    body_for_hash = "\n".join(p for p in ((title or "").strip(), text.strip()) if p)
    return RawEnvelope(
        source_type=SourceType.app_store,
        source_id=source_id,
        url=url,
        fetched_at=fetched_at,
        published_at=published,
        platform="ios",
        raw_text=text,
        raw_title=title,
        star_rating=int(rating_raw) if rating_raw and str(rating_raw).isdigit() else None,
        parent_context={"app_id": app_id, "app_version": version, "country": country},
        author_hash=hash_author(username, hmac_secret),
        content_hash=content_hash(body_for_hash),
        ingest_run_id=ingest_run_id,
        date_anomaly=date_anomaly(published, now),
    )


def run_app_store_ingest(
    repo: DocumentRepository,
    settings: Settings,
    *,
    fetch_page: FetchRssPage | None = None,
    object_store: LocalObjectStore | None = None,
    max_reviews: int | None = None,
    max_items: int | None = None,
    force_full: bool = False,
    env_enabled: bool | None = None,
    retries: int = 4,
) -> IngestRun:
    del retries
    fetch_page = fetch_page or default_fetch_page
    object_store = object_store or LocalObjectStore(settings.raw_store_path)
    run = begin_ingest_run(repo, APP_STORE_SOURCE)
    if run.status != "running":
        return run
    enabled_env = settings.app_store_enabled if env_enabled is None else env_enabled
    skipped = skip_if_disabled(
        repo,
        run,
        enabled_db=repo.is_enabled(APP_STORE_SOURCE),
        enabled_env=enabled_env,
        source_type=APP_STORE_SOURCE,
    )
    if skipped:
        return skipped

    require_myntra_app_store_id(settings.app_store_app_id)
    watermark = None if force_full else repo.get_watermark(APP_STORE_SOURCE)
    run.watermark_before = watermark
    limit = max_items if max_items is not None else max_reviews
    if limit is None:
        limit = settings.app_store_max_reviews

    try:
        reviews: list[dict[str, Any]] = []
        amp_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        if fetch_page is default_fetch_page:
            countries = [
                part.strip().lower()
                for part in (settings.app_store_countries or settings.app_store_country).split(",")
                if part.strip()
            ]
            home = (settings.app_store_country or "in").strip().lower()
            if home and home not in countries:
                countries.insert(0, home)
            delay = max(settings.app_store_page_sleep_seconds, 0.0)
            for country in countries:
                if len(reviews) >= limit:
                    break
                batch = collect_reviews(
                    fetch_page,
                    settings.app_store_app_id,
                    country,
                    limit - len(reviews),
                    watermark,
                    settings.app_store_page_sleep_seconds,
                )
                for entry in batch:
                    ident = review_source_id(entry)
                    if ident in seen_ids:
                        continue
                    seen_ids.add(ident)
                    entry["_store_country"] = country
                    reviews.append(entry)
                    if len(reviews) >= limit:
                        break
                for page in range(1, MAX_RSS_PAGES + 1):
                    if len(reviews) >= limit:
                        break
                    try:
                        helpful = default_fetch_page(
                            settings.app_store_app_id, country, page, sort="mosthelpful"
                        )
                    except ConnectorBlocked:
                        break
                    except Exception as exc:  # noqa: BLE001
                        logger.info("app_store helpful RSS %s page %s failed: %s", country, page, exc)
                        break
                    if not helpful:
                        break
                    for entry in helpful:
                        ident = review_source_id(entry)
                        if ident in seen_ids:
                            continue
                        seen_ids.add(ident)
                        entry["_store_country"] = country
                        reviews.append(entry)
                        if len(reviews) >= limit:
                            break
                    if delay:
                        time.sleep(delay)
                try:
                    amp_items.extend(
                        collect_amp_reviews(
                            settings.app_store_app_id,
                            country,
                            limit,
                            watermark,
                            settings.app_store_page_sleep_seconds,
                            seen_ids,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.info("app_store AMP skipped country=%s err=%s", country, exc)
        else:
            reviews = collect_reviews(
                fetch_page,
                settings.app_store_app_id,
                settings.app_store_country,
                limit,
                watermark,
                settings.app_store_page_sleep_seconds,
            )
    except ConnectorBlocked as exc:
        return fail_run(repo, run, exc, blocked=True)
    except Exception as exc:  # noqa: BLE001
        return fail_run(repo, run, exc)

    if not reviews and not amp_items:
        return succeed_run(repo, run, fetched=0, upserted=0, watermark=watermark)

    now = utcnow()
    fetched_at = utcnow()
    secret = settings.require_hmac_secret()
    items = [
        (
            entry_to_envelope(
                entry,
                app_id=settings.app_store_app_id,
                country=str(entry.get("_store_country") or settings.app_store_country),
                hmac_secret=secret,
                ingest_run_id=run.id,
                fetched_at=fetched_at,
                now=now,
            ),
            entry,
        )
        for entry in reviews
    ]
    items.extend(
        (
            amp_to_envelope(
                item,
                app_id=settings.app_store_app_id,
                country=settings.app_store_country,
                hmac_secret=secret,
                ingest_run_id=run.id,
                fetched_at=fetched_at,
                now=now,
            ),
            item,
        )
        for item in amp_items
        if item.get("id")
    )
    persisted = persist_envelopes(
        repo,
        object_store,
        APP_STORE_SOURCE,
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
            fetched=len(items),
            upserted=persisted.upserted,
            watermark=persisted.watermark_after,
            payload_warning=warning,
        )
    return succeed_run(
        repo,
        run,
        fetched=len(items),
        upserted=persisted.upserted,
        watermark=persisted.watermark_after,
        payload_warning=warning,
    )
