from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


USERNAME_KEYS = {
    "userName",
    "user_name",
    "author",
    "username",
    "userImage",
    "user_image",
    "authorDisplayName",
    "author_display_name",
    "authorChannelId",
    "authorChannelUrl",
    "authorProfileImageUrl",
    "screen_name",
    "display_name",
}


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    return value


def _deep_redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, inner in value.items():
            if key in USERNAME_KEYS:
                continue
            out[key] = _deep_redact(inner)
        return out
    if isinstance(value, list):
        return [_deep_redact(item) for item in value]
    return _jsonable(value)


def redact_payload(payload: dict[str, Any], author_hash: str | None) -> dict[str, Any]:
    redacted = _deep_redact(payload)
    if not isinstance(redacted, dict):
        redacted = {"payload": redacted}
    redacted["author_hash"] = author_hash
    for key, value in list(redacted.items()):
        if isinstance(value, datetime):
            redacted[key] = value.isoformat()
    return redacted


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(self, source_type: str, source_id: str, payload: dict[str, Any]) -> str | None:
        try:
            directory = self.root / source_type
            directory.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in source_id)
            path = directory / f"{safe_id}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            return str(path)
        except OSError:
            return None
