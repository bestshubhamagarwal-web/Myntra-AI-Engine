"""Write scrubbed review files so an operator can inspect the Phase 3 corpus."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from src.db.repository import DocumentRepository, ExtractionRecord, NormalizedRecord
from src.models.envelope import RawEnvelope
from src.normalize.pii import scrub_pii
from src.timeutil import utcnow

THEME_HINT = re.compile(
    r"wishlist|wish[\s\-]?list|size(?:s| chart| guide)?|sizing|runs? (?:small|large)|"
    r"return(?:s|ed|ing)?|\bcart\b|shopping bag",
    re.IGNORECASE,
)

PARENT_KEYS = (
    "video_title",
    "video_id",
    "channel_title",
    "query",
    "subreddit",
    "thread_title",
    "kind",
    "app_id",
    "app_version",
    "country",
    "conversation_id",
)

CSV_FIELDS = (
    "source_type",
    "stage",
    "source_id",
    "url",
    "published_at",
    "language",
    "product_category",
    "eligible",
    "star_rating",
    "reject_reason",
    "parent_title",
    "theme_hit",
    "text",
)

DUMP_SUFFIXES = {".json", ".jsonl", ".md", ".csv"}


@dataclass
class ReviewDumpResult:
    output_dir: Path
    files: list[str] = field(default_factory=list)
    source_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    live_source_types: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=utcnow)


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _parent_context(env: RawEnvelope) -> dict[str, Any]:
    raw = env.parent_context or {}
    return {key: raw.get(key) for key in PARENT_KEYS if raw.get(key) not in (None, "")}


def _parent_title(env: RawEnvelope) -> str | None:
    ctx = env.parent_context or {}
    for key in ("video_title", "thread_title"):
        value = ctx.get(key)
        if value:
            return str(value)
    if env.raw_title:
        return env.raw_title
    return None


def _relevance(env: RawEnvelope) -> str | None:
    if env.myntra_relevance is None:
        return None
    return env.myntra_relevance.value


def _stage(env: RawEnvelope, rec: NormalizedRecord | None) -> str:
    if rec is not None:
        return "normalized"
    if _relevance(env) == "reject":
        return "rejected"
    return "pending"


def _text(env: RawEnvelope, rec: NormalizedRecord | None) -> str:
    if rec is not None:
        return rec.text_original or ""
    blob = "\n".join(p for p in ((env.raw_title or "").strip(), (env.raw_text or "").strip()) if p)
    return scrub_pii(blob)


def _extraction_preview(record: ExtractionRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "status": record.extraction_status,
        "intent_tag": record.intent_tag,
        "intent_mode": record.intent_mode,
        "friction_tags": list(record.friction_tags or []),
        "residual_uncertainties": list(record.residual_uncertainties or []),
        "sentiment_primary": record.sentiment_primary,
        "maps_to_questions": list(record.maps_to_questions or []),
        "verbatim_quotes": _jsonable(record.verbatim_quotes or []),
        "metrics_eligible": record.metrics_eligible,
    }


def _record_row(
    env: RawEnvelope,
    rec: NormalizedRecord | None,
    extraction: ExtractionRecord | None,
) -> dict[str, Any]:
    text = _text(env, rec)
    return {
        "source_type": env.source_type.value,
        "source_id": env.source_id,
        "stage": _stage(env, rec),
        "url": env.url,
        "published_at": _jsonable(env.published_at),
        "fetched_at": _jsonable(env.fetched_at),
        "platform": env.platform,
        "star_rating": rec.star_rating if rec else env.star_rating,
        "language": rec.language if rec else None,
        "product_category": rec.product_category if rec else None,
        "eligible": rec.eligible if rec else False,
        "myntra_relevance": _relevance(env),
        "reject_reason": env.reject_reason,
        "parent_context": _parent_context(env),
        "parent_title": _parent_title(env),
        "theme_hit": bool(THEME_HINT.search(text)),
        "text": text,
        "extraction": _extraction_preview(extraction),
    }


def _reset_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_file() and child.suffix in DUMP_SUFFIXES:
            child.unlink()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "source_type": row.get("source_type") or "",
                    "stage": row.get("stage") or "",
                    "source_id": row.get("source_id") or "",
                    "url": row.get("url") or "",
                    "published_at": row.get("published_at") or "",
                    "language": row.get("language") or "",
                    "product_category": row.get("product_category") or "",
                    "eligible": row.get("eligible"),
                    "star_rating": row.get("star_rating") if row.get("star_rating") is not None else "",
                    "reject_reason": row.get("reject_reason") or "",
                    "parent_title": row.get("parent_title") or "",
                    "theme_hit": row.get("theme_hit"),
                    "text": (row.get("text") or "").replace("\r\n", " ").replace("\n", " "),
                }
            )


def _source_status_payload(repo: DocumentRepository) -> list[dict[str, Any]]:
    rows = []
    for row in repo.list_source_status():
        rows.append(
            {
                "source_type": row.source_type,
                "status": row.status,
                "enabled": row.enabled,
                "raw_count": row.raw_count,
                "normalized_count": row.normalized_count,
                "last_run_status": row.last_run_status,
                "last_source_available": row.last_source_available,
                "notes": row.notes,
            }
        )
    return rows


def _source_status_md(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Phase 3 source status",
        "",
        "Zeros are real counts, not imputed volumes. `unavailable` means the connector was skipped or is out of scope.",
        "",
        "| source | status | enabled | raw | normalized | last run | notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        notes = (row.get("notes") or "").replace("|", "/")
        lines.append(
            f"| {row['source_type']} | {row['status']} | {row['enabled']} | "
            f"{row['raw_count']} | {row['normalized_count']} | "
            f"{row.get('last_run_status') or '-'} | {notes} |"
        )
    live = [
        r["source_type"]
        for r in rows
        if r["status"] == "live" and int(r["normalized_count"] or 0) > 0
    ]
    lines.extend(
        [
            "",
            f"Normalized source types with rows: {', '.join(live) if live else '(none)'} "
            f"(n={len(live)}).",
            "",
        ]
    )
    return "\n".join(lines)


def _sample_md(rows_by_source: dict[str, list[dict[str, Any]]], per_source: int = 4) -> str:
    lines = [
        "# Phase 3 qualitative sample",
        "",
        "Scrubbed text only. Prefer rows that mention wishlist, sizing, or returns (EV-3-11).",
        "",
    ]
    for source, rows in sorted(rows_by_source.items()):
        accepted = [r for r in rows if r["stage"] == "normalized"]
        if not accepted:
            continue
        ranked = sorted(accepted, key=lambda r: (not r["theme_hit"], r.get("source_id") or ""))
        picked = ranked[:per_source]
        lines.append(f"## {source}")
        lines.append("")
        for row in picked:
            flags = []
            if row["theme_hit"]:
                flags.append("wishlist/sizing/returns")
            if row.get("language"):
                flags.append(str(row["language"]))
            if row.get("product_category"):
                flags.append(str(row["product_category"]))
            flag_text = ", ".join(flags) if flags else "no theme keywords"
            preview = (row.get("text") or "").replace("\n", " ").strip()
            if len(preview) > 400:
                preview = preview[:397] + "..."
            parent = row.get("parent_title")
            parent_bit = f" · {parent}" if parent else ""
            lines.append(f"- `{row['source_id']}` ({flag_text}){parent_bit}")
            lines.append("")
            lines.append(f"  > {preview}")
            lines.append("")
        rejected = [r for r in rows if r["stage"] == "rejected"][:2]
        if rejected:
            lines.append("Rejected (sample):")
            lines.append("")
            for row in rejected:
                preview = (row.get("text") or "").replace("\n", " ").strip()[:180]
                lines.append(
                    f"- `{row['source_id']}` reason={row.get('reject_reason') or '-'} | {preview}"
                )
            lines.append("")
    return "\n".join(lines)


def write_review_dump(repo: DocumentRepository, output_dir: Path) -> ReviewDumpResult:
    output_dir = Path(output_dir)
    _reset_dir(output_dir)

    normalized_by_raw = {rec.raw_id: rec for rec in repo.list_normalized(limit=None)}
    extraction_by_doc = {row.document_id: row for row in repo.list_extractions()}

    rows_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for env in repo.list_raw():
        rec = normalized_by_raw.get(env.id)
        extraction = extraction_by_doc.get(rec.id) if rec else None
        rows_by_source[env.source_type.value].append(_record_row(env, rec, extraction))

    source_counts: dict[str, dict[str, int]] = {}
    all_rows: list[dict[str, Any]] = []
    extraction_rows: list[dict[str, Any]] = []
    written: list[str] = []

    for source, rows in sorted(rows_by_source.items()):
        rows.sort(key=lambda r: (r["stage"], r.get("published_at") or "", r["source_id"]))
        counts = {
            "raw": len(rows),
            "normalized": sum(1 for r in rows if r["stage"] == "normalized"),
            "rejected": sum(1 for r in rows if r["stage"] == "rejected"),
            "pending": sum(1 for r in rows if r["stage"] == "pending"),
            "theme_hit": sum(1 for r in rows if r["theme_hit"]),
        }
        source_counts[source] = counts
        all_rows.extend(rows)
        jsonl_path = output_dir / f"{source}.jsonl"
        _write_jsonl(jsonl_path, rows)
        written.append(jsonl_path.name)
        for row in rows:
            if row.get("extraction"):
                extraction_rows.append(
                    {
                        "source_type": row["source_type"],
                        "source_id": row["source_id"],
                        **row["extraction"],
                    }
                )

    status_rows = _source_status_payload(repo)
    live = [
        r["source_type"]
        for r in status_rows
        if r["status"] == "live" and int(r["normalized_count"] or 0) > 0
    ]
    summary = {
        "generated_at": utcnow().isoformat(),
        "live_source_types": live,
        "live_source_type_count": len(live),
        "source_counts": source_counts,
        "extraction_rows": len(extraction_rows),
        "total_raw": sum(c["raw"] for c in source_counts.values()),
        "total_normalized": sum(c["normalized"] for c in source_counts.values()),
        "total_rejected": sum(c["rejected"] for c in source_counts.values()),
    }

    files = {
        "summary.json": lambda: _write_json(output_dir / "summary.json", summary),
        "source_status.json": lambda: _write_json(output_dir / "source_status.json", status_rows),
        "source_status.md": lambda: (output_dir / "source_status.md").write_text(
            _source_status_md(status_rows), encoding="utf-8"
        ),
        "sample.md": lambda: (output_dir / "sample.md").write_text(
            _sample_md(rows_by_source), encoding="utf-8"
        ),
        "all.csv": lambda: _write_csv(output_dir / "all.csv", all_rows),
        "all.jsonl": lambda: _write_jsonl(output_dir / "all.jsonl", all_rows),
        "extractions.jsonl": lambda: _write_jsonl(output_dir / "extractions.jsonl", extraction_rows),
    }
    for name, writer in files.items():
        writer()
        written.append(name)

    return ReviewDumpResult(
        output_dir=output_dir,
        files=sorted(set(written)),
        source_counts=source_counts,
        live_source_types=live,
    )
