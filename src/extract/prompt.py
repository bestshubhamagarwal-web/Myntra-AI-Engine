"""Versioned extraction prompt (prompts/extract.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.db.repository import NormalizedRecord

PROMPT_FILENAME = "extract.json"
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / PROMPT_FILENAME


@dataclass(frozen=True)
class ExtractPrompt:
    version: str
    system: str
    user_template: str
    path: Path


def load_extract_prompt(path: Path | None = None) -> ExtractPrompt:
    prompt_path = path or DEFAULT_PROMPT_PATH
    if path is not None and not path.exists():
        prompt_path = DEFAULT_PROMPT_PATH
    data = json.loads(prompt_path.read_text(encoding="utf-8"))
    version = str(data.get("version") or "").strip()
    system = str(data.get("system") or "").strip()
    template = str(data.get("user_template") or "").strip()
    if not version or not system or not template:
        raise ValueError(f"extract prompt at {prompt_path} is missing version/system/user_template")
    return ExtractPrompt(version=version, system=system, user_template=template, path=prompt_path)


def gloss_block(document: NormalizedRecord) -> str:
    """Hinglish/Hindi gloss is Groq input only — never a replacement for text_original."""
    lang = (document.language or "").lower()
    if lang not in {"hi", "hinglish"}:
        return ""
    lines = [
        "Note: this is Hindi/Hinglish. Extract from text_original. Do not translate quotes.",
        "Any english_gloss is optional context for you only.",
    ]
    if document.text_en and document.text_en.strip():
        lines.append("english_gloss_for_model_only:")
        lines.append(document.text_en.strip())
    return "\n".join(lines) + "\n"


def build_extract_messages(document: NormalizedRecord, prompt: ExtractPrompt) -> list[dict[str, str]]:
    user = prompt.user_template.format(
        language=document.language,
        category=document.product_category,
        gloss_block=gloss_block(document),
        text_original=document.text_original,
    )
    return [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": user},
    ]
