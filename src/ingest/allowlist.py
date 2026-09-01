"""Myntra-only app identities. Competitor store pages are never ingested."""

from __future__ import annotations

from src.models.envelope import ARCHITECTURE_SOURCE_TYPES, SourceType

MYNTRA_PLAY_STORE_APP_ID = "com.myntra.android"
MYNTRA_APP_STORE_APP_ID = "907394059"

# Implemented Phase 3 connectors. Instagram/Facebook stay unavailable (ToS).
IMPLEMENTED_SOURCE_TYPES: tuple[str, ...] = (
    SourceType.play_store.value,
    SourceType.app_store.value,
    SourceType.reddit.value,
    SourceType.youtube.value,
    SourceType.x.value,
)

# Fifth-source candidates that are documented unavailable unless a ToS-clear
# public API exists. Quora HTML and Myntra site scrape are not shipped.
UNAVAILABLE_WITHOUT_CONNECTOR: tuple[str, ...] = tuple(
    name for name in ARCHITECTURE_SOURCE_TYPES if name not in IMPLEMENTED_SOURCE_TYPES
)

DEFAULT_SOURCE_NOTES: dict[str, str] = {
    SourceType.play_store.value: "Myntra Android app reviews",
    SourceType.app_store.value: "Myntra iOS app reviews (official iTunes customer-reviews RSS)",
    SourceType.reddit.value: "PRAW or public Reddit JSON; subreddits + site search; Myntra-filtered",
    SourceType.youtube.value: "YouTube comments on haul / size-guide / vs / unboxing videos mentioning Myntra (Data API or public Invidious/Piped/Innertube)",
    SourceType.x.value: "X public RSS (Nitter/RSSHub) or API v2 recent search when a bearer token is set",
    SourceType.quora.value: "unavailable — no ToS-clear public API in Phase 3",
    SourceType.forum.value: "unavailable — no ToS-clear public connector in Phase 3",
    SourceType.instagram.value: "unavailable until a public ToS-compliant path exists",
    SourceType.facebook.value: "unavailable until a public ToS-compliant path exists",
    SourceType.myntra_qa.value: "unavailable — Myntra on-site Q&A not ingested (ToS)",
    SourceType.myntra_review.value: "unavailable — Myntra on-site reviews not ingested (ToS)",
    SourceType.other.value: "unavailable — reserved enum; not a live connector",
}

REDDIT_DEFAULT_SUBREDDITS: tuple[str, ...] = (
    "IndianFashionAddicts",
    "IndianStreetwear",
    "FashionReps",
    "femalefashionadvice",
    "malefashionadvice",
    "india",
    "AskIndia",
    "Mumbai",
    "delhi",
    "bangalore",
    "Pune",
    "Hyderabad",
    "Chennai",
    "Kolkata",
    "IndiaSpeaks",
)


def require_myntra_play_app_id(app_id: str) -> str:
    value = (app_id or "").strip()
    if value != MYNTRA_PLAY_STORE_APP_ID:
        raise ValueError(
            "Play Store connector is Myntra-only "
            f"({MYNTRA_PLAY_STORE_APP_ID}). Competitor app pages are out of scope."
        )
    return value


def require_myntra_app_store_id(app_id: str) -> str:
    value = (app_id or "").strip()
    if value != MYNTRA_APP_STORE_APP_ID:
        raise ValueError(
            "App Store connector is Myntra-only "
            f"(id {MYNTRA_APP_STORE_APP_ID}). Competitor app pages are out of scope."
        )
    return value


def parse_subreddits(value: str | None) -> list[str]:
    if not value:
        return list(REDDIT_DEFAULT_SUBREDDITS)
    names = []
    for part in value.split(","):
        name = part.strip().lstrip("r/")
        if name:
            names.append(name)
    return names or list(REDDIT_DEFAULT_SUBREDDITS)
