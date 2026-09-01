"""Load prompts/theme_label.md (version from HTML comment)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROMPT_FILENAME = "theme_label.md"
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / PROMPT_FILENAME
VERSION_RE = re.compile(r"version:\s*([a-zA-Z0-9._-]+)")


@dataclass(frozen=True)
class ThemeLabelPrompt:
    version: str
    body: str
    path: Path


def load_theme_label_prompt(path: Path | None = None) -> ThemeLabelPrompt:
    prompt_path = path or DEFAULT_PROMPT_PATH
    body = prompt_path.read_text(encoding="utf-8").strip()
    if not body:
        raise ValueError(f"theme label prompt at {prompt_path} is empty")
    match = VERSION_RE.search(body)
    version = match.group(1) if match else "theme_label.v1"
    return ThemeLabelPrompt(version=version, body=body, path=prompt_path)


def build_label_messages(
    prompt: ThemeLabelPrompt,
    *,
    quotes: list[str],
    friction_hist: dict[str, int],
    intent_mode_hist: dict[str, int],
    intent_tag_hist: dict[str, int],
    categories: dict[str, int],
    source_types: dict[str, int],
    member_count: int,
    retry_generic: bool = False,
) -> list[dict[str, str]]:
    quote_lines = "\n".join(f"- {span}" for span in quotes[:12]) or "- (no verbatim spans)"
    extra = ""
    if retry_generic:
        extra = (
            "\nThe previous name was too generic. Choose a specific Myntra "
            "wishlist/sizing/returns/price/comparison opportunity area from the quotes.\n"
        )
    user = (
        f"member_count: {member_count}\n"
        f"friction_tag_histogram: {friction_hist}\n"
        f"intent_tag_histogram: {intent_tag_hist}\n"
        f"intent_mode_histogram: {intent_mode_hist}\n"
        f"product_category_histogram: {categories}\n"
        f"source_type_histogram: {source_types}\n"
        f"{extra}"
        f"verbatim_quotes:\n{quote_lines}\n"
    )
    return [
        {"role": "system", "content": prompt.body},
        {"role": "user", "content": user},
    ]
