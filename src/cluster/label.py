"""Groq (GROQ_MODEL_LIGHT) labels for HDBSCAN clusters. Heuristic fallback when skipped."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import Sequence

from pydantic import ValidationError

from src.cluster.label_schema import (
    BookmarkVsStall,
    ThemeLabelPayload,
    bookmark_vs_stall_from_modes,
    is_generic_theme_name,
    payload_from_json_text,
)
from src.cluster.prompt import ThemeLabelPrompt, build_label_messages
from src.config import Settings
from src.extract.groq_client import (
    GroqAuthError,
    GroqJsonResult,
    GroqRateLimitError,
    GroqRetryableError,
)
from src.extract.pipeline import TpmWindow, backoff_seconds, estimate_prompt_tokens

logger = logging.getLogger(__name__)

CompleteFn = Callable[..., GroqJsonResult]


def heuristic_label(
    *,
    quotes: Sequence[str],
    friction_tags: Sequence[str],
    intent_tags: Sequence[str],
    intent_modes: Sequence[str],
) -> ThemeLabelPayload:
    frictions = Counter(tag for tag in friction_tags if tag)
    intents = Counter(tag for tag in intent_tags if tag and tag not in {"unknown", "not_applicable"})
    top_friction = frictions.most_common(1)[0][0] if frictions else None
    top_intent = intents.most_common(1)[0][0] if intents else None
    mode = bookmark_vs_stall_from_modes(list(intent_modes))
    parts: list[str] = []
    if top_friction:
        parts.append(top_friction.replace("_", " "))
    if top_intent:
        parts.append(top_intent.replace("_", " "))
    if mode == BookmarkVsStall.bookmark:
        parts.append("passive bookmarking")
    elif mode == BookmarkVsStall.stall:
        parts.append("purchase stall")
    name = " — ".join(parts[:2]) if parts else "Unlabeled evidence cluster"
    if is_generic_theme_name(name) and quotes:
        name = quotes[0][:80].strip()
    description = quotes[0] if quotes else "Cluster members share extraction tags; Groq label was skipped."
    return ThemeLabelPayload(
        name=name[:160],
        description=description[:800],
        hypothesis_flag=True,
        bookmark_vs_stall=mode,
    )


def _invoke(
    complete_fn: CompleteFn,
    messages: list[dict[str, str]],
    *,
    settings: Settings,
    tpm: TpmWindow,
    sleep,
    clock,
) -> GroqJsonResult:
    last_error: Exception | None = None
    estimate = estimate_prompt_tokens(messages)
    for attempt in range(1, settings.groq_max_retries + 1):
        tpm.wait(estimate, clock(), sleep)
        try:
            result = complete_fn(
                messages,
                model=settings.groq_model_light,
                max_tokens=settings.groq_label_max_tokens,
            )
            used = result.prompt_tokens + result.completion_tokens or estimate
            tpm.record(used, clock())
            return result
        except GroqAuthError:
            raise
        except (GroqRateLimitError, GroqRetryableError) as exc:
            last_error = exc
            delay = backoff_seconds(attempt, base=settings.groq_backoff_base_seconds)
            logger.warning("theme label retry attempt=%s delay=%.2fs error=%s", attempt, delay, exc)
            sleep(delay)
    raise last_error or GroqRetryableError("theme label failed")


def label_cluster(
    *,
    quotes: list[str],
    friction_hist: dict[str, int],
    intent_mode_hist: dict[str, int],
    intent_tag_hist: dict[str, int],
    categories: dict[str, int],
    source_types: dict[str, int],
    member_count: int,
    intent_modes: list[str],
    friction_tags: list[str],
    intent_tags: list[str],
    prompt: ThemeLabelPrompt,
    settings: Settings,
    complete_fn: CompleteFn | None,
    tpm: TpmWindow,
    sleep=None,
    clock=None,
) -> tuple[ThemeLabelPayload, str, int, int]:
    """Returns payload, label_status, prompt_tokens, completion_tokens."""
    import time as time_mod

    sleep = sleep or time_mod.sleep
    clock = clock or time_mod.time
    fallback = heuristic_label(
        quotes=quotes,
        friction_tags=friction_tags,
        intent_tags=intent_tags,
        intent_modes=intent_modes,
    )
    if complete_fn is None:
        return fallback, "heuristic", 0, 0

    prompt_tokens = 0
    completion_tokens = 0
    last_payload: ThemeLabelPayload | None = None
    for retry_generic in (False, True):
        messages = build_label_messages(
            prompt,
            quotes=quotes,
            friction_hist=friction_hist,
            intent_mode_hist=intent_mode_hist,
            intent_tag_hist=intent_tag_hist,
            categories=categories,
            source_types=source_types,
            member_count=member_count,
            retry_generic=retry_generic,
        )
        json_retries = max(1, settings.groq_json_retries)
        parsed: ThemeLabelPayload | None = None
        for _attempt in range(json_retries):
            try:
                result = _invoke(
                    complete_fn,
                    messages,
                    settings=settings,
                    tpm=tpm,
                    sleep=sleep,
                    clock=clock,
                )
            except GroqAuthError:
                raise
            except Exception as exc:
                logger.warning("theme label groq error: %s", exc)
                break
            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            try:
                parsed = payload_from_json_text(result.content)
                break
            except (ValidationError, ValueError, TypeError) as exc:
                logger.warning("theme label invalid JSON: %s", exc)
                parsed = None
        if parsed is None:
            continue
        last_payload = parsed
        if not is_generic_theme_name(parsed.name):
            return parsed, "groq", prompt_tokens, completion_tokens
    if last_payload is not None:
        # Keep Groq description/flags; replace generic name with heuristic.
        merged = ThemeLabelPayload(
            name=fallback.name,
            description=last_payload.description or fallback.description,
            hypothesis_flag=last_payload.hypothesis_flag,
            bookmark_vs_stall=last_payload.bookmark_vs_stall,
        )
        return merged, "groq_generic_replaced", prompt_tokens, completion_tokens
    return fallback, "heuristic", prompt_tokens, completion_tokens
