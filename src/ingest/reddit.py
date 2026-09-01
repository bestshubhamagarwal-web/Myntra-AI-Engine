"""Reddit connector: PRAW when credentials exist, else public JSON. Public only."""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.config import Settings
from src.db.repository import DocumentRepository, IngestRun
from src.ingest.allowlist import parse_subreddits
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
from src.ingest.http import DEFAULT_USER_AGENT, get_json_soft, get_text_soft, url_with_query
from src.ingest.object_store import LocalObjectStore
from src.ingest.queries import queries_for_source
from src.models.envelope import RawEnvelope, SourceType
from src.normalize.hashing import content_hash
from src.normalize.pii import hash_author
from src.timeutil import parse_datetime, utcnow

logger = logging.getLogger(__name__)

REDDIT_SOURCE = SourceType.reddit.value
MYNTRA_TOKEN = "myntra"
ARCTIC_SHIFT = "https://arctic-shift.photon-reddit.com"
PULLPUSH = "https://api.pullpush.io/reddit/search"
REDDIT_JSON_HOSTS = (
    "https://old.reddit.com",
    "https://www.reddit.com",
)
REDDIT_LINK_RE = re.compile(
    r"https?://(?:www\.|old\.)?reddit\.com/r/([^/]+)/comments/([a-z0-9]+)",
    re.IGNORECASE,
)

FetchReddit = Callable[[list[str], list[str], int, int, datetime | None], list[dict[str, Any]]]


def _mentions_myntra(*parts: str | None) -> bool:
    return any(MYNTRA_TOKEN in (part or "").lower() for part in parts)


def _permalink_url(permalink: str | None, fallback_id: str) -> str:
    if permalink:
        if permalink.startswith("http"):
            return permalink
        return "https://www.reddit.com" + permalink
    return f"https://www.reddit.com/{fallback_id}"


def item_to_envelope(
    item: dict[str, Any],
    *,
    hmac_secret: str,
    ingest_run_id,
    fetched_at: datetime,
    now: datetime,
) -> RawEnvelope:
    kind = item.get("kind") or "submission"
    source_id = str(item.get("id") or "")
    if not source_id:
        raise ValueError("Reddit item missing id")
    if not source_id.startswith(("t1_", "t3_")):
        source_id = f"{'t1' if kind == 'comment' else 't3'}_{source_id}"
    title = item.get("title")
    body = item.get("selftext") if kind != "comment" else item.get("body")
    if body is None:
        body = item.get("body") or item.get("selftext") or ""
    published = parse_datetime(item.get("created_utc") or item.get("created"))
    subreddit = str(item.get("subreddit") or "")
    thread_title = item.get("thread_title") or (title if kind == "submission" else None)
    parent_context = {
        "subreddit": subreddit,
        "thread_title": thread_title,
        "kind": kind,
        "query": item.get("query"),
    }
    url = _permalink_url(item.get("permalink"), source_id)
    username = item.get("author")
    if isinstance(username, dict):
        username = username.get("name")
    body_for_hash = "\n".join(
        p for p in ((str(title).strip() if title else ""), str(body).strip()) if p
    )
    return RawEnvelope(
        source_type=SourceType.reddit,
        source_id=source_id,
        url=url,
        fetched_at=fetched_at,
        published_at=published,
        platform="reddit",
        raw_text=str(body) if body is not None else "",
        raw_title=str(title) if title else thread_title,
        star_rating=None,
        parent_context=parent_context,
        author_hash=hash_author(username, hmac_secret),
        content_hash=content_hash(body_for_hash),
        ingest_run_id=ingest_run_id,
        date_anomaly=date_anomaly(published, now),
    )


