from __future__ import annotations

from src.models.envelope import RawEnvelope
from src.normalize.hashing import content_hash
from src.normalize.pii import contains_unscrubbed_pii, scrub_pii


def envelope_text(envelope: RawEnvelope) -> str:
    """Body used for relevance, language, PII, and exact-hash dedup."""
    title = (envelope.raw_title or "").strip()
    body = (envelope.raw_text or "").strip()
    if title and body:
        return f"{title}\n{body}"
    return title or body


def scrubbed_analysis_text(original: str) -> str:
    scrubbed = scrub_pii(original)
    if contains_unscrubbed_pii(scrubbed):
        scrubbed = scrub_pii(scrubbed)
    return scrubbed


def expected_content_hash(envelope: RawEnvelope) -> str:
    return content_hash(scrubbed_analysis_text(envelope_text(envelope)))
