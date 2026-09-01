"""Weekly report: theme_metrics diff + Groq narrative + PDF on disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from src.config import Settings, load_settings
from src.db.repository import DocumentRepository, ReportArtifact
from src.extract.groq_client import GroqJsonResult, groq_complete_json
from src.reports.diff import diff_theme_metrics
from src.reports.pdf import write_report_pdf
from src.timeutil import utcnow

CompleteFn = Callable[..., GroqJsonResult]

CORRELATION_CAVEAT = (
    "Findings are stated user language, not proven causal drop-off. "
    "Correlation is not causation."
)
REFUSE_SOLUTIONS = (
    "Do not recommend product solutions, features, PRDs, or roadmaps. "
    "Describe evidence only."
)

DEFAULT_PROMPT = """You write a weekly research narrative for Myntra wishlist conversations.

Rules:
- Use only the supplied diff JSON and quotes. Never invent SoV, counts, or trends.
- If first_week is true, this is a baseline. Do not claim percent growth.
- If do_not_interpret_as_volume_drop is true, a source became unavailable.
  Do not claim complaints fell because ingest failed.
- Separate bookmark vs stall. Label hypothesis_flag themes as hypotheses.
- """ + REFUSE_SOLUTIONS + """
- """ + CORRELATION_CAVEAT + """

