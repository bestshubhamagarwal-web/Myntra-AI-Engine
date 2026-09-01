"""YouTube Data API: search Myntra videos, then pull comments. Title goes in parent_context."""

from __future__ import annotations

import logging
import time
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
from src.ingest.http import get_json, get_json_soft, post_json_soft, url_with_query
from src.ingest.object_store import LocalObjectStore
from src.ingest.queries import queries_for_source
from src.models.envelope import RawEnvelope, SourceType
from src.normalize.hashing import content_hash
from src.normalize.pii import hash_author
from src.timeutil import parse_datetime, utcnow

logger = logging.getLogger(__name__)

YOUTUBE_SOURCE = SourceType.youtube.value
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
MYNTRA_TOKEN = "myntra"

# Public web client key shipped in youtube.com (same pattern as Reddit public JSON).
INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
INNERTUBE_CONTEXT = {
    "client": {
        "hl": "en",
        "gl": "IN",
        "clientName": "WEB",
        "clientVersion": "2.20240101.00.00",
    }
}

# Third-party public YouTube APIs (Arctic Shift analog). Tried in order.
INVIDIOUS_HOSTS = (
    "https://inv.nadeko.net",
    "https://yewtu.be",
    "https://invidious.nerdvpn.de",
    "https://invidious.fdn.fr",
    "https://iv.ggtyler.dev",
)
PIPED_HOSTS = (
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.reallyaweso.me",
)

FetchYoutube = Callable[[list[str], int, int, datetime | None], list[dict[str, Any]]]


class PublicSourceUnavailable(RuntimeError):
    """No public host answered; treat as unconfigured, do not impute counts."""


def video_mentions_myntra(title: str | None, description: str | None) -> bool:
    blob = f"{title or ''} {description or ''}".lower()
    return MYNTRA_TOKEN in blob


def comment_to_envelope(
    item: dict[str, Any],
    *,
    hmac_secret: str,
    ingest_run_id,
    fetched_at: datetime,
    now: datetime,
) -> RawEnvelope:
    source_id = str(item.get("id") or "")
    if not source_id:
        raise ValueError("YouTube comment missing id")
    video_id = str(item.get("video_id") or "")
    video_title = item.get("video_title")
    text = item.get("text") or item.get("textOriginal") or ""
    published = parse_datetime(item.get("published_at") or item.get("publishedAt"))
    url = f"https://www.youtube.com/watch?v={video_id}&lc={source_id}" if video_id else None
    parent_context = {
        "video_id": video_id,
        "video_title": video_title,
        "channel_title": item.get("channel_title"),
        "query": item.get("query"),
    }
    return RawEnvelope(
        source_type=SourceType.youtube,
        source_id=source_id,
        url=url,
        fetched_at=fetched_at,
        published_at=published,
        platform="youtube",
        raw_text=str(text),
        raw_title=video_title,
        star_rating=None,
        parent_context=parent_context,
        author_hash=hash_author(item.get("author"), hmac_secret),
        content_hash=content_hash(str(text).strip()),
        ingest_run_id=ingest_run_id,
        date_anomaly=date_anomaly(published, now),
    )


def search_videos(api_key: str, query: str, max_results: int) -> list[dict[str, Any]]:
    url = url_with_query(
        f"{YOUTUBE_API}/search",
        {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(max_results, 25),
            "order": "date",
            "key": api_key,
        },
    )
    payload = get_json(url, retries=3, timeout=30)
    out: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        ident = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not ident:
            continue
        title = snippet.get("title")
        description = snippet.get("description")
        if not video_mentions_myntra(title, description):
            continue
        out.append(
            {
                "video_id": ident,
                "video_title": title,
                "channel_title": snippet.get("channelTitle"),
                "description": description,
                "query": query,
            }
        )
    return out


