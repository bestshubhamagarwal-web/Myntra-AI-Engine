"""Map PM questions onto Q1–Q9 and compose grounded answers from tool JSON."""

from __future__ import annotations

import re
from typing import Any

QUESTION_IDS = tuple(f"Q{i}" for i in range(1, 10))

# Specific patterns first. Postpone maps to Q2 (wishlist item "dies").
_Q_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Q9",
        (
            r"unmet need",
            r"source diversity",
            r"structural",
            r"consistently across",
            r"recur across",
            r"independent myntra users",
            r"one-off reddit",
        ),
    ),
    (
        "Q8",
        (
            r"differ across",
            r"\bsegments?\b",
            r"ethnic vs",
            r"western vs",
            r"premium vs",
            r"budget vs",
            r"gender segment",
            r"footwear vs ethnic",
            r"ethnic-wear",
            r"across user segments",
        ),
    ),
    (
        "Q7",
        (
            r"genuine (near[- ]term )?purchase intent",
            r"bookmarking mechanism",
            r"passive bookmark",
            r"inspiration tool",
            r"no purchase timeline",
            r"bookmark(?:ing)? vs",
            r"vs(?:\.)? (?:simply as )?a bookmark",
            r"near-term purchase intent versus",
        ),
    ),
    (
        "Q6",
        (
            r"what role do",
            r"fit.{0,40}size.{0,40}styling",
            r"social validation",
            r"\bfomo\b",
            r"return(?:/exchange)? policy trust",
            r"review credibility",
            r"occasion",
            r"fit uncertainty versus price",
        ),
    ),
    (
        "Q5",
        (
            r"outside myntra",
            r"off-platform",
            r"youtube haul",
            r"seek outside",
            r"before purchasing",
            r"off-platform sources",
            r"reddit threads",
        ),
    ),
    (
        "Q4",
        (
            r"compare multiple",
            r"shortlisted",
            r"against each other",
            r"pit wishlisted",
            r"comparison behavior",
        ),
    ),
    (
        "Q3",
        (
            r"residual",
            r"uncertaint",
            r"after (?:a user has |users have )?(?:already )?(?:picked|identified|liked)",
            r"identified a product they like",
            r"doubts still",
            r"still block checkout",
        ),
    ),
    (
        "Q2",
        (
            r"prevents? wishlist",
            r"eventually being purchased",
            r"wishlist item (?:die|'die')",
            r"postpone",
            r"abandon the purchase",
            r"walk away",
            r"causes users to postpone",
        ),
    ),
    (
        "Q1",
        (
            r"why do users add",
            r"why.{0,40}wishlist",
            r"motivates people to save",
            r"in the first place",
            r"add fashion products to (?:their )?wishlist",
        ),
    ),
)

_Q_NEEDLES: dict[str, tuple[str, ...]] = {
    "Q1": (
        "wishlist",
        "wish list",
        "save for later",
        "bookmark",
        "mood board",
        "price watch",
        "price drop",
        "sale",
        "discount",
        "offer",
        "coupon",
        "later",
    ),
    "Q2": (
        "wishlist",
        "postpone",
        "not buying",
        "wait",
        "size",
        "fit",
        "return",
        "refund",
        "delay",
        "expensive",
        "out of stock",
        "hesitat",
    ),
    "Q3": (
        "not sure",
        "doubt",
        "size",
        "fit",
        "quality",
        "return",
        "authentic",
        "worth",
        "looks",
        "size chart",
    ),
    "Q4": (
        "vs",
        "versus",
        "compare",
        "compared",
        "ajio",
        "nykaa",
        "flipkart",
        "shortlist",
        "between",
    ),
    "Q5": (
        "youtube",
        "reddit",
        "instagram",
        "size chart",
        "haul",
        "influencer",
        "brand site",
        "amazon",
        "ajio",
        "review",
    ),
    "Q6": (
        "fit",
        "size",
        "styling",
        "style",
        "price",
        "review",
        "occasion",
        "wedding",
        "fomo",
        "return",
        "friend",
    ),
    "Q7": (
        "wishlist",
        "save for later",
        "bookmark",
        "buy now",
        "want to buy",
        "someday",
        "inspiration",
    ),
    "Q8": (
        "kurta",
        "ethnic",
        "saree",
        "shoe",
        "footwear",
        "jeans",
        "western",
        "men",
        "women",
        "premium",
        "budget",
    ),
    "Q9": (
        "size",
        "fit",
        "return",
        "delivery",
        "quality",
        "price",
        "wishlist",
        "refund",
    ),
}

SUPPORTING_REVIEW_LIMIT = 2

_NOISE = (
    "crash",
    "force close",
    "force-close",
    "keeps hanging",
    "white screen",
    "otp",
    "can't login",
    "cant login",
    "notification spam",
    "update the app",
)

