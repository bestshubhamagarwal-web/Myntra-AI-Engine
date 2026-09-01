"""Frozen Groq extraction payload (Architecture §8.2).

Nulls are allowed; the model must not guess. Validate with Pydantic, then
ground against the source text (quotes, competitors, price/comparison cues).
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QUESTION_IDS: tuple[str, ...] = tuple(f"Q{i}" for i in range(1, 10))

INTENT_TAGS: tuple[str, ...] = (
    "price_watch",
    "save_for_later",
    "bookmark",
    "mood_board",
    "indecision_parking",
    "gift",
    "restock_wait",
    "unknown",
    "not_applicable",
)

INTENT_MODES: tuple[str, ...] = (
    "near_term_purchase",
    "passive_bookmark",
    "mixed",
    "unknown",
)

FRICTION_TAGS: tuple[str, ...] = (
    "fit_uncertainty",
    "quality_doubt",
    "return_risk",
    "authenticity",
    "styling_doubt",
    "price_sensitivity",
    "review_credibility",
    "social_validation",
    "policy_trust",
    "comparison_paralysis",
    "delivery_or_availability",
    "other",
)

RESIDUAL_UNCERTAINTIES: tuple[str, ...] = (
    "fit",
    "quality",
    "returns",
    "authenticity",
    "styling",
    "value_for_money",
)

OFF_PLATFORM: tuple[str, ...] = (
    "reddit",
    "youtube",
    "influencer",
    "size_chart",
    "brand_site",
    "resale_check",
    "competitor_app",
    "other",
)

COMPARISON_BEHAVIORS: tuple[str, ...] = ("true", "false", "unknown")

SENTIMENT_PRIMARY: tuple[str, ...] = (
    "trust",
    "delight",
    "frustration",
    "doubt",
    "mixed",
    "neutral",
)


class IntentTag(str, Enum):
    price_watch = "price_watch"
    save_for_later = "save_for_later"
    bookmark = "bookmark"
    mood_board = "mood_board"
    indecision_parking = "indecision_parking"
    gift = "gift"
    restock_wait = "restock_wait"
    unknown = "unknown"
    not_applicable = "not_applicable"


class IntentMode(str, Enum):
    near_term_purchase = "near_term_purchase"
    passive_bookmark = "passive_bookmark"
    mixed = "mixed"
    unknown = "unknown"


class FrictionTag(str, Enum):
    fit_uncertainty = "fit_uncertainty"
    quality_doubt = "quality_doubt"
    return_risk = "return_risk"
    authenticity = "authenticity"
    styling_doubt = "styling_doubt"
    price_sensitivity = "price_sensitivity"
    review_credibility = "review_credibility"
    social_validation = "social_validation"
    policy_trust = "policy_trust"
    comparison_paralysis = "comparison_paralysis"
    delivery_or_availability = "delivery_or_availability"
    other = "other"


class ResidualUncertainty(str, Enum):
    fit = "fit"
    quality = "quality"
    returns = "returns"
    authenticity = "authenticity"
    styling = "styling"
    value_for_money = "value_for_money"


class OffPlatformChannel(str, Enum):
    reddit = "reddit"
    youtube = "youtube"
    influencer = "influencer"
    size_chart = "size_chart"
    brand_site = "brand_site"
    resale_check = "resale_check"
    competitor_app = "competitor_app"
    other = "other"


class ComparisonBehavior(str, Enum):
    true = "true"
    false = "false"
    unknown = "unknown"


class SentimentPrimary(str, Enum):
    trust = "trust"
    delight = "delight"
    frustration = "frustration"
    doubt = "doubt"
    mixed = "mixed"
    neutral = "neutral"


def _enum_or_default(value: Any, enum_cls: type[Enum], default: Enum) -> Enum:
    if value is None or value == "":
        return default
    if isinstance(value, enum_cls):
        return value
    raw = str(value).strip()
    try:
        return enum_cls(raw)
    except ValueError:
        lowered = raw.lower().replace(" ", "_").replace("-", "_")
        try:
            return enum_cls(lowered)
        except ValueError:
            return default


class ExtractionEntities(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str | None = None
    brand: str | None = None
    occasion: str | None = None
    size_fit_mentioned: bool | None = None
    price_mentioned: bool | None = None
    competitor_mentions: list[str] = Field(default_factory=list)

    @field_validator("competitor_mentions", mode="before")
    @classmethod
    def _mentions_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return [str(v) for v in value if str(v).strip()]


class SentimentBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary: SentimentPrimary = SentimentPrimary.neutral
    severity: float = 0.0

    @field_validator("primary", mode="before")
    @classmethod
    def _primary(cls, value: Any) -> Any:
        return _enum_or_default(value, SentimentPrimary, SentimentPrimary.neutral)

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, value: Any) -> float:
        if value is None or value == "":
            return 0.0
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("sentiment.severity must be a number") from exc
        if number < 0:
            return 0.0
        if number > 1:
            return 1.0
        return number


class VerbatimQuote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    span: str
    start_char: int | None = None
    end_char: int | None = None


class ExtractionPayload(BaseModel):
    """Per-document Groq JSON. `intent_mode` is first-class and not a friction tag."""

    model_config = ConfigDict(extra="ignore")

    intent_tag: IntentTag = IntentTag.unknown
    intent_mode: IntentMode = IntentMode.unknown
    friction_tag: list[FrictionTag] = Field(default_factory=list)
    residual_uncertainties: list[ResidualUncertainty] = Field(default_factory=list)
    comparison_behavior: ComparisonBehavior = ComparisonBehavior.unknown
    off_platform_info_seeking: list[OffPlatformChannel] = Field(default_factory=list)
    entities: ExtractionEntities = Field(default_factory=ExtractionEntities)
    sentiment: SentimentBlock = Field(default_factory=SentimentBlock)
    verbatim_quotes: list[VerbatimQuote] = Field(default_factory=list)
    maps_to_questions: list[str] = Field(default_factory=list)
    extraction_confidence: float = 0.0

    @field_validator("intent_tag", mode="before")
    @classmethod
    def _intent_tag(cls, value: Any) -> Any:
        return _enum_or_default(value, IntentTag, IntentTag.unknown)

    @field_validator("intent_mode", mode="before")
    @classmethod
    def _intent_mode(cls, value: Any) -> Any:
        return _enum_or_default(value, IntentMode, IntentMode.unknown)

    @field_validator("friction_tag", mode="before")
    @classmethod
    def _friction(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        items = [value] if isinstance(value, str) else list(value)
        out: list[FrictionTag] = []
        seen: set[str] = set()
        for item in items:
            tag = _enum_or_default(item, FrictionTag, FrictionTag.other)
            if tag.value not in seen:
                seen.add(tag.value)
                out.append(tag)  # type: ignore[arg-type]
        return out

    @field_validator("residual_uncertainties", mode="before")
    @classmethod
    def _residuals(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        items = [value] if isinstance(value, str) else list(value)
        out: list[ResidualUncertainty] = []
        seen: set[str] = set()
        for item in items:
            if item is None or item == "":
                continue
            try:
                tag = ResidualUncertainty(str(item).strip())
            except ValueError:
                continue
            if tag.value not in seen:
                seen.add(tag.value)
                out.append(tag)
        return out

    @field_validator("comparison_behavior", mode="before")
    @classmethod
    def _comparison(cls, value: Any) -> Any:
        if value is True:
            return ComparisonBehavior.true
        if value is False:
            return ComparisonBehavior.false
        if value is None or value == "":
            return ComparisonBehavior.unknown
        raw = str(value).strip().lower()
        if raw in {"true", "yes", "1"}:
            return ComparisonBehavior.true
        if raw in {"false", "no", "0"}:
            return ComparisonBehavior.false
        return _enum_or_default(value, ComparisonBehavior, ComparisonBehavior.unknown)

    @field_validator("off_platform_info_seeking", mode="before")
    @classmethod
    def _off_platform(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        items = [value] if isinstance(value, str) else list(value)
        out: list[OffPlatformChannel] = []
        seen: set[str] = set()
        for item in items:
            tag = _enum_or_default(item, OffPlatformChannel, OffPlatformChannel.other)
            if tag.value not in seen:
                seen.add(tag.value)
                out.append(tag)  # type: ignore[arg-type]
        return out

    @field_validator("maps_to_questions", mode="before")
    @classmethod
    def _questions(cls, value: Any) -> list[str]:
        if value is None:
            return []
        items = [value] if isinstance(value, str) else list(value)
        out: list[str] = []
        for item in items:
            token = str(item).strip().upper()
            if not token:
                continue
            if not token.startswith("Q"):
                token = f"Q{token}" if token.isdigit() else token
            if token in QUESTION_IDS and token not in out:
                out.append(token)
        return out

    @field_validator("verbatim_quotes", mode="before")
    @classmethod
    def _quotes(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        return list(value)

    @field_validator("entities", mode="before")
    @classmethod
    def _entities(cls, value: Any) -> Any:
        if value is None:
            return ExtractionEntities()
        return value

    @field_validator("sentiment", mode="before")
    @classmethod
    def _sentiment_block(cls, value: Any) -> Any:
        if value is None:
            return SentimentBlock()
        return value

    @field_validator("extraction_confidence", mode="before")
    @classmethod
    def _confidence(cls, value: Any) -> float:
        if value is None or value == "":
            return 0.0
        number = float(value)
        if number < 0:
            return 0.0
        if number > 1:
            return 1.0
        return number

    @model_validator(mode="after")
    def intent_mode_is_not_friction(self) -> ExtractionPayload:
        friction_values = {tag.value for tag in self.friction_tag}
        if self.intent_mode.value in friction_values:
            # Impossible with current enums; keep the split if a future tag overlaps.
            pass
        return self

    def friction_values(self) -> list[str]:
        return [tag.value for tag in self.friction_tag]


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_content(text: str) -> dict[str, Any]:
    """Parse model content as a JSON object. Strips markdown fences."""
    if not text or not str(text).strip():
        raise json.JSONDecodeError("empty content", text or "", 0)
    raw = str(text).strip()
    if raw.startswith("```"):
        raw = _FENCE_RE.sub("", raw).strip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("extraction JSON must be an object")
    return data


def payload_from_json_text(text: str) -> ExtractionPayload:
    return ExtractionPayload.model_validate(parse_json_content(text))


def architecture_field_names() -> set[str]:
    return {
        "intent_tag",
        "intent_mode",
        "friction_tag",
        "residual_uncertainties",
        "comparison_behavior",
        "off_platform_info_seeking",
        "entities",
        "sentiment",
        "verbatim_quotes",
        "maps_to_questions",
        "extraction_confidence",
    }
