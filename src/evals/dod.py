"""Project-level definition of done (Implementation Plan top + EV-7-08)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.api.schemas import PHASE6_PATHS
from src.ingest.allowlist import IMPLEMENTED_SOURCE_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_VIEWS = (
    "app/(shell)/overview/page.tsx",
    "app/(shell)/themes/page.tsx",
    "app/(shell)/evidence/page.tsx",
    "app/(shell)/categories/page.tsx",
    "app/(shell)/trends/page.tsx",
    "app/(shell)/segments/page.tsx",
    "app/(shell)/sources/page.tsx",
    "app/(shell)/phrases/page.tsx",
    "app/(shell)/reports/page.tsx",
    "app/(shell)/copilot/page.tsx",
)

REQUIRED_TABLE_HINTS = (
    "raw_documents",
    "normalized_documents",
    "extractions",
    "chunks",
    "themes",
    "theme_metrics",
)


def static_dod(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or REPO_ROOT
    web = root / "web"
    migrations = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((root / "migrations").glob("*.sql"))
    )
    checks = {
        "ingest_4_5_sources": len(IMPLEMENTED_SOURCE_TYPES) >= 4,
        "implemented_source_types": list(IMPLEMENTED_SOURCE_TYPES),
        "raw_structured_tables": all(name in migrations for name in REQUIRED_TABLE_HINTS),
        "copilot_api": (root / "src" / "api" / "copilot.py").is_file(),
        "query_api_paths": list(PHASE6_PATHS),
        "dashboard_views": all((web / rel).is_file() for rel in REQUIRED_VIEWS),
        "theme_explorer": (web / "components" / "ThemeCard.tsx").is_file(),
        "evidence_drawer": (web / "components" / "EvidenceDrawer.tsx").is_file(),
        "gold_file": (root / "evals" / "q1_q9.jsonl").is_file(),
        "runbook": (root / "docs" / "Runbook.md").is_file(),
    }
    checks["passed"] = all(
        bool(checks[key])
        for key in (
            "ingest_4_5_sources",
            "raw_structured_tables",
            "copilot_api",
            "dashboard_views",
            "theme_explorer",
            "evidence_drawer",
            "gold_file",
            "runbook",
        )
    )
    missing_views = [rel for rel in REQUIRED_VIEWS if not (web / rel).is_file()]
    if missing_views:
        checks["missing_views"] = missing_views
    return checks


def live_dod(repo) -> dict[str, Any]:
    """Optional DB snapshot. Empty corpus is recorded, not imputed."""
    statuses = repo.list_source_status()
    live_types = sorted(
        {
            row.source_type
            for row in statuses
            if getattr(row, "normalized_count", 0) > 0
        }
    )
    run = repo.latest_cluster_run(success_only=True)
    themes = repo.list_themes(run.id) if run else []
    published = [t for t in themes if t.published]
    return {
        "normalized_source_types": live_types,
        "n_normalized_source_types": len(live_types),
        "raw_count": repo.count_raw(),
        "normalized_count": repo.count_normalized(),
        "published_themes": len(published),
        "cluster_run_id": str(run.id) if run else None,
        "meets_4_sources_in_db": len(live_types) >= 4,
    }
