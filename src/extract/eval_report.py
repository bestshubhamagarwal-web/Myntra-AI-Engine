"""Operator report for Phase 2 extract/embed smoke (plan task 4)."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.db.repository import DocumentRepository, ExtractionRecord
from src.extract.grounding import repair_quotes
from src.extract.schema import ExtractionPayload, VerbatimQuote


@dataclass
class ExtractEvalReport:
    total: int
    ok: int
    failed: int
    pending: int
    ok_rate: float
    quote_span_ok: int
    quote_span_checked: int
    intent_mode_distinct: bool
    metrics_eligible: int
    notes: list[str] = field(default_factory=list)


def _quotes_valid(record: ExtractionRecord, text: str) -> bool:
    quotes = []
    for item in record.verbatim_quotes:
        span = item.get("span") if isinstance(item, dict) else None
        if not span:
            continue
        quotes.append(
            VerbatimQuote(
                span=span,
                start_char=item.get("start_char"),
                end_char=item.get("end_char"),
            )
        )
    dummy = ExtractionPayload(verbatim_quotes=quotes)
    repaired = repair_quotes(dummy, text)
    return len(repaired) == len(quotes)


def build_extract_eval_report(
    repo: DocumentRepository,
    *,
    limit: int | None = 50,
) -> ExtractEvalReport:
    rows = repo.list_extractions(limit=limit)
    ok = sum(1 for r in rows if r.extraction_status == "ok")
    failed = sum(1 for r in rows if r.extraction_status == "failed")
    pending = sum(1 for r in rows if r.extraction_status == "pending")
    total = len(rows)
    quote_ok = 0
    quote_checked = 0
    distinct = True
    for record in rows:
        if record.extraction_status != "ok":
            continue
        if record.intent_mode and record.intent_mode in (record.friction_tags or []):
            distinct = False
        document = repo.get_normalized(record.document_id)
        if document is None:
            continue
        quote_checked += 1
        if _quotes_valid(record, document.text_original):
            quote_ok += 1
    eligible = len(repo.list_extractions(metrics_eligible_only=True, limit=limit))
    ok_rate = (ok / total) if total else 0.0
    notes: list[str] = []
    if total and ok_rate < 0.8:
        notes.append(f"ok_rate {ok_rate:.0%} below 80% sample bar (EV-2-01)")
    if quote_checked and quote_ok != quote_checked:
        notes.append("some ok rows have quote spans that are not in text_original")
    if not distinct:
        notes.append("intent_mode collided with friction_tag on at least one row")
    return ExtractEvalReport(
        total=total,
        ok=ok,
        failed=failed,
        pending=pending,
        ok_rate=ok_rate,
        quote_span_ok=quote_ok,
        quote_span_checked=quote_checked,
        intent_mode_distinct=distinct,
        metrics_eligible=eligible,
        notes=notes,
    )
