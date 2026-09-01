"""Rules router for Copilot (Architecture §11.2). Groq never invents the route."""

from __future__ import annotations

import re
from enum import Enum

SOLUTION_RE = re.compile(
    r"\b(prd|write a spec|feature spec|what should (we|myntra) (build|ship|add|fix)|"
    r"build (a |an |the )?(fit )?widget|recommend (a |an |the )?(feature|widget|fix)|"
    r"how (can|should) myntra (fix|solve)|solution design)\b",
    re.IGNORECASE,
)
FUNNEL_RE = re.compile(
    r"\b(funnel conversion|ios conversion|conversion rate|internal analytics|"
    r"session replay|yesterday'?s conversion)\b",
    re.IGNORECASE,
)
AJIO_CORPUS_RE = re.compile(
    r"\b((how does|how is) ajio|ajio'?s? wishlist|ajio conversion)\b",
    re.IGNORECASE,
)
COMPARATIVE_RE = re.compile(
    r"\b(vs\.?|versus|compar(e|ison)|footwear.*ethnic|ethnic.*footwear|drop-off)\b",
    re.IGNORECASE,
)
QUANT_RE = re.compile(
    r"\b(%|percent|percentage|share of voice|sov|how many|what (fraction|share|percent)|"
    r"mention count|data_confidence)\b",
    re.IGNORECASE,
)
WHY_RE = re.compile(r"\b(why|reason|what makes|how come)\b", re.IGNORECASE)
QUOTES_RE = re.compile(r"\b((just )?(give|show)( me)? (more )?quotes|quotes only)\b", re.IGNORECASE)
INJECTION_RE = re.compile(
    r"\b(ignore (your )?tools|sov is 90|share of voice is 90)\b",
    re.IGNORECASE,
)


class QuestionIntent(str, Enum):
    refuse_solution = "refuse_solution"
    refuse_competitor_corpus = "refuse_competitor_corpus"
    decline_internal = "decline_internal"
    quotes_only = "quotes_only"
    comparative = "comparative"
    quantitative = "quantitative"
    qualitative = "qualitative"


def classify_question(question: str) -> QuestionIntent:
    text = (question or "").strip()
    if SOLUTION_RE.search(text):
        return QuestionIntent.refuse_solution
    if AJIO_CORPUS_RE.search(text):
        return QuestionIntent.refuse_competitor_corpus
    if FUNNEL_RE.search(text):
        return QuestionIntent.decline_internal
    if QUOTES_RE.search(text):
        return QuestionIntent.quotes_only
    if COMPARATIVE_RE.search(text):
        return QuestionIntent.comparative
    if QUANT_RE.search(text):
        return QuestionIntent.quantitative
    if WHY_RE.search(text):
        return QuestionIntent.qualitative
    return QuestionIntent.qualitative


def is_tool_injection(question: str) -> bool:
    return bool(INJECTION_RE.search(question or ""))
