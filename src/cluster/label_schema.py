"""Groq theme label payload (Architecture §8.3)."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.extract.schema import _enum_or_default, parse_json_content

GENERIC_THEME_NAMES = frozenset(
    {
        "customer issues",
        "issues",
        "problems",
        "feedback",
        "miscellaneous",
        "misc",
        "other",
        "other insights",
        "general",
        "reviews",
        "comments",
        "shopping",
        "myntra",
        "theme",
        "cluster",
        "opportunity",
        "opportunity area",
        "various",
        "mixed",
        "various issues",
        "general feedback",
        "user complaints",
    }
)


class BookmarkVsStall(str, Enum):
    bookmark = "bookmark"
    stall = "stall"
    both = "both"
    unclear = "unclear"


class ThemeLabelPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    hypothesis_flag: bool = True
    bookmark_vs_stall: BookmarkVsStall = BookmarkVsStall.unclear

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            raise ValueError("theme name is required")
        return text

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("hypothesis_flag", mode="before")
    @classmethod
    def _hypothesis(cls, value: Any) -> bool:
        if value is None or value == "":
            return True
        if isinstance(value, bool):
            return value
        raw = str(value).strip().lower()
        if raw in {"true", "1", "yes"}:
            return True
        if raw in {"false", "0", "no"}:
            return False
        return True

    @field_validator("bookmark_vs_stall", mode="before")
    @classmethod
    def _mode(cls, value: Any) -> Any:
        return _enum_or_default(value, BookmarkVsStall, BookmarkVsStall.unclear)


def is_generic_theme_name(name: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    if not compact:
        return True
    if compact in GENERIC_THEME_NAMES:
        return True
    if len(compact.split()) <= 1 and compact in {"issue", "problem", "feedback", "myntra"}:
        return True
    return False


def payload_from_json_text(text: str) -> ThemeLabelPayload:
    return ThemeLabelPayload.model_validate(parse_json_content(text))


def bookmark_vs_stall_from_modes(intent_modes: list[str]) -> BookmarkVsStall:
    counts = {"bookmark": 0, "stall": 0, "mixed": 0}
    for mode in intent_modes:
        token = (mode or "").strip().lower()
        if token == "passive_bookmark":
            counts["bookmark"] += 1
        elif token == "near_term_purchase":
            counts["stall"] += 1
        elif token == "mixed":
            counts["mixed"] += 1
    if counts["mixed"] and (counts["bookmark"] or counts["stall"]):
        return BookmarkVsStall.both
    if counts["bookmark"] and counts["stall"]:
        return BookmarkVsStall.both
    if counts["mixed"] and not counts["bookmark"] and not counts["stall"]:
        return BookmarkVsStall.both
    if counts["bookmark"] > counts["stall"]:
        return BookmarkVsStall.bookmark
    if counts["stall"] > counts["bookmark"]:
        return BookmarkVsStall.stall
    return BookmarkVsStall.unclear
