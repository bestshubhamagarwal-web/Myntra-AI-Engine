"""Publish opportunity areas from extraction tags when BGE clustering is not available."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID, uuid4, uuid5

from src.cluster.eligibility import quote_spans
from src.cluster.label_schema import bookmark_vs_stall_from_modes
from src.db.repository import ClusterRun, DocumentTheme, ThemeRecord
from src.timeutil import utcnow

THEME_NS = UUID("3c0a0e1c-7b2a-4d55-9c1f-8a6e2f0b1d44")

THEME_CATALOG: tuple[dict[str, str], ...] = (
    {
        "key": "fit_uncertainty",
        "name": "Fit and size uncertainty",
        "description": "Public comments where shoppers hesitate because size, fit, or the size chart is unclear.",
        "friction": "fit_uncertainty",
    },
    {
        "key": "delivery_or_availability",
        "name": "Delivery and order delays",
        "description": "Orders described as pending, late, or stuck with courier or stock issues.",
        "friction": "delivery_or_availability",
    },
    {
        "key": "return_risk",
        "name": "Returns and refund friction",
        "description": "Return, refund, exchange, and reverse-pickup complaints that stall a purchase.",
        "friction": "return_risk",
    },
    {
        "key": "wishlist_bookmark",
        "name": "Wishlist as bookmark / save for later",
        "description": "People using wishlist language to park items, save for later, or mood-board rather than buy now.",
        "intent": "passive_bookmark",
    },
    {
        "key": "price_sensitivity",
        "name": "Price watching and discount waiting",
        "description": "Price, MRP, coupon, and sale language that delays checkout.",
        "friction": "price_sensitivity",
    },
    {
        "key": "quality_doubt",
        "name": "Product quality doubts",
        "description": "Fabric, stitch, fade, and quality complaints in public reviews.",
        "friction": "quality_doubt",
    },
    {
        "key": "policy_trust",
        "name": "Support and policy trust",
        "description": "Customer care, no-response, and trust complaints after an order or return.",
        "friction": "policy_trust",
    },
    {
        "key": "comparison_paralysis",
        "name": "Comparison shopping",
        "description": "Mentions of AJIO, Flipkart, Nykaa, or explicit vs/compare language inside Myntra-relevant docs.",
        "friction": "comparison_paralysis",
    },
    {
        "key": "other",
        "name": "App reliability and other stated problems",
        "description": "Crashes, payments, login, and other stated problems that did not map to a named friction.",
        "friction": "other",
    },
)


@dataclass
class KeywordThemeResult:
    run_id: UUID
    n_themes: int
    n_assigned: int
    status: str = "success"


def _theme_id(key: str) -> UUID:
    return uuid5(THEME_NS, key)


def _primary_key(friction_tags: list[str], intent_mode: str | None) -> str:
    tags = [str(t) for t in (friction_tags or []) if t]
    if intent_mode == "passive_bookmark":
        return "wishlist_bookmark"
    for spec in THEME_CATALOG:
        friction = spec.get("friction")
        if friction and friction in tags and friction != "other":
            return spec["key"]
    if "other" in tags:
        return "other"
    return "other"


def run_keyword_themes(repo) -> KeywordThemeResult:
    started = utcnow()
    run = ClusterRun(
        id=uuid4(),
        started_at=started,
        status="running",
        mode="recluster",
        algorithm="keyword_friction",
        params={"method": "keyword_friction_v1"},
        embedding_model=None,
        groq_model_light=None,
        prompt_version="heuristic.v1",
        corpus="multi_source",
        c_max=200,
        s_max=4,
    )
    repo.start_cluster_run(run)

    members: dict[str, list] = defaultdict(list)
    mode_hist: dict[str, list[str]] = defaultdict(list)
    store = getattr(repo, "normalized", None)
    ext_store = getattr(repo, "extractions", None)
    if store is not None:
        docs = [d for d in store.values() if d.eligible and d.duplicate_of is None]
    else:
        docs = repo.list_normalized(limit=None, eligible_only=True)

    for document in docs:
        extraction = ext_store.get(document.id) if ext_store is not None else repo.get_extraction(document.id)
        if extraction is None or extraction.extraction_status != "ok":
            continue
        key = _primary_key(extraction.friction_tags or [], extraction.intent_mode)
        members[key].append((document, extraction))
        mode_hist[key].append(extraction.intent_mode or "unknown")
        if extraction.intent_mode == "passive_bookmark" and key != "wishlist_bookmark":
            members["wishlist_bookmark"].append((document, extraction))
            mode_hist["wishlist_bookmark"].append(extraction.intent_mode)

    assignments: list[DocumentTheme] = []
    published = 0
    assigned = 0
    for spec in THEME_CATALOG:
        key = spec["key"]
        cluster_members = members.get(key) or []
        quotes = []
        for _doc, extraction in cluster_members:
            quotes.extend(quote_spans(extraction))
            if len(quotes) >= 3:
                break
        if not cluster_members or not quotes:
            continue
        theme = ThemeRecord(
            id=_theme_id(key),
            name=spec["name"],
            cluster_run_id=run.id,
            description=spec["description"],
            hypothesis_flag=True,
            bookmark_vs_stall=bookmark_vs_stall_from_modes(mode_hist.get(key) or []).value,
            published=True,
            label_status="heuristic",
            hdbscan_label=published,
            created_at=started,
            updated_at=utcnow(),
        )
        repo.upsert_theme(theme)
        published += 1
        seen: set[UUID] = set()
        for document, _extraction in cluster_members:
            if document.id in seen:
                continue
            seen.add(document.id)
            assignments.append(
                DocumentTheme(
                    document_id=document.id,
                    theme_id=theme.id,
                    cluster_run_id=run.id,
                    assignment_method="keyword_friction",
                    assignment_confidence=0.48,
                )
            )
            assigned += 1

    repo.replace_document_themes(run.id, assignments)
    run.status = "success"
    run.finished_at = utcnow()
    run.n_documents = sum(len(v) for v in members.values())
    run.n_clustered = assigned
    run.n_noise = 0
    run.n_themes = published
    repo.finish_cluster_run(run)
    return KeywordThemeResult(run_id=run.id, n_themes=published, n_assigned=assigned)


def run_local_index(repo, settings) -> dict[str, object]:
    """Heuristic extract → keyword themes → metrics → n-grams → weekly report."""
    from src.extract.heuristic import run_heuristic_extract
    from src.metrics.pipeline import run_metrics
    from src.ngrams.pipeline import run_ngrams
    from src.reports.pipeline import run_report

    held = bool(getattr(repo, "_suppress_save", False))
    if hasattr(repo, "_suppress_save"):
        repo._suppress_save = True
    try:
        extract = run_heuristic_extract(repo, force=True)
        themes = run_keyword_themes(repo)
        metrics = run_metrics(repo, settings, cluster_run_id=themes.run_id)
        ngrams = run_ngrams(
            repo,
            settings,
            cluster_run_id=themes.run_id,
            min_count=3,
            max_rows=4000,
        )
        report = run_report(repo, settings)
    finally:
        if hasattr(repo, "_suppress_save"):
            repo._suppress_save = held
            save = getattr(repo, "save", None)
            if callable(save):
                save()
    return {
        "extract_ok": extract.ok,
        "themes": themes.n_themes,
        "assigned": themes.n_assigned,
        "snapshots": metrics.n_snapshots,
        "ngrams": ngrams.n_rows,
        "report_id": str(report.report_id),
        "cluster_run_id": str(themes.run_id),
    }
