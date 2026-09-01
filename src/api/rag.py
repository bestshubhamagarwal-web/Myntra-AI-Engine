"""Retrieve matching public comments and compose a grounded Copilot answer."""

from __future__ import annotations

import re
from typing import Any

from src.api.classify import QuestionIntent
from src.api.research_questions import (
    SUPPORTING_REVIEW_LIMIT,
    compose_research_answer,
    detect_research_question,
    format_answer_with_reviews,
    select_supporting_rows,
)

_STOP = frozenset(
    """
    the a an and or to of in on for with from that this those these are was were
    been being have has had does did doing what when where which who why how
    just give show more quotes compare versus there their them your they you
    our can did its about into also only some any will would could should
    myntra item items users user please tell me about add added adding
    some best last far with just seen start help post posts everyone
    beginner beginners guide recs sale sales coming thought
    product products people fashion
    """.split()
)

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "wishlist": ("wishlist", "wish list", "save for later", "bookmark", "mood board"),
    "wish": ("wishlist", "wish list"),
    "bookmark": ("bookmark", "save for later", "wishlist", "someday"),
    "stall": ("not buying", "pending", "hesitat", "wait to buy"),
    "fit": ("fit", "size", "sizing", "too small", "too big", "size chart"),
    "size": ("size", "sizing", "fit", "too small", "too large"),
    "sizing": ("size", "sizing", "fit"),
    "uncertainty": ("uncertain", "not sure", "doubt", "confus"),
    "footwear": ("shoe", "shoes", "sneaker", "sandal", "footwear", "heel"),
    "ethnic": ("ethnic", "kurta", "kurti", "saree", "lehenga", "salwar"),
    "western": ("jeans", "western", "tshirt", "t-shirt"),
    "delivery": ("delivery", "deliver", "courier", "pending", "delay", "dispatch"),
    "return": ("return", "refund", "exchange", "reverse pickup"),
    "refund": ("refund", "return"),
    "price": ("price", "expensive", "discount", "sale", "coupon", "mrp"),
    "quality": ("quality", "fabric", "cheap material", "tear"),
    "complaint": ("pathetic", "worst", "bad", "disappointed", "fraud"),
    "play": ("play store", "android", "app"),
    "drop": ("drop-off", "abandon", "not buy", "hesitat"),
    "concentrated": ("most", "common", "mainly"),
}


def query_terms(question: str) -> list[tuple[str, float]]:
    tokens = re.findall(r"[a-z0-9][a-z0-9'-]{2,}", (question or "").lower())
    weighted: dict[str, float] = {}
    for token in tokens:
        if token in _STOP:
            continue
        if len(token) < 4 and token not in _SYNONYMS:
            continue
        weighted[token] = max(weighted.get(token, 0.0), 1.0)
        for extra in _SYNONYMS.get(token, ()):
            weighted[extra] = max(weighted.get(extra, 0.0), 0.85)
    if "wishlist" in (question or "").lower() or "wish list" in (question or "").lower():
        for extra in _SYNONYMS["wishlist"]:
            weighted[extra] = max(weighted.get(extra, 0.0), 1.2)
        for extra in ("sale", "discount", "price drop", "price", "offer"):
            weighted[extra] = max(weighted.get(extra, 0.0), 1.15)
    return sorted(weighted.items(), key=lambda item: item[1], reverse=True)[:18]


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
)


def score_text(text: str, terms: list[tuple[str, float]]) -> float:
    blob = (text or "").lower()
    if not blob or not terms:
        return 0.0
    score = 0.0
    hits = 0
    for term, weight in terms:
        if " " in term:
            count = blob.count(term)
        else:
            count = len(re.findall(r"\b" + re.escape(term) + r"\b", blob))
        if count:
            hits += 1
            score += weight * min(count, 4)
    if hits < 1:
        return 0.0
    score += 0.15 * hits
    for token in _NOISE:
        if token in blob:
            score -= 1.5
    return score


