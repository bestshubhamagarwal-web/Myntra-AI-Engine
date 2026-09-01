# Operator runbook

**Project:** Myntra Discovery Engine  
**Companion:** [Architecture.md](./Architecture.md) §18, [eval.md](./eval.md) Phase 7, [ImplementationPlan.md](./ImplementationPlan.md) Phase 7

This is the operator playbook. Pause a connector rather than bypassing a block. Do not impute volume. Do not switch Groq for another LLM host. Do not change `C_MAX` / `S_MAX` / model ids silently.

---

## Daily / weekly loop

```powershell
python -m src.cli pipeline --sources all --cluster
python -m src.cli ngrams
python -m src.cli report
python -m src.cli source status
python -m src.cli eval --check
```

After a cluster refresh that should be scored (needs `GROQ_API_KEY` + populated Copilot path):

```powershell
python -m src.cli cluster --eval
# or
python -m src.cli eval
```

Artifacts: `evals/runs/7/<date>/score.json` (SHA, `GROQ_MODEL`, `GROQ_MODEL_LIGHT`, `BGE_MODEL_ID` + revision, prompt versions, `cluster_run_id`, `C_max`, `S_max`).

Schedules (pick **one** orchestrator so n8n and Task Scheduler do not both fire — EC-OP-06):

| Host | Entry |
| --- | --- |
| n8n | Import `ops/n8n/discovery-pipeline.json`. Set execute-command cwd to the repo. |
| Windows | `ops/windows/Register-PipelineTask.ps1` (daily 02:00). |
| cron | `ops/cron/discovery.crontab`. |

All of these wrap `python -m src.cli pipeline`, which takes `data/locks/pipeline.lock`. Overlapping jobs exit `1` with `skipped_locked` / “pipeline lock held”. Counts are not doubled.

---

## Pause a source (unavailable, not zero)

```powershell
python -m src.cli source disable play_store
python -m src.cli source status
```

Or `PLAY_STORE_ENABLED=false` in `.env`.

**User-visible:** Overview unavailable badge, theme cards list `unavailable_sources`, Copilot turn includes the same list. Share of voice is **not** filled from other platforms.

Re-enable:

```powershell
python -m src.cli source enable play_store
```

---

## Architecture §18 failures

### Source API quota / block (403 / 429 / quotaExceeded)

**User-visible:** that `source_type` is **unavailable**. Last successful pull date may still be shown, labeled as such. Metrics that need the source do not reuse last week’s volume as if it were current.

**Operator:** Pause the connector (`source disable` or `*_ENABLED=false`). Do not scrape around the block. Do not interpolate. When the vendor quota resets, re-enable and run ingest. Partial YouTube seed lists stay partial — do not scale remaining videos “as if” they were pulled (EC-OP-05).

### Groq 429 / TPM exceeded

**User-visible:** extraction lags; Copilot may return a structured error after one retry. Dashboard can show corpus growth with **stale themes** — trust `themes refreshed on …` in the header, not an implied new SoV.

**Operator:** Raise `GROQ_MIN_INTERVAL_SECONDS`, lower batch `--limit`, keep `GROQ_MAX_TPM`. Exponential backoff is already in the client. **Do not** point `GROQ_BASE_URL` at OpenAI or any other host.

### Groq invalid JSON / extraction schema fail

**User-visible:** the document stays in evidence with `extraction_status=failed`. It is **excluded** from theme metrics until a valid extract exists.

**Operator:** `python -m src.cli extract` retries `failed` rows by default. Inspect `extractions` / `python -m src.cli extract-eval`. Fix `prompts/extract.json` before assuming you need more scraping (Phase 7 risk). Record `GROQ_MODEL` + prompt version; do not mix unlabeled prompts in one eval (EC-OP-03).

### Empty cluster / tiny corpus

**User-visible:** few or zero opportunity areas, low `data_confidence`, Copilot caveats or declines. Header still shows `themes refreshed on …`.

**Operator:** Do not force k=10. Do not relabel HDBSCAN noise as opportunities. A Play-Store-only debug cluster is not product-ready; recluster after multi-source docs exist.

