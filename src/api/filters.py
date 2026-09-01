"""Global filter query params shared by every metrics/evidence route."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from src.timeutil import coerce_aware, parse_datetime


@dataclass(frozen=True)
class GlobalFilters:
    date_from: datetime | None = None
    date_to: datetime | None = None
    source_type: str | None = None
    product_category: str | None = None
    gender_segment: str | None = None
    price_tier: str | None = None
    platform_used: str | None = None
    intent_mode: str | None = None
    theme_id: UUID | None = None
    friction_tag: str | None = None
    intent_tag: str | None = None
    q: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for key, value in asdict(self).items():
            if value is None:
                out[key] = None
            elif isinstance(value, datetime):
                out[key] = value.isoformat()
            else:
                out[key] = str(value)
        return out

    def has_theme_or_category(self) -> bool:
        return self.theme_id is not None or bool(self.product_category)


def parse_uuid(value: str | UUID | None) -> UUID | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def filters_from_params(
    *,
    date_from: str | datetime | None = None,
    date_to: str | datetime | None = None,
    source_type: str | None = None,
    product_category: str | None = None,
    gender_segment: str | None = None,
    price_tier: str | None = None,
    platform_used: str | None = None,
    intent_mode: str | None = None,
    theme_id: str | UUID | None = None,
    friction_tag: str | None = None,
    intent_tag: str | None = None,
    q: str | None = None,
) -> GlobalFilters:
    return GlobalFilters(
        date_from=coerce_aware(parse_datetime(date_from)) if date_from else None,
        date_to=coerce_aware(parse_datetime(date_to)) if date_to else None,
        source_type=_clean(source_type),
        product_category=_clean(product_category),
        gender_segment=_clean(gender_segment),
        price_tier=_clean(price_tier),
        platform_used=_clean(platform_used),
        intent_mode=_clean(intent_mode),
        theme_id=parse_uuid(theme_id),
        friction_tag=_clean(friction_tag),
        intent_tag=_clean(intent_tag),
        q=_clean(q),
    )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_metrics_slice(filters: GlobalFilters) -> tuple[str, dict[str, str]]:
    """Pick the precomputed theme_metrics slice. Combined filters still join evidence."""
    if filters.product_category:
        return "product_category", {
            "kind": "product_category",
            "product_category": filters.product_category,
        }
    if filters.source_type:
        return "source_type", {"kind": "source_type", "source_type": filters.source_type}
    return "global", {"kind": "global"}