def _listing_children(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or {}
    children = data.get("children") or []
    out = []
    for child in children:
        if not isinstance(child, dict):
            continue
        inner = child.get("data") if isinstance(child.get("data"), dict) else child
        kind = child.get("kind") or inner.get("kind")
        row = dict(inner)
        if kind == "t1":
            row["kind"] = "comment"
        elif kind == "t3":
            row["kind"] = "submission"
        out.append(row)
    return out


def _atom_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    if node.text:
        return node.text.strip()
    parts = [child.text or "" for child in list(node) if child.text]
    return " ".join(p.strip() for p in parts if p.strip())


def _rss_items(xml_text: str) -> list[dict[str, Any]]:
    if not xml_text or "<" not in xml_text:
        return []
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
        for child in list(item):
            ctag = child.tag.split("}")[-1].lower()
            text = _atom_text(child)
            if ctag == "title":
                title = text
            elif ctag in {"link", "id"}:
                href = child.attrib.get("href") or text
                if href:
                    link = href
            elif ctag in {"description", "content", "summary"}:
                body = text
            elif ctag in {"updated", "published", "date"}:
                created = text
        match = REDDIT_LINK_RE.search(link) if link else None
        if not match:
            continue
        subreddit, ident = match.group(1), match.group(2)
        rows.append(
            {
                "id": ident,
                "kind": "submission",
                "title": title,
                "selftext": body,
                "created_utc": created,
                "subreddit": subreddit,
                "permalink": link,
                "author": None,
            }
        )
    return rows


def _rss_search(query: str, subreddit: str | None, limit: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query, "sort": "new", "limit": min(limit, 100)}
    hosts = REDDIT_JSON_HOSTS
    rows: list[dict[str, Any]] = []
    for host in hosts:
        if subreddit:
            params["restrict_sr"] = "on"
            url = url_with_query(f"{host}/r/{subreddit}/search.rss", params)
        else:
            url = url_with_query(f"{host}/search.rss", params)
        xml_text = get_text_soft(
            url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/rss+xml"}
        )
        if not xml_text:
            continue
        rows = _rss_items(xml_text)
        if rows:
            break
    for row in rows:
        row["query"] = query
    return rows[:limit]


def _rss_comments(post_id: str, limit: int) -> list[dict[str, Any]]:
    bare = post_id.replace("t3_", "")
    for host in REDDIT_JSON_HOSTS:
        xml_text = get_text_soft(
            f"{host}/comments/{bare}.rss",
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/rss+xml"},
        )
        if not xml_text:
            continue
        rows = []
        for item in _rss_items(xml_text):
            item["kind"] = "comment"
            item["body"] = item.get("selftext") or ""
            rows.append(item)
        if rows:
            return rows[:limit]
    return []


def _listing_payload_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if payload.get("error"):
            return []
        rows = payload.get("data") or payload.get("posts") or payload.get("comments") or []
        if isinstance(rows, dict):
            rows = rows.get("children") or []
        return [row for row in rows if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _unix_ts(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return parsed.timestamp()


def _bare_reddit_id(value: Any) -> str:
    ident = str(value or "")
    if ident.startswith(("t1_", "t3_")):
        return ident.split("_", 1)[-1]
    return ident


def _map_archive_item(row: dict[str, Any], *, kind: str, query: str) -> dict[str, Any] | None:
    ident = _bare_reddit_id(row.get("id"))
    if not ident:
        return None
    permalink = row.get("permalink") or row.get("url")
    if kind == "comment":
        return {
            "id": ident,
            "kind": "comment",
            "body": row.get("body") or row.get("selftext") or "",
            "created_utc": row.get("created_utc"),
            "subreddit": row.get("subreddit") or "",
            "permalink": permalink,
            "author": row.get("author"),
            "query": query,
            "thread_title": row.get("link_title") or row.get("title") or "",
        }
    return {
        "id": ident,
        "kind": "submission",
        "title": row.get("title") or "",
        "selftext": row.get("selftext") or row.get("body") or "",
        "created_utc": row.get("created_utc"),
        "subreddit": row.get("subreddit") or "",
        "permalink": permalink,
        "author": row.get("author"),
        "query": query,
    }


def _archive_paginate(
    url: str,
    query: str,
    limit: int,
    *,
    kind: str,
    extra: dict[str, Any] | None = None,
    sleep_seconds: float = 1.2,
    text_param: str = "query",
) -> list[dict[str, Any]]:
    """Newest-first pages. Uses `before` = oldest created_utc."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    before: int | None = None
    arctic = "arctic-shift" in url
    while len(out) < limit:
        params: dict[str, Any] = {}
        if arctic:
            if text_param:
                params[text_param] = query
            params["limit"] = min(100, max(1, limit - len(out)))
            params["sort"] = "desc"
        else:
            params["q"] = query
            params["size"] = min(100, max(1, limit - len(out)))
            params["sort"] = "desc"
        if extra:
            params.update(extra)
        if before is not None:
            params["before"] = before
        payload = get_json_soft(url_with_query(url, params), retries=1, timeout=12)
        rows = _listing_payload_rows(payload)
        if not rows:
            break
        oldest: float | None = None
        added = 0
        for row in rows:
            mapped = _map_archive_item(row, kind=kind, query=query)
            if not mapped:
                continue
            ident = str(mapped.get("id") or "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            out.append(mapped)
            added += 1
            ts = _unix_ts(row.get("created_utc"))
            if ts is not None and (oldest is None or ts < oldest):
                oldest = ts
        if oldest is None or added == 0:
            break
        break
    return out[:limit]


def _arctic_search(query: str, subreddits: list[str], limit: int) -> list[dict[str, Any]]:
    token = (query or "myntra").replace(" vs ", " ").strip() or "myntra"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sub in list(dict.fromkeys(subreddits))[:12]:
        if len(out) >= limit:
            break
        batch = _archive_paginate(
            f"{ARCTIC_SHIFT}/api/posts/search",
            token,
            min(100, limit - len(out)),
            kind="submission",
            extra={"subreddit": sub},
            text_param="query",
        )
        for row in batch:
            ident = str(row.get("id") or "")
            if ident and ident not in seen:
                seen.add(ident)
                out.append(row)
        time.sleep(1.0)
    return out[:limit]


def _arctic_comments_query(
    query: str, limit: int, subreddits: list[str] | None = None
) -> list[dict[str, Any]]:
    token = (query or "myntra").replace(" vs ", " ").strip() or "myntra"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sub in list(dict.fromkeys(subreddits or []))[:12]:
        if len(out) >= limit:
            break
        batch = _archive_paginate(
            f"{ARCTIC_SHIFT}/api/comments/search",
            token,
            min(300, limit - len(out)),
            kind="comment",
            extra={"subreddit": sub},
            text_param="body",
        )
        for row in batch:
            ident = str(row.get("id") or "")
            if ident and ident not in seen:
                seen.add(ident)
                out.append(row)
        time.sleep(1.0)
    return out[:limit]


def _arctic_comments(post_id: str, limit: int) -> list[dict[str, Any]]:
    bare = _bare_reddit_id(post_id)
    return _archive_paginate(
        f"{ARCTIC_SHIFT}/api/comments/search",
        "myntra",
        limit,
        kind="comment",
        extra={"link_id": bare},
        text_param="",
    )


def _pullpush_search(kind: str, query: str, limit: int) -> list[dict[str, Any]]:
    del kind, query, limit
    return []


def _public_search(query: str, subreddit: str | None, limit: int) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    after: str | None = None
    remaining = max(limit, 1)
    while remaining > 0:
        batch_limit = min(100, remaining)
        params: dict[str, Any] = {
            "q": query,
            "sort": "new",
            "limit": batch_limit,
            "t": "year",
            "raw_json": 1,
        }
        if after:
            params["after"] = after
        payload = None
        for host in REDDIT_JSON_HOSTS:
            if subreddit:
                params["restrict_sr"] = 1
                url = url_with_query(f"{host}/r/{subreddit}/search.json", params)
            else:
                url = url_with_query(f"{host}/search.json", params)
            payload = get_json_soft(
                url, headers={"User-Agent": DEFAULT_USER_AGENT}, retries=2, timeout=30
            )
            if payload:
                break
        if not payload:
            break
        rows = _listing_children(payload)
        if not rows:
            break
        collected.extend(rows)
        remaining = limit - len(collected)
        after = None
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            after = data.get("after")
        if not after:
            break
    for row in collected:
        row["query"] = query
        row.setdefault("kind", "submission")
    return collected[:limit]


def _public_comments(post_id: str, limit: int) -> list[dict[str, Any]]:
    bare = post_id.replace("t3_", "")
    for host in REDDIT_JSON_HOSTS:
        url = url_with_query(
            f"{host}/comments/{bare}.json",
            {"limit": min(limit, 100), "depth": 2, "raw_json": 1},
        )
        payload = get_json_soft(
            url, headers={"User-Agent": DEFAULT_USER_AGENT}, retries=2, timeout=25
        )
        if isinstance(payload, list) and len(payload) >= 2:
            return _listing_children(payload[1])
    return _rss_comments(bare, limit)


def fetch_via_public_json(
    queries: list[str],
    subreddits: list[str],
    max_posts: int,
    max_comments_per_post: int,
    watermark: datetime | None,
    sleep_seconds: float = 0.8,
) -> list[dict[str, Any]]:
    """Archive search only. reddit.com JSON/RSS is 403/429 and is not called."""
    seen: set[str] = set()
    posts: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    total_cap = max(max_posts, 1)
    if max_comments_per_post > 0:
        post_cap = min(400, max(80, total_cap // 3))
        comment_cap = max(total_cap - post_cap, (total_cap * 2) // 3)
    else:
        post_cap = total_cap
        comment_cap = 0

    def _accept(row: dict[str, Any]) -> bool:
        ident = str(row.get("id") or "")
        if not ident or ident in seen:
            return False
        published = parse_datetime(row.get("created_utc") or row.get("created"))
        if watermark is not None and published is not None and published <= watermark:
            return False
        title = str(row.get("title") or row.get("thread_title") or "")
        body = str(row.get("selftext") or row.get("body") or "")
        if not _mentions_myntra(title, body):
            return False
        seen.add(ident)
        return True

    search_terms: list[str] = ["myntra"]
    preferred = (
        "IndianFashionAddicts",
        "IndianStreetwear",
        "IndiaSpeaks",
        "Frugal_Ind",
        "AskIndia",
        "IndianMakeupAddicts",
        "delhi",
        "bangalore",
        "Pune",
        "Hyderabad",
    )
    listed = list(dict.fromkeys(subreddits))
    targets = [name for name in preferred if name in listed] or listed[:8]

    for query in search_terms:
        if len(posts) >= post_cap:
            break
        try:
            extra = _arctic_search(query, targets, post_cap - len(posts))
        except Exception as exc:  # noqa: BLE001
            logger.info("reddit arctic posts failed q=%s err=%s", query, exc)
            extra = []
        for row in extra:
            if _accept(row):
                posts.append(row)
                if len(posts) >= post_cap:
                    break

    if len(posts) < post_cap:
        for sub in targets:
            if len(posts) >= post_cap:
                break
            try:
                extra = _archive_paginate(
                    f"{ARCTIC_SHIFT}/api/posts/search",
                    "myntra",
                    min(100, post_cap - len(posts)),
                    kind="submission",
                    extra={"subreddit": sub},
                    text_param="",
                )
            except Exception as err:  # noqa: BLE001
                logger.info("reddit recent posts failed sub=%s err=%s", sub, err)
                extra = []
            for row in extra:
                if _accept(row):
                    posts.append(row)
                    if len(posts) >= post_cap:
                        break
            time.sleep(0.8)

    per_post = max(max_comments_per_post, 25)
    for post in posts[:40]:
        if len(comments) >= comment_cap:
            break
        post_id = str(post.get("id") or "")
        if not post_id:
            continue
        try:
            extra = _arctic_comments(post_id, min(per_post, comment_cap - len(comments)))
        except Exception as err:  # noqa: BLE001
            logger.info("reddit arctic comments failed id=%s err=%s", post_id, err)
            extra = []
        thread_title = post.get("title") or ""
        for row in extra:
            row["thread_title"] = thread_title
            if _accept(row):
                comments.append(row)
                if len(comments) >= comment_cap:
                    break
        time.sleep(0.35)

    logger.info(
        "reddit archive fetch posts=%s comments=%s (reddit.com JSON not used)",
        len(posts),
        len(comments),
    )
    return posts + comments


def fetch_via_praw(
    queries: list[str],
    subreddits: list[str],
    max_posts: int,
    max_comments_per_post: int,
    watermark: datetime | None,
    *,
    client_id: str,
    client_secret: str,
    user_agent: str,
    sleep_seconds: float = 0.5,
) -> list[dict[str, Any]]:
    import praw

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )
    reddit.read_only = True
    posts: list[dict[str, Any]] = []
    seen: set[str] = set()
    delay = max(sleep_seconds, 0.0)

    def _submission_row(submission: Any, query: str) -> dict[str, Any] | None:
        ident = str(getattr(submission, "id", "") or "")
        if not ident or ident in seen:
            return None
        title = str(getattr(submission, "title", "") or "")
        body = str(getattr(submission, "selftext", "") or "")
        created = getattr(submission, "created_utc", None)
        published = parse_datetime(created)
        if watermark is not None and published is not None and published <= watermark:
            return None
        if not _mentions_myntra(title, body):
            return None
        seen.add(ident)
        author = getattr(submission, "author", None)
        author_name = str(author) if author is not None else None
        return {
            "id": ident,
            "kind": "submission",
            "title": title,
            "selftext": body,
            "created_utc": created,
            "subreddit": str(getattr(submission, "subreddit", "") or ""),
            "permalink": getattr(submission, "permalink", None),
            "author": author_name,
            "query": query,
            "thread_title": title,
        }

    searches: list[tuple[str, str]] = [("all", q) for q in queries]
    searches.extend((sub, "Myntra") for sub in subreddits)

    for sub_name, query in searches:
        if len(posts) >= max_posts:
            break
        remaining = max_posts - len(posts)
        try:
            listing = reddit.subreddit(sub_name).search(
                query, sort="new", time_filter="year", limit=remaining
            )
            for submission in listing:
                row = _submission_row(submission, query)
                if row:
                    posts.append(row)
                if len(posts) >= max_posts:
                    break
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if any(tok in text for tok in ("401", "403", "429", "forbidden")):
                raise ConnectorBlocked(str(exc)) from exc
            logger.info("praw search failed sub=%s q=%s err=%s", sub_name, query, exc)
        if delay:
            time.sleep(delay)

    items = list(posts)
    if max_comments_per_post <= 0:
        return items
    for post in posts:
        try:
            submission = reddit.submission(id=str(post["id"]))
            submission.comments.replace_more(limit=0)
            comments = submission.comments.list()[:max_comments_per_post]
        except Exception as exc:  # noqa: BLE001
            logger.info("praw comments failed id=%s err=%s", post.get("id"), exc)
            continue
        for comment in comments:
            ident = str(getattr(comment, "id", "") or "")
            if not ident or ident in seen:
                continue
            body = str(getattr(comment, "body", "") or "")
            thread_title = post.get("title")
            if not _mentions_myntra(thread_title, body):
                continue
            created = getattr(comment, "created_utc", None)
            published = parse_datetime(created)
            if watermark is not None and published is not None and published <= watermark:
                continue
            author = getattr(comment, "author", None)
            seen.add(ident)
            items.append(
                {
                    "id": ident,
                    "kind": "comment",
                    "body": body,
                    "created_utc": created,
                    "subreddit": post.get("subreddit"),
                    "permalink": getattr(comment, "permalink", None),
                    "author": str(author) if author is not None else None,
                    "query": post.get("query"),
                    "thread_title": thread_title,
                }
            )
        if delay:
            time.sleep(delay)
    return items


def default_fetch(
    queries: list[str],
    subreddits: list[str],
    max_posts: int,
    max_comments_per_post: int,
    watermark: datetime | None,
    settings: Settings,
) -> list[dict[str, Any]]:
    client_id = (settings.reddit_client_id or "").strip()
    client_secret = (settings.reddit_client_secret or "").strip()
    if client_id and client_secret:
        try:
            return fetch_via_praw(
                queries,
                subreddits,
                max_posts,
                max_comments_per_post,
                watermark,
                client_id=client_id,
                client_secret=client_secret,
                user_agent=settings.reddit_user_agent,
                sleep_seconds=settings.reddit_page_sleep_seconds,
            )
        except ImportError:
            logger.warning("praw not installed; falling back to public Reddit JSON")
    logger.info("reddit: using Arctic Shift + public JSON fallback")
    return fetch_via_public_json(
        queries,
        subreddits,
        max_posts,
        max_comments_per_post,
        watermark,
        sleep_seconds=settings.reddit_page_sleep_seconds,
    )


def run_reddit_ingest(
    repo: DocumentRepository,
    settings: Settings,
    *,
    fetch_items: FetchReddit | None = None,
    object_store: LocalObjectStore | None = None,
    max_reviews: int | None = None,
    max_items: int | None = None,
    force_full: bool = False,
    env_enabled: bool | None = None,
    retries: int = 4,
) -> IngestRun:
    del retries
    object_store = object_store or LocalObjectStore(settings.raw_store_path)
    run = begin_ingest_run(repo, REDDIT_SOURCE)
    if run.status != "running":
        return run
    enabled_env = settings.reddit_enabled if env_enabled is None else env_enabled
    skipped = skip_if_disabled(
        repo,
        run,
        enabled_db=repo.is_enabled(REDDIT_SOURCE),
        enabled_env=enabled_env,
        source_type=REDDIT_SOURCE,
    )
    if skipped:
        return skipped

    watermark = None if force_full else repo.get_watermark(REDDIT_SOURCE)
    run.watermark_before = watermark
    limit = max_items if max_items is not None else max_reviews
    if limit is None:
        limit = settings.reddit_max_posts
    queries = queries_for_source(repo, REDDIT_SOURCE)
    subreddits = parse_subreddits(settings.reddit_subreddits)

    try:
        if fetch_items is not None:
            rows = fetch_items(
                queries, subreddits, limit, settings.reddit_max_comments_per_post, watermark
            )
        else:
            rows = default_fetch(
                queries,
                subreddits,
                limit,
                settings.reddit_max_comments_per_post,
                watermark,
                settings,
            )
    except ConnectorBlocked as exc:
        return fail_run(repo, run, exc, blocked=True)
    except Exception as exc:  # noqa: BLE001
        return fail_run(repo, run, exc)

    if not rows:
        return succeed_run(repo, run, fetched=0, upserted=0, watermark=watermark)

    now = utcnow()
    fetched_at = utcnow()
    secret = settings.require_hmac_secret()
    items = []
    for row in rows:
        try:
            items.append(
                (
                    item_to_envelope(
                        row,
                        hmac_secret=secret,
                        ingest_run_id=run.id,
                        fetched_at=fetched_at,
                        now=now,
                    ),
                    row,
                )
            )
        except ValueError:
            continue
    persisted = persist_envelopes(
        repo,
        object_store,
        REDDIT_SOURCE,
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