Return JSON: {"narrative": "..."} only.
"""


@dataclass
class ReportJobResult:
    report_id: UUID
    status: str
    path: str | None
    first_week: bool


def _load_prompt(settings: Settings) -> str:
    path = settings.report_prompt_path
    if path is None:
        default = Path(__file__).resolve().parents[2] / "prompts" / "report_narrative.md"
        path = default if default.exists() else None
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    return DEFAULT_PROMPT


def _quotes_for_top(repo: DocumentRepository, theme_ids: list[str], limit: int = 8) -> list[str]:
    quotes: list[str] = []
    for theme in theme_ids:
        try:
            theme_id = UUID(theme)
        except ValueError:
            continue
        for row in repo.list_document_themes(theme_id=theme_id):
            extraction = repo.get_extraction(row.document_id)
            if not extraction:
                continue
            for item in extraction.verbatim_quotes or []:
                span = item.get("span") or item.get("text")
                if span:
                    quotes.append(str(span)[:240])
                if len(quotes) >= limit:
                    return quotes
    return quotes


def _unavailable(rows) -> list[str]:
    if not rows:
        return []
    return list(rows[0].unavailable_sources or [])


def run_report(
    repo: DocumentRepository,
    settings: Settings | None = None,
    *,
    complete_fn: CompleteFn | None = None,
) -> ReportJobResult:
    cfg = settings or load_settings()
    cfg.ensure_runtime_dirs()
    current_run = repo.latest_cluster_run(success_only=True)
    if current_run is None:
        raise ValueError("no successful cluster_run; run cluster + metrics first")
    runs = [r for r in repo.list_cluster_runs() if r.status == "success"]
    runs.sort(key=lambda r: r.started_at)
    previous_run = None
    for run in runs:
        if run.id == current_run.id:
            continue
        previous_run = run
    current_metrics = repo.list_theme_metrics(
        cluster_run_id=current_run.id, slice_kind="global", published_only=True
    )
    previous_metrics = (
        repo.list_theme_metrics(
            cluster_run_id=previous_run.id, slice_kind="global", published_only=True
        )
        if previous_run
        else []
    )
    current_unavail = _unavailable(current_metrics)
    previous_unavail = _unavailable(previous_metrics)
    newly = sorted(set(current_unavail) - set(previous_unavail)) if previous_run else []
    diff = diff_theme_metrics(current_metrics, previous_metrics, newly_unavailable=newly)
    eligible = current_metrics[0].eligible_corpus_count if current_metrics else 0
    included = []
    for status in repo.list_source_status():
        if status.status == "live":
            included.append(status.source_type)
    header = {
        "corpus_size": eligible,
        "included_sources": included,
        "unavailable_sources": current_unavail,
        "newly_unavailable_sources": newly,
        "correlation_caveat": CORRELATION_CAVEAT,
        "cluster_run_id": str(current_run.id),
        "previous_cluster_run_id": str(previous_run.id) if previous_run else None,
        "period_start": current_metrics[0].period_start.isoformat()
        if current_metrics and current_metrics[0].period_start
        else None,
        "period_end": current_metrics[0].period_end.isoformat()
        if current_metrics and current_metrics[0].period_end
        else None,
        "first_week": diff["first_week"],
        "chart_theme_ids": [row["theme_id"] for row in diff["top_themes"]],
        "refuse_solutions": True,
    }
    quotes = _quotes_for_top(repo, header["chart_theme_ids"])
    names = {str(t.id): t.name for t in repo.list_themes(current_run.id) if t.published}
    for row in diff.get("top_themes") or []:
        row["name"] = names.get(row.get("theme_id") or "")
    narrative = _narrative(
        cfg,
        header=header,
        diff=diff,
        quotes=quotes,
        complete_fn=complete_fn,
    )
    if _has_solutioning(narrative):
        narrative = (
            "Evidence-only summary. Product solutioning was stripped. "
            + CORRELATION_CAVEAT
            + " "
            + _baseline_or_diff_copy(diff)
        )
    title = "Weekly Myntra wishlist discovery report"
    report_id = uuid4()
    pdf_path = Path(cfg.reports_path) / f"{report_id}.pdf"
    period = f"{header.get('period_start') or 'n/a'} – {header.get('period_end') or 'n/a'}"
    header_lines = [
        f"Corpus (eligible): {eligible}",
        f"Included sources: {', '.join(included) or '(none)'}",
        f"Unavailable sources: {', '.join(current_unavail) or '(none)'}",
        CORRELATION_CAVEAT,
        "Charts use the same theme_metrics snapshot as this narrative.",
    ]
    write_report_pdf(
        pdf_path,
        title=title,
        header_lines=header_lines,
        narrative=narrative,
        top_themes=diff["top_themes"],
        period=period,
    )
    artifact = ReportArtifact(
        id=report_id,
        title=title,
        status="success",
        created_at=utcnow(),
        period_start=current_metrics[0].period_start if current_metrics else None,
        period_end=current_metrics[0].period_end if current_metrics else None,
        cluster_run_id=current_run.id,
        previous_cluster_run_id=previous_run.id if previous_run else None,
        path=str(pdf_path),
        header=header,
        diff=diff,
        narrative=narrative,
        groq_model=cfg.groq_model_light,
    )
    repo.insert_report(artifact)
    return ReportJobResult(
        report_id=report_id,
        status="success",
        path=str(pdf_path),
        first_week=bool(diff["first_week"]),
    )


def _baseline_or_diff_copy(diff: dict[str, Any]) -> str:
    if diff.get("first_week"):
        parts = ["This is the first snapshot; percent changes are not computed."]
    elif diff.get("do_not_interpret_as_volume_drop"):
        missing = ", ".join(diff.get("newly_unavailable_sources") or [])
        parts = [
            f"Source(s) newly unavailable ({missing}). "
            "Do not read volume change as a real drop in conversation."
        ]
    else:
        parts = ["Theme ranks follow impact_score on the current snapshot."]
    ranked = []
    for row in (diff.get("top_themes") or [])[:5]:
        name = row.get("name") or row.get("theme_id")
        ranked.append(
            f"{name}: mention_count {row.get('mention_count')}, "
            f"share_of_voice {row.get('share_of_voice')}, "
            f"impact_score {row.get('impact_score')}."
        )
    if ranked:
        parts.append("Top opportunity areas: " + " ".join(ranked))
    return " ".join(parts)


def _has_solutioning(text: str) -> bool:
    lowered = (text or "").lower()
    needles = (
        "should build",
        "we recommend adding",
        "prd",
        "size predictor",
        "roadmap",
        "ship a feature",
    )
    return any(item in lowered for item in needles)


def _narrative(
    settings: Settings,
    *,
    header: dict[str, Any],
    diff: dict[str, Any],
    quotes: list[str],
    complete_fn: CompleteFn | None,
) -> str:
    fallback = _baseline_or_diff_copy(diff) + " " + CORRELATION_CAVEAT
    import json

    user = json.dumps({"header": header, "diff": diff, "quotes": quotes}, default=str)
    messages = [
        {"role": "system", "content": _load_prompt(settings)},
        {"role": "user", "content": user},
    ]
    fn = complete_fn or groq_complete_json
    try:
        result = fn(
            settings,
            messages,
            model=settings.groq_model_light,
            max_tokens=settings.groq_label_max_tokens,
        )
        content = result.content if isinstance(result, GroqJsonResult) else str(result)
        payload = json.loads(content or "{}")
        text = payload.get("narrative") if isinstance(payload, dict) else None
        if text:
            return str(text)
    except Exception:
        return fallback
    return fallback
