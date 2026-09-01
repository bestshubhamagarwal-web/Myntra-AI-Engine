"""X/Twitter — official API v2 when a bearer is set, else public RSS (Nitter/RSSHub)."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.db.repository import DocumentRepository, IngestRun
from src.ingest.common import (
    ConnectorBlocked,
    begin_ingest_run,
    date_anomaly,
    fail_run,
    payload_warning_text,
    persist_envelopes,
    skip_if_disabled,
    skip_unconfigured,
    succeed_run,
)
from src.ingest.http import DEFAULT_USER_AGENT, get_json, get_text_soft, url_with_query
from src.ingest.object_store import LocalObjectStore
from src.ingest.queries import queries_for_source
from src.models.envelope import RawEnvelope, SourceType
from src.normalize.hashing import content_hash
from src.normalize.pii import hash_author
from src.timeutil import parse_datetime, utcnow

logger = logging.getLogger(__name__)

X_SOURCE = SourceType.x.value
X_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
MYNTRA_TOKEN = "myntra"
STATUS_ID_RE = re.compile(r"(?:status|statuses)/(\d+)")
NITTER_RSS_HOSTS = (
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.cz",
)
RSSHUB_KEYWORD = "https://rsshub.app/twitter/keyword/{query}"

FetchX = Callable[[list[str], int, datetime | None], list[dict[str, Any]]]


class PublicSourceUnavailable(RuntimeError):
    """No public RSS host answered; treat as unconfigured, do not impute counts."""


def tweet_to_envelope(
    item: dict[str, Any],
    *,
    hmac_secret: str,
    ingest_run_id,
    fetched_at: datetime,
    now: datetime,
) -> RawEnvelope:
    source_id = str(item.get("id") or "")
    if not source_id:
        raise ValueError("X tweet missing id")
    text = item.get("text") or ""
    published = parse_datetime(item.get("created_at"))
    url = f"https://x.com/i/web/status/{source_id}"
    parent_context = {
        "query": item.get("query"),
        "conversation_id": item.get("conversation_id"),
    }
    return RawEnvelope(
        source_type=SourceType.x,
        source_id=source_id,
        url=url,
        fetched_at=fetched_at,
        published_at=published,
        platform="x",
        raw_text=str(text),
        raw_title=None,
        star_rating=None,
        parent_context=parent_context,
        author_hash=hash_author(item.get("author_id"), hmac_secret),
        content_hash=content_hash(str(text).strip()),
        ingest_run_id=ingest_run_id,
        date_anomaly=date_anomaly(published, now),
    )


def _strip_html(blob: str) -> str:
    return re.sub(r"<[^>]+>", " ", blob or "").replace("&amp;", "&").strip()


def parse_status_rss(xml_text: str, query: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    rows: list[dict[str, Any]] = []
    for item in root.iter():
        tag = item.tag.split("}")[-1].lower()
        if tag not in {"item", "entry"}:
            continue
        title = ""
        link = ""
        body = ""
        created = None
        author = None
        for child in list(item):
            ctag = child.tag.split("}")[-1].lower()
            text = (child.text or "").strip()
            if ctag == "title":
                title = _strip_html(text)
            elif ctag in {"link", "id", "guid"}:
                href = child.attrib.get("href") or text
                if href:
                    link = href
            elif ctag in {"description", "content", "summary"}:
                body = _strip_html("".join(child.itertext()))
            elif ctag in {"updated", "published", "pubdate", "date"}:
                created = text
            elif ctag in {"creator", "author"}:
                author = text
        match = STATUS_ID_RE.search(link or "")
        if not match:
            continue
        text_blob = body or title
        if MYNTRA_TOKEN not in f"{title} {body}".lower():
            continue
        rows.append(
            {
                "id": match.group(1),
                "text": text_blob,
                "created_at": created,
                "author_id": author,
                "conversation_id": match.group(1),
                "query": query,
            }
        )
    return rows


def public_fetch(
    queries: list[str],
    max_tweets: int,
    watermark: datetime | None,
    sleep_seconds: float,
) -> list[dict[str, Any]]:
    """Nitter / RSSHub public RSS when X_BEARER_TOKEN is empty."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    delay = max(sleep_seconds, 0.0)
    answered = False
    for query in queries:
        if len(out) >= max_tweets:
            break
        urls = [
            url_with_query(f"{host}/search/rss", {"f": "tweets", "q": query})
            for host in NITTER_RSS_HOSTS
        ]
        urls.append(RSSHUB_KEYWORD.format(query=query.replace(" ", "%20")))
        for url in urls:
            xml_text = get_text_soft(
                url,
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/rss+xml, application/xml"},
                retries=1,
                timeout=20,
            )
            if not xml_text:
                continue
            answered = True
            for row in parse_status_rss(xml_text, query):
                ident = str(row.get("id") or "")
                if not ident or ident in seen:
                    continue
                published = parse_datetime(row.get("created_at"))
                if watermark is not None and published is not None and published <= watermark:
                    continue
                blob = str(row.get("text") or "")
                if MYNTRA_TOKEN not in blob.lower():
                    continue
                seen.add(ident)
                out.append(row)
                if len(out) >= max_tweets:
                    break
            if out:
                break
        if delay:
            time.sleep(delay)
    if not out and not answered:
        raise PublicSourceUnavailable(
            "X public RSS hosts did not respond and bearer token is missing; "
            "source unavailable — no metrics imputed"
        )
    return out


