"""FastAPI Query API + Copilot (Architecture §10–11). No UI."""

from __future__ import annotations

import logging
import threading
import time
from uuid import UUID, uuid4

import psycopg
from pydantic import ValidationError
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.security import APIKeyHeader

from src.api.copilot import CopilotService, _normalize_copilot_turn
from src.api.rag import compose_rag_answer, retrieve_quotes
from src.api.filters import GlobalFilters, filters_from_params
from src.api.query import QueryService, SEGMENT_DIMENSIONS
from src.api.schemas import (
    CopilotQueryRequest,
    CopilotTurnResponse,
    EvidenceResponse,
    NgramsResponse,
    OverviewResponse,
    PHASE6_PATHS,
    ReportsResponse,
    SegmentsResponse,
    ThemesResponse,
    TrendsResponse,
)
from src.config import Settings, load_settings
from src.db.connect import POSTGRES_UNREACHABLE, connect_store, resolve_database_url
from src.db.local import PersistentMemoryRepository
from src.db.migrate import apply_migrations
from src.db.postgres import PostgresRepository
from src.db.repository import DocumentRepository

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
log = logging.getLogger(__name__)
VERCEL_ORIGIN_RE = r"https://([a-z0-9-]+\.)*vercel\.app"


def _store_kind(store: DocumentRepository | None) -> str:
    if store is None:
        return "pending"
    if isinstance(store, PostgresRepository):
        return "postgres"
    if isinstance(store, PersistentMemoryRepository):
        return "memory"
    return "memory"


def _attach_store(
    app: FastAPI,
    store: DocumentRepository,
    cfg: Settings,
    *,
    embed_query=None,
    complete_tools=None,
) -> None:
    app.state.repo = store
    app.state.query = QueryService(store, cfg)
    app.state.copilot = CopilotService(
        store,
        cfg,
        embed_query=embed_query,
        complete_tools=complete_tools,
    )
    app.state.boot_error = None


def pending_store_detail(boot_error: str | None) -> str:
    err = (boot_error or "").strip()
    if not err:
        return "Query API is connecting to Postgres. Retry in a few seconds."
    lowered = err.lower()
    if "vector" in lowered or "extension" in lowered:
        return (
            "Migrations need pgvector. Use Railway's pgvector template (not default "
            "Postgres) and set DATABASE_URL to ${{Postgres.DATABASE_URL}}. "
            f"Last error: {err}"
        )
    if "localhost" in lowered or "127.0.0.1" in lowered:
        return (
            "DATABASE_URL still points at localhost. In Railway Variables set "
            "DATABASE_URL=${{Postgres.DATABASE_URL}} from the pgvector service. "
            f"Last error: {err}"
        )
    return f"Query API cannot reach Postgres. {err}"


def _boot_store(
    app: FastAPI,
    cfg: Settings,
    *,
    migrate: bool,
    embed_query=None,
    complete_tools=None,
) -> None:
    """Connect (and optionally migrate) after the process is already listening."""
    while True:
        try:
            cfg.database_url = resolve_database_url(cfg.database_url)
            store = connect_store(cfg)
            if migrate:
                db_url = store.database_url if isinstance(store, PostgresRepository) else cfg.database_url
                applied = apply_migrations(db_url)
                log.info("boot migrate applied=%s", applied)
            _attach_store(
                app,
                store,
                cfg,
                embed_query=embed_query,
                complete_tools=complete_tools,
            )
            log.info("store ready kind=%s", _store_kind(store))
            return
        except Exception as exc:  # noqa: BLE001 — keep listening; retry
            app.state.boot_error = str(exc)
            log.warning("store boot retry: %s", exc)
            time.sleep(0.05 if float(cfg.postgres_wait_seconds) <= 0 else 3.0)


