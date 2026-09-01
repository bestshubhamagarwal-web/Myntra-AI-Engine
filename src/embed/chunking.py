"""Whitespace-token chunking (Architecture §8.1).

One short review = one chunk. Longer text: 200–500 tokens with 50 overlap.
SoV later must count distinct document_id, not chunks.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

CHUNK_MAX_TOKENS = 400
CHUNK_OVERLAP_TOKENS = 50
ONE_CHUNK_IF_AT_MOST = 500


def tokenize(text: str) -> list[str]:
    stripped = (text or "").strip()
    if not stripped:
        return []
    words = stripped.split()
    if len(words) == 1 and len(stripped) > 2000:
        size = 800
        return [stripped[i : i + size] for i in range(0, len(stripped), size)]
    return words


def estimate_tokens(text: str) -> int:
    return max(0, len(tokenize(text)))


def chunk_text(
    text: str,
    *,
    max_tokens: int = CHUNK_MAX_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
    one_if_at_most: int = ONE_CHUNK_IF_AT_MOST,
) -> list[str]:
    """Split PII-scrubbed text. Empty / whitespace-only → no chunks (do not embed)."""
    stripped = (text or "").strip()
    if not stripped:
        return []
    tokens = tokenize(stripped)
    if not tokens:
        return []
    if max_tokens < 200 or max_tokens > 500:
        raise ValueError("chunk max_tokens must be in 200–500 (Architecture §8.1)")
    if overlap < 0 or overlap >= max_tokens:
        raise ValueError("chunk overlap must be >= 0 and < max_tokens")
    if len(tokens) <= one_if_at_most:
        return [stripped]
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        piece = " ".join(tokens[start:end]).strip()
        if piece:
            chunks.append(piece)
        if end >= len(tokens):
            break
        start = end - overlap
        if start < 0:
            start = 0
    return chunks


def mention_count_from_document_ids(document_ids: Sequence[UUID]) -> int:
    """EV-2-16 / EC-EM-03: SoV mention_count is distinct document_id."""
    return len({item for item in document_ids})
