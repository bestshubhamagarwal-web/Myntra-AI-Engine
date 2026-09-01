"""Load and validate evals/q1_q9.jsonl (docs/eval.md gold schema)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLD_PATH = REPO_ROOT / "evals" / "q1_q9.jsonl"

Behavior = Literal["answer", "caveat", "decline", "refuse_solution", "refuse_ooscope"]
QUESTION_IDS = tuple(f"Q{i}" for i in range(1, 10))
MIN_PARAPHRASES = 2


class GoldRow(BaseModel):
    id: str
    question_id: str
    prompt: str
    expected_behavior: Behavior
    require_citation: bool = False
    require_metrics_match: bool = False
    require_bookmark_stall_split: bool = False
    eval_id: str = ""
    notes: str = ""
    acceptable_behaviors: list[Behavior] = Field(default_factory=list)

    @field_validator("prompt")
    @classmethod
    def prompt_not_empty(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("gold prompt must be non-empty")
        return text

    def behaviors_ok(self) -> set[str]:
        allowed = set(self.acceptable_behaviors or [])
        allowed.add(self.expected_behavior)
        return allowed


def load_gold(path: Path | None = None) -> list[GoldRow]:
    gold_path = Path(path) if path else DEFAULT_GOLD_PATH
    rows: list[GoldRow] = []
    for lineno, line in enumerate(gold_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{gold_path}:{lineno}: invalid JSON ({exc})") from exc
        rows.append(GoldRow.model_validate(payload))
    if not rows:
        raise ValueError(f"{gold_path} has no gold rows")
    return rows


def gold_coverage_errors(rows: Iterable[GoldRow]) -> list[str]:
    """Must: ≥2 paraphrases per Q1–Q9 and refuse probes R1–R3."""
    by_q: dict[str, list[GoldRow]] = {}
    for row in rows:
        by_q.setdefault(row.question_id, []).append(row)
    errors: list[str] = []
    for qid in QUESTION_IDS:
        n = len(by_q.get(qid, []))
        if n < MIN_PARAPHRASES:
            errors.append(f"{qid} has {n} paraphrases; need ≥{MIN_PARAPHRASES}")
    for rid, eval_id, behavior in (
        ("R1", "EV-7-R1", "refuse_solution"),
        ("R2", "EV-7-R2", "refuse_ooscope"),
        ("R3", "EV-7-R3", "decline"),
    ):
        matching = [
            row
            for row in rows
            if row.id == rid or row.eval_id == eval_id or row.question_id == rid
        ]
        if not matching:
            errors.append(f"missing refuse/decline probe {rid} ({eval_id})")
            continue
        if matching[0].expected_behavior != behavior:
            errors.append(
                f"{rid} expected_behavior={matching[0].expected_behavior!r} "
                f"want {behavior}"
            )
    q7 = [row for row in rows if row.question_id == "Q7"]
    if q7 and not any(row.require_bookmark_stall_split for row in q7):
        errors.append("Q7 must set require_bookmark_stall_split on at least one row")
    ids = [row.id for row in rows]
    if len(ids) != len(set(ids)):
        errors.append("duplicate gold id")
    return errors


def gold_as_dicts(rows: Iterable[GoldRow]) -> list[dict[str, Any]]:
    return [row.model_dump() for row in rows]
