"""Frozen raw envelope (Architecture §6.1). Connectors must emit this shape.

Changing these fields after Phase 1 forces connector rewrites — keep the
§6.1 contract stable. Extra persistence columns (id, content_hash, …) are
allowed as long as connectors still populate every frozen field.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# Architecture §6.1 — required on every connector emission.
FROZEN_RAW_ENVELOPE_FIELDS: tuple[str, ...] = (
    "source_type",
    "source_id",
    "url",
    "fetched_at",
    "published_at",
    "platform",
    "raw_text",
    "raw_title",
    "star_rating",
    "parent_context",
    "author_hash",
    "payload_uri",
    "myntra_relevance",
)


class SourceType(str, Enum):
    play_store = "play_store"
    app_store = "app_store"
    reddit = "reddit"
    youtube = "youtube"
    x = "x"
    quora = "quora"
    forum = "forum"
    instagram = "instagram"
    facebook = "facebook"
    myntra_qa = "myntra_qa"
    myntra_review = "myntra_review"
    other = "other"


# Architecture §6.1 source_type enum — every value must appear in source_status.
ARCHITECTURE_SOURCE_TYPES: tuple[str, ...] = tuple(item.value for item in SourceType)


class MyntraRelevance(str, Enum):
    explicit = "explicit"
    inferred = "inferred"
    reject = "reject"


class RawEnvelope(BaseModel):
    """Canonical ingest record. Usernames are hashed before this object is stored."""

    id: UUID = Field(default_factory=uuid4)
    source_type: SourceType
    source_id: str
    url: str | None = None
    fetched_at: datetime
    published_at: datetime | None = None
    platform: str | None = None
    raw_text: str | None = None
    raw_title: str | None = None
    star_rating: int | None = None
    parent_context: dict[str, Any] = Field(default_factory=dict)
    author_hash: str | None = None
    payload_uri: str | None = None
    myntra_relevance: MyntraRelevance | None = None
    reject_reason: str | None = None
    content_hash: str | None = None
    ingest_run_id: UUID | None = None
    date_anomaly: bool = False