### BGE checkpoint or dimension change

**User-visible:** retrieval quality shift; pgvector `vector(1024)` will not accept another dim.

**Operator:** `EMBEDDING_DIM` must stay **1024** for BGE-M3. A different checkpoint requires a **full re-embed** (`python -m src.cli embed --force`), a collection/version bump, and a new eval run. Vendor weights under `./data/models` if Hugging Face is blocked; set `BGE_MODEL_ID` to that folder. Never pad/truncate vectors.

### Theme recluster

**User-visible:** names may change. The UI shows **themes refreshed on …** (API `themes_refreshed_at`). `theme_id` is preserved when centroids match (`CLUSTER_CENTROID_MATCH_MIN_SIMILARITY=0.70`).

**Operator:** Prefer `--mode incremental` for small deltas. Full `--mode recluster` after large corpus growth. Diff reports use `theme_id`, not display name (EC-RP-04). Then run `python -m src.cli eval`.

---

## Constants freeze (EV-7-05)

| Constant | Frozen value | Where |
| --- | --- | --- |
| `C_max` | 200 | `src/config.py` `FROZEN_C_MAX`, `.env` `C_MAX` |
| `S_max` | 4 | `FROZEN_S_MAX`, `S_MAX` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | `FROZEN_GROQ_MODEL` |
| `GROQ_MODEL_LIGHT` | `openai/gpt-oss-20b` | `FROZEN_GROQ_MODEL_LIGHT` |
| `BGE_MODEL_ID` | `BAAI/bge-m3` | `FROZEN_BGE_MODEL_ID` |
| `EMBEDDING_DIM` | 1024 | `FROZEN_EMBEDDING_DIM` |

Changing `.env` without updating `FROZEN_*` fails `python -m src.cli eval --check`. If Groq deprecates a model (EC-OP-04), change **both** the freeze in git and `.env.example`, re-extract/re-label as needed, and record a new score.json. `ALLOW_UNFROZEN_CONSTANTS=true` is only for a documented experiment — eval still reports the mismatch.

---

## Auth beyond localhost

Bind stays `127.0.0.1` by default. If you expose the API:

1. Set `API_SHARED_SECRET` in `.env` and `web/.env.local`.
2. `python -m src.cli serve --host 0.0.0.0` refuses to start without the secret.
3. The Next.js app sends `X-API-Key`. Prototype auth is this shared secret, not SSO.

Railway image (`Dockerfile`) binds `0.0.0.0`, honors `PORT`, runs `serve --migrate`, and sets `REQUIRE_POSTGRES=true` so a down database cannot serve `local_store.pkl`. See [deployment-plan.md](./deployment-plan.md).

---

## Groq cost / TPM

- Tokens are billed for extract, theme labels, Copilot, and report narrative. BGE is local.
- Cache extract by `content_hash`; skip unchanged docs.
- Default `GROQ_MAX_TPM=8000`. If you 429, slow down — do not fan-out another provider.
- First BGE download is ~2GB into `HF_HOME` (`./data/models`).

---

## Live vs unavailable sources

Implemented connectors: Play Store, App Store, Reddit, YouTube, optional X. Instagram, Facebook, Quora, on-site Myntra Q&A/reviews stay **unavailable** unless a ToS-clear connector is added. `python -m src.cli source status` is the source of truth. Unavailable ≠ zero series.

---

## Object-store snapshots vs CSV export

Evidence CSV is scrubbed text only. `data/raw/` snapshots may still contain pre-scrub fields — restrict access (EC-SEC-06). Chat sessions store prompts without operator PII.

---

## When eval fails, what to fix first

| Symptom | Fix first |
| --- | --- |
| Invented SoV / S2 fail | Copilot tools / `prompts/copilot_system.md` — not more scraping |
| Bad Hinglish tags | Extract prompt; keep `text_original` |
| Generic app-crash themes | Relevance + cluster eligibility |
| Dashboard ≠ Copilot | Query API is the only metrics path |
| Play volume “recovered” after a failed pull | `unavailable_sources` — do not interpolate |
| Q2 decline | Check whether extraction tagged abandon language before scraping more |
