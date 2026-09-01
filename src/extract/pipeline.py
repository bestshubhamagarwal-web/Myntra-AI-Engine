"""Batch Groq extraction with cache, resume, and retries (Phase 2)."""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import ValidationError

from src.config import Settings, load_settings
from src.db.repository import (
    DocumentRepository,
    ExtractionRecord,
    ExtractRun,
    NormalizedRecord,
)
from src.extract.grounding import ground_payload
from src.extract.groq_client import (
    GroqAuthError,
    GroqJsonResult,
    GroqRateLimitError,
    GroqRetryableError,
    groq_complete_json,
)
from src.extract.prompt import ExtractPrompt, build_extract_messages, load_extract_prompt
from src.extract.schema import ExtractionPayload, payload_from_json_text
from src.timeutil import utcnow

logger = logging.getLogger(__name__)

CompleteFn = Callable[..., GroqJsonResult]


@dataclass
class ExtractBatchResult:
    run_id: UUID
    status: str
    ok: int
    failed: int
    skipped: int
    prompt_tokens: int
    completion_tokens: int
    error_message: str | None = None


class TpmWindow:
    def __init__(self, max_tpm: int) -> None:
        self.max_tpm = max(1, max_tpm)
        self.events: list[tuple[float, int]] = []

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        self.events = [item for item in self.events if item[0] >= cutoff]

    def wait(self, estimate: int, now: float, sleep: Callable[[float], None]) -> None:
        self._prune(now)
        used = sum(tokens for _, tokens in self.events)
        if used + estimate <= self.max_tpm:
            return
        oldest = min(self.events, key=lambda item: item[0])[0]
        delay = max(0.05, 60.0 - (now - oldest) + 0.05)
        logger.info("tpm wait seconds=%.2f used=%s estimate=%s", delay, used, estimate)
        sleep(delay)

    def record(self, tokens: int, now: float) -> None:
        self.events.append((now, max(0, tokens)))


def backoff_seconds(attempt: int, base: float = 2.0, cap: float = 60.0) -> float:
    return min(cap, base * (2 ** (attempt - 1))) + random.uniform(0, 0.25)


def estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    chars = sum(len(m.get("content") or "") for m in messages)
    return max(32, chars // 4)


def record_from_payload(
    document: NormalizedRecord,
    payload: ExtractionPayload,
    *,
    prompt_version: str,
    groq_model: str | None,
    status: str,
    raw_response: str | None,
    error_message: str | None,
    retry_count: int,
    prompt_tokens: int,
    completion_tokens: int,
) -> ExtractionRecord:
    return ExtractionRecord(
        document_id=document.id,
        content_hash=document.content_hash,
        prompt_version=prompt_version,
        extraction_status=status,
        groq_model=groq_model,
        intent_tag=payload.intent_tag.value,
        intent_mode=payload.intent_mode.value,
        friction_tags=payload.friction_values(),
        residual_uncertainties=[item.value for item in payload.residual_uncertainties],
        comparison_behavior=payload.comparison_behavior.value,
        off_platform_info_seeking=[item.value for item in payload.off_platform_info_seeking],
        entities=payload.entities.model_dump(),
        sentiment_primary=payload.sentiment.primary.value,
        sentiment_severity=payload.sentiment.severity,
        verbatim_quotes=[q.model_dump() for q in payload.verbatim_quotes],
        maps_to_questions=list(payload.maps_to_questions),
        extraction_confidence=payload.extraction_confidence,
        raw_response=raw_response,
        error_message=error_message,
        retry_count=retry_count,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        extracted_at=utcnow(),
    )


def pending_record(document: NormalizedRecord, prompt_version: str, groq_model: str) -> ExtractionRecord:
    return ExtractionRecord(
        document_id=document.id,
        content_hash=document.content_hash,
        prompt_version=prompt_version,
        extraction_status="pending",
        groq_model=groq_model,
    )


def failed_record(
    document: NormalizedRecord,
    *,
    prompt_version: str,
    groq_model: str | None,
    raw_response: str | None,
    error_message: str,
    retry_count: int,
    prompt_tokens: int,
    completion_tokens: int,
) -> ExtractionRecord:
    return ExtractionRecord(
        document_id=document.id,
        content_hash=document.content_hash,
        prompt_version=prompt_version,
        extraction_status="failed",
        groq_model=groq_model,
        raw_response=raw_response,
        error_message=error_message,
        retry_count=retry_count,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        extracted_at=utcnow(),
    )


def _invoke_complete(
    complete_fn: CompleteFn,
    messages: list[dict[str, str]],
    document: NormalizedRecord,
    *,
    settings: Settings,
    sleep: Callable[[float], None],
    tpm: TpmWindow,
    clock: Callable[[], float],
) -> GroqJsonResult:
    last_error: Exception | None = None
    estimate = estimate_prompt_tokens(messages)
    for attempt in range(1, settings.groq_max_retries + 1):
        tpm.wait(estimate, clock(), sleep)
        try:
            result = complete_fn(messages, document=document)
            used = result.prompt_tokens + result.completion_tokens or estimate
            tpm.record(used, clock())
            return result
        except GroqAuthError:
            raise
        except (GroqRateLimitError, GroqRetryableError) as exc:
            last_error = exc
            delay = backoff_seconds(attempt, base=settings.groq_backoff_base_seconds)
            logger.warning(
                "groq retryable document_id=%s attempt=%s delay=%.2fs error=%s",
                document.id,
                attempt,
                delay,
                exc,
            )
            sleep(delay)
    raise last_error or GroqRetryableError("Groq retries exhausted")


def extract_one_document(
    document: NormalizedRecord,
    *,
    prompt: ExtractPrompt,
    settings: Settings,
    complete_fn: CompleteFn,
    sleep: Callable[[float], None],
    tpm: TpmWindow,
    clock: Callable[[], float],
) -> ExtractionRecord:
    messages = build_extract_messages(document, prompt)
    last_raw = None
    last_error = "invalid json"
    prompt_tokens = 0
    completion_tokens = 0
    groq_model = settings.groq_model
    attempts = 0
    try:
        for attempt in range(1, settings.groq_json_retries + 1):
            attempts = attempt
            result = _invoke_complete(
                complete_fn,
                messages,
                document,
                settings=settings,
                sleep=sleep,
                tpm=tpm,
                clock=clock,
            )
            last_raw = result.content
            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            groq_model = result.model or groq_model
            try:
                payload = payload_from_json_text(result.content)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = str(exc)
                logger.info(
                    "extract invalid_json document_id=%s attempt=%s error=%s",
                    document.id,
                    attempt,
                    exc,
                )
                sleep(0.2)
                continue
            grounded = ground_payload(payload, document.text_original)
            record = record_from_payload(
                document,
                grounded,
                prompt_version=prompt.version,
                groq_model=groq_model,
                status="ok",
                raw_response=result.content,
                error_message=None,
                retry_count=attempt - 1,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            logger.info(
                "extract ok document_id=%s tokens=%s/%s intent_mode=%s friction=%s",
                document.id,
                prompt_tokens,
                completion_tokens,
                record.intent_mode,
                record.friction_tags,
            )
            return record
    except GroqAuthError:
        raise
    except (GroqRateLimitError, GroqRetryableError) as exc:
        last_error = str(exc)
        last_raw = last_raw
    return failed_record(
        document,
        prompt_version=prompt.version,
        groq_model=groq_model,
        raw_response=last_raw,
        error_message=last_error,
        retry_count=max(0, attempts - 1),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def run_extract(
    repo: DocumentRepository,
    settings: Settings | None = None,
    *,
    complete_fn: CompleteFn | None = None,
    limit: int | None = None,
    resume_after: UUID | None = None,
    retry_failed: bool = True,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
    prompt: ExtractPrompt | None = None,
) -> ExtractBatchResult:
    cfg = settings or load_settings()
    sleeper = sleep or time.sleep
    tick = clock or time.monotonic
    spec = prompt or load_extract_prompt(cfg.extract_prompt_path)
    completer = complete_fn or (
        lambda messages, **kwargs: groq_complete_json(cfg, messages, **kwargs)
    )
    tpm = TpmWindow(cfg.groq_max_tpm)

    run = ExtractRun(
        id=uuid4(),
        started_at=utcnow(),
        status="running",
        prompt_version=spec.version,
        groq_model=cfg.groq_model,
        resume_after_document_id=resume_after,
    )
    repo.start_extract_run(run)

    ok = failed = skipped = 0
    prompt_tokens = completion_tokens = 0
    error_message = None
    status = "success"

    try:
        candidates = repo.list_extract_candidates(
            resume_after=resume_after,
            limit=limit,
            retry_failed=retry_failed,
        )
        for document in candidates:
            existing = repo.get_extraction(document.id)
            if (
                existing
                and existing.extraction_status == "ok"
                and existing.content_hash == document.content_hash
            ):
                skipped += 1
                logger.info(
                    "extract skip document_id=%s reason=content_hash tokens=0",
                    document.id,
                )
                continue

            repo.upsert_extraction(pending_record(document, spec.version, cfg.groq_model))
            if cfg.groq_min_interval_seconds > 0:
                sleeper(cfg.groq_min_interval_seconds)
            record = extract_one_document(
                document,
                prompt=spec,
                settings=cfg,
                complete_fn=completer,
                sleep=sleeper,
                tpm=tpm,
                clock=tick,
            )
            repo.upsert_extraction(record)
            prompt_tokens += record.prompt_tokens
            completion_tokens += record.completion_tokens
            if record.extraction_status == "ok":
                ok += 1
                repo.set_normalized_intent_mode(document.id, record.intent_mode)
                repo.update_chunk_metadata(document.id, record)
            else:
                failed += 1
                logger.warning(
                    "extract failed document_id=%s error=%s (row kept for evidence)",
                    document.id,
                    record.error_message,
                )
                repo.update_chunk_metadata(document.id, record)
    except GroqAuthError as exc:
        status = "failed"
        error_message = str(exc)
        logger.error("extract aborted: %s", exc)
        raise
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
        logger.exception("extract batch crashed")
        raise
    finally:
        run.finished_at = utcnow()
        run.status = status
        run.rows_ok = ok
        run.rows_failed = failed
        run.rows_skipped = skipped
        run.prompt_tokens = prompt_tokens
        run.completion_tokens = completion_tokens
        run.error_message = error_message
        repo.finish_extract_run(run)

    return ExtractBatchResult(
        run_id=run.id,
        status=status,
        ok=ok,
        failed=failed,
        skipped=skipped,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error_message=error_message,
    )