def create_app(
    repo: DocumentRepository | None = None,
    settings: Settings | None = None,
    *,
    embed_query=None,
    complete_tools=None,
    migrate_on_boot: bool = False,
) -> FastAPI:
    cfg = settings or load_settings()
    defer = repo is None and cfg.require_postgres
    store: DocumentRepository | None
    if repo is not None:
        store = repo
    elif defer:
        store = None
    else:
        store = connect_store(cfg)

    app = FastAPI(
        title="Myntra Discovery Engine Query API",
        version="0.1.0",
        description=(
            "Single metrics path for the Phase 6 Next.js app and Copilot tools. "
            "SoV, impact, and confidence are read from theme_metrics — the client "
            "must not re-aggregate. Prototype auth: shared secret header X-API-Key "
            "(optional on localhost). CORS allows the local Next.js origin and https://*.vercel.app."
        ),
        openapi_tags=[
            {"name": "metrics", "description": "Precomputed theme_metrics + n-grams"},
            {"name": "evidence", "description": "Quotes with source URL or link_unavailable"},
            {"name": "reports", "description": "Weekly PDF artifacts on disk"},
            {"name": "copilot", "description": "Grounded Q&A; numbers ⊆ tool JSON"},
        ],
    )
    app.state.settings = cfg
    app.state.repo = store
    app.state.query = None
    app.state.copilot = None
    app.state.boot_error = None
    if store is not None:
        _attach_store(
            app,
            store,
            cfg,
            embed_query=embed_query,
            complete_tools=complete_tools,
        )
    elif defer:
        threading.Thread(
            target=_boot_store,
            kwargs={
                "app": app,
                "cfg": cfg,
                "migrate": migrate_on_boot,
                "embed_query": embed_query,
                "complete_tools": complete_tools,
            },
            name="store-boot",
            daemon=True,
        ).start()

    origins = cfg.cors_origin_list() or [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=VERCEL_ORIGIN_RE,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(psycopg.Error)
    async def postgres_error(_request: Request, _exc: psycopg.Error):
        return JSONResponse(status_code=503, content={"detail": POSTGRES_UNREACHABLE})

    def require_auth(
        x_api_key: str | None = Depends(API_KEY_HEADER),
        authorization: str | None = Header(default=None),
    ) -> None:
        secret = (cfg.api_shared_secret or "").strip()
        if not secret:
            return
        bearer = ""
        if authorization and authorization.lower().startswith("bearer "):
            bearer = authorization[7:].strip()
        if x_api_key == secret or bearer == secret:
            return
        raise HTTPException(status_code=401, detail="invalid or missing API shared secret")

    def _unavailable() -> HTTPException:
        return HTTPException(
            status_code=503,
            detail=pending_store_detail(getattr(app.state, "boot_error", None)),
        )

    def query_svc() -> QueryService:
        svc = getattr(app.state, "query", None)
        if svc is None:
            raise _unavailable()
        return svc

    def copilot_svc() -> CopilotService:
        svc = getattr(app.state, "copilot", None)
        if svc is None:
            raise _unavailable()
        return svc

    def repo_svc() -> DocumentRepository:
        current = getattr(app.state, "repo", None)
        if current is None:
            raise _unavailable()
        return current

    def parse_filters(
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        source_type: str | None = Query(default=None),
        product_category: str | None = Query(default=None),
        gender_segment: str | None = Query(default=None),
        price_tier: str | None = Query(default=None),
        platform_used: str | None = Query(default=None),
        intent_mode: str | None = Query(default=None),
        theme_id: str | None = Query(default=None),
        friction_tag: str | None = Query(default=None),
        intent_tag: str | None = Query(default=None),
        q: str | None = Query(default=None),
    ) -> GlobalFilters:
        return filters_from_params(
            date_from=date_from,
            date_to=date_to,
            source_type=source_type,
            product_category=product_category,
            gender_segment=gender_segment,
            price_tier=price_tier,
            platform_used=platform_used,
            intent_mode=intent_mode,
            theme_id=theme_id,
            friction_tag=friction_tag,
            intent_tag=intent_tag,
            q=q,
        )

    @app.get("/health")
    def health():
        current = getattr(app.state, "repo", None)
        if current is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "starting",
                    "store": "pending",
                    "detail": pending_store_detail(getattr(app.state, "boot_error", None)),
                },
            )
        return {"status": "ok", "store": _store_kind(current)}

    @app.get("/")
    def root():
        current = getattr(app.state, "repo", None)
        return {
            "status": "ok" if current is not None else "starting",
            "service": "Myntra Discovery Engine Query API",
            "docs": "/docs",
            "health": "/health",
            "store": _store_kind(current),
        }

    @app.get(
        "/metrics/overview",
        response_model=OverviewResponse,
        tags=["metrics"],
        dependencies=[Depends(require_auth)],
    )
    def metrics_overview(filters: GlobalFilters = Depends(parse_filters)):
        return query_svc().overview(filters)

    @app.get(
        "/metrics/themes",
        response_model=ThemesResponse,
        tags=["metrics"],
        dependencies=[Depends(require_auth)],
    )
    def metrics_themes(filters: GlobalFilters = Depends(parse_filters)):
        return query_svc().themes(filters)

    @app.get(
        "/metrics/segments",
        response_model=SegmentsResponse,
        tags=["metrics"],
        dependencies=[Depends(require_auth)],
    )
    def metrics_segments(
        filters: GlobalFilters = Depends(parse_filters),
        dimension: str = Query(default="product_category"),
    ):
        if dimension not in SEGMENT_DIMENSIONS:
            dimension = "product_category"
        return query_svc().segments(filters, dimension=dimension)

    @app.get(
        "/metrics/trends",
        response_model=TrendsResponse,
        tags=["metrics"],
        dependencies=[Depends(require_auth)],
    )
    def metrics_trends(filters: GlobalFilters = Depends(parse_filters)):
        return query_svc().trends(filters)

    @app.get(
        "/metrics/ngrams",
        response_model=NgramsResponse,
        tags=["metrics"],
        dependencies=[Depends(require_auth)],
    )
    def metrics_ngrams(
        filters: GlobalFilters = Depends(parse_filters),
        n: int | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ):
        return query_svc().ngrams(filters, n=n, limit=limit)

    @app.get(
        "/evidence",
        tags=["evidence"],
        dependencies=[Depends(require_auth)],
        responses={
            200: {
                "description": "JSON rows or CSV of scrubbed quotes (no usernames)",
            }
        },
    )
    def evidence(
        request: Request,
        filters: GlobalFilters = Depends(parse_filters),
        format: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=5000),
    ):
        want_csv = (format or "").lower() == "csv" or "text/csv" in (
            request.headers.get("accept") or ""
        )
        if want_csv:
            body = query_svc().evidence_csv(filters)
            return PlainTextResponse(body, media_type="text/csv; charset=utf-8")
        payload = query_svc().evidence(filters, limit=limit)
        return EvidenceResponse.model_validate(payload)

    @app.get(
        "/reports",
        response_model=ReportsResponse,
        tags=["reports"],
        dependencies=[Depends(require_auth)],
    )
    def reports():
        return query_svc().reports()

    @app.get(
        "/reports/{report_id}",
        tags=["reports"],
        dependencies=[Depends(require_auth)],
    )
    def report_download(report_id: UUID, format: str | None = Query(default=None)):
        if (format or "").lower() == "json":
            payload = query_svc().report_detail(report_id)
            if payload is None:
                raise HTTPException(status_code=404, detail="report not found")
            return payload
        pdf_path = query_svc().resolve_report_pdf(report_id)
        if pdf_path is None:
            raise HTTPException(status_code=404, detail="report PDF not on disk")
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"{report_id}.pdf",
        )

    @app.post(
        "/copilot/query",
        response_model=CopilotTurnResponse,
        tags=["copilot"],
        dependencies=[Depends(require_auth)],
    )
    def copilot_query(body: CopilotQueryRequest):
        filters = filters_from_params(
            date_from=body.date_from,
            date_to=body.date_to,
            source_type=body.source_type,
            product_category=body.product_category,
            gender_segment=body.gender_segment,
            price_tier=body.price_tier,
            platform_used=body.platform_used,
            intent_mode=body.intent_mode,
            theme_id=body.theme_id,
        )
        try:
            turn = copilot_svc().query_turn(body.question, filters, session_id=body.session_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — chat UI must not see a 500
            logging.getLogger(__name__).exception("copilot HTTP handler failed")
            pack: dict = {}
            try:
                pack["themes"] = query_svc().themes(filters)
                pack["retrieval_rows"] = retrieve_quotes(repo_svc(), body.question, limit=6)
            except Exception:
                logging.getLogger(__name__).exception("copilot emergency prefetch failed")
            turn = {
                "session_id": str(body.session_id or uuid4()),
                "status": "ok",
                "answer": compose_rag_answer(body.question, pack),
                "citations": [],
                "metrics_used": [],
                "tools_used": [],
                "confidence_band": "caveat",
                "data_confidence": None,
                "unavailable_sources": [],
                "hypothesis_flags": [],
                "latency_ms": 0.0,
                "error": str(exc),
                "filters": filters.as_dict(),
            }
        try:
            return CopilotTurnResponse.model_validate(_normalize_copilot_turn(turn))
        except ValidationError:
            logging.getLogger(__name__).exception("copilot response failed schema validation")
            return CopilotTurnResponse(
                session_id=str(body.session_id or uuid4()),
                status="ok",
                answer=(
                    "The Query API is running. I could not format the last Copilot payload, "
                    "but Overview still has the same filter counts."
                ),
                citations=[],
                metrics_used=[],
                tools_used=[],
                confidence_band="caveat",
                unavailable_sources=[],
                error="response validation failed",
                filters=filters.as_dict(),
            )

    app.state.phase6_paths = PHASE6_PATHS
    return app


def app_for_cli() -> FastAPI:
    return create_app()