def list_comments(
    api_key: str,
    video: dict[str, Any],
    max_comments: int,
    watermark: datetime | None,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    page_token: str | None = None
    video_id = video["video_id"]
    while len(collected) < max_comments:
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": min(100, max_comments - len(collected)),
            "order": "time",
            "textFormat": "plainText",
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token
        url = url_with_query(f"{YOUTUBE_API}/commentThreads", params)
        try:
            payload = get_json(url, retries=3, timeout=30)
        except ConnectorBlocked:
            raise
        except Exception as exc:  # noqa: BLE001 — commentsDisabled is not a block
            logger.info("youtube comments skipped video=%s err=%s", video_id, exc)
            break
        stop = False
        for item in payload.get("items") or []:
            snippet = ((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
            comment_id = (item.get("snippet") or {}).get("topLevelComment", {}).get("id") or item.get(
                "id"
            )
            published = parse_datetime(snippet.get("publishedAt"))
            if watermark is not None and published is not None and published <= watermark:
                stop = True
                break
            collected.append(
                {
                    "id": comment_id,
                    "video_id": video_id,
                    "video_title": video.get("video_title"),
                    "channel_title": video.get("channel_title"),
                    "query": video.get("query"),
                    "text": snippet.get("textOriginal") or snippet.get("textDisplay") or "",
                    "published_at": snippet.get("publishedAt"),
                    "author": snippet.get("authorDisplayName"),
                }
            )
            if len(collected) >= max_comments:
                break
        if stop:
            break
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    return collected


def _runs_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if "simpleText" in node:
            return str(node.get("simpleText") or "")
        runs = node.get("runs")
        if isinstance(runs, list):
            return "".join(str(part.get("text") or "") for part in runs if isinstance(part, dict))
    return ""


def _unix_or_iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    return str(value)


def parse_invidious_search(payload: Any) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else (payload.get("videos") if isinstance(payload, dict) else None)
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "video")
        if kind not in {"video", "shortVideo"}:
            continue
        video_id = item.get("videoId") or item.get("videoID")
        title = item.get("title")
        if not video_id:
            continue
        out.append(
            {
                "video_id": str(video_id),
                "video_title": title,
                "channel_title": item.get("author") or item.get("authorId"),
                "description": item.get("description") or "",
            }
        )
    return out


def parse_piped_search(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("items") or payload.get("content") or []
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("itemType") or "")
        if kind and kind.lower() not in {"stream", "video"}:
            continue
        url = str(item.get("url") or item.get("id") or "")
        video_id = item.get("id")
        if not video_id and "watch?v=" in url:
            video_id = url.split("watch?v=", 1)[-1].split("&", 1)[0]
        title = item.get("title")
        if not video_id:
            continue
        out.append(
            {
                "video_id": str(video_id),
                "video_title": title,
                "channel_title": item.get("uploaderName") or item.get("uploader"),
                "description": item.get("shortDescription") or item.get("description") or "",
            }
        )
    return out


def parse_invidious_comments(payload: Any, video: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    comments = payload.get("comments") or []
    if not isinstance(comments, list):
        return []
    out: list[dict[str, Any]] = []
    for item in comments:
        if not isinstance(item, dict):
            continue
        comment_id = item.get("commentId") or item.get("commentID") or item.get("id")
        text = item.get("content") or item.get("text") or ""
        if not comment_id:
            continue
        out.append(
            {
                "id": str(comment_id),
                "video_id": video["video_id"],
                "video_title": video.get("video_title"),
                "channel_title": video.get("channel_title"),
                "query": video.get("query"),
                "text": text,
                "published_at": _unix_or_iso(item.get("published") or item.get("publishedText")),
                "author": item.get("author"),
            }
        )
    return out


def _walk_innertube_videos(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        renderer = node.get("videoRenderer") or node.get("compactVideoRenderer")
        if isinstance(renderer, dict) and renderer.get("videoId"):
            out.append(
                {
                    "video_id": str(renderer["videoId"]),
                    "video_title": _runs_text(renderer.get("title")),
                    "channel_title": _runs_text(
                        ((renderer.get("ownerText") or renderer.get("shortBylineText")) or {})
                    ),
                    "description": _runs_text(
                        renderer.get("descriptionSnippet") or renderer.get("detailedMetadataSnippets")
                    ),
                }
            )
        for value in node.values():
            _walk_innertube_videos(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_innertube_videos(item, out)


def parse_innertube_search(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    _walk_innertube_videos(payload, found)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in found:
        vid = row["video_id"]
        if vid in seen:
            continue
        seen.add(vid)
        unique.append(row)
    return unique


def _walk_innertube_comments(node: Any, video: dict[str, Any], out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        renderer = node.get("commentRenderer") or node.get("commentSimpleboxRenderer")
        if isinstance(renderer, dict) and (renderer.get("commentId") or renderer.get("id")):
            comment_id = renderer.get("commentId") or renderer.get("id")
            out.append(
                {
                    "id": str(comment_id),
                    "video_id": video["video_id"],
                    "video_title": video.get("video_title"),
                    "channel_title": video.get("channel_title"),
                    "query": video.get("query"),
                    "text": _runs_text(renderer.get("contentText")),
                    "published_at": _runs_text(renderer.get("publishedTimeText")),
                    "author": _runs_text(renderer.get("authorText")),
                }
            )
        for value in node.values():
            _walk_innertube_comments(value, video, out)
    elif isinstance(node, list):
        for item in node:
            _walk_innertube_comments(item, video, out)


def parse_innertube_comments(payload: Any, video: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    _walk_innertube_comments(payload, video, found)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in found:
        ident = row["id"]
        if ident in seen:
            continue
        seen.add(ident)
        unique.append(row)
    return unique


def _collect_continuation_tokens(node: Any, tokens: list[str]) -> None:
    if isinstance(node, dict):
        token = None
        endpoint = node.get("continuationEndpoint") or node.get("continuationCommand")
        if isinstance(endpoint, dict):
            token = (endpoint.get("continuationCommand") or {}).get("token") or endpoint.get("token")
        if not token and isinstance(node.get("nextContinuationData"), dict):
            token = node["nextContinuationData"].get("continuation")
        if token:
            tokens.append(str(token))
        for value in node.values():
            _collect_continuation_tokens(value, tokens)
    elif isinstance(node, list):
        for item in node:
            _collect_continuation_tokens(item, tokens)


def innertube_search(query: str, max_results: int) -> list[dict[str, Any]] | None:
    url = url_with_query(
        "https://www.youtube.com/youtubei/v1/search",
        {"key": INNERTUBE_KEY, "prettyPrint": "false"},
    )
    payload = post_json_soft(
        url,
        {"context": INNERTUBE_CONTEXT, "query": query},
        retries=2,
        timeout=20,
    )
    if payload is None:
        return None
    rows = parse_innertube_search(payload)
    return rows[:max_results]


def innertube_comments(video: dict[str, Any], max_comments: int) -> list[dict[str, Any]]:
    url = url_with_query(
        "https://www.youtube.com/youtubei/v1/next",
        {"key": INNERTUBE_KEY, "prettyPrint": "false"},
    )
    collected: list[dict[str, Any]] = []
    payload = post_json_soft(
        url,
        {"context": INNERTUBE_CONTEXT, "videoId": video["video_id"]},
        retries=2,
        timeout=30,
    )
    if payload is None:
        return []
    collected.extend(parse_innertube_comments(payload, video))
    tokens: list[str] = []
    _collect_continuation_tokens(payload, tokens)
    seen_tokens: set[str] = set()
    for token in tokens:
        if len(collected) >= max_comments:
            break
        if token in seen_tokens:
            continue
        seen_tokens.add(token)
        page = post_json_soft(
            url,
            {"context": INNERTUBE_CONTEXT, "continuation": token},
            retries=1,
            timeout=30,
        )
        if page is None:
            continue
        collected.extend(parse_innertube_comments(page, video))
    return collected[:max_comments]


def _public_search_videos(query: str, max_results: int) -> tuple[list[dict[str, Any]], bool]:
    """Return (videos, host_answered). host_answered is True if any public API returned JSON."""
    innertube_rows = innertube_search(query, max_results)
    if innertube_rows:
        return innertube_rows, True
    answered = innertube_rows is not None
    for host in INVIDIOUS_HOSTS:
        payload = get_json_soft(
            url_with_query(f"{host}/api/v1/search", {"q": query, "type": "video"}),
            retries=1,
            timeout=8,
        )
        if payload is None:
            continue
        answered = True
        rows = parse_invidious_search(payload)
        if rows:
            return rows[:max_results], True
    for host in PIPED_HOSTS:
        payload = get_json_soft(
            url_with_query(f"{host}/search", {"q": query, "filter": "videos"}),
            retries=1,
            timeout=8,
        )
        if payload is None:
            continue
        answered = True
        rows = parse_piped_search(payload)
        if rows:
            return rows[:max_results], True
    return [], answered


def _public_comments(video: dict[str, Any], max_comments: int) -> list[dict[str, Any]]:
    video_id = video["video_id"]
    for host in INVIDIOUS_HOSTS:
        payload = get_json_soft(f"{host}/api/v1/comments/{video_id}", retries=1, timeout=8)
        if payload is None:
            continue
        rows = parse_invidious_comments(payload, video)
        if rows:
            return rows[:max_comments]
    for host in PIPED_HOSTS:
        payload = get_json_soft(f"{host}/comments/{video_id}", retries=1, timeout=8)
        if payload is None:
            continue
        comments = []
        if isinstance(payload, dict):
            comments = payload.get("comments") or payload.get("items") or []
        if isinstance(comments, list) and comments:
            shaped = {"comments": []}
            for item in comments:
                if not isinstance(item, dict):
                    continue
                shaped["comments"].append(
                    {
                        "commentId": item.get("commentId") or item.get("id"),
                        "content": item.get("comment") or item.get("content") or item.get("text"),
                        "published": item.get("commentedTime") or item.get("published"),
                        "author": item.get("author") or item.get("uploaderName"),
                    }
                )
            rows = parse_invidious_comments(shaped, video)
            if rows:
                return rows[:max_comments]
    return innertube_comments(video, max_comments)


def public_fetch(
    queries: list[str],
    max_videos: int,
    max_comments_per_video: int,
    watermark: datetime | None,
    sleep_seconds: float,
) -> list[dict[str, Any]]:
    """Invidious / Piped / Innertube when YOUTUBE_API_KEY is empty (Reddit public-JSON analog)."""
    videos: list[dict[str, Any]] = []
    seen_videos: set[str] = set()
    delay = max(sleep_seconds, 0.0)
    per_query = max(1, max_videos // max(len(queries), 1))
    any_host = False
    for query in queries:
        if len(videos) >= max_videos:
            break
        found, answered = _public_search_videos(query, per_query)
        any_host = any_host or answered or bool(found)
        for video in found:
            if not video_mentions_myntra(video.get("video_title"), video.get("description")):
                continue
            vid = video["video_id"]
            if vid in seen_videos:
                continue
            seen_videos.add(vid)
            video["query"] = query
            videos.append(video)
            if len(videos) >= max_videos:
                break
        if delay:
            time.sleep(delay)

    if not videos and not any_host:
        raise PublicSourceUnavailable(
            "youtube public hosts did not respond and Data API key is missing; "
            "source unavailable — no metrics imputed"
        )

    comments: list[dict[str, Any]] = []
    for video in videos:
        rows = _public_comments(video, max_comments_per_video)
        for row in rows:
            published = parse_datetime(row.get("published_at"))
            if watermark is not None and published is not None and published <= watermark:
                continue
            comments.append(row)
        if delay:
            time.sleep(delay)
    return comments


def default_fetch(
    queries: list[str],
    max_videos: int,
    max_comments_per_video: int,
    watermark: datetime | None,
    api_key: str,
    sleep_seconds: float,
) -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    seen_videos: set[str] = set()
    delay = max(sleep_seconds, 0.0)
    per_query = max(1, max_videos // max(len(queries), 1))
    for query in queries:
        if len(videos) >= max_videos:
            break
        try:
            found = search_videos(api_key, query, per_query)
        except ConnectorBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.info("youtube search failed q=%s err=%s", query, exc)
            continue
        for video in found:
            vid = video["video_id"]
            if vid in seen_videos:
                continue
            seen_videos.add(vid)
            videos.append(video)
            if len(videos) >= max_videos:
                break
        if delay:
            time.sleep(delay)

    comments: list[dict[str, Any]] = []
    for video in videos:
        comments.extend(
            list_comments(api_key, video, max_comments_per_video, watermark)
        )
        if delay:
            time.sleep(delay)
    return comments


def run_youtube_ingest(
    repo: DocumentRepository,
    settings: Settings,
    *,
    fetch_items: FetchYoutube | None = None,
    object_store: LocalObjectStore | None = None,
    max_reviews: int | None = None,
    max_items: int | None = None,
    force_full: bool = False,
    env_enabled: bool | None = None,
    retries: int = 4,
) -> IngestRun:
    del retries
    object_store = object_store or LocalObjectStore(settings.raw_store_path)
    run = begin_ingest_run(repo, YOUTUBE_SOURCE)
    if run.status != "running":
        return run
    enabled_env = settings.youtube_enabled if env_enabled is None else env_enabled
    skipped = skip_if_disabled(
        repo,
        run,
        enabled_db=repo.is_enabled(YOUTUBE_SOURCE),
        enabled_env=enabled_env,
        source_type=YOUTUBE_SOURCE,
    )
    if skipped:
        return skipped

    api_key = (settings.youtube_api_key or "").strip()
    watermark = None if force_full else repo.get_watermark(YOUTUBE_SOURCE)
    run.watermark_before = watermark
    limit = max_items if max_items is not None else max_reviews
    video_cap = settings.youtube_max_videos
    comment_cap = settings.youtube_max_comments_per_video
    if limit is not None:
        comment_cap = min(comment_cap, max(1, limit))
        video_cap = min(40, max(video_cap, (limit + max(comment_cap, 1) - 1) // max(comment_cap, 1)))
    queries = queries_for_source(repo, YOUTUBE_SOURCE)

    try:
        if fetch_items is not None:
            rows = fetch_items(queries, video_cap, comment_cap, watermark)
        elif api_key:
            rows = default_fetch(
                queries,
                video_cap,
                comment_cap,
                watermark,
                api_key,
                settings.youtube_page_sleep_seconds,
            )
        else:
            logger.info("youtube: no Data API key; using public Invidious/Piped/Innertube")
            rows = public_fetch(
                queries,
                video_cap,
                comment_cap,
                watermark,
                settings.youtube_page_sleep_seconds,
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
            comment_to_envelope(
                row,
                hmac_secret=secret,
                ingest_run_id=run.id,
                fetched_at=fetched_at,
                now=now,
            ),
            row,
        )
        for row in rows
        if row.get("id")
    ]
    persisted = persist_envelopes(
        repo,
        object_store,
        YOUTUBE_SOURCE,
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
