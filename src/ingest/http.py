"""JSON HTTP with retries. 401/403/429 become ConnectorBlocked (do not scrape around)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from src.ingest.common import ConnectorBlocked, is_block_error

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "myntra-discovery-engine/0.1 (research prototype; public read-only)"


class HttpStatusError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = status


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    retries: int = 4,
    timeout: int = 30,
    sleep_seconds: float = 0.0,
) -> Any:
    last_error: BaseException | None = None
    req_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=req_headers, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            if sleep_seconds:
                time.sleep(sleep_seconds)
            if not raw.strip():
                return {}
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            last_error = HttpStatusError(exc.code, f"HTTP {exc.code} {exc.reason}")
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001
                body = ""
            if body:
                last_error = HttpStatusError(exc.code, f"HTTP {exc.code} {exc.reason}: {body}")
            if exc.code in {401, 403, 429, 503} or is_block_error(last_error):
                if attempt < retries - 1 and exc.code in {429, 503}:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise ConnectorBlocked(str(last_error)) from exc
            if attempt < retries - 1 and exc.code >= 500:
                time.sleep(min(2**attempt, 30))
                continue
            raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(min(2**attempt, 30))
                continue
            raise
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON from {url}: {exc}") from exc
    if last_error:
        raise last_error
    raise RuntimeError(f"empty response from {url}")


def get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    retries: int = 4,
    timeout: int = 30,
) -> str:
    last_error: BaseException | None = None
    req_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=req_headers, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = HttpStatusError(exc.code, f"HTTP {exc.code} {exc.reason}")
            if exc.code in {401, 403, 429, 503} or is_block_error(last_error):
                if attempt < retries - 1 and exc.code in {429, 503}:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise ConnectorBlocked(str(last_error)) from exc
            if attempt < retries - 1 and exc.code >= 500:
                time.sleep(min(2**attempt, 30))
                continue
            raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(min(2**attempt, 30))
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError(f"empty response from {url}")


def get_json_soft(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    retries: int = 2,
    timeout: int = 30,
) -> Any | None:
    """JSON GET that returns None on block/HTTP errors instead of failing the source."""
    try:
        return get_json(url, headers=headers, retries=retries, timeout=timeout)
    except (ConnectorBlocked, HttpStatusError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
        logger.info("soft GET failed url=%s err=%s", url, exc)
        return None


def get_text_soft(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    retries: int = 2,
    timeout: int = 30,
) -> str | None:
    try:
        return get_text(url, headers=headers, retries=retries, timeout=timeout)
    except (ConnectorBlocked, HttpStatusError, urllib.error.URLError, RuntimeError) as exc:
        logger.info("soft text GET failed url=%s err=%s", url, exc)
        return None


def post_json(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    retries: int = 4,
    timeout: int = 30,
) -> Any:
    last_error: BaseException | None = None
    req_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)
    payload = json.dumps(body).encode("utf-8")
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, data=payload, headers=req_headers, method="POST")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            if not raw.strip():
                return {}
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            last_error = HttpStatusError(exc.code, f"HTTP {exc.code} {exc.reason}")
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001
                body_text = ""
            if body_text:
                last_error = HttpStatusError(exc.code, f"HTTP {exc.code} {exc.reason}: {body_text}")
            if exc.code in {401, 403, 429, 503} or is_block_error(last_error):
                if attempt < retries - 1 and exc.code in {429, 503}:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise ConnectorBlocked(str(last_error)) from exc
            if attempt < retries - 1 and exc.code >= 500:
                time.sleep(min(2**attempt, 30))
                continue
            raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(min(2**attempt, 30))
                continue
            raise
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON from {url}: {exc}") from exc
    if last_error:
        raise last_error
    raise RuntimeError(f"empty response from {url}")


def post_json_soft(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    retries: int = 2,
    timeout: int = 30,
) -> Any | None:
    try:
        return post_json(url, body, headers=headers, retries=retries, timeout=timeout)
    except (ConnectorBlocked, HttpStatusError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
        logger.info("soft POST failed url=%s err=%s", url, exc)
        return None


def url_with_query(base: str, params: dict[str, Any]) -> str:
    filtered = {k: v for k, v in params.items() if v is not None and v != ""}
    query = urllib.parse.urlencode(filtered, doseq=True)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{query}" if query else base