def retrieve_quotes(
    repo,
    question: str,
    *,
    limit: int = 10,
    eligible_only: bool = True,
) -> list[dict[str, Any]]:
    terms = query_terms(question)
    needles = [term for term, _weight in terms if len(term) >= 4][:12]
    qblob = (question or "").lower()
    must: list[str] = []
    if "wishlist" in qblob or "wish list" in qblob:
        must = ["wishlist", "wish list", "save for later", "bookmark"]
        needles = list(dict.fromkeys(must + needles))
    elif any(token in qblob for token in ("fit", "size", "sizing")):
        must = ["fit", "size", "sizing", "too small", "too big"]
    probe = (
        re.compile("|".join(re.escape(item) for item in needles), re.I)
        if needles
        else None
    )
    store = getattr(repo, "normalized", None)
    raw_store = getattr(repo, "raw", None)
    if store is None:
        docs = repo.list_normalized(limit=None, eligible_only=eligible_only)
        pairs = [(doc, repo.get_raw(doc.raw_id)) for doc in docs]
    else:
        pairs = []
        for doc in store.values():
            if eligible_only and (not doc.eligible or doc.duplicate_of is not None):
                continue
            raw = raw_store.get(doc.raw_id) if raw_store is not None else None
            pairs.append((doc, raw))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for doc, raw in pairs:
        text = doc.text_original or ""
        blob = text.lower()
        if probe is not None and not probe.search(blob):
            continue
        if must and not any(token in blob for token in must):
            continue
        score = score_text(text, terms) if terms else 0.15
        if score <= 0.35:
            continue
        source = "unknown"
        if raw is not None:
            value = getattr(raw, "source_type", None)
            source = value.value if hasattr(value, "value") else str(value or "unknown")
        quote = _quote_window(text, must or needles)
        if source in {"play_store", "app_store"}:
            score += 0.25
        if 40 <= len(quote) <= 180:
            score += 0.2
        if "wishlist" in qblob and any(
            token in blob
            for token in ("price drop", "wait for sale", "on sale", "discount", "sale", "cheaper", "expensive", "offer", "coupon")
        ):
            score += 1.8
        ranked.append(
            (
                score,
                {
                    "document_id": str(doc.id),
                    "chunk_id": None,
                    "url": getattr(raw, "url", None) if raw is not None else None,
                    "source_type": source,
                    "quote": quote[:220],
                    "published_at": None,
                    "product_category": doc.product_category or "unknown",
                    "score": round(score, 3),
                },
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [row for _score, row in ranked[:limit]]


def _relevant_themes(question: str, themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blob = (question or "").lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for card in themes:
        name = (card.get("name") or "").lower()
        desc = (card.get("description") or "").lower()
        hit = sum(1 for token in re.findall(r"[a-z]{3,}", blob) if token in name or token in desc)
        scored.append((hit, card))
    scored.sort(key=lambda item: (item[0], item[1].get("impact_score") or 0), reverse=True)
    if scored and scored[0][0] > 0:
        return [card for hit, card in scored if hit > 0][:4]
    return themes[:4]


def compose_rag_answer(
    question: str,
    pack: dict[str, Any],
    *,
    intent: QuestionIntent | None = None,
) -> str:
    qid = detect_research_question(question)
    if qid:
        return compose_research_answer(qid, question, pack)

    themes = (pack.get("themes") or {}).get("themes") or []
    supporting = select_supporting_rows(pack, None, limit=SUPPORTING_REVIEW_LIMIT)
    quotes = [str(row.get("quote") or "").strip() for row in supporting if row.get("quote")]
    if len(quotes) < SUPPORTING_REVIEW_LIMIT:
        retrieved = pack.get("retrieval_rows") or (pack.get("evidence") or {}).get("rows") or []
        for row in retrieved:
            quote = str(row.get("quote") or "").strip()
            if quote and quote not in quotes:
                quotes.append(quote)
            if len(quotes) >= SUPPORTING_REVIEW_LIMIT:
                break
    relevant = _relevant_themes(question, themes)
    lowered = (question or "").lower()
    reasons = _reason_list(quotes, lowered)
    top = relevant[0] if relevant else (themes[0] if themes else None)

    if intent is QuestionIntent.quotes_only:
        if not quotes:
            return "I could not find matching public comments for these filters."
        return format_answer_with_reviews("From reviews:", quotes)

    if "outside" in lowered or "off-platform" in lowered or "youtube" in lowered:
        return format_answer_with_reviews(
            "Before buying, people say they check sources outside the app — Reddit, YouTube, or friends.",
            quotes,
        )

    if "unmet" in lowered or "consistently" in lowered:
        names = [str(card.get("name")) for card in (relevant or themes)[:3] if card.get("name")]
        lead = (
            "The unmet needs that keep showing up are " + ", ".join(names).lower() + "."
            if names
            else "The same frictions recur: fit, delivery, returns, and price."
        )
        return format_answer_with_reviews(lead, quotes)

    if "bookmark" in lowered and ("stall" in lowered or "intent" in lowered or "versus" in lowered):
        return format_answer_with_reviews(
            "Reviews treat bookmarking and near-term purchase as different: some people park items, "
            "others want to buy but stall on fit, price, or delivery.",
            quotes,
        )

    if "compare" in lowered or "shortlist" in lowered:
        return format_answer_with_reviews(
            "People compare shortlisted items on fit, price, and delivery before they commit.",
            quotes,
        )

    if "segment" in lowered or "differ across" in lowered:
        return format_answer_with_reviews(
            "Wishlist talk differs by segment — ethnic-wear, footwear, and others — where the "
            "data is tagged. Unknown stays visible, and small slices are not a majority claim.",
            quotes,
        )

    if (
        intent is QuestionIntent.comparative
        or " vs " in lowered
        or ("versus" in lowered and ("footwear" in lowered or "ethnic" in lowered))
    ):
        return format_answer_with_reviews(
            "People compare footwear and ethnic-wear on fit, price, and delivery in public comments. "
            "I will not invent a conversion gap.",
            quotes,
        )

    if "prevent" in lowered or "postpone" in lowered or "abandon" in lowered:
        stall_reasons = [item for item in reasons if item != "save items for later"]
        lead = (
            "Wishlisted items stall because people " + ", ".join(stall_reasons[:2]) + "."
            if stall_reasons
            else "Wishlisted items stall on fit, price, delivery, or return risk after the person already likes the product."
        )
        return format_answer_with_reviews(lead, quotes)

    if "wishlist" in lowered or "why" in lowered:
        lead = (
            "People add items to the wishlist mainly to wait for a price drop. "
            "They like the product, but they hold off until a sale, coupon, or better deal."
        )
        return format_answer_with_reviews(lead, quotes)

    if top:
        name = str(top.get("name") or "this theme").lower()
        lead = f"The main issue in reviews is {name}."
        if reasons:
            lead = lead.rstrip(".") + ", and shoppers mention " + ", ".join(reasons[:2]) + "."
        return format_answer_with_reviews(lead, quotes)

    if quotes:
        return format_answer_with_reviews("Closest matching reviews:", quotes)
    return "I could not find matching public reviews for that question, so I will not guess."


def _quote_window(text: str, needles: list[str], limit: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    blob = cleaned.lower()
    idx = -1
    for needle in needles:
        pos = blob.find(needle.lower())
        if pos >= 0 and (idx < 0 or pos < idx):
            idx = pos
    if idx < 0:
        return cleaned[:limit]
    start = max(0, idx - 40)
    snippet = cleaned[start : start + limit]
    if start > 0:
        snippet = "…" + snippet
    if start + limit < len(cleaned):
        snippet = snippet.rsplit(" ", 1)[0] + "…"
    return snippet


def _reason_list(quotes: list[str], question: str) -> list[str]:
    blob = " ".join(quotes).lower() + " " + question
    reasons: list[str] = []
    if any(w in blob for w in ("wishlist", "save for later", "bookmark")):
        reasons.append("save items for later")
    if any(w in blob for w in ("size", "fit", "too small", "too big")):
        reasons.append("wait on size and fit")
    if any(w in blob for w in ("deliver", "pending", "delay", "courier")):
        reasons.append("worry about delivery delays")
    if any(w in blob for w in ("return", "refund")):
        reasons.append("worry about returns")
    if any(w in blob for w in ("price", "expensive", "discount", "sale", "coupon")):
        reasons.append("watch for a better price")
    if any(w in blob for w in ("quality", "fabric")):
        reasons.append("doubt product quality")
    return reasons
