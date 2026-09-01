from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from src.db.repository import DocumentRepository, NormalizedRecord
from src.models.envelope import MyntraRelevance, RawEnvelope
from src.normalize.category import infer_gender_segment, infer_price_tier, infer_product_category
from src.normalize.hashing import content_hash
from src.normalize.language import detect_language
from src.normalize.quality import is_empty_or_emoji_only, quality_score
from src.normalize.relevance import classify_relevance, is_removed_body
from src.normalize.text import envelope_text, expected_content_hash, scrubbed_analysis_text
from src.timeutil import utcnow


@dataclass
class NormalizeResult:
    run_id: UUID
    accepted: int
    rejected: int
    duplicates: int = 0
    status: str = "success"


def decide_raw(envelope: RawEnvelope) -> tuple[str, str | None]:
    text = envelope_text(envelope)
    source_type = envelope.source_type.value
    if source_type == "reddit":
        body = envelope.raw_text
        if is_removed_body(body) or is_empty_or_emoji_only(body):
            return MyntraRelevance.reject.value, "removed"
    if is_empty_or_emoji_only(text):
        return MyntraRelevance.reject.value, "empty_or_emoji"
    relevance, reason = classify_relevance(
        text,
        source_type=source_type,
        parent_context=envelope.parent_context,
    )
    return relevance, reason


def build_normalized(
    envelope: RawEnvelope,
    *,
    normalize_run_id: UUID,
    existing_hash_id: UUID | None,
) -> NormalizedRecord:
    original_language_text = envelope_text(envelope)
    language = detect_language(original_language_text)
    scrubbed = scrubbed_analysis_text(original_language_text)
    hashed = content_hash(scrubbed)
    duplicate_of = existing_hash_id if existing_hash_id else None
    category = infer_product_category(original_language_text)
    gender = infer_gender_segment(original_language_text)
    q = quality_score(
        original_language_text,
        envelope.myntra_relevance.value if envelope.myntra_relevance else "inferred",
        envelope.reject_reason,
    )
    return NormalizedRecord(
        id=uuid4(),
        raw_id=envelope.id,
        text_original=scrubbed,
        text_en=None,
        language=language,
        product_category=category or "unknown",
        gender_segment=gender or "unknown",
        price_tier=infer_price_tier(original_language_text) or "unknown",
        platform_used="unknown",
        occasion="unknown",
        star_rating=envelope.star_rating,
        review_date=envelope.published_at,
        quality_score=q,
        content_hash=hashed,
        duplicate_of=duplicate_of,
        eligible=duplicate_of is None,
        pii_scrubbed_at=utcnow(),
        normalize_run_id=normalize_run_id,
        intent_mode=None,
    )


def _envelopes_to_process(
    repo: DocumentRepository,
    *,
    since_run_id: UUID | None,
) -> list[RawEnvelope]:
    if since_run_id is not None:
        return repo.list_raw_for_run(since_run_id)
    by_id: dict[UUID, RawEnvelope] = {}
    for env in repo.list_raw_pending_normalize():
        by_id[env.id] = env
    for env in repo.list_stale_raw():
        by_id[env.id] = env
    return list(by_id.values())


def run_normalize(
    repo: DocumentRepository,
    *,
    since_run_id: UUID | None = None,
    process_all: bool = False,
) -> NormalizeResult:
    del process_all  # pending + stale is the default; --since-run selects a pull
    run_id = uuid4()
    started = utcnow()
    repo.start_normalize_run(run_id, started, since_run_id)

    envelopes = _envelopes_to_process(repo, since_run_id=since_run_id)
    accepted = 0
    rejected = 0
    duplicates = 0
    processed = 0
    try:
        for env in envelopes:
            already = repo.get_normalized_by_raw_id(env.id)
            relevance, reason = decide_raw(env)
            repo.mark_raw_decision(env.id, relevance, reason)
            env.myntra_relevance = MyntraRelevance(relevance)
            env.reject_reason = reason
            if relevance == MyntraRelevance.reject.value:
                rejected += 1
            else:
                hashed = expected_content_hash(env)
                survivor_id = repo.find_normalized_by_content_hash(hashed)
                is_duplicate = bool(
                    survivor_id and (already is None or already.id != survivor_id)
                )
                record = build_normalized(
                    env,
                    normalize_run_id=run_id,
                    existing_hash_id=survivor_id if is_duplicate else None,
                )
                if already:
                    record.id = already.id
                repo.upsert_normalized(record)
                if is_duplicate:
                    duplicates += 1
                else:
                    accepted += 1
            processed += 1
            if processed % 500 == 0:
                saver = getattr(repo, "save", None)
                if callable(saver):
                    saver()
        result = NormalizeResult(
            run_id=run_id,
            accepted=accepted,
            rejected=rejected,
            duplicates=duplicates,
            status="success",
        )
        repo.finish_normalize_run(
            run_id, utcnow(), accepted, rejected + duplicates, "success"
        )
        return result
    except Exception:
        repo.finish_normalize_run(
            run_id, utcnow(), accepted, rejected + duplicates, "failed"
        )
        raise
