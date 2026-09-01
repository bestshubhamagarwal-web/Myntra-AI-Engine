"""Hybrid Copilot: metrics tools first, BGE retrieval second, Groq grounded generation."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from src.api.classify import QuestionIntent, classify_question, is_tool_injection
from src.api.filters import GlobalFilters
from src.api.grounding import compact_tool_pack, dump_tools, numbers_subset_of_tools
from src.api.query import QueryService
from src.api.rag import compose_rag_answer, retrieve_quotes
from src.api.research_questions import (
    SUPPORTING_REVIEW_LIMIT,
    compose_research_answer,
    detect_research_question,
    select_supporting_rows,
)
from src.config import Settings, load_settings
from src.db.repository import ChatMessage, ChatSession, DocumentRepository
from src.embed.bge import encode_query, load_bge_model, query_text_for_model
from src.extract.groq_client import (
    GroqAuthError,
    GroqConfigError,
    GroqJsonResult,
    GroqRateLimitError,
    GroqRetryableError,
    GroqToolResult,
    groq_complete_text,
    groq_complete_tools,
)
from src.metrics.formulas import confidence_band
from src.normalize.pii import scrub_pii
from src.timeutil import utcnow

logger = logging.getLogger(__name__)

EmbedFn = Callable[[str], list[float]]
CompleteToolsFn = Callable[..., GroqToolResult]

REFUSE_SOLUTION = (
    "This engine discovers stated user problems in public conversation. "
    "Product solution design is out of scope — I will not recommend features, "
    "widgets, or write a PRD."
)
REFUSE_AJIO = (
    "AJIO is not a parallel corpus in this engine. Competitor brands appear only "
    "as mentions inside Myntra-relevant documents. I will not describe AJIO's "
    "wishlist conversion."
)
DECLINE_INTERNAL = (
    "I do not have Myntra internal analytics (funnel, iOS conversion, session replay). "
    "I can only report public conversation evidence with citations."
)
DECLINE_THIN = (
    "Evidence is too thin to quantify (data_confidence below 0.35 or empty slice). "
    "I will not invent a percentage. Ask for quotes if you want examples."
)
DECLINE_EMPTY = (
    "No documents match these filters. I will not drop the filters or fill with "
    "typical patterns."
)

COPILOT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_metrics_overview",
            "description": "Corpus counts, unavailable sources, intent mix. Call for any quantitative question.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics_themes",
            "description": "Ranked opportunity areas with SoV, mention_count, confidence, impact from theme_metrics.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics_segments",
            "description": "Theme × segment cross-tab. Use for footwear vs ethnic comparisons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": [
                            "product_category",
                            "source_type",
                            "gender_segment",
                            "price_tier",
                            "platform_used",
                        ],
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_evidence",
            "description": "Filterable quotes with document_id and URL. Not for computing SoV.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_chunks",
            "description": "BGE vector search over chunks. Qualitative why. Do not use for SoV.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "friction_tag": {"type": "string"},
                    "intent_mode": {"type": "string"},
                },
            },
        },
    },
]


def load_copilot_prompt(settings: Settings) -> str:
    path = settings.copilot_prompt_path
    if path is None:
        path = Path(__file__).resolve().parents[2] / "prompts" / "copilot_system.md"
    return Path(path).read_text(encoding="utf-8")


def _citations_from_evidence(rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        doc = str(row.get("document_id") or "")
        if not doc or doc in seen:
            continue
        seen.add(doc)
        out.append(
            {
                "document_id": doc,
                "chunk_id": row.get("chunk_id"),
                "url": row.get("url"),
                "source_type": row.get("source_type") or "unknown",
                "quote": row.get("quote") or "",
                "published_at": row.get("published_at"),
            }
        )
        if len(out) >= limit:
            break
    return out


def _citations_from_chunks(chunks, limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for chunk in chunks[:limit]:
        published = chunk.published_at.date().isoformat() if chunk.published_at else None
        out.append(
            {
                "document_id": str(chunk.document_id),
                "chunk_id": str(chunk.id),
                "url": None,
                "source_type": chunk.source_type or "unknown",
                "quote": (chunk.text or "")[:280],
                "published_at": published,
            }
        )
    return out


class CopilotService:
    def __init__(
        self,
        repo: DocumentRepository,
        settings: Settings | None = None,
        *,
        embed_query: EmbedFn | None = None,
        complete_tools: CompleteToolsFn | None = None,
    ) -> None:
        self.repo = repo
        self.settings = settings or load_settings()
        self.query = QueryService(repo, self.settings)
        self._embed_query = embed_query
        self._complete_tools = complete_tools
        self._bge_model = None

    def _groq_ready(self) -> bool:
        if self._complete_tools is not None:
            return True
        return bool((self.settings.groq_api_key or "").strip())

    def embed_query_text(self, text: str) -> list[float]:
        """Same BGE-M3 checkpoint as chunks. M3 has no instruction prefix (EV-5-29)."""
        if self._embed_query is not None:
            _ = query_text_for_model(text, self.settings.bge_model_id)
            return self._embed_query(text)
        if self._bge_model is None:
            self._bge_model = load_bge_model(self.settings)
        return encode_query(
            self._bge_model,
            text,
            model_id=self.settings.bge_model_id,
            expected_dim=self.settings.embedding_dim,
        )

    def _run_tool(self, name: str, args: dict[str, Any], filters: GlobalFilters) -> Any:
        if name == "get_metrics_overview":
            return self.query.overview(filters)
        if name == "get_metrics_themes":
            return self.query.themes(filters)
        if name == "get_metrics_segments":
            dimension = args.get("dimension") or "product_category"
            return self.query.segments(filters, dimension=str(dimension))
        if name == "get_evidence":
            return self.query.evidence(filters)
        if name == "search_chunks":
            query_text = str(args.get("query") or "")
            if not query_text.strip():
                return {"chunks": [], "filters_kept": True}
            try:
                vector = self.embed_query_text(query_text)
            except Exception as exc:  # noqa: BLE001 — retrieval is optional
                logger.warning("copilot BGE retrieve skipped: %s", exc)
                return {
                    "chunks": [],
                    "filters_kept": True,
                    "embed_error": "vector search unavailable; using tagged quotes only",
                }
            chunks = self.repo.nearest_chunks(
                vector,
                k=self.settings.copilot_max_chunks,
                friction_tag=args.get("friction_tag") or filters.friction_tag,
                intent_mode=args.get("intent_mode") or filters.intent_mode,
                product_category=filters.product_category,
                source_type=filters.source_type,
                date_from=filters.date_from,
                date_to=filters.date_to,
            )
            return {
                "filters_kept": True,
                "chunks": [
                    {
                        "chunk_id": str(c.id),
                        "document_id": str(c.document_id),
                        "quote": (c.text or "")[:280],
                        "source_type": c.source_type,
                        "intent_mode": c.intent_mode,
                        "friction_tags": c.friction_tags,
                        "similarity": c.similarity,
                    }
                    for c in chunks
                ],
            }
        return {"error": f"unknown tool {name}"}

    def _safe_tool(
        self, name: str, args: dict[str, Any], filters: GlobalFilters, used: list[str]
    ) -> Any:
        try:
            payload = self._run_tool(name, args, filters)
        except Exception as exc:  # noqa: BLE001 — Copilot must still answer
            logger.warning("copilot tool %s failed: %s", name, exc)
            return {"error": str(exc)}
        used.append(name)
        return payload

    def _prefetch(
        self, intent: QuestionIntent, question: str, filters: GlobalFilters
    ) -> tuple[list[str], dict[str, Any]]:
        used: list[str] = []
        pack: dict[str, Any] = {}
        pack["themes"] = self._safe_tool("get_metrics_themes", {}, filters, used)
        blob = (question or "").lower()
        need_counts = self._complete_tools is not None or intent in {
            QuestionIntent.quantitative,
            QuestionIntent.comparative,
        }
        if need_counts:
            pack["overview"] = self._safe_tool("get_metrics_overview", {}, filters, used)
        else:
            pack["overview"] = {"empty": False, "filters": filters.as_dict()}
        if need_counts or intent is QuestionIntent.comparative or "segment" in blob:
            pack["segments"] = self._safe_tool(
                "get_metrics_segments", {"dimension": "product_category"}, filters, used
            )
        if (
            self._complete_tools is not None
            and intent in {QuestionIntent.qualitative, QuestionIntent.quotes_only}
            and (self._embed_query is not None or self._bge_model is not None)
        ):
            pack["retrieval"] = self._safe_tool(
                "search_chunks", {"query": question}, filters, used
            )
        return used, pack

    def _confidence(self, pack: dict[str, Any]) -> tuple[str, float | None]:
        scores: list[float] = []
        themes = (pack.get("themes") or {}).get("themes") or []
        for card in themes:
            value = card.get("data_confidence")
            if value is not None:
                scores.append(float(value))
        if not scores:
            overview = pack.get("overview") or {}
            if overview.get("empty") or overview.get("eligible_corpus_count") == 0:
                return "decline", 0.0
            return "caveat", None
        lowest = min(scores)
        return confidence_band(lowest), lowest

    def _unavailable(self, pack: dict[str, Any]) -> list[str]:
        for key in ("overview", "themes", "segments"):
            block = pack.get(key) or {}
            if block.get("unavailable_sources"):
                return list(block["unavailable_sources"])
        return self.query._unavailable()

    def query_turn(
        self,
        question: str,
        filters: GlobalFilters,
        *,
        session_id: UUID | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        session = self._session(session_id, filters)
        intent = classify_question(question)
        scrubbed = scrub_pii(question)
        try:
            self.repo.insert_chat_message(
                ChatMessage(
                    id=uuid4(),
                    session_id=session.id,
                    role="user",
                    content=scrubbed,
                    created_at=utcnow(),
                )
            )
        except Exception:
            logger.exception("copilot persist user message failed")

        canned = None
        status = "ok"
        if intent is QuestionIntent.refuse_solution:
            canned, status = REFUSE_SOLUTION, "refused"
        elif intent is QuestionIntent.refuse_competitor_corpus:
            canned, status = REFUSE_AJIO, "refused"
        elif intent is QuestionIntent.decline_internal:
            canned, status = DECLINE_INTERNAL, "declined"

        tools_used: list[str] = []
        pack: dict[str, Any] = {}
        citations: list[dict[str, Any]] = []
        if canned is None:
            try:
                tools_used, pack = self._prefetch(intent, question, filters)
            except Exception:
                logger.exception("copilot prefetch failed")
                tools_used, pack = [], {}
            try:
                pack["retrieval_rows"] = retrieve_quotes(self.repo, question, limit=16)
                if pack["retrieval_rows"]:
                    pack["evidence"] = {
                        "rows": pack["retrieval_rows"],
                        "empty": False,
                        "filters": filters.as_dict(),
                    }
                    if "get_evidence" not in tools_used:
                        tools_used.append("get_evidence")
            except Exception:
                logger.exception("copilot keyword retrieve failed")
                pack["retrieval_rows"] = []
        band, conf = self._confidence(pack) if pack else ("decline", None)
        unavailable = self._unavailable(pack) if pack else self.query._unavailable()
        empty = bool((pack.get("overview") or {}).get("empty"))
        if not empty:
            empty = bool((pack.get("themes") or {}).get("empty")) and any(
                value for value in filters.as_dict().values()
            )

        if canned is None and empty:
            canned, status = DECLINE_EMPTY, "declined"

        answer = canned
        error = None
        qid = detect_research_question(question) if canned is None else None
        if canned is None and qid:
            pack["retrieval_rows"] = select_supporting_rows(
                pack, qid, limit=SUPPORTING_REVIEW_LIMIT
            )
            if pack["retrieval_rows"]:
                pack["evidence"] = {
                    "rows": pack["retrieval_rows"],
                    "empty": False,
                    "filters": filters.as_dict(),
                }
            answer = compose_research_answer(qid, question, pack)
            status = "ok"
        elif canned is None:
            try:
                if self._complete_tools is not None:
                    answer = self._generate(question, filters, pack, tools_used)
                    if not numbers_subset_of_tools(answer or "", pack):
                        status = "failed_grounding"
                        error = "assistant numbers not present in tool JSON"
                        answer = None
                    elif _has_solutioning(answer or ""):
                        status = "refused"
                        answer = REFUSE_SOLUTION
                    else:
                        status = "ok"
                else:
                    answer = compose_rag_answer(question, pack, intent=intent)
                    status = "ok"
            except GroqRateLimitError as exc:
                try:
                    answer = self._generate(question, filters, pack, tools_used)
                    if not numbers_subset_of_tools(answer or "", pack):
                        status = "failed_grounding"
                        error = "assistant numbers not present in tool JSON"
                        answer = None
                    else:
                        status = "ok"
                except GroqRateLimitError:
                    status = "error"
                    error = f"Groq 429 after retry: {exc}"
                    answer = None
            except (GroqConfigError, GroqAuthError) as err:
                logger.warning("copilot Groq unavailable: %s", err)
                answer = compose_rag_answer(question, pack, intent=intent)
                status = "ok"
                error = str(err)
            except (GroqRetryableError, RuntimeError) as err:
                logger.warning("copilot generation failed: %s", err)
                answer = compose_rag_answer(question, pack, intent=intent)
                status = "ok"
                error = str(err)
            except Exception as err:  # noqa: BLE001 — never 500 the chat UI
                logger.exception("copilot unexpected error")
                answer = compose_rag_answer(question, pack, intent=intent)
                status = "ok"
                error = str(err)

        if not citations:
            citations = _citations_from_evidence(
                pack.get("retrieval_rows") or [], limit=SUPPORTING_REVIEW_LIMIT
            )
            if not citations:
                evidence_rows = (pack.get("evidence") or {}).get("rows") or []
                citations = _citations_from_evidence(
                    evidence_rows, limit=SUPPORTING_REVIEW_LIMIT
                )
            if not citations:
                retrieval = pack.get("retrieval") or {}
                chunk_rows = retrieval.get("chunks") or []
                citations = [
                    {
                        "document_id": str(row.get("document_id")),
                        "chunk_id": str(row["chunk_id"]) if row.get("chunk_id") else None,
                        "url": None,
                        "source_type": row.get("source_type") or "unknown",
                        "quote": row.get("quote") or "",
                        "published_at": None,
                    }
                    for row in chunk_rows[:SUPPORTING_REVIEW_LIMIT]
                    if row.get("document_id")
                ]
        citations = _sanitize_citations(citations)[:SUPPORTING_REVIEW_LIMIT]

        metrics_used = []
        for card in (pack.get("themes") or {}).get("themes") or []:
            metrics_used.append(
                {
                    "theme_id": card.get("theme_id"),
                    "mention_count": card.get("mention_count"),
                    "share_of_voice": card.get("share_of_voice"),
                    "data_confidence": card.get("data_confidence"),
                    "source_diversity": card.get("source_diversity"),
                    "unavailable_sources": [
                        name
                        for name in (card.get("unavailable_sources") or [])
                        if name in {"play_store", "app_store"}
                    ],
                }
            )
        flags = [
            card.get("name")
            for card in (pack.get("themes") or {}).get("themes") or []
            if card.get("hypothesis_flag")
        ]
        latency_ms = (time.perf_counter() - started) * 1000.0
        turn = {
            "session_id": str(session.id),
            "status": status,
            "answer": answer,
            "citations": citations,
            "metrics_used": metrics_used,
            "tools_used": tools_used,
            "confidence_band": band if canned is None else (
                "decline" if status in {"declined", "refused", "error"} else band
            ),
            "data_confidence": conf,
            "unavailable_sources": unavailable,
            "hypothesis_flags": [name for name in flags if name],
            "latency_ms": round(latency_ms, 1),
            "error": error,
            "filters": filters.as_dict(),
            "intent": intent.value,
            "tool_injection_ignored": is_tool_injection(question),
        }
        try:
            self.repo.insert_chat_message(
                ChatMessage(
                    id=uuid4(),
                    session_id=session.id,
                    role="assistant",
                    content=scrub_pii(answer or error or status),
                    created_at=utcnow(),
                    citations=citations,
                    metrics_used=metrics_used,
                    tools_used=tools_used,
                    confidence_band=turn["confidence_band"],
                    status=status,
                )
            )
        except Exception:
            logger.exception("copilot persist assistant message failed")
        return _normalize_copilot_turn(turn)

    def _generate(
        self,
        question: str,
        filters: GlobalFilters,
        pack: dict[str, Any],
        tools_used: list[str],
    ) -> str:
        system = load_copilot_prompt(self.settings)
        compact = compact_tool_pack(pack)
        qid = detect_research_question(question)
        qid_line = (
            f"\nThis is discovery research question {qid}. Write one short claim, then quote "
            "exactly two supporting reviews. Do not print mention_count, share_of_voice, "
            "eligible corpus, intent mix, opportunity-area dumps, or extra reviews.\n"
            if qid
            else ""
        )
        context = (
            "Tool JSON (only source of numbers). Metrics come first.\n"
            + dump_tools(compact)
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Filters: {dump_tools(filters.as_dict())}\n"
                    f"{qid_line}Question: {question}"
                ),
            },
            {"role": "system", "content": context},
        ]
        fn = self._complete_tools or groq_complete_tools
        try:
            return self._tool_loop(fn, messages, pack, tools_used, filters)
        except GroqRateLimitError:
            raise
        except (GroqRetryableError, GroqConfigError, GroqAuthError, RuntimeError) as exc:
            if self._complete_tools is not None:
                raise
            logger.warning("copilot tools path failed, completing from prefetch: %s", exc)
            return self._generate_from_pack(question, filters, compact)

    def _tool_loop(
        self,
        fn: CompleteToolsFn,
        messages: list[dict[str, Any]],
        pack: dict[str, Any],
        tools_used: list[str],
        filters: GlobalFilters,
    ) -> str:
        rounds = 0
        while rounds < self.settings.copilot_max_tool_rounds:
            rounds += 1
            result = fn(
                self.settings,
                messages,
                COPILOT_TOOLS,
                model=self.settings.groq_model,
                max_tokens=self.settings.groq_copilot_max_tokens,
            )
            if isinstance(result, GroqToolResult) and result.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": result.content,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": call.arguments,
                                },
                            }
                            for call in result.tool_calls
                        ],
                    }
                )
                for call in result.tool_calls:
                    try:
                        args = json.loads(call.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    payload = self._run_tool(call.name, args, filters)
                    tools_used.append(call.name)
                    pack[call.name] = payload
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": dump_tools(compact_tool_pack({call.name: payload})),
                        }
                    )
                continue
            content = None
            if isinstance(result, GroqToolResult):
                content = result.content
            elif isinstance(result, GroqJsonResult):
                content = result.content
            else:
                content = getattr(result, "content", None) or str(result)
            if content:
                return content.strip()
            break
        raise RuntimeError("Groq returned no grounded completion")

    def _generate_from_pack(
        self,
        question: str,
        filters: GlobalFilters,
        compact: dict[str, Any],
    ) -> str:
        system = load_copilot_prompt(self.settings)
        qid = detect_research_question(question)
        qid_line = (
            f"This is discovery research question {qid}. Write one short claim, then quote "
            "exactly two supporting reviews. Do not print mention_count, share_of_voice, "
            "eligible corpus, or extra reviews.\n"
            if qid
            else ""
        )
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Filters: {dump_tools(filters.as_dict())}\n"
                    f"{qid_line}Question: {question}\n\n"
                    f"Tool JSON (only source of numbers):\n{dump_tools(compact)}"
                ),
            },
        ]
        result = groq_complete_text(
            self.settings,
            messages,
            model=self.settings.groq_model,
            max_tokens=self.settings.groq_copilot_max_tokens,
        )
        content = (result.content or "").strip()
        if not content:
            raise RuntimeError("Groq returned no grounded completion")
        return content

    def _session(self, session_id: UUID | None, filters: GlobalFilters) -> ChatSession:
        if session_id is not None:
            existing = self.repo.get_chat_session(session_id)
            if existing:
                return existing
        session = ChatSession(
            id=session_id or uuid4(),
            created_at=utcnow(),
            groq_model=self.settings.groq_model,
            bge_model=self.settings.bge_model_id,
            filters=filters.as_dict(),
        )
        self.repo.insert_chat_session(session)
        return session


def _has_solutioning(text: str) -> bool:
    lowered = text.lower()
    return any(
        needle in lowered
        for needle in (
            "should build",
            "we recommend adding",
            "write a prd",
            "ship a feature",
            "build a widget",
        )
    )


def _as_iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    return str(value)


def _sanitize_citations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        doc = row.get("document_id")
        if not doc:
            continue
        out.append(
            {
                "document_id": str(doc),
                "chunk_id": str(row["chunk_id"]) if row.get("chunk_id") else None,
                "url": str(row["url"]) if row.get("url") else None,
                "source_type": str(row.get("source_type") or "unknown"),
                "quote": str(row.get("quote") or "")[:500],
                "published_at": _as_iso(row.get("published_at")),
            }
        )
    return out


def _normalize_copilot_turn(turn: dict[str, Any]) -> dict[str, Any]:
    """Coerce types so FastAPI response_model cannot 500 the chat UI."""
    out = dict(turn)
    out["session_id"] = str(out.get("session_id") or uuid4())
    out["status"] = str(out.get("status") or "error")
    out["confidence_band"] = str(out.get("confidence_band") or "decline")
    out["citations"] = _sanitize_citations(list(out.get("citations") or []))
    out["tools_used"] = [str(name) for name in (out.get("tools_used") or [])]
    out["unavailable_sources"] = [
        str(name)
        for name in (out.get("unavailable_sources") or [])
        if str(name) in {"play_store", "app_store"}
    ]
    out["hypothesis_flags"] = [str(name) for name in (out.get("hypothesis_flags") or []) if name]
    filters = out.get("filters")
    if filters is not None and not isinstance(filters, dict):
        out["filters"] = None
    return out
