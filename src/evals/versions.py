"""Record frozen model ids, prompt versions, and cluster_run_id for a scored run."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from src.cluster.prompt import load_theme_label_prompt
from src.config import Settings, frozen_snapshot, load_settings
from src.extract.prompt import load_extract_prompt
from src.timeutil import utcnow

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_RE = re.compile(r"version:\s*([a-zA-Z0-9._-]+)")


def git_sha(repo_root: Path | None = None) -> str:
    root = repo_root or REPO_ROOT
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        return out.decode("utf-8").strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def copilot_prompt_version(settings: Settings | None = None) -> str:
    cfg = settings or load_settings()
    path = cfg.copilot_prompt_path
    if path is None:
        path = REPO_ROOT / "prompts" / "copilot_system.md"
    text = Path(path).read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    return match.group(1) if match else "copilot.unknown"


def prompt_versions(settings: Settings | None = None) -> dict[str, str]:
    cfg = settings or load_settings()
    extract = load_extract_prompt(cfg.extract_prompt_path)
    theme = load_theme_label_prompt()
    return {
        "extract": extract.version,
        "theme_label": theme.version,
        "copilot": copilot_prompt_version(cfg),
    }


def run_metadata(
    settings: Settings,
    *,
    cluster_run_id: str | None = None,
    embedding_revision: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    freeze = frozen_snapshot(settings)
    return {
        "phase": 7,
        "date": utcnow().date().isoformat(),
        "recorded_at": utcnow().isoformat(),
        "git_sha": git_sha(repo_root),
        "GROQ_MODEL": settings.groq_model,
        "GROQ_MODEL_LIGHT": settings.groq_model_light,
        "BGE_MODEL_ID": settings.bge_model_id,
        "BGE_revision": embedding_revision or "unknown",
        "EMBEDDING_DIM": settings.embedding_dim,
        "C_max": settings.c_max,
        "S_max": settings.s_max,
        "cluster_run_id": cluster_run_id,
        "prompt_versions": prompt_versions(settings),
        "constants_frozen": freeze["matches_frozen"],
        "frozen_expected": freeze["expected"],
        "allow_unfrozen_constants": settings.allow_unfrozen_constants,
    }
