"""Keyword extraction when Groq is not configured. Tags stay in the frozen schema."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.extract.pipeline import record_from_payload
from src.extract.schema import (
    ComparisonBehavior,
    ExtractionEntities,
    ExtractionPayload,
    FrictionTag,
    IntentMode,
    IntentTag,
    OffPlatformChannel,
    ResidualUncertainty,
    SentimentBlock,
    SentimentPrimary,
    VerbatimQuote,
)
from src.normalize.category import infer_product_category
from src.normalize.pii import scrub_pii

FRICTION_PATTERNS: list[tuple[FrictionTag, re.Pattern[str]]] = [
    (
        FrictionTag.fit_uncertainty,
        re.compile(
            r"\b(size|sizing|size chart|too small|too big|too large|tight|loose|"
            r"doesn'?t fit|dont fit|not fit|wrong size|fit issue|fitting|"
            r"runs small|runs large|length|waist|bust)\b",
            re.I,
        ),
    ),
    (
        FrictionTag.delivery_or_availability,
        re.compile(
            r"\b(deliver(?:y|ed)?|dispatch|courier|pending|delay(?:ed)?|late|"
            r"not arrived|out of stock|unavailable|15 days|two weeks|2 weeks|"
            r"order stuck|never came)\b",
            re.I,
        ),
    ),
    (
        FrictionTag.return_risk,
        re.compile(
            r"\b(return|refund|exchange|reverse pickup|replacement|"
            r"money back|not refund)\b",
            re.I,
        ),
    ),
    (
        FrictionTag.price_sensitivity,
        re.compile(
            r"\b(price|priced|expensive|cheap|costly|discount|sale|coupon|"
            r"mrp|₹|rs\.?|offer|cashback|overpriced)\b",
            re.I,
        ),
    ),
    (
        FrictionTag.quality_doubt,
        re.compile(
            r"\b(quality|fabric|cheap material|tear|torn|fade|faded|stich|"
            r"stitch|colour fade|color fade|poor quality|duplicate look)\b",
            re.I,
        ),
    ),
    (
        FrictionTag.authenticity,
        re.compile(r"\b(fake|original|genuine|authentic|duplicate product|knock[- ]off)\b", re.I),
    ),
    (
        FrictionTag.policy_trust,
        re.compile(
            r"\b(customer care|customer support|not responding|no one (?:is )?respond|"
            r"pathetic|worst (?:app|service)|fraud|scam|cheat)\b",
            re.I,
        ),
    ),
    (
        FrictionTag.comparison_paralysis,
        re.compile(r"\b(vs\.?|versus|compared?|ajio|nykaa|flipkart|meesho|amazon)\b", re.I),
    ),
    (
        FrictionTag.review_credibility,
        re.compile(r"\b(fake review|paid review|review (?:is )?fake)\b", re.I),
    ),
    (
        FrictionTag.styling_doubt,
        re.compile(r"\b(style|styling|looks cheap|doesn'?t look|not as shown|photo)\b", re.I),
    ),
    (
        FrictionTag.social_validation,
        re.compile(
            r"\b(fomo|viral|everyone(?:'s| is) (?:buying|wearing)|friend(?:s)? (?:said|told|recommended)|"
            r"instagram|influencer)\b",
            re.I,
        ),
    ),
]

WISHLIST_RE = re.compile(
    r"\b(wishlist|wish list|wish-list|save for later|saved for later|"
    r"bookmark|mood board|add(?:ed|ing)? to (?:the )?wish)\b",
    re.I,
)
APP_RE = re.compile(
    r"\b(app crash|crashes|login|otp|payment fail|cart|checkout|notification)\b",
    re.I,
)
NEGATIVE_RE = re.compile(
    r"\b(worst|pathetic|hate|useless|fraud|scam|never|don'?t buy|waste|"
    r"disappointed|horrible|terrible|bad experience)\b",
    re.I,
)
POSITIVE_RE = re.compile(
    r"\b(love|great|amazing|best app|good quality|happy|excellent)\b", re.I
)
PASSIVE_RE = re.compile(
    r"mood\s*board|someday|maybe later|inspiration|bookmark|"
    r"not buying now|saving (?:this )?for later|wishlist",
    re.I,
)
NEAR_RE = re.compile(
    r"(?<!not )(?<!n't )\b(?:buying now|buy now|order now|checkout|need it (?:for|by)|want to buy)\b",
    re.I,
)


def _quote_span(text: str, match: re.Match[str] | None) -> VerbatimQuote:
    cleaned = scrub_pii(text or "")
    if match:
        start = max(0, match.start() - 80)
        end = min(len(cleaned), match.end() + 120)
        span = cleaned[start:end].strip()
        if span:
            return VerbatimQuote(span=span[:280], start_char=start, end_char=min(end, start + len(span)))
    snippet = cleaned.strip()[:220]
    return VerbatimQuote(span=snippet, start_char=0, end_char=len(snippet) if snippet else 0)


def extract_payload(text: str) -> ExtractionPayload:
    body = text or ""
    frictions: list[FrictionTag] = []
    first_match: re.Match[str] | None = None
    for tag, pattern in FRICTION_PATTERNS:
        found = pattern.search(body)
        if found:
            frictions.append(tag)
            if first_match is None:
                first_match = found
    wish = WISHLIST_RE.search(body)
    if wish and first_match is None:
        first_match = wish
    if APP_RE.search(body) and FrictionTag.other not in frictions and not frictions:
        frictions.append(FrictionTag.other)
    if not frictions:
        frictions = [FrictionTag.other]

    if wish and NEAR_RE.search(body):
        intent_mode = IntentMode.mixed
        intent_tag = IntentTag.indecision_parking
    elif wish or PASSIVE_RE.search(body):
        intent_mode = IntentMode.passive_bookmark
        intent_tag = IntentTag.save_for_later if wish else IntentTag.bookmark
    elif NEAR_RE.search(body) or any(
        tag in {FrictionTag.fit_uncertainty, FrictionTag.return_risk, FrictionTag.delivery_or_availability}
        for tag in frictions
    ):
        intent_mode = IntentMode.near_term_purchase
        intent_tag = IntentTag.unknown
    else:
        intent_mode = IntentMode.unknown
        intent_tag = IntentTag.unknown

    if NEGATIVE_RE.search(body) and not POSITIVE_RE.search(body):
        sentiment = SentimentBlock(primary=SentimentPrimary.frustration, severity=0.75)
    elif POSITIVE_RE.search(body) and NEGATIVE_RE.search(body):
        sentiment = SentimentBlock(primary=SentimentPrimary.mixed, severity=0.45)
    elif POSITIVE_RE.search(body):
        sentiment = SentimentBlock(primary=SentimentPrimary.delight, severity=0.15)
    elif frictions and frictions[0] != FrictionTag.other:
        sentiment = SentimentBlock(primary=SentimentPrimary.doubt, severity=0.5)
    else:
        sentiment = SentimentBlock(primary=SentimentPrimary.neutral, severity=0.2)

    residuals: list[ResidualUncertainty] = []
    if FrictionTag.fit_uncertainty in frictions:
        residuals.append(ResidualUncertainty.fit)
    if FrictionTag.quality_doubt in frictions:
        residuals.append(ResidualUncertainty.quality)
    if FrictionTag.return_risk in frictions:
        residuals.append(ResidualUncertainty.returns)
    if FrictionTag.price_sensitivity in frictions:
        residuals.append(ResidualUncertainty.value_for_money)
    if FrictionTag.styling_doubt in frictions:
        residuals.append(ResidualUncertainty.styling)
    if FrictionTag.authenticity in frictions:
        residuals.append(ResidualUncertainty.authenticity)

    comparison = ComparisonBehavior.true if FRICTION_PATTERNS[7][1].search(body) else ComparisonBehavior.unknown
    category = infer_product_category(body)
    quote = _quote_span(body, first_match or wish)
    off_platform: list[OffPlatformChannel] = []
    if re.search(r"\byoutube\b", body, re.I):
        off_platform.append(OffPlatformChannel.youtube)
    if re.search(r"\breddit\b", body, re.I):
        off_platform.append(OffPlatformChannel.reddit)
    if re.search(r"size chart", body, re.I):
        off_platform.append(OffPlatformChannel.size_chart)
    if re.search(r"\b(instagram|influencer)\b", body, re.I):
        off_platform.append(OffPlatformChannel.influencer)
    if re.search(r"official (?:site|website)|brand site", body, re.I):
        off_platform.append(OffPlatformChannel.brand_site)
    if re.search(r"\b(ajio|nykaa|flipkart|meesho|amazon)\b", body, re.I):
        off_platform.append(OffPlatformChannel.competitor_app)

    occasion = None
    occ = re.search(r"\b(wedding|festive|festival|office|party|occasion|date night)\b", body, re.I)
    if occ:
        occasion = occ.group(1).lower()

    questions: list[str] = []
    if wish or intent_tag in {
        IntentTag.save_for_later,
        IntentTag.bookmark,
        IntentTag.mood_board,
        IntentTag.price_watch,
        IntentTag.indecision_parking,
    }:
        questions.extend(["Q1", "Q7"])
    if any(
        tag
        in {
            FrictionTag.fit_uncertainty,
            FrictionTag.price_sensitivity,
            FrictionTag.delivery_or_availability,
            FrictionTag.return_risk,
            FrictionTag.quality_doubt,
        }
        for tag in frictions
    ):
        questions.append("Q2")
    if residuals:
        questions.append("Q3")
    if comparison == ComparisonBehavior.true or FrictionTag.comparison_paralysis in frictions:
        questions.append("Q4")
    if off_platform:
        questions.append("Q5")
    if any(
        tag
        in {
            FrictionTag.fit_uncertainty,
            FrictionTag.styling_doubt,
            FrictionTag.price_sensitivity,
            FrictionTag.review_credibility,
            FrictionTag.social_validation,
            FrictionTag.policy_trust,
        }
        for tag in frictions
    ) or occasion:
        questions.append("Q6")
    if wish:
        questions.append("Q7")
    questions = list(dict.fromkeys(questions))

    return ExtractionPayload(
        intent_tag=intent_tag,
        intent_mode=intent_mode,
        friction_tag=frictions,
        residual_uncertainties=residuals,
        comparison_behavior=comparison,
        off_platform_info_seeking=off_platform,
        entities=ExtractionEntities(
            category=None if category == "unknown" else category,
            occasion=occasion,
            size_fit_mentioned=FrictionTag.fit_uncertainty in frictions,
            price_mentioned=FrictionTag.price_sensitivity in frictions,
            competitor_mentions=["AJIO"] if re.search(r"\bajio\b", body, re.I) else [],
        ),
        sentiment=sentiment,
        verbatim_quotes=[quote] if quote.span else [],
        maps_to_questions=questions,
        extraction_confidence=0.48,
    )


@dataclass
class HeuristicExtractResult:
    ok: int
    skipped: int


def run_heuristic_extract(repo, *, force: bool = False) -> HeuristicExtractResult:
    """Tag every eligible normalized doc. Does not call Groq."""
    from src.db.repository import ExtractRun
    from src.timeutil import utcnow
    from uuid import uuid4

    run = ExtractRun(
        id=uuid4(),
        started_at=utcnow(),
        status="running",
        prompt_version="heuristic.v1",
        groq_model=None,
    )
    repo.start_extract_run(run)
    ok = 0
    skipped = 0
    store = getattr(repo, "normalized", None)
    if store is not None:
        docs = [d for d in store.values() if d.eligible and d.duplicate_of is None]
    else:
        docs = repo.list_normalized(limit=None, eligible_only=True)
    for document in docs:
        existing = repo.get_extraction(document.id)
        if (
            existing
            and existing.extraction_status == "ok"
            and existing.prompt_version != "heuristic.v1"
            and not force
        ):
            skipped += 1
            continue
        payload = extract_payload(document.text_original or "")
        record = record_from_payload(
            document,
            payload,
            prompt_version="heuristic.v1",
            groq_model=None,
            status="ok",
            raw_response="heuristic",
            error_message=None,
            retry_count=0,
            prompt_tokens=0,
            completion_tokens=0,
        )
        repo.upsert_extraction(record)
        repo.set_normalized_intent_mode(document.id, record.intent_mode)
        category = payload.entities.category
        if category and store is not None:
            live = store.get(document.id)
            if live is not None and (not live.product_category or live.product_category == "unknown"):
                live.product_category = category
        ok += 1
    run.status = "success"
    run.finished_at = utcnow()
    run.rows_ok = ok
    run.rows_skipped = skipped
    run.rows_failed = 0
    repo.finish_extract_run(run)
    return HeuristicExtractResult(ok=ok, skipped=skipped)
