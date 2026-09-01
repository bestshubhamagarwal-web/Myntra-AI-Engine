"""Groq-only generation client (Architecture §5.1).

Uses the OpenAI Python SDK pointed at Groq. Never call the OpenAI API host
for chat or embeddings. Vectors are local BGE only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from src.config import GROQ_DEFAULT_BASE_URL, Settings, load_settings


class GroqConfigError(RuntimeError):
    """Missing key or wrong host — fail loudly, do not silently switch providers."""


class GroqAuthError(RuntimeError):
    """401 / bad key. Abort the job; do not fake extractions."""


class GroqRateLimitError(RuntimeError):
    """429 / TPM. Caller must backoff; never switch LLM host."""


class GroqRetryableError(RuntimeError):
    """Timeouts and 5xx. Retry, then mark the document failed."""


def build_groq_client(settings: Settings | None = None):
    """Return an OpenAI SDK client bound to Groq."""
    cfg = settings or load_settings()
    key = (cfg.groq_api_key or "").strip()
    if not key:
        raise GroqConfigError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add a Groq key. "
            "Do not set an OpenAI chat/embed key."
        )
    base = (cfg.groq_base_url or "").strip().rstrip("/")
    if "api.openai.com" in base:
        raise GroqConfigError("GROQ_BASE_URL must be Groq, not the OpenAI API host")
    if "groq.com" not in base:
        raise GroqConfigError(f"GROQ_BASE_URL must point at Groq, got {base!r}")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise GroqConfigError(
            "The openai package is required as a Groq-compatible client. "
            "Install project dependencies with pip install -e ."
        ) from exc

    return OpenAI(api_key=key, base_url=base or GROQ_DEFAULT_BASE_URL)


def ping_groq(settings: Settings | None = None) -> dict[str, Any]:
    """EV-0-07: models.list, or a 1-token chat if listing is unavailable."""
    cfg = settings or load_settings()
    client = build_groq_client(cfg)
    try:
        listed = client.models.list()
        model_ids = [item.id for item in listed.data]
        return {
            "ok": True,
            "method": "models.list",
            "model_count": len(model_ids),
            "base_url": cfg.groq_base_url,
        }
    except Exception as list_error:
        completion = client.chat.completions.create(
            model=cfg.groq_model,
            messages=[{"role": "user", "content": "."}],
            max_tokens=1,
            temperature=0,
        )
        choice = completion.choices[0] if completion.choices else None
        return {
            "ok": True,
            "method": "chat.completions",
            "id": completion.id,
            "finish_reason": getattr(choice, "finish_reason", None),
            "base_url": cfg.groq_base_url,
            "models_list_error": str(list_error),
        }


@dataclass
class GroqJsonResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


def _usage_tokens(completion: Any) -> tuple[int, int]:
    usage = getattr(completion, "usage", None)
    if usage is None:
        return 0, 0
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(
        getattr(usage, "completion_tokens", 0) or 0
    )


def groq_complete_json(
    settings: Settings,
    messages: Sequence[dict[str, str]],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    **_kwargs: Any,
) -> GroqJsonResult:
    """One Groq chat.completions call in json_object mode. No embeddings."""
    client = build_groq_client(settings)
    model_id = (model or settings.groq_model).strip()
    token_cap = settings.groq_extract_max_tokens if max_tokens is None else max_tokens
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
        )
    except ImportError:
        AuthenticationError = Exception  # type: ignore[misc, assignment]
        RateLimitError = Exception  # type: ignore[misc, assignment]
        APIStatusError = Exception  # type: ignore[misc, assignment]
        APITimeoutError = Exception  # type: ignore[misc, assignment]
        APIConnectionError = Exception  # type: ignore[misc, assignment]

    try:
        completion = client.chat.completions.create(
            model=model_id,
            messages=list(messages),
            temperature=0,
            max_tokens=token_cap,
            response_format={"type": "json_object"},
        )
    except AuthenticationError as exc:
        raise GroqAuthError(
            "Groq authentication failed. Check GROQ_API_KEY. "
            "Do not fall back to another LLM host."
        ) from exc
    except RateLimitError as exc:
        raise GroqRateLimitError(f"Groq 429/TPM: {exc}") from exc
    except APITimeoutError as exc:
        raise GroqRetryableError(f"Groq timeout: {exc}") from exc
    except APIConnectionError as exc:
        raise GroqRetryableError(f"Groq connection error: {exc}") from exc
    except APIStatusError as exc:
        status = getattr(exc, "status_code", None)
        if status in {401, 403}:
            raise GroqAuthError(f"Groq HTTP {status}: {exc}") from exc
        if status in {429, 500, 502, 503, 529}:
            if status == 429:
                raise GroqRateLimitError(f"Groq HTTP 429: {exc}") from exc
            raise GroqRetryableError(f"Groq HTTP {status}: {exc}") from exc
        raise

    choice = completion.choices[0] if completion.choices else None
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    prompt_tokens, completion_tokens = _usage_tokens(completion)
    return GroqJsonResult(
        content=content or "",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=getattr(completion, "model", None) or model_id,
    )


@dataclass
class GroqToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class GroqToolResult:
    content: str | None
    tool_calls: list[GroqToolCall]
    finish_reason: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


def groq_complete_tools(
    settings: Settings,
    messages: Sequence[dict[str, Any]],
    tools: Sequence[dict[str, Any]],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> GroqToolResult:
    """Groq chat.completions with tool calling. Never embeddings; never another host."""
    client = build_groq_client(settings)
    model_id = (model or settings.groq_model).strip()
    token_cap = settings.groq_copilot_max_tokens if max_tokens is None else max_tokens
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
        )
    except ImportError:
        AuthenticationError = Exception  # type: ignore[misc, assignment]
        RateLimitError = Exception  # type: ignore[misc, assignment]
        APIStatusError = Exception  # type: ignore[misc, assignment]
        APITimeoutError = Exception  # type: ignore[misc, assignment]
        APIConnectionError = Exception  # type: ignore[misc, assignment]

    try:
        completion = client.chat.completions.create(
            model=model_id,
            messages=list(messages),
            tools=list(tools) or None,
            temperature=0,
            max_tokens=token_cap,
        )
    except AuthenticationError as exc:
        raise GroqAuthError(
            "Groq authentication failed. Check GROQ_API_KEY. "
            "Do not fall back to another LLM host."
        ) from exc
    except RateLimitError as exc:
        raise GroqRateLimitError(f"Groq 429/TPM: {exc}") from exc
    except APITimeoutError as exc:
        raise GroqRetryableError(f"Groq timeout: {exc}") from exc
    except APIConnectionError as exc:
        raise GroqRetryableError(f"Groq connection error: {exc}") from exc
    except APIStatusError as exc:
        status = getattr(exc, "status_code", None)
        if status in {401, 403}:
            raise GroqAuthError(f"Groq HTTP {status}: {exc}") from exc
        if status == 429:
            raise GroqRateLimitError(f"Groq HTTP 429: {exc}") from exc
        if status in {400, 413, 500, 502, 503, 529}:
            raise GroqRetryableError(f"Groq HTTP {status}: {exc}") from exc
        raise

    choice = completion.choices[0] if completion.choices else None
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    raw_calls = getattr(message, "tool_calls", None) if message is not None else None
    calls: list[GroqToolCall] = []
    for item in raw_calls or []:
        fn = getattr(item, "function", None)
        calls.append(
            GroqToolCall(
                id=str(getattr(item, "id", "") or ""),
                name=str(getattr(fn, "name", "") or ""),
                arguments=str(getattr(fn, "arguments", "") or "{}"),
            )
        )
    prompt_tokens, completion_tokens = _usage_tokens(completion)
    return GroqToolResult(
        content=content,
        tool_calls=calls,
        finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=getattr(completion, "model", None) or model_id,
    )


def groq_complete_text(
    settings: Settings,
    messages: Sequence[dict[str, Any]],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> GroqJsonResult:
    """Plain Groq chat completion (no tools, no json_object). Copilot fallback."""
    client = build_groq_client(settings)
    model_id = (model or settings.groq_model).strip()
    token_cap = settings.groq_copilot_max_tokens if max_tokens is None else max_tokens
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
        )
    except ImportError:
        AuthenticationError = Exception  # type: ignore[misc, assignment]
        RateLimitError = Exception  # type: ignore[misc, assignment]
        APIStatusError = Exception  # type: ignore[misc, assignment]
        APITimeoutError = Exception  # type: ignore[misc, assignment]
        APIConnectionError = Exception  # type: ignore[misc, assignment]

    try:
        completion = client.chat.completions.create(
            model=model_id,
            messages=list(messages),
            temperature=0,
            max_tokens=token_cap,
        )
    except AuthenticationError as exc:
        raise GroqAuthError(
            "Groq authentication failed. Check GROQ_API_KEY. "
            "Do not fall back to another LLM host."
        ) from exc
    except RateLimitError as exc:
        raise GroqRateLimitError(f"Groq 429/TPM: {exc}") from exc
    except APITimeoutError as exc:
        raise GroqRetryableError(f"Groq timeout: {exc}") from exc
    except APIConnectionError as exc:
        raise GroqRetryableError(f"Groq connection error: {exc}") from exc
    except APIStatusError as exc:
        status = getattr(exc, "status_code", None)
        if status in {401, 403}:
            raise GroqAuthError(f"Groq HTTP {status}: {exc}") from exc
        if status == 429:
            raise GroqRateLimitError(f"Groq HTTP 429: {exc}") from exc
        if status in {400, 413, 500, 502, 503, 529}:
            raise GroqRetryableError(f"Groq HTTP {status}: {exc}") from exc
        raise

    choice = completion.choices[0] if completion.choices else None
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    prompt_tokens, completion_tokens = _usage_tokens(completion)
    return GroqJsonResult(
        content=content or "",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=getattr(completion, "model", None) or model_id,
    )