def search_recent(
    bearer: str,
    query: str,
    max_results: int,
    watermark: datetime | None,
    sleep_seconds: float,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    next_token: str | None = None
    delay = max(sleep_seconds, 0.0)
    start_time = None
    if watermark is not None:
        start_time = watermark.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Recent search is AND of Myntra-relevant language; never a competitor-app crawl.
    api_query = f"({query}) Myntra -is:retweet"
    while len(collected) < max_results:
        params: dict[str, Any] = {
            "query": api_query,
            "max_results": min(100, max(10, max_results - len(collected))),
            "tweet.fields": "created_at,author_id,conversation_id",
        }
        if start_time:
            params["start_time"] = start_time
        if next_token:
            params["next_token"] = next_token
        url = url_with_query(X_SEARCH_URL, params)
        payload = get_json(
            url,
            headers={"Authorization": f"Bearer {bearer}"},
            retries=3,
            timeout=30,
        )
        for tweet in payload.get("data") or []:
            tweet = dict(tweet)
            tweet["query"] = query
            published = parse_datetime(tweet.get("created_at"))
            if watermark is not None and published is not None and published <= watermark:
                return collected
            collected.append(tweet)
            if len(collected) >= max_results:
                break
        meta = payload.get("meta") or {}
        next_token = meta.get("next_token")
        if not next_token:
            break
        if delay:
            time.sleep(delay)
    return collected


def default_fetch(
    queries: list[str],
    max_tweets: int,
    watermark: datetime | None,
    bearer: str,
    sleep_seconds: float,
) -> list[dict[str, Any]]:
    per_query = max(10, max_tweets // max(len(queries), 1))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for query in queries:
        if len(out) >= max_tweets:
            break
        try:
            rows = search_recent(bearer, query, per_query, watermark, sleep_seconds)
        except ConnectorBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.info("x search failed q=%s err=%s", query, exc)
            continue
        for row in rows:
            ident = str(row.get("id") or "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            out.append(row)
            if len(out) >= max_tweets:
                break
    return out


def run_x_ingest(
    repo: DocumentRepository,
    settings: Settings,
    *,
    fetch_items: FetchX | None = None,
    object_store: LocalObjectStore | None = None,
    max_reviews: int | None = None,
    max_items: int | None = None,
    force_full: bool = False,
    env_enabled: bool | None = None,
    retries: int = 4,
) -> IngestRun:
    del retries
    object_store = object_store or LocalObjectStore(settings.raw_store_path)
    run = begin_ingest_run(repo, X_SOURCE)
    if run.status != "running":
        return run
    enabled_env = settings.x_enabled if env_enabled is None else env_enabled
    skipped = skip_if_disabled(
        repo,
        run,
        enabled_db=repo.is_enabled(X_SOURCE),
        enabled_env=enabled_env,
        source_type=X_SOURCE,
    )
    if skipped:
        return skipped

    bearer = (settings.x_bearer_token or "").strip()
    watermark = None if force_full else repo.get_watermark(X_SOURCE)
    run.watermark_before = watermark
    limit = max_items if max_items is not None else max_reviews
    if limit is None:
        limit = settings.x_max_tweets
    queries = queries_for_source(repo, X_SOURCE)

    try:
        if fetch_items is not None:
            rows = fetch_items(queries, limit, watermark)
        elif bearer:
            rows = default_fetch(
                queries,
                limit,
                watermark,
                bearer,
                settings.x_page_sleep_seconds,
            )
        else:
            logger.info("x: no bearer token; using public Nitter/RSSHub RSS")
            rows = public_fetch(
                queries,
                limit,
                watermark,
                settings.x_page_sleep_seconds,
            )
    except PublicSourceUnavailable as exc:
        return skip_unconfigured(repo, run, str(exc))
    except ConnectorBlocked as exc:
        return fail_run(repo, run, exc, blocked=True)
    except Exception as exc:  # noqa: BLE001
        return fail_run(repo, run, exc)

    if not rows:
        return succeed_run(repo, run, fetched=0, upserted=0, watermark=watermark)

    now = utcnow()
    fetched_at = utcnow()
    secret = settings.require_hmac_secret()
    items = [
        (
            tweet_to_envelope(
                row,
                hmac_secret=secret,
                ingest_run_id=run.id,
                fetched_at=fetched_at,
                now=now,
            ),
            row,
        )
        for row in rows
    ]
    persisted = persist_envelopes(repo, object_store, X_SOURCE, items, watermark)
    warning = payload_warning_text(persisted.payload_warnings)
    if persisted.error:
        return fail_run(
            repo,
            run,
            persisted.error,
            fetched=len(rows),
            upserted=persisted.upserted,
            watermark=persisted.watermark_after,
            payload_warning=warning,
        )
    return succeed_run(
        repo,
        run,
        fetched=len(rows),
        upserted=persisted.upserted,
        watermark=persisted.watermark_after,
        payload_warning=warning,
    )
