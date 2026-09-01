"""Phase 7 Q1–Q9 gold harness (Architecture §11.5, docs/eval.md)."""

from src.evals.gold import GoldRow, load_gold
from src.evals.scorer import RowScore, SuiteReport, score_row, score_suite

__all__ = [
    "GoldRow",
    "RowScore",
    "SuiteReport",
    "load_gold",
    "score_row",
    "score_suite",
]
