"""Document-level HDBSCAN (or incremental kNN) → published themes.

Noise is not an opportunity area. Themes without a verbatim quote are not published.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from src.cluster.algorithm import (
    ClusterParams,
    cosine,
    cluster_vectors,
    knn_assign,
    match_centroids,
    mean_vector,
    params_from_settings,
)
from src.cluster.eligibility import is_cluster_eligible, quote_spans
from src.cluster.label import CompleteFn, label_cluster
from src.cluster.prompt import load_theme_label_prompt
from src.config import Settings, load_settings
from src.db.repository import (
    ClusterRun,
    DocumentRepository,
    DocumentTheme,
    ExtractionRecord,
    NormalizedRecord,
    ThemeRecord,
)
from src.extract.groq_client import GroqConfigError, groq_complete_json
from src.extract.pipeline import TpmWindow
from src.models.envelope import SourceType
from src.timeutil import utcnow

logger = logging.getLogger(__name__)


@dataclass
class ClusterMember:
    document_id: UUID
    vector: list[float]
    extraction: ExtractionRecord
    normalized: NormalizedRecord
    source_type: str
    author_hash: str | None
    quotes: list[str]
    published_at: object | None = None


@dataclass
class ClusterBatchResult:
    run_id: UUID
    status: str
    mode: str
    algorithm: str
    n_documents: int
    n_clustered: int
    n_noise: int
    n_themes: int
    n_incremental: int = 0
    corpus: str | None = None
    caveat: str | None = None
    error_message: str | None = None
    theme_ids: list[UUID] = field(default_factory=list)


def document_vector(chunks) -> list[float] | None:
    vectors = [c.embedding for c in chunks if c.embedding]
    return mean_vector(vectors)


def collect_members(repo: DocumentRepository) -> list[ClusterMember]:
    extractions = {
        row.document_id: row
        for row in repo.list_extractions(metrics_eligible_only=True)
    }
    members: list[ClusterMember] = []
    for document_id, extraction in extractions.items():
        normalized = repo.get_normalized(document_id)
        if normalized is None or not is_cluster_eligible(extraction, normalized):
            continue
        chunks = repo.list_chunks(document_id)
        vector = document_vector(chunks)
        if vector is None:
            continue
        raw = repo.get_raw(normalized.raw_id)
        source_type = raw.source_type.value if raw and isinstance(raw.source_type, SourceType) else (
            raw.source_type if raw else "unknown"
        )
        if hasattr(source_type, "value"):
            source_type = source_type.value
        members.append(
            ClusterMember(
                document_id=document_id,
                vector=vector,
                extraction=extraction,
                normalized=normalized,
                source_type=str(source_type or "unknown"),
                author_hash=raw.author_hash if raw else None,
                quotes=quote_spans(extraction),
                published_at=normalized.review_date or (raw.published_at if raw else None),
            )
        )
    members.sort(key=lambda item: item.document_id)
    return members


def _corpus_label(members: list[ClusterMember]) -> str:
    sources = {item.source_type for item in members}
    if sources == {"play_store"}:
        return "play_only"
    if not sources:
        return "empty"
    return "multi_source"


def _hist(values: list[str]) -> dict[str, int]:
    return dict(Counter(v for v in values if v))


def _latest_labeled_run(repo: DocumentRepository) -> ClusterRun | None:
    runs = [r for r in repo.list_cluster_runs() if r.status == "success" and r.n_themes]
    if not runs:
        return None
    return max(runs, key=lambda r: r.started_at)


def _previous_themes(repo: DocumentRepository) -> list[ThemeRecord]:
    run = _latest_labeled_run(repo)
    if run is None:
        return []
    return [t for t in repo.list_themes(run.id) if t.published and t.centroid]


def _choose_mode(
    requested: str,
    unassigned: int,
    previous: list[ThemeRecord],
    params: ClusterParams,
) -> str:
    if requested == "recluster":
        return "recluster"
    if requested == "incremental":
        return "incremental" if previous else "recluster"
    if not previous:
        return "recluster"
    if unassigned >= params.recluster_new_docs:
        return "recluster"
    return "incremental"


def _theme_from_cluster(
    *,
    theme_id: UUID,
    run_id: UUID,
    label: int,
    centroid: list[float],
    cluster_members: list[ClusterMember],
    prompt,
    settings: Settings,
    complete_fn: CompleteFn | None,
    tpm: TpmWindow,
    sleep,
    clock,
) -> ThemeRecord | None:
    quotes: list[str] = []
    for member in cluster_members:
        quotes.extend(member.quotes)
    if not quotes:
        logger.info("skipping cluster label=%s: no verbatim quotes", label)
        return None
    friction_tags = [tag for m in cluster_members for tag in (m.extraction.friction_tags or [])]
    intent_tags = [m.extraction.intent_tag or "unknown" for m in cluster_members]
    intent_modes = [m.extraction.intent_mode or "unknown" for m in cluster_members]
    payload, label_status, _pt, _ct = label_cluster(
        quotes=quotes,
        friction_hist=_hist(friction_tags),
        intent_mode_hist=_hist(intent_modes),
        intent_tag_hist=_hist(intent_tags),
        categories=_hist([m.normalized.product_category for m in cluster_members]),
        source_types=_hist([m.source_type for m in cluster_members]),
        member_count=len(cluster_members),
        intent_modes=intent_modes,
        friction_tags=friction_tags,
        intent_tags=intent_tags,
        prompt=prompt,
        settings=settings,
        complete_fn=complete_fn,
        tpm=tpm,
        sleep=sleep,
        clock=clock,
    )
    now = utcnow()
    return ThemeRecord(
        id=theme_id,
        name=payload.name,
        description=payload.description,
        hypothesis_flag=bool(payload.hypothesis_flag),
        bookmark_vs_stall=payload.bookmark_vs_stall.value,
        published=True,
        label_status=label_status,
        cluster_run_id=run_id,
        centroid=centroid,
        hdbscan_label=label,
        created_at=now,
        updated_at=now,
    )


def _run_recluster(
    repo: DocumentRepository,
    members: list[ClusterMember],
    *,
    run: ClusterRun,
    params: ClusterParams,
    settings: Settings,
    complete_fn: CompleteFn | None,
    prompt,
    tpm: TpmWindow,
    sleep,
    clock,
    force_algorithm: str | None,
) -> ClusterBatchResult:
    fit = cluster_vectors(
        [m.vector for m in members],
        params,
        force_algorithm=force_algorithm,
    )
    run.algorithm = fit.algorithm
    previous = _previous_themes(repo)
    matched = match_centroids(
        fit.centroids,
        [(t.id, t.centroid) for t in previous],
        params.centroid_match_min_similarity,
    )
    by_label: dict[int, list[ClusterMember]] = defaultdict(list)
    for member, label in zip(members, fit.labels):
        if label < 0:
            continue
        by_label[int(label)].append(member)

    themes: list[ThemeRecord] = []
    assignments: list[DocumentTheme] = []
    for label, centroid in fit.centroids.items():
        cluster_members = by_label.get(label, [])
        if not cluster_members:
            continue
        theme_id = matched.label_to_theme_id.get(label, uuid4())
        theme = _theme_from_cluster(
            theme_id=theme_id,
            run_id=run.id,
            label=label,
            centroid=centroid,
            cluster_members=cluster_members,
            prompt=prompt,
            settings=settings,
            complete_fn=complete_fn,
            tpm=tpm,
            sleep=sleep,
            clock=clock,
        )
        if theme is None:
            continue
        repo.upsert_theme(theme)
        themes.append(theme)
        for member in cluster_members:
            assignments.append(
                DocumentTheme(
                    document_id=member.document_id,
                    theme_id=theme.id,
                    cluster_run_id=run.id,
                    assignment_method="cluster",
                    assignment_confidence=cosine(member.vector, centroid),
                )
            )
    repo.replace_document_themes(run.id, assignments)
    n_assigned = len({row.document_id for row in assignments})
    return ClusterBatchResult(
        run_id=run.id,
        status="success",
        mode="recluster",
        algorithm=fit.algorithm,
        n_documents=len(members),
        n_clustered=n_assigned,
        n_noise=len(members) - n_assigned,
        n_themes=len(themes),
        corpus=_corpus_label(members),
        caveat=fit.caveat,
        theme_ids=[t.id for t in themes],
    )


def _run_incremental(
    repo: DocumentRepository,
    members: list[ClusterMember],
    *,
    run: ClusterRun,
    params: ClusterParams,
    previous_run: ClusterRun,
) -> ClusterBatchResult:
    previous_themes = [t for t in repo.list_themes(previous_run.id) if t.published]
    if not previous_themes:
        raise RuntimeError("incremental assign needs published themes from a prior run")
    member_by_id = {m.document_id: m for m in members}
    prior_rows = repo.list_document_themes(cluster_run_id=previous_run.id)
    theme_ids = {t.id for t in previous_themes}
    labeled: list[tuple[UUID, list[float]]] = []
    copied: list[DocumentTheme] = []
    assigned_docs: set[UUID] = set()
    for row in prior_rows:
        if row.theme_id not in theme_ids:
            continue
        member = member_by_id.get(row.document_id)
        if member is None:
            continue
        copied.append(
            DocumentTheme(
                document_id=row.document_id,
                theme_id=row.theme_id,
                cluster_run_id=run.id,
                assignment_method=row.assignment_method,
                assignment_confidence=row.assignment_confidence,
            )
        )
        assigned_docs.add(row.document_id)
        labeled.append((row.theme_id, member.vector))

    for theme in previous_themes:
        theme.cluster_run_id = run.id
        theme.updated_at = utcnow()
        repo.upsert_theme(theme)
        if theme.centroid:
            labeled.append((theme.id, theme.centroid))

    n_incremental = 0
    for member in members:
        if member.document_id in assigned_docs:
            continue
        theme_id, confidence = knn_assign(
            member.vector,
            labeled,
            k=params.knn_k,
            min_similarity=params.knn_min_similarity,
        )
        if theme_id is None:
            continue
        copied.append(
            DocumentTheme(
                document_id=member.document_id,
                theme_id=theme_id,
                cluster_run_id=run.id,
                assignment_method="knn_incremental",
                assignment_confidence=confidence,
            )
        )
        assigned_docs.add(member.document_id)
        labeled.append((theme_id, member.vector))
        n_incremental += 1

    repo.replace_document_themes(run.id, copied)
    return ClusterBatchResult(
        run_id=run.id,
        status="success",
        mode="incremental",
        algorithm="knn_incremental",
        n_documents=len(members),
        n_clustered=len(assigned_docs),
        n_noise=len(members) - len(assigned_docs),
        n_themes=len(previous_themes),
        n_incremental=n_incremental,
        corpus=_corpus_label(members),
        theme_ids=[t.id for t in previous_themes],
    )


def run_cluster(
    repo: DocumentRepository,
    settings: Settings | None = None,
    *,
    mode: str = "auto",
    label: bool = True,
    complete_fn: CompleteFn | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
    force_algorithm: str | None = None,
    skip_metrics: bool = False,
) -> ClusterBatchResult:
    import time as time_mod

    cfg = settings or load_settings()
    params = params_from_settings(cfg)
    sleep = sleep or time_mod.sleep
    clock = clock or time_mod.time
    prompt = load_theme_label_prompt()
    groq_complete: CompleteFn | None = None
    if label:
        if complete_fn is not None:
            groq_complete = complete_fn
        else:
            if not (cfg.groq_api_key or "").strip():
                raise GroqConfigError(
                    "GROQ_API_KEY is not set. Pass --no-label to skip Groq theme names, "
                    "or add a Groq key. Do not fall back to another LLM host."
                )
            groq_complete = lambda messages, **kwargs: groq_complete_json(
                cfg,
                messages,
                model=kwargs.get("model") or cfg.groq_model_light,
                max_tokens=kwargs.get("max_tokens") or cfg.groq_label_max_tokens,
            )

    members = collect_members(repo)
    previous = _previous_themes(repo)
    assigned: set[UUID] = set()
    if previous:
        assigned = {
            row.document_id
            for row in repo.list_document_themes(cluster_run_id=previous[0].cluster_run_id)
        }
    unassigned = sum(1 for m in members if m.document_id not in assigned)
    chosen = _choose_mode(mode, unassigned, previous, params)
    embedding_model = None
    embedding_revision = None
    if members:
        chunks = repo.list_chunks(members[0].document_id)
        if chunks:
            embedding_model = chunks[0].embedding_model
            embedding_revision = chunks[0].embedding_revision

    run = ClusterRun(
        id=uuid4(),
        started_at=utcnow(),
        status="running",
        mode=chosen,
        algorithm="pending",
        params=params.as_dict(),
        embedding_model=embedding_model,
        embedding_revision=embedding_revision,
        groq_model_light=cfg.groq_model_light if label else None,
        prompt_version=prompt.version if label else None,
        corpus=_corpus_label(members),
        c_max=cfg.c_max,
        s_max=cfg.s_max,
    )
    repo.start_cluster_run(run)
    tpm = TpmWindow(cfg.groq_max_tpm)
    try:
        prior = _latest_labeled_run(repo) if chosen == "incremental" else None
        if chosen == "incremental" and prior is None:
            chosen = "recluster"
            run.mode = chosen
        if chosen == "incremental" and prior is not None:
            result = _run_incremental(
                repo,
                members,
                run=run,
                params=params,
                previous_run=prior,
            )
        else:
            result = _run_recluster(
                repo,
                members,
                run=run,
                params=params,
                settings=cfg,
                complete_fn=groq_complete,
                prompt=prompt,
                tpm=tpm,
                sleep=sleep,
                clock=clock,
                force_algorithm=force_algorithm,
            )
        run.status = result.status
        run.algorithm = result.algorithm
        run.mode = result.mode
        run.corpus = result.corpus
        run.n_documents = result.n_documents
        run.n_clustered = result.n_clustered
        run.n_noise = result.n_noise
        run.n_themes = result.n_themes
        run.n_incremental = result.n_incremental
        run.finished_at = utcnow()
        repo.finish_cluster_run(run)
        if not skip_metrics:
            from src.metrics.pipeline import run_metrics

            run_metrics(repo, cfg, cluster_run_id=run.id)
        return result
    except Exception as exc:
        run.status = "failed"
        run.algorithm = run.algorithm or "failed"
        run.error_message = str(exc)
        run.finished_at = utcnow()
        repo.finish_cluster_run(run)
        logger.exception("cluster run failed")
        return ClusterBatchResult(
            run_id=run.id,
            status="failed",
            mode=chosen,
            algorithm=run.algorithm,
            n_documents=len(members),
            n_clustered=0,
            n_noise=len(members),
            n_themes=0,
            corpus=_corpus_label(members),
            error_message=str(exc),
        )
