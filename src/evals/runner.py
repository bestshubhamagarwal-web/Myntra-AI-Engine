"""Run the Q1–Q9 gold file against Copilot and write evals/runs/7/<date>/score.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from src.api.classify import QuestionIntent, classify_question
from src.api.copilot import CopilotService
from src.api.filters import GlobalFilters, filters_from_params
from src.api.query import QueryService
from src.config import Settings, frozen_snapshot, load_settings
from src.db.postgres import PostgresRepository
from src.db.repository import DocumentRepository
from src.evals.dod import live_dod, static_dod
from src.evals.gold import GoldRow, gold_coverage_errors, load_gold
from src.evals.scorer import SuiteReport, score_row, score_suite
from src.evals.versions import run_metadata
from src.timeutil import utcnow

CopilotFn = Callable[[str, GlobalFilters], dict[str, Any]]
MetricsFn = Callable[[GlobalFilters], Any]

REPO_ROOT = Path(__file__).resolve().parents[2]
NEEDS_GENERATION = {
    QuestionIntent.comparative,
    QuestionIntent.quantitative,
    QuestionIntent.qualitative,
    QuestionIntent.quotes_only,
}


def artifact_dir(repo_root: Path | None = None, day: str | None = None) -> Path:
    root = repo_root or REPO_ROOT
    stamp = day or utcnow().date().isoformat()
    dest = root / "evals" / "runs" / "7" / stamp
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def metrics_payload(query: QueryService, filters: GlobalFilters) -> dict[str, Any]:
    return {
        "overview": query.overview(filters),
        "themes": query.themes(filters),
        "evidence": query.evidence(filters),
        "segments": query.segments(filters, dimension="product_category"),
    }


def _needs_groq(gold: GoldRow) -> bool:
    intent = classify_question(gold.prompt)
    return intent in NEEDS_GENERATION


def run_gold_suite(
    rows: list[GoldRow],
    copilot_fn: CopilotFn,
    metrics_fn: MetricsFn,
    *,
    groq_available: bool = True,
) -> tuple[list[dict[str, Any]], SuiteReport]:
    scores = []
    turns: list[dict[str, Any]] = []
    for gold in rows:
        skipped = False
        skip_reason = None
        if _needs_groq(gold) and not groq_available:
            skipped = True
            skip_reason = "GROQ_API_KEY missing; generation rows skipped (refuse/decline still scored)"
            turn: dict[str, Any] = {"status": "skipped", "answer": None, "citations": []}
            payload = {}
        else:
            filters = filters_from_params()
            turn = copilot_fn(gold.prompt, filters)
            payload = metrics_fn(filters)
        score = score_row(gold, turn, payload, skipped=skipped, skip_reason=skip_reason)
        scores.append(score)
        turns.append({"id": gold.id, "prompt": gold.prompt, "turn": turn})
    return turns, score_suite(scores)


def write_score(
    dest: Path,
    *,
    settings: Settings,
    suite: SuiteReport,
    turns: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
    cluster_run_id: str | None = None,
    embedding_revision: str | None = None,
    repo_root: Path | None = None,
) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    payload = run_metadata(
        settings,
        cluster_run_id=cluster_run_id,
        embedding_revision=embedding_revision,
        repo_root=repo_root,
    )
    payload.update(suite.as_dict())
    payload["result"] = "pass" if suite.suite_pass else "fail"
    if extra:
        payload.update(extra)
    score_path = dest / "score.json"
    score_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (dest / "turns.jsonl").write_text(
        "\n".join(json.dumps(row, default=str, ensure_ascii=False) for row in turns) + "\n",
        encoding="utf-8",
    )
    return score_path


def run_check(
    settings: Settings | None = None,
    *,
    gold_path: Path | None = None,
    dest: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    cfg = settings or load_settings()
    rows = load_gold(gold_path)
    coverage = gold_coverage_errors(rows)
    freeze = frozen_snapshot(cfg)
    dod = static_dod(repo_root)
    payload = run_metadata(cfg, repo_root=repo_root)
    payload.update(
        {
            "mode": "check",
            "n_gold": len(rows),
            "coverage_errors": coverage,
            "dod": dod,
            "constants_frozen": freeze["matches_frozen"],
            "result": "pass" if not coverage and freeze["matches_frozen"] and dod["passed"] else "fail",
        }
    )
    if dest is not None:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "check.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
    return payload


def run_live(
    repo: DocumentRepository,
    settings: Settings,
    *,
    gold_path: Path | None = None,
    dest: Path | None = None,
    copilot: CopilotService | None = None,
    query: QueryService | None = None,
    groq_available: bool | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    rows = load_gold(gold_path)
    coverage = gold_coverage_errors(rows)
    if coverage:
        raise ValueError("gold coverage errors: " + "; ".join(coverage))
    query = query or QueryService(repo, settings)
    copilot = copilot or CopilotService(repo, settings)
    has_groq = (
        groq_available
        if groq_available is not None
        else bool((settings.groq_api_key or "").strip())
    )

    def copilot_fn(prompt: str, filters: GlobalFilters) -> dict[str, Any]:
        return copilot.query_turn(prompt, filters)

    def metrics_fn(filters: GlobalFilters) -> dict[str, Any]:
        return metrics_payload(query, filters)

    turns, suite = run_gold_suite(rows, copilot_fn, metrics_fn, groq_available=has_groq)
    run = repo.latest_cluster_run(success_only=True)
    revision = run.embedding_revision if run else None
    extra = {
        "mode": "live",
        "coverage_errors": coverage,
        "dod": static_dod(repo_root),
        "live_dod": live_dod(repo),
        "groq_available": has_groq,
    }
    out = dest or artifact_dir(repo_root)
    path = write_score(
        out,
        settings=settings,
        suite=suite,
        turns=turns,
        extra=extra,
        cluster_run_id=str(run.id) if run else None,
        embedding_revision=revision,
        repo_root=repo_root,
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    report["score_path"] = str(path)
    return report


def run_from_cli(
    args,
    *,
    settings: Settings | None = None,
    repo: DocumentRepository | None = None,
) -> int:
    cfg = settings or load_settings()
    gold_path = Path(args.gold) if getattr(args, "gold", None) else None
    dest = Path(args.out) if getattr(args, "out", None) else artifact_dir()
    if getattr(args, "check", False):
        payload = run_check(cfg, gold_path=gold_path, dest=dest)
        print(json.dumps({k: payload[k] for k in ("result", "n_gold", "coverage_errors", "constants_frozen") if k in payload}, indent=2))
        print(f"wrote {dest / 'check.json'}")
        return 0 if payload.get("result") == "pass" else 1
    store = repo or PostgresRepository(cfg.database_url)
    report = run_live(store, cfg, gold_path=gold_path, dest=dest)
    print(
        f"suite_pass={report.get('suite_pass')} "
        f"row_pass_rate={report.get('row_pass_rate')} "
        f"result={report.get('result')} "
        f"path={report.get('score_path')}"
    )
    if report.get("notes"):
        for note in report["notes"]:
            print(f"note={note}")
    return 0 if report.get("suite_pass") else 1
