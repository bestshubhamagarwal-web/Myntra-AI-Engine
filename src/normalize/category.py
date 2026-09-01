from __future__ import annotations

import re

CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ethnic", re.compile(r"\b(kurta|kurti|lehenga|saree|sari|salwar|anarkali|ethnic|sherwani)\b", re.I)),
    ("footwear", re.compile(r"\b(shoe|shoes|sneaker|sneakers|sandal|sandals|heel|heels|footwear|mojari)\b", re.I)),
    ("dresses", re.compile(r"\b(dress|dresses|gown|gowns)\b", re.I)),
    ("accessories", re.compile(r"\b(bag|bags|jewellery|jewelry|earring|watch|accessory|accessories)\b", re.I)),
    ("beauty-adjacent", re.compile(r"\b(lipstick|makeup|beauty|kajal|foundation)\b", re.I)),
    ("western", re.compile(r"\b(jeans|western|crop top|t-?shirt|jogger)\b", re.I)),
]

GENDER_EXPLICIT = [
    ("men", re.compile(r"\b(for men|men's|menswear|male)\b", re.I)),
    ("women", re.compile(r"\b(for women|women's|womenswear|female)\b", re.I)),
]


def infer_product_category(text: str | None) -> str:
    body = text or ""
    for name, pattern in CATEGORY_PATTERNS:
        if pattern.search(body):
            return name
    return "unknown"


def infer_gender_segment(text: str | None) -> str:
    body = text or ""
    hits = [name for name, pattern in GENDER_EXPLICIT if pattern.search(body)]
    gift_female = bool(re.search(r"\b(wife|girlfriend)\b", body, re.I))
    gift_male = bool(re.search(r"\b(husband|boyfriend)\b", body, re.I))
    if "men" in hits and gift_female:
        return "unknown"
    if "women" in hits and gift_male:
        return "unknown"
    if len(hits) == 1:
        return hits[0]
    return "unknown"


PREMIUM_PRICE = re.compile(
    r"\b(premium|luxury|expensive|costly|overpriced|high[- ]end|designer)\b",
    re.I,
)
BUDGET_PRICE = re.compile(
    r"\b(budget|cheap|cheaper|affordable|inexpensive|value for money|"
    r"discount|discounts|coupon|sale|under ?(?:rs\.?|inr|₹)\s*\d+)\b",
    re.I,
)
MID_PRICE = re.compile(r"\b(mid[- ]range|mid[- ]tier|midrange)\b", re.I)


def infer_price_tier(text: str | None) -> str:
    """Budget / mid / premium from price mentions. unknown if absent or mixed."""
    body = text or ""
    premium = bool(PREMIUM_PRICE.search(body))
    budget = bool(BUDGET_PRICE.search(body))
    mid = bool(MID_PRICE.search(body))
    hits = [name for name, flag in (("premium", premium), ("budget", budget), ("mid", mid)) if flag]
    if len(hits) == 1:
        return hits[0]
    return "unknown"
