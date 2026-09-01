from __future__ import annotations

import re

DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
LATIN_RE = re.compile(r"[A-Za-z]")

HINGLISH_LEXICON = {
    "hai",
    "hain",
    "nahi",
    "nahin",
    "acha",
    "accha",
    "achha",
    "bhai",
    "yaar",
    "mat",
    "karo",
    "kiya",
    "gaya",
    "mein",
    "ka",
    "ki",
    "ke",
    "se",
    "aur",
    "bahut",
    "bohot",
    "thoda",
    "zyada",
    "chhota",
    "chota",
    "bada",
    "sahi",
    "galat",
    "bakwas",
    "bekar",
    "mast",
    "theek",
    "thik",
    "paise",
    "kapde",
    "kapda",
    "pehen",
    "pehna",
    "daala",
    "dala",
    "wala",
    "wali",
    "kya",
    "kyun",
    "bilkul",
    "ekdum",
}


def detect_language(text: str | None) -> str:
    """Return en | hi | hinglish | other. Never replace the original string."""
    if not text or not text.strip():
        return "other"
    has_deva = bool(DEVANAGARI_RE.search(text))
    has_latin = bool(LATIN_RE.search(text))
    if has_deva and has_latin:
        return "hinglish"
    if has_deva:
        return "hi"
    tokens = re.findall(r"[A-Za-z']+", text.lower())
    hits = sum(1 for t in tokens if t in HINGLISH_LEXICON)
    if hits >= 2 or (hits >= 1 and any(t in {"chhota", "chota", "daala", "bakwas", "bekar"} for t in tokens)):
        return "hinglish"
    if has_latin:
        return "en"
    return "other"
