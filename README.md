# Myntra Discovery Engine

Research prototype: ingest public Myntra conversation data, structure it, and (later phases) quantify wishlist-to-purchase themes.

**Phase 0** is the foundation: a runnable repo, Postgres + pgvector, a frozen raw envelope, and Groq + local BGE-M3 smokes. Generation is Groq-only; embeddings are local BGE-M3. Do not add OpenAI chat or embedding keys.

**Deploy:** FastAPI Query API and Next.js dashboard are two [Vercel](https://vercel.com) projects from [bestshubhamagarwal-web/Myntra-AI-Engine](https://github.com/bestshubhamagarwal-web/Myntra-AI-Engine). Postgres + pgvector lives on [Neon](https://neon.tech) (Vercel Marketplace). See [docs/deployment-plan.md](docs/deployment-plan.md).

| Surface | Live URL |
| ------- | -------- |
| Dashboard | https://myntra-ai-engine-web.vercel.app |
| Query API | https://myntra-ai-engine-server.vercel.app |

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

Set `GROQ_API_KEY` in `.env`. Set `AUTHOR_HMAC_SECRET` to a long random string before any real ingest.

Start Postgres with pgvector (Docker Desktop required):

```powershell
docker compose up -d
python -m src.cli migrate
```

`migrate` fails fast (8s) if nothing is listening on `DATABASE_URL`. After apply it queries empty foundation tables (`raw_documents`, `normalized_documents`, `ingest_runs`, `ingest_queries`) and checks `chunks.embedding` is `vector(1024)`.

Object-store snapshots go to `./data/raw/`. Scrubbed review dumps (JSONL / CSV / Markdown) go to `./data/review/phase3/` via `python -m src.cli dump`. BGE weights cache under `HF_HOME` (`./data/models` by default).

## Smoke (Phase 0)

```powershell
python -m src.cli smoke
```

This checks:

1. Postgres foundation (tables, unique `(source_type, source_id)`, query seeds, pgvector 1024)
2. Groq `models.list` (or a 1-token chat) at `https://api.groq.com/openai/v1`
3. Local BGE-M3: encode one sentence and assert dimension **1024**

First BGE load downloads `BAAI/bge-m3` (~2GB) into `./data/models`. If Hugging Face is blocked, vendor the weights under that folder and set `BGE_MODEL_ID` to the local path. The smoke **fails** if the dim is not 1024; it does not truncate, pad, or invent vectors.

Skip pieces while iterating:

```powershell
python -m src.cli smoke --skip-bge
python -m src.cli smoke --skip-groq --skip-bge
```

CI may skip live Groq/BGE; record a developer-machine artifact under `evals/runs/0/` for the SHA.

## Layout

Matches Architecture §19: `src/ingest`, `normalize`, `extract`, `embed`, `cluster`, `metrics`, `api`, `prompts/`. `web/` is the Phase 6 Next.js product UI.

## Phase 2 — Groq extract + local BGE embeddings

Normalized docs → structured tags (`extractions`) and 1024-d chunk vectors. Generation is Groq-only; embeddings never leave the machine.

```powershell
python -m src.cli migrate
python -m src.cli extract --limit 50
python -m src.cli embed --limit 50
python -m src.cli extract-eval --limit 50
python -m src.cli search "Myntra size too small / runs small" -k 8
```

`enrich --limit 50` runs extract then embed. Re-running extract skips unchanged `content_hash` (no Groq re-bill). Invalid Groq JSON is retried, then stored as `extraction_status=failed` and **excluded** from later theme metrics (`extraction_metrics_eligible` / `metrics_eligible`). Failed rows stay on `extractions` for audit.

Hinglish/Hindi is sent to Groq as `text_original`; any gloss is prompt-only and does not replace stored text. BGE encodes the same scrubbed original text (no Groq vectors, no `en-v1.5` query prefix for M3).

Phase 2 auto evals: `pytest tests/test_eval_phase2.py`. Live Groq/BGE sample (EV-2-01/10/12) is opt-in.

## Tests

```powershell
pytest
```

Phase 0 auto checks live in `tests/test_eval_phase0.py`. Phase 1: `tests/test_eval_phase1.py`. Phase 2: `tests/test_eval_phase2.py`. Phase 3: `tests/test_eval_phase3.py`. Phase 4: `tests/test_eval_phase4.py`. Phase 5: `tests/test_eval_phase5.py`. Phase 6: `tests/test_eval_phase6.py`. Phase 7: `tests/test_eval_phase7.py`. Live Groq/BGE smokes are opt-in (`GROQ_API_KEY` / `RUN_LIVE_BGE=1` / `RUN_LIVE_EXTRACT=1`).

## Phase 1 pipeline (already in tree)

Play Store → raw → normalized, with PII scrubbed. Not required to close Phase 0.

```powershell
python -m src.cli ingest play_store --max-reviews 200
python -m src.cli normalize --since-run <ingest_run_uuid>
python -m src.cli spot-check --limit 20
python -m src.cli source status
```

Re-running ingest upserts on `(play_store, source_id)` and does not duplicate rows. Incremental pulls use `max(published_at)` excluding future-dated anomalies. Reviews with a null publish time are still stored.

Normalize applies: relevance gate → language (`en` / `hi` / `hinglish` / `other`) → exact-hash dedup → PII scrub → keyword `product_category` (else `unknown`). Rejects stay on `raw_documents` with an auditable reason and are **not** copied to `normalized_documents`.

### Disable a source (no imputed metrics)

```powershell
python -m src.cli source disable play_store
```

Or set `PLAY_STORE_ENABLED=false` in `.env`. The next ingest records `skipped_disabled` / `source_available=false`. Counts are not invented.

If Play Store returns 403/429 after retries, the connector **stops** and the run is `failed`. Do not scrape around blocks.

Live Play Store ingest (EV-1-01/02/04) is not part of CI; use the CLI above, then `spot-check`.

## Phase 3 — Multi-source ingest

Same envelope and normalize/extract path. Implemented connectors: Play Store, App Store (iTunes RSS), Reddit (PRAW or public JSON), YouTube Data API comments. Optional fifth: X API v2 recent search. Instagram, Facebook, Quora, and on-site Myntra Q&A/reviews stay **unavailable** (no imputed volumes).

```powershell
python -m src.cli migrate
python -m src.cli ingest all --max-items 50
python -m src.cli normalize
python -m src.cli enrich --limit 50
python -m src.cli source status
```

`pipeline` runs ingest → normalize → extract → embed and only bills Groq / encodes BGE for **new or changed** docs:

```powershell
python -m src.cli pipeline --sources all --max-items 50 --limit 50
```

Per source:

```powershell
python -m src.cli ingest app_store --max-items 80
python -m src.cli ingest reddit --max-items 40
python -m src.cli ingest youtube --max-items 80
python -m src.cli ingest x --max-items 40
```

Dump the live corpus to files for qualitative review (EV-3-11). Text is PII-scrubbed; usernames are never written. Open `data/review/phase3/sample.md` first, then per-source `.jsonl` or `all.csv`.

```powershell
python -m src.cli dump
python -m src.cli dump --live --max-items 40
```

`--live` fetches Play / App Store / Reddit / YouTube / X into memory and writes the same files even if Postgres is down. Object-store snapshots still go to `./data/raw/`.

YouTube needs `YOUTUBE_API_KEY` for the official Data API. If the key is empty, the connector uses public Invidious / Piped / Innertube (same idea as Reddit public JSON). Reddit works with public JSON if `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` are empty; PRAW is used when both are set. X uses public Nitter/RSSHub RSS without `X_BEARER_TOKEN`, or the official API when a bearer is set. Instagram, Facebook, Quora, and on-site Myntra Q&A stay **unavailable** — `source status` shows that honestly, not a fake zero series.

App IDs are locked to the Myntra apps (`com.myntra.android`, iTunes `907394059`). Competitor store pages are not crawled; `"Myntra vs AJIO"` is a search query for comparison talk inside Myntra-relevant threads.

Disable any source without inventing metrics:

```powershell
python -m src.cli source disable youtube
python -m src.cli source status
```

n8n **or** Task Scheduler wrapping `python -m src.cli pipeline` (see Phase 7). Phase 3 auto evals: `pytest tests/test_eval_phase3.py`.

## Phase 4 — Clustering, metrics, impact score

Named **opportunity areas** from BGE embeddings, then shared SQL metrics (Architecture §8.3–8.6). The UI is still Phase 6; Copilot HTTP is Phase 5. This phase writes `themes` + `theme_metrics` only.

```powershell
python -m src.cli migrate
python -m src.cli cluster
python -m src.cli themes
```

`cluster` runs HDBSCAN on document-level BGE vectors (`CLUSTER_MIN_*` frozen in `.env`). Tiny corpora and all-noise results stay **0–few** themes — k-means is only used if HDBSCAN errors, and never with a forced k=10. `not_applicable` / empty friction+intent and `extraction_status=failed` are excluded. Noise is not ranked.

Groq labels each published cluster with `GROQ_MODEL_LIGHT` (`prompts/theme_label.md`): `name`, `description`, `hypothesis_flag`, `bookmark_vs_stall`. A theme is published only if it has ≥1 document and a verbatim quote. Skip Groq while debugging:

```powershell
python -m src.cli cluster --no-label
python -m src.cli cluster --mode incremental
python -m src.cli metrics
```

`--mode incremental` kNN-assigns new eligible docs (`assignment_method=knn_incremental`). Recluster matches centroids so `theme_id` stays stable. Impact is:

```
impact_score = share_of_voice × sentiment_severity × segment_breadth × data_confidence
```

SoV denominator is eligible normalized docs after relevance + quality (not imputed sources). Missing connectors appear in `unavailable_sources`. Delight-only clusters get blocking severity 0. Monetary/coupon themes are **not** filtered. `unknown` is a real segment slice.

```powershell
python -m src.cli pipeline --sources all --max-items 50 --limit 50 --cluster
pytest tests/test_eval_phase4.py
```

A Play-Store-only cluster is allowed for pipeline debug (`cluster_runs.corpus=play_only`); ranks are not product-ready until multi-source docs are included.

## Phase 5 — Query API + Copilot + jobs

HTTP contract for Phase 6. The UI never computes SoV, impact, or confidence. Bind defaults to **localhost**. Set `API_SHARED_SECRET` before exposing the port.

```powershell
python -m src.cli migrate
python -m src.cli ngrams
python -m src.cli report
python -m src.cli serve
```

OpenAPI: `http://127.0.0.1:8000/docs`. Routes: `/metrics/overview|themes|segments|trends|ngrams`, `/evidence` (JSON or `?format=csv`), `/reports`, `/reports/{id}` (PDF), `POST /copilot/query`.

Global filters on metrics/evidence: `date_from`, `date_to`, `source_type`, `product_category`, plus segment fields where the slice exists. Failed ingest shows **source unavailable** (not last week’s volume). CSV export is scrubbed text only.

Copilot uses Groq tool-calling against the same Query methods and the same local BGE-M3 checkpoint for retrieval. Numbers in the answer must appear in tool JSON. Thin slices decline quantification; product solutions and internal funnel questions are refused.

```powershell
python -m src.cli copilot "Compare footwear vs ethnic-wear wishlist drop-off reasons"
pytest tests/test_eval_phase5.py
```

## Phase 6 — Next.js product UI

All Architecture §12 views plus Copilot, in `web/`. Visual system matches `docs/stitch_discovery_wishlist_analytics`. The UI **never** computes SoV, impact, or confidence — charts render Query API series.

```powershell
python -m src.cli serve
cd web
copy .env.example .env.local
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Routes: Overview, Themes, Evidence, Categories, Trends, Segments, Sources, Phrases, Reports, Copilot. Filters are stored in the URL. Theme cards and Copilot citation chips open the same evidence drawer (quote → source URL or “link unavailable”).

```powershell
pytest tests/test_eval_phase6.py
```

## Phase 7 — Q1–Q9 eval, runbooks, hardening

Hold-out gold file `evals/q1_q9.jsonl` (two paraphrases per Q1–Q9 plus refuse probes). The scorer checks citation, metric⊆API JSON, expected behavior (including thin-corpus caveat/decline), no solutioning, Q7 bookmark/stall split, and jailbreak SoV.

```powershell
python -m src.cli eval --check
python -m src.cli eval
python -m src.cli cluster --eval
```

`--check` validates gold coverage, frozen constants (`C_MAX=200`, `S_MAX=4`, Groq/BGE ids), and the project definition of done — no Groq call. Live `eval` writes `evals/runs/7/<date>/score.json` with git SHA, prompt versions, `cluster_run_id`, and model ids. Do not change `GROQ_MODEL` mid-run.

Operator playbook: [docs/Runbook.md](docs/Runbook.md). Pause a source without inventing metrics:

```powershell
python -m src.cli source disable play_store
```

Overview, theme cards, and Copilot all list `unavailable_sources`. Recluster keeps `theme_id` via centroid match; the header shows **themes refreshed**.

**Schedules (pick one):** import `ops/n8n/discovery-pipeline.json`, or `ops/windows/Register-PipelineTask.ps1`, or `ops/cron/discovery.crontab`. Overlapping jobs take `data/locks/pipeline.lock` and skip rather than double-write (EC-IN-16).

**Live vs unavailable:** Play Store, App Store, Reddit, YouTube, and X are implemented (YouTube/X use official APIs when keyed, otherwise public Invidious/Nitter-style hosts). Instagram, Facebook, Quora, and on-site Myntra Q&A stay unavailable. Groq TPM: back off, smaller `--limit` — never switch LLM host. BGE weights cache under `HF_HOME` (`./data/models`).

```powershell
pytest tests/test_eval_phase7.py
```