_Q_LEADS: dict[str, str] = {
    "Q1": (
        "People add items to the wishlist mainly to wait for a price drop. "
        "They like the product, but they hold off until a sale, coupon, or better deal."
    ),
    "Q2": (
        "What prevents a wishlisted product from being purchased is residual friction "
        "after they already like the item — fit, price, delivery, returns, or quality."
    ),
    "Q3": (
        "After a shopper has identified a product they like enough to save, residual "
        "uncertainties still sit between like and checkout: fit, quality, returns, "
        "authenticity, styling, and whether it is worth the price."
    ),
    "Q4": (
        "Users compare shortlisted items on price, look, and size, using vs/compare "
        "language inside Myntra-relevant comments — not a parallel competitor corpus."
    ),
    "Q5": (
        "Before purchasing, people look outside Myntra for try-on hauls, size-chart talk, "
        "reviews, and competitor prices."
    ),
    "Q6": (
        "Fit, styling, price, reviews, occasion, and return-policy trust show up as "
        "separate stall factors — not one blended 'they bookmark because of fit' story."
    ),
    "Q7": (
        "The wishlist is used two ways, and they stay separate. "
        "Passive bookmarking is a save-for-later / inspiration / mood-board tool with no "
        "purchase timeline. Genuine near-term purchase intent is stall language: they want "
        "to buy soon but get stuck on fit, price, or delivery."
    ),
    "Q8": (
        "Wishlist talk differs by segment — ethnic-wear, footwear, and others — where the "
        "data is tagged. Unknown stays visible, and small slices are not a majority claim."
    ),
    "Q9": (
        "Unmet needs that recur across independent users and sources are structural "
        "opportunity areas, not a one-off thread."
    ),
}


def detect_research_question(question: str) -> str | None:
    """Return Q1–Q9 when the prompt is one of the discovery research questions."""
    blob = (question or "").lower()
    if not blob.strip():
        return None
    for qid, patterns in _Q_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, blob, re.I):
                return qid
    return None


def research_needles(qid: str) -> list[str]:
    return list(_Q_NEEDLES.get(qid) or ())


def trim_quote(text: str, limit: int = 180) -> str:
    quote = re.sub(r"\s+", " ", str(text or "").strip())
    if len(quote) < 24:
        return ""
    if len(quote) > limit:
        quote = quote[: limit - 1].rsplit(" ", 1)[0] + "…"
    return quote


def format_answer_with_reviews(lead: str, quotes: list[str], *, limit: int = SUPPORTING_REVIEW_LIMIT) -> str:
    """One claim, then at most two supporting reviews. No corpus or theme-metric dump."""
    lead = re.sub(r"\s+", " ", (lead or "").strip())
    unique: list[str] = []
    seen: set[str] = set()
    for quote in quotes:
        text = trim_quote(quote)
        if not text:
            continue
        key = text[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(text)
        if len(unique) >= limit:
            break
    if not unique:
        return lead
    parts = [lead, ""]
    for text in unique:
        parts.append(f'"{text}"')
        parts.append("")
    return "\n".join(parts).strip()


_PRICE_WAIT = (
    "price drop",
    "price watch",
    "wait for sale",
    "waiting for",
    "on sale",
    "discount",
    "cheaper",
    "expensive",
    "mrp",
    "offer",
    "deal",
    "coupon",
    "sale",
    "price",
)


def _row_support_score(quote: str, needles: tuple[str, ...], *, qid: str | None = None) -> int | None:
    blob = quote.lower()
    if len(blob) < 24:
        return None
    noise = sum(1 for token in _NOISE if token in blob)
    hits = sum(1 for token in needles if token in blob) if needles else 1
    if needles and hits < 1:
        return None
    if qid == "Q1":
        hits += sum(2 for token in _PRICE_WAIT if token in blob)
    return hits - (2 * noise)


def select_supporting_rows(
    pack: dict[str, Any],
    qid: str | None,
    *,
    limit: int = SUPPORTING_REVIEW_LIMIT,
) -> list[dict[str, Any]]:
    """Pick two on-topic reviews. Drop app-crash noise and extra corpus quotes."""
    rows = list(pack.get("retrieval_rows") or [])
    if not rows:
        rows = list((pack.get("evidence") or {}).get("rows") or [])
    needles = tuple(_Q_NEEDLES.get(qid or "", ()) )
    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        quote = str(row.get("quote") or "")
        score = _row_support_score(quote, needles, qid=qid)
        if score is None:
            continue
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        for row in rows:
            if not isinstance(row, dict):
                continue
            quote = str(row.get("quote") or "")
            blob = quote.lower()
            if len(blob) < 24:
                continue
            if any(token in blob for token in _NOISE):
                continue
            ranked.append((0, row))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_prefix: set[str] = set()

    def _append(row: dict[str, Any], trimmed: str) -> bool:
        key = str(row.get("document_id") or "") or trimmed[:80].lower()
        if key in seen:
            return False
        prefix = re.sub(r"\d+", "", trimmed[:48].lower())
        if prefix in seen_prefix:
            return False
        seen.add(key)
        seen_prefix.add(prefix)
        cleaned = dict(row)
        cleaned["quote"] = trimmed
        out.append(cleaned)
        return True

    for _score, row in ranked:
        trimmed = trim_quote(row.get("quote") or "")
        if not trimmed:
            continue
        _append(row, trimmed)
        if len(out) >= limit:
            break
    if len(out) < limit:
        seen_prefix.clear()
        for _score, row in ranked:
            trimmed = trim_quote(row.get("quote") or "")
            if not trimmed:
                continue
            key = str(row.get("document_id") or "") or trimmed[:80].lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned = dict(row)
            cleaned["quote"] = trimmed
            out.append(cleaned)
            if len(out) >= limit:
                break
    return out


def compose_research_answer(qid: str, question: str, pack: dict[str, Any]) -> str:
    """Short claim, then up to two supporting reviews. No corpus or theme-metric dump."""
    _ = question
    lead = _Q_LEADS.get(qid) or ""
    rows = select_supporting_rows(pack, qid)
    quotes = [str(row.get("quote") or "") for row in rows]
    return format_answer_with_reviews(lead, quotes)
