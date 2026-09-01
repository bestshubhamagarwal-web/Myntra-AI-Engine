from datetime import datetime, timezone
from uuid import UUID


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def coerce_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_datetime(value) -> datetime | None:
    """Parse API timestamps (datetime, unix seconds, or ISO-8601) to aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return coerce_aware(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return coerce_aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def as_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
