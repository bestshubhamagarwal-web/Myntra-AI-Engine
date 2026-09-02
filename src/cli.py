"""Operator CLI. Phase 0–7: migrate, ingest, cluster, Query API, Copilot, Q1–Q9 eval."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from uuid import UUID

from src.config import load_settings, require_frozen_constants
from src.cluster.pipeline import run_cluster
from src.db.connect import PostgresRequiredError, connect_store, wait_for_postgres
from src.db.memory import MemoryRepository
from src.db.migrate import apply_migrations
from src.db.postgres import PostgresRepository
from src.embed.bge import encode_query, load_bge_model, smoke_bge
from src.embed.pipeline import run_embed
from src.extract.eval_report import build_extract_eval_report
from src.extract.groq_client import GroqAuthError, GroqConfigError, ping_groq
from src.extract.pipeline import run_extract
from src.ingest.lock import ExclusiveFileLock
from src.ingest.object_store import LocalObjectStore
from src.ingest.review_dump import write_review_dump
from src.ingest.registry import (
    CONNECTORS,
    INGEST_ALL_SOURCES,
    UNAVAILABLE_WITHOUT_CONNECTOR,
    run_source_ingest,
)
from src.metrics.pipeline import run_metrics
from src.ngrams.pipeline import run_ngrams
from src.normalize.pipeline import run_normalize
from src.normalize.spotcheck import (
    analysis_record_username_fields,
    spotcheck_text_failures,
)
from src.reports.pipeline import run_report
from src.smoke import smoke_db


def _repo():
    settings = load_settings()
    settings.ensure_runtime_dirs()
    return settings, connect_store(settings)


def _wait_for_required_postgres(settings) -> int:
    if not settings.require_postgres:
        return 0
    try:
        wait_for_postgres(settings)
    except PostgresRequiredError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_migrate(_args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_runtime_dirs()
    if _wait_for_required_postgres(settings) != 0:
        return 1
    applied = apply_migrations(settings.database_url)
    if applied:
        print("applied:", ", ".join(applied))
    else:
        print("migrations already applied")
    check = smoke_db(settings)
    print(
        "foundation ok:",
        ", ".join(f"{name}={check.counts[name]}" for name in check.counts),
    )
    print("unique:", check.unique_constraint)
    print("embedding:", check.embedding_type)
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_runtime_dirs()
    failures: list[str] = []

    if not args.skip_db:
        try:
            check = smoke_db(settings)
            print(
                "db: ok tables=",
                ",".join(sorted(check.counts)),
                "unique=",
                check.unique_constraint,
                "embedding=",
                check.embedding_type,
                "queries=",
                len(check.ingest_queries),
            )
        except Exception as exc:
            print(f"db: FAIL {exc}", file=sys.stderr)
            failures.append("db")

    if not args.skip_groq:
        try:
            result = ping_groq(settings)
            print(
                "groq: ok",
                f"method={result['method']}",
                f"base_url={result['base_url']}",
            )
        except Exception as exc:
            print(f"groq: FAIL {exc}", file=sys.stderr)
            failures.append("groq")

    if not args.skip_bge:
        try:
            result = smoke_bge(settings)
            print(
                "bge: ok",
                f"model={result['model_id']}",
                f"dim={result['dim']}",
            )
        except Exception as exc:
            print(f"bge: FAIL {exc}", file=sys.stderr)
            failures.append("bge")

    if failures:
        print("smoke failed:", ", ".join(failures), file=sys.stderr)
        return 1
    print("smoke passed")
    return 0


def _print_ingest_run(run) -> None:
    print(
        f"ingest_run={run.id} source={run.source_type} status={run.status} "
        f"fetched={run.rows_fetched} upserted={run.rows_upserted} "
        f"source_available={run.source_available} "
        f"watermark={run.watermark_after}"
    )
    if run.error_message:
        print(f"error={run.error_message}", file=sys.stderr)
    if run.payload_warning:
        print(f"warning={run.payload_warning}", file=sys.stderr)


def _ingest_limit(args: argparse.Namespace) -> int | None:
    if getattr(args, "max_items", None) is not None:
        return args.max_items
    return args.max_reviews


def cmd_ingest(args: argparse.Namespace) -> int:
    known = set(CONNECTORS) | set(UNAVAILABLE_WITHOUT_CONNECTOR) | {"all"}
    if args.source not in known:
        print(
            f"unknown source {args.source!r}; "
            f"known: {', '.join([*INGEST_ALL_SOURCES, 'all'])}",
            file=sys.stderr,
        )
        return 2
    settings, repo = _repo()
    store = LocalObjectStore(settings.raw_store_path)
    sources = list(INGEST_ALL_SOURCES) if args.source == "all" else [args.source]
    last = None
    any_failed = False
    for source in sources:
        run = run_source_ingest(
            source,
            repo,
            settings,
            object_store=store,
            max_items=_ingest_limit(args),
            force_full=args.force_full,
        )
        last = run
        _print_ingest_run(run)
        if run.status == "failed":
            any_failed = True
    if args.source == "all":
        return 1 if any_failed else 0
    if last and last.status in {
        "failed",
        "skipped_disabled",
        "skipped_unconfigured",
        "skipped_locked",
    }:
        return 1
    return 0


def cmd_normalize(args: argparse.Namespace) -> int:
    _settings, repo = _repo()
    since = UUID(args.since_run) if args.since_run else None
    result = run_normalize(repo, since_run_id=since, process_all=args.all)
    print(
        f"normalize_run={result.run_id} status={result.status} "
        f"accepted={result.accepted} rejected={result.rejected} "
        f"duplicates={result.duplicates}"
    )
    return 0 if result.status == "success" else 1


def cmd_source(args: argparse.Namespace) -> int:
    _settings, repo = _repo()
    if args.action == "status":
        rows = repo.list_source_status()
        for row in rows:
            print(
                f"{row.source_type}\t{row.status}\tenabled={row.enabled}\t"
                f"raw={row.raw_count}\tnormalized={row.normalized_count}\t"
                f"last={row.last_run_status}\tavailable={row.last_source_available}\t"
                f"{row.notes or ''}"
            )
        live = [r.source_type for r in rows if r.status == "live" and r.normalized_count > 0]
        print(f"normalized_source_types={','.join(live) or '(none)'} n={len(live)}")
        return 0
    if not args.source_type:
        print("source_type required for enable/disable", file=sys.stderr)
        return 2
    enabled = args.action == "enable"
    repo.set_enabled(args.source_type, enabled)
    print(f"{args.source_type} enabled={enabled} (unavailable sources are not imputed)")
    return 0


def cmd_spot_check(args: argparse.Namespace) -> int:
    _settings, repo = _repo()
    rows = repo.list_normalized(
        limit=args.limit, eligible_only=True, random_sample=True
    )
    if not rows:
        print("no eligible normalized_documents; run ingest + normalize first", file=sys.stderr)
        return 1
    failed = 0
    print(f"=== {len(rows)} eligible normalized rows ===")
    for rec in rows:
        raw = repo.get_raw(rec.raw_id)
        pii = spotcheck_text_failures(rec.text_original)
        user_fields = analysis_record_username_fields(rec)
        author = raw.author_hash if raw else None
        ok = not pii and not user_fields
        if author and (len(author) != 64 or author != author.lower()):
            pii = [*pii, "author_hash_not_hmac"]
            ok = False
        if not ok:
            failed += 1
        preview = rec.text_original.replace("\n", " ")[:180]
        print(
            f"{'FAIL' if not ok else 'ok'} id={rec.id} lang={rec.language} "
            f"cat={rec.product_category} hash_author={author[:8] + '…' if author else 'none'} "
            f"pii={pii or '-'} | {preview}"
        )
    rejects = repo.list_raw_rejected(limit=args.limit)
    print(f"=== {len(rejects)} rejected raw rows (sample) ===")
    for env in rejects:
        preview = (env.raw_text or "").replace("\n", " ")[:140]
        print(f"reject={env.reject_reason} id={env.source_id} | {preview}")
    print(f"spot-check failures={failed}/{len(rows)}")
    return 1 if failed else 0


def cmd_extract(args: argparse.Namespace) -> int:
    settings, repo = _repo()
    lock = _pipeline_lock(settings, "extract")
    if lock is None:
        return 1
    try:
        return _cmd_extract_unlocked(args, settings, repo)
    finally:
        lock.release()


def _cmd_extract_unlocked(args: argparse.Namespace, settings, repo) -> int:
    resume = UUID(args.resume_after) if args.resume_after else None
    try:
        result = run_extract(
            repo,
            settings,
            limit=args.limit,
            resume_after=resume,
            retry_failed=not args.no_retry_failed,
        )
    except (GroqAuthError, GroqConfigError) as exc:
        print(f"extract aborted: {exc}", file=sys.stderr)
        return 1
    print(
        f"extract_run={result.run_id} status={result.status} "
        f"ok={result.ok} failed={result.failed} skipped={result.skipped} "
        f"tokens={result.prompt_tokens}/{result.completion_tokens}"
    )
    if result.error_message:
        print(f"error={result.error_message}", file=sys.stderr)
    return 0 if result.status == "success" else 1


def cmd_embed(args: argparse.Namespace) -> int:
    settings, repo = _repo()
    lock = _pipeline_lock(settings, "embed")
    if lock is None:
        return 1
    try:
        return _cmd_embed_unlocked(args, settings, repo)
    finally:
        lock.release()


def _cmd_embed_unlocked(args: argparse.Namespace, settings, repo) -> int:
    try:
        result = run_embed(repo, settings, limit=args.limit, force=args.force)
    except Exception as exc:
        print(f"embed aborted: {exc}", file=sys.stderr)
        return 1
    print(
        f"embed_run={result.run_id} status={result.status} "
        f"encoded={result.encoded} skipped={result.skipped} "
        f"model={result.embedding_model} rev={result.embedding_revision}"
    )
    return 0 if result.status == "success" else 1


def cmd_enrich(args: argparse.Namespace) -> int:
    extract_ns = argparse.Namespace(
        limit=args.limit,
        resume_after=args.resume_after,
        no_retry_failed=args.no_retry_failed,
    )
    extract_code = cmd_extract(extract_ns)
    if extract_code != 0:
        return extract_code
    embed_ns = argparse.Namespace(limit=args.limit, force=args.force)
    return cmd_embed(embed_ns)


def _pipeline_lock(settings, label: str) -> ExclusiveFileLock | None:
    settings.ensure_runtime_dirs()
    lock = ExclusiveFileLock(
        settings.lock_path / "pipeline.lock",
        stale_seconds=int(settings.lock_stale_seconds),
    )
    if not lock.acquire():
        print(
            f"{label} skipped: pipeline lock held (EC-IN-16 / EC-OP-06)",
            file=sys.stderr,
        )
        return None
    return lock


def cmd_pipeline(args: argparse.Namespace) -> int:
    settings = load_settings()
    lock = _pipeline_lock(settings, "pipeline")
    if lock is None:
        return 1
    try:
        return _cmd_pipeline_unlocked(args)
    finally:
        lock.release()


def _cmd_pipeline_unlocked(args: argparse.Namespace) -> int:
    source_arg = args.sources or "all"
    parts = [p.strip() for p in source_arg.split(",") if p.strip()]
    ingest_code = 0
    if len(parts) > 1:
        any_failed = False
        for part in parts:
            code = cmd_ingest(
                argparse.Namespace(
                    source=part,
                    max_reviews=args.max_items,
                    max_items=args.max_items,
                    force_full=args.force_full,
                )
            )
            if code == 2:
                return 2
            if code:
                any_failed = True
        ingest_code = 1 if any_failed else 0
    else:
        ingest_code = cmd_ingest(
            argparse.Namespace(
                source=parts[0] if parts else "all",
                max_reviews=args.max_items,
                max_items=args.max_items,
                force_full=args.force_full,
            )
        )
    normalize_ns = argparse.Namespace(since_run=None, all=False)
    normalize_code = cmd_normalize(normalize_ns)
    if args.skip_enrich:
        return ingest_code or normalize_code
    enrich_ns = argparse.Namespace(
        limit=args.limit,
        resume_after=args.resume_after,
        no_retry_failed=args.no_retry_failed,
        force=args.force,
    )
    enrich_code = cmd_enrich(enrich_ns)
    if getattr(args, "cluster", False):
        cluster_ns = argparse.Namespace(
            mode="auto",
            no_label=False,
            skip_metrics=False,
            algorithm=None,
            run_eval=False,
        )
        cluster_code = cmd_cluster(cluster_ns)
        return ingest_code or normalize_code or enrich_code or cluster_code
    return ingest_code or normalize_code or enrich_code


def cmd_dump(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_runtime_dirs()
    dest = Path(args.out) if args.out else settings.review_dump_path / "phase3"
    if args.live:
        repo = MemoryRepository()
        store = LocalObjectStore(settings.raw_store_path)
        limit = args.max_items
        any_ok = False
        for source in INGEST_ALL_SOURCES:
            run = run_source_ingest(
                source,
                repo,
                settings,
                object_store=store,
                max_items=limit,
                force_full=False,
            )
            _print_ingest_run(run)
            if run.status == "success" and (run.rows_upserted or 0) > 0:
                any_ok = True
        if not any_ok:
            print("live fetch stored no rows; check network / API keys", file=sys.stderr)
        norm = run_normalize(repo)
        print(
            f"normalize_run={norm.run_id} status={norm.status} "
            f"accepted={norm.accepted} rejected={norm.rejected}"
        )
    else:
        repo = PostgresRepository(settings.database_url)
    result = write_review_dump(repo, dest)
    print(f"dump={result.output_dir}")
    print(
        f"live_source_types={','.join(result.live_source_types) or '(none)'} "
        f"n={len(result.live_source_types)}"
    )
    if not result.source_counts:
        print("no documents; run ingest + normalize, then dump again", file=sys.stderr)
        return 1
    for source, counts in result.source_counts.items():
        print(
            f"{source}\traw={counts['raw']}\tnormalized={counts['normalized']}\t"
            f"rejected={counts['rejected']}\ttheme_hit={counts['theme_hit']}"
        )
    print("files:", ", ".join(result.files))
    return 0


def cmd_extract_eval(args: argparse.Namespace) -> int:
    _settings, repo = _repo()
    report = build_extract_eval_report(repo, limit=args.limit)
    print(
        f"extractions={report.total} ok={report.ok} failed={report.failed} "
        f"pending={report.pending} ok_rate={report.ok_rate:.0%} "
        f"quote_spans={report.quote_span_ok}/{report.quote_span_checked} "
        f"intent_mode_distinct={report.intent_mode_distinct} "
        f"metrics_eligible={report.metrics_eligible}"
    )
    for note in report.notes:
        print(f"note={note}", file=sys.stderr)
    if report.total == 0:
        print("no extractions; run extract first", file=sys.stderr)
        return 1
    if report.ok_rate < 0.8 or report.quote_span_ok != report.quote_span_checked:
        return 1
    if not report.intent_mode_distinct:
        return 1
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    settings, repo = _repo()
    model = load_bge_model(settings)
    vector = encode_query(model, args.query, model_id=settings.bge_model_id)
    rows = repo.nearest_chunks(
        vector,
        k=args.k,
        friction_tag=args.friction_tag,
        intent_mode=args.intent_mode,
    )
    if not rows:
        print("no embedded chunks; run embed first", file=sys.stderr)
        return 1
    for rank, chunk in enumerate(rows, start=1):
        preview = chunk.text.replace("\n", " ")[:180]
        print(
            f"{rank}. sim={chunk.similarity:.3f} doc={chunk.document_id} "
            f"mode={chunk.intent_mode} friction={chunk.friction_tags} | {preview}"
        )
    return 0


def cmd_cluster(args: argparse.Namespace) -> int:
    settings, repo = _repo()
    lock = _pipeline_lock(settings, "cluster")
    if lock is None:
        return 1
    try:
        try:
            require_frozen_constants(settings)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        try:
            result = run_cluster(
                repo,
                settings,
                mode=args.mode,
                label=not args.no_label,
                skip_metrics=args.skip_metrics,
                force_algorithm=args.algorithm,
            )
        except (GroqAuthError, GroqConfigError) as exc:
            print(f"cluster aborted: {exc}", file=sys.stderr)
            return 1
        print(
            f"cluster_run={result.run_id} status={result.status} mode={result.mode} "
            f"algorithm={result.algorithm} corpus={result.corpus} "
            f"docs={result.n_documents} clustered={result.n_clustered} "
            f"noise={result.n_noise} themes={result.n_themes} "
            f"incremental={result.n_incremental}"
        )
        if result.caveat:
            print(f"caveat={result.caveat}")
        if result.error_message:
            print(f"error={result.error_message}", file=sys.stderr)
        code = 0 if result.status == "success" else 1
        if code == 0 and getattr(args, "run_eval", False):
            eval_ns = argparse.Namespace(check=False, gold=None, out=None)
            eval_code = cmd_eval(eval_ns)
            return eval_code or code
        return code
    finally:
        lock.release()


def cmd_metrics(args: argparse.Namespace) -> int:
    settings, repo = _repo()
    run_id = UUID(args.cluster_run) if args.cluster_run else None
    try:
        result = run_metrics(repo, settings, cluster_run_id=run_id)
    except Exception as exc:
        print(f"metrics aborted: {exc}", file=sys.stderr)
        return 1
    print(
        f"cluster_run={result.cluster_run_id} snapshots={result.n_snapshots} "
        f"themes={result.n_themes} status={result.status}"
    )
    return 0 if result.status == "success" else 1


def cmd_themes(args: argparse.Namespace) -> int:
    _settings, repo = _repo()
    run = (
        repo.get_cluster_run(UUID(args.cluster_run))
        if args.cluster_run
        else repo.latest_cluster_run()
    )
    if run is None:
        print("no successful cluster_run; run cluster first", file=sys.stderr)
        return 1
    themes = {t.id: t for t in repo.list_themes(run.id) if t.published}
    metrics = repo.list_theme_metrics(
        cluster_run_id=run.id, slice_kind="global", published_only=True
    )
    if not metrics:
        print("no theme_metrics snapshots; run metrics", file=sys.stderr)
        return 1
    refreshed = run.started_at.isoformat() if run.started_at else ""
    print(
        f"cluster_run={run.id} algorithm={run.algorithm} corpus={run.corpus} "
        f"refreshed={refreshed}"
    )
    for rank, row in enumerate(metrics, start=1):
        theme = themes.get(row.theme_id)
        name = theme.name if theme else str(row.theme_id)
        flags = []
        if theme and theme.hypothesis_flag:
            flags.append("hypothesis")
        if theme:
            flags.append(f"mode={theme.bookmark_vs_stall}")
        print(
            f"{rank}. {name}  SoV={row.share_of_voice:.4f}  n={row.mention_count}  "
            f"impact={row.impact_score:.4f}  conf={row.data_confidence:.3f}  "
            f"sev={row.sentiment_severity:.3f}  breadth={row.segment_breadth:.3f}  "
            f"{' '.join(flags)}  unavailable={','.join(row.unavailable_sources) or '-'}"
        )
    print(f"denominator={metrics[0].denominator_definition}")
    return 0


def cmd_ngrams(args: argparse.Namespace) -> int:
    settings, repo = _repo()
    run_id = UUID(args.cluster_run) if args.cluster_run else None
    try:
        result = run_ngrams(repo, settings, cluster_run_id=run_id)
    except Exception as exc:
        print(f"ngrams aborted: {exc}", file=sys.stderr)
        return 1
    print(f"cluster_run={result.cluster_run_id} rows={result.n_rows} status={result.status}")
    return 0 if result.status == "success" else 1


def cmd_report(args: argparse.Namespace) -> int:
    settings, repo = _repo()
    try:
        result = run_report(repo, settings)
    except Exception as exc:
        print(f"report aborted: {exc}", file=sys.stderr)
        return 1
    print(
        f"report={result.report_id} status={result.status} "
        f"first_week={result.first_week} path={result.path}"
    )
    return 0 if result.status == "success" else 1


def cmd_index(_args: argparse.Namespace) -> int:
    settings, repo = _repo()
    from src.cluster.keyword_themes import run_local_index

    try:
        result = run_local_index(repo, settings)
    except Exception as exc:
        print(f"index aborted: {exc}", file=sys.stderr)
        return 1
    print(
        "index ok "
        f"extract={result['extract_ok']} themes={result['themes']} "
        f"assigned={result['assigned']} snapshots={result['snapshots']} "
        f"ngrams={result['ngrams']} report={result['report_id']} "
        f"cluster_run={result['cluster_run_id']}"
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from src.api.serve import run_serve

    return run_serve(host=args.host, port=args.port, migrate=bool(getattr(args, "migrate", False)))


def cmd_eval(args: argparse.Namespace) -> int:
    from src.evals.runner import run_from_cli

    settings = load_settings()
    try:
        require_frozen_constants(settings)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        if not getattr(args, "check", False):
            return 2
        print("eval --check still records the mismatch", file=sys.stderr)
    return run_from_cli(args, settings=settings)


def cmd_copilot(args: argparse.Namespace) -> int:
    import json

    from src.api.copilot import CopilotService
    from src.api.filters import filters_from_params

    settings, repo = _repo()
    filters = filters_from_params(
        source_type=args.source_type,
        product_category=args.product_category,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    service = CopilotService(repo, settings)
    turn = service.query_turn(args.question, filters)
    print(json.dumps(turn, default=str, indent=2))
    return 0 if turn.get("status") in {"ok", "declined", "refused"} else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Apply SQL migrations and verify foundation tables").set_defaults(
        func=cmd_migrate
    )

    smoke = sub.add_parser("smoke", help="Postgres + Groq + BGE-M3 foundation checks")
    smoke.add_argument("--skip-db", action="store_true", help="Skip Postgres foundation check")
    smoke.add_argument("--skip-groq", action="store_true", help="Skip Groq models.list / 1-token chat")
    smoke.add_argument("--skip-bge", action="store_true", help="Skip loading BGE-M3 (~2GB first download)")
    smoke.set_defaults(func=cmd_smoke)

    ingest = sub.add_parser("ingest", help="Run a source connector (or all implemented sources)")
    ingest.add_argument(
        "source",
        help="play_store | app_store | reddit | youtube | x | all",
    )
    ingest.add_argument("--max-reviews", type=int, default=None)
    ingest.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Alias of --max-reviews for non-store sources",
    )
    ingest.add_argument(
        "--force-full",
        action="store_true",
        help="Ignore watermark and re-fetch newest pages",
    )
    ingest.set_defaults(func=cmd_ingest)

    normalize = sub.add_parser("normalize", help="Relevance, language, PII, exact-hash dedup")
    normalize.add_argument("--since-run", dest="since_run", default=None, help="ingest_run UUID")
    normalize.add_argument(
        "--all",
        action="store_true",
        help="Ignored; pending + stale is the default without --since-run",
    )
    normalize.set_defaults(func=cmd_normalize)

    source = sub.add_parser("source", help="Enable/disable sources without inventing metrics")
    source.add_argument("action", choices=["status", "enable", "disable"])
    source.add_argument("source_type", nargs="?", default=None)
    source.set_defaults(func=cmd_source)

    spot = sub.add_parser("spot-check", help="EV-1-05 PII / username rubric on sample rows")
    spot.add_argument("--limit", type=int, default=20)
    spot.set_defaults(func=cmd_spot_check)

    extract = sub.add_parser("extract", help="Groq structured extraction for normalized docs")
    extract.add_argument("--limit", type=int, default=None)
    extract.add_argument("--resume-after", dest="resume_after", default=None, help="document UUID cursor")
    extract.add_argument(
        "--no-retry-failed",
        action="store_true",
        help="Do not re-queue extraction_status=failed rows",
    )
    extract.set_defaults(func=cmd_extract)

    embed = sub.add_parser("embed", help="Chunk + local BGE-M3 embeddings (no Groq vectors)")
    embed.add_argument("--limit", type=int, default=None)
    embed.add_argument("--force", action="store_true", help="Re-encode chunks that already have vectors")
    embed.set_defaults(func=cmd_embed)

    enrich = sub.add_parser("enrich", help="Extract then embed")
    enrich.add_argument("--limit", type=int, default=None)
    enrich.add_argument("--resume-after", dest="resume_after", default=None)
    enrich.add_argument("--no-retry-failed", action="store_true")
    enrich.add_argument("--force", action="store_true")
    enrich.set_defaults(func=cmd_enrich)

    pipeline = sub.add_parser(
        "pipeline",
        help="Ingest sources, normalize new docs, then Groq extract + BGE embed (skips unchanged hashes)",
    )
    pipeline.add_argument(
        "--sources",
        default="all",
        help="play_store, app_store, reddit, youtube, x, or all",
    )
    pipeline.add_argument("--max-items", type=int, default=None)
    pipeline.add_argument("--force-full", action="store_true")
    pipeline.add_argument("--skip-enrich", action="store_true", help="Stop after normalize")
    pipeline.add_argument("--limit", type=int, default=None, help="Extract/embed doc cap")
    pipeline.add_argument("--resume-after", dest="resume_after", default=None)
    pipeline.add_argument("--no-retry-failed", action="store_true")
    pipeline.add_argument("--force", action="store_true", help="Re-embed existing vectors")
    pipeline.add_argument(
        "--cluster",
        action="store_true",
        help="After enrich, run HDBSCAN + Groq labels + theme_metrics",
    )
    pipeline.set_defaults(func=cmd_pipeline)

    dump = sub.add_parser(
        "dump",
        help="Write scrubbed JSONL / CSV / Markdown under data/review/phase3 for operator review",
    )
    dump.add_argument(
        "--out",
        default=None,
        help="Output directory (default: data/review/phase3)",
    )
    dump.add_argument(
        "--live",
        action="store_true",
        help="Fetch connectors into memory and write files (no Postgres required)",
    )
    dump.add_argument(
        "--max-items",
        type=int,
        default=40,
        help="Per-source fetch cap when using --live",
    )
    dump.set_defaults(func=cmd_dump)

    extract_eval = sub.add_parser("extract-eval", help="Schema / quote-span / intent_mode report")
    extract_eval.add_argument("--limit", type=int, default=50)
    extract_eval.set_defaults(func=cmd_extract_eval)

    search = sub.add_parser("search", help="BGE nearest-neighbor over chunks")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=8)
    search.add_argument("--friction-tag", dest="friction_tag", default=None)
    search.add_argument("--intent-mode", dest="intent_mode", default=None)
    search.set_defaults(func=cmd_search)

    cluster = sub.add_parser(
        "cluster",
        help="HDBSCAN (k-means fallback) + Groq theme labels; writes theme_metrics",
    )
    cluster.add_argument(
        "--mode",
        choices=["auto", "recluster", "incremental"],
        default="auto",
        help="auto reclusters after CLUSTER_RECLUSTER_NEW_DOCS new eligible docs",
    )
    cluster.add_argument(
        "--no-label",
        action="store_true",
        help="Skip Groq; heuristic names from friction/intent tags",
    )
    cluster.add_argument(
        "--skip-metrics",
        action="store_true",
        help="Do not write theme_metrics snapshots",
    )
    cluster.add_argument(
        "--algorithm",
        choices=["hdbscan", "kmeans"],
        default=None,
        help="Force algorithm (default: HDBSCAN, k-means only on error)",
    )
    cluster.add_argument(
        "--eval",
        dest="run_eval",
        action="store_true",
        help="After a successful cluster, run the Q1–Q9 gold scorer (Phase 7)",
    )
    cluster.set_defaults(func=cmd_cluster)

    metrics_cmd = sub.add_parser(
        "metrics",
        help="Recompute theme_metrics snapshots for a cluster_run (SQL-shared formulas)",
    )
    metrics_cmd.add_argument(
        "--cluster-run",
        dest="cluster_run",
        default=None,
        help="cluster_run UUID (default: latest successful)",
    )
    metrics_cmd.set_defaults(func=cmd_metrics)

    themes_cmd = sub.add_parser(
        "themes",
        help="Print ranked opportunity areas (SoV, impact, confidence)",
    )
    themes_cmd.add_argument(
        "--cluster-run",
        dest="cluster_run",
        default=None,
        help="cluster_run UUID (default: latest successful)",
    )
    themes_cmd.set_defaults(func=cmd_themes)

    ngrams_cmd = sub.add_parser("ngrams", help="Precompute 1–3 grams (en/hi stopwords)")
    ngrams_cmd.add_argument("--cluster-run", dest="cluster_run", default=None)
    ngrams_cmd.set_defaults(func=cmd_ngrams)

    report_cmd = sub.add_parser("report", help="Weekly theme_metrics diff → PDF on disk")
    report_cmd.set_defaults(func=cmd_report)

    index_cmd = sub.add_parser(
        "index",
        help="Heuristic extract + keyword themes + metrics + n-grams + report (no Groq required)",
    )
    index_cmd.set_defaults(func=cmd_index)

    serve_cmd = sub.add_parser("serve", help="Run FastAPI Query API + Copilot (localhost by default)")
    serve_cmd.add_argument("--host", default=None, help="Bind host (default API_HOST=127.0.0.1)")
    serve_cmd.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: PORT env, then API_PORT=8000)",
    )
    serve_cmd.add_argument(
        "--migrate",
        action="store_true",
        help="Apply SQL migrations before serving (Railway/hosted boot)",
    )
    serve_cmd.set_defaults(func=cmd_serve)

    copilot_cmd = sub.add_parser("copilot", help="Phase 5 debug: POST-equivalent Copilot turn on stdout")
    copilot_cmd.add_argument("question")
    copilot_cmd.add_argument("--source-type", dest="source_type", default=None)
    copilot_cmd.add_argument("--product-category", dest="product_category", default=None)
    copilot_cmd.add_argument("--date-from", dest="date_from", default=None)
    copilot_cmd.add_argument("--date-to", dest="date_to", default=None)
    copilot_cmd.set_defaults(func=cmd_copilot)

    eval_cmd = sub.add_parser(
        "eval",
        help="Phase 7 Q1–Q9 gold harness; writes evals/runs/7/<date>/score.json",
    )
    eval_cmd.add_argument(
        "--check",
        action="store_true",
        help="Validate gold coverage, frozen constants, and DoD without calling Groq",
    )
    eval_cmd.add_argument(
        "--gold",
        default=None,
        help="Gold JSONL (default: evals/q1_q9.jsonl)",
    )
    eval_cmd.add_argument(
        "--out",
        default=None,
        help="Artifact directory (default: evals/runs/7/<today>)",
    )
    eval_cmd.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
