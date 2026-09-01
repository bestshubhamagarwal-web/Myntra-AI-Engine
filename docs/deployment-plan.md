# Deployment Plan

**Project:** Myntra Discovery Engine  
**Target:** FastAPI Query API on **Railway**, Next.js dashboard on **Vercel**  
**Companion:** [Architecture.md](./Architecture.md), [Runbook.md](./Runbook.md), [ImplementationPlan.md](./ImplementationPlan.md)

This is a research prototype, not production scraper HA. The goal is a public (or shareable) dashboard whose numbers still come from the Query API — the UI never computes SoV, impact, or confidence.

---

## 1. What we are deploying

| Piece | Lives in | Host | Role |
| --- | --- | --- | --- |
| Query API + Copilot (`src/api`) | repo root | **Railway** web service | Metrics, evidence, reports, `POST /copilot/query` |
| Postgres + **pgvector** `vector(1024)` | migrations `001`–`007` | **Railway** pgvector template | Source of truth |
| Next.js App Router (`web/`) | `web/` | **Vercel** | Product UI; browsers talk only to Vercel |
| Pipeline CLI (`ingest` → `cluster` → `ngrams` → `report`) | repo root | Railway **cron/one-off**, or your laptop | Writes the corpus. Not required for the API process to stay up |

The frontend already proxies every API call through a Next.js route:

```
Browser  →  https://<vercel>/api/query/...
         →  https://<railway>/{metrics,evidence,copilot,...}
```

Implemented in `web/app/api/query/[...path]/route.ts`. The browser never needs the Railway URL. Do **not** prefix `API_BASE_URL` or `API_SHARED_SECRET` with `NEXT_PUBLIC_`.

```
Public sources
    → CLI pipeline (worker or laptop)
    → Railway Postgres (pgvector)
    → Railway FastAPI  (Query API + Copilot + Groq)
         ↑
    Vercel Next.js  (proxy + dashboard)
         ↑
    Browser
```

---

## 2. Constraints that shape this plan

These are not optional footnotes. They decide machine size, start command, and where the pipeline runs.

### 2.1 Auth and bind address

`src/cli.py serve` and `Settings.require_api_secret_if_public()` refuse to bind anything other than localhost unless `API_SHARED_SECRET` is set. Railway must bind `0.0.0.0` and `$PORT`.

Prototype auth is header `X-API-Key` (or `Authorization: Bearer …`). `/health` and `/` are unauthenticated — use `/health` as the Railway health check.

### 2.2 Postgres fallback is a local-dev trap

`src/db/connect.py` falls back to `data/local_store.pkl` if Postgres is not listening. On Railway that file is **ephemeral** unless you mount a volume, and the dashboard would look empty after every restart.

Production `DATABASE_URL` must be reachable at process start. Prefer the **private** Railway URL (`${{Postgres.DATABASE_URL}}`), not `DATABASE_PUBLIC_URL`.

### 2.3 pgvector is required

`migrations/001_init.sql` runs `CREATE EXTENSION IF NOT EXISTS vector` and `chunks.embedding vector(1024)`. Railway’s **standard** Postgres image does **not** ship pgvector. Deploy the [pgvector template](https://railway.com/deploy/pgvector-postgresql) (Postgres 16 + volume), then let `python -m src.cli migrate` enable the extension.

### 2.4 BGE-M3 is local and heavy

Embeddings never leave the machine (`BAAI/bge-m3`, dim **1024**). Weights are ~2 GB under `HF_HOME`. Copilot `search_chunks` loads Sentence-Transformers on first use (`src/api/copilot.py`). Dashboard metrics work without loading BGE; Copilot vector search does not.

Budget **≥8 GB RAM** for the API service if Copilot retrieval should work. A 512 MB / 1 GB replica will OOM on first Copilot retrieve (metrics-only still works; Copilot then uses tagged quotes / Groq tools only, with `embed_error` in tool JSON).

Do not switch to OpenAI embeddings to “make Railway cheaper.” That violates Architecture §5.1.

### 2.5 Ephemeral disk

Railway containers lose the filesystem on each deploy. Persist:

| Path | Env | Why |
| --- | --- | --- |
| `/data/models` | `HF_HOME` | BGE weights (~2 GB); skip re-download |
| `/data/reports` | `REPORTS_PATH` | Weekly PDF bytes served by `GET /reports/{id}` |
| `/data/raw` | `RAW_STORE_PATH` | Redacted ingest snapshots |
| `/data/locks` | `LOCK_PATH` | Pipeline overlap lock (`EC-IN-16`) |

Postgres data is on the **database** volume, not this one.

### 2.6 Cloud IPs vs connectors

Play Store / public Reddit / Nitter-style hosts often **403/429 from datacenter IPs**. The runbook says: pause the source, do not scrape around the block, do not impute volume.

**Recommended split:** run ingest (and optionally the full pipeline) from a laptop against Railway Postgres via `DATABASE_PUBLIC_URL`. Keep the Railway web service as a **read path** (API + Copilot). If you do run ingest on Railway, expect Play Store `failed` / `unavailable` and treat that as honest, not a bug to bypass.

### 2.7 Copilot latency

`web/app/api/query/[...path]/route.ts` already sets `maxDuration = 120` and a 45 s upstream abort; the client waits up to 60 s. Vercel Fluid compute allows this on Hobby (max 300 s). First Copilot turn after a cold BGE load can still be slow — that is expected.

---

## 3. Target Railway project

One Railway **project**, three (or four) services:

| Service | Type | Public? |
| --- | --- | --- |
| `Postgres` | pgvector template | No (private net). Optional TCP proxy for laptop pipeline |
| `api` | GitHub → this repo, **root directory = repo root** | Yes (FastAPI) |
| `volume-api` | Volume mounted on `api` at `/data` | — |
| `worker` (optional) | Cron / one-off, same image as `api` | No |

Do **not** point the Vercel project at the repo root. Vercel’s root directory is `web/`.

---

## 4. Prerequisites

- GitHub repo with this project (Railway and Vercel both deploy from git). `.env` and `script.md` stay gitignored.
- Railway account, Vercel account, Groq key (`GROQ_API_KEY`).
- A long random `API_SHARED_SECRET` and `AUTHOR_HMAC_SECRET`. Generate once; **do not rotate HMAC after real ingest** or `author_hash` values diverge.
- Python 3.11+ locally if you bootstrap the corpus from the laptop.
- Optional: `YOUTUBE_API_KEY`, Reddit PRAW pair, `X_BEARER_TOKEN`. Empty keys already have public fallbacks; Instagram / Facebook / Quora stay unavailable.

---

## 5. Files in the repo

These are in git. Railway uses the Dockerfile; Vercel uses `web/` (including `web/vercel.json`). Do not let Nixpacks guess `python -m src.cli serve` on `127.0.0.1:8000`.

### 5.1 `railway.toml` (repo root)

```toml
[build]
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 120
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

Set the start command in the Dockerfile `CMD` (below). Railway injects `PORT`.

### 5.2 `Dockerfile` (repo root)

CPU PyTorch only. A CUDA wheel will bloat the image and fail on Railway.

```dockerfile
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    API_HOST=0.0.0.0 \
    REQUIRE_POSTGRES=true \
    HF_HOME=/data/models \
    RAW_STORE_PATH=/data/raw \
    REVIEW_DUMP_PATH=/data/review \
    REPORTS_PATH=/data/reports \
    LOCK_PATH=/data/locks \
    LOCAL_STORE_PATH=/data/local_store.pkl

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY prompts ./prompts

RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -e . --extra-index-url https://download.pytorch.org/whl/cpu

EXPOSE 8000

CMD ["sh", "-c", "python -m src.cli serve --migrate --host 0.0.0.0"]
```

The image sets `API_HOST=0.0.0.0` and `REQUIRE_POSTGRES=true`. `serve` reads Railway’s `PORT`, waits for Postgres, then applies migrations (`schema_migrations` is idempotent).

If migrate-on-boot feels too tight, run it once as a Railway one-off and drop `--migrate` from the start command:

```powershell
railway run python -m src.cli migrate
```

**Do not** bind `127.0.0.1` or hard-code port 8000 on Railway. Health checks will fail.

### 5.3 `.dockerignore` (repo root)

```
.venv
web/node_modules
web/.next
data
evals/runs
.git
.env
.env.local
script.md
```

### 5.4 `web/vercel.json`

Framework preset **Next.js**, Vercel **Root Directory = `web`**. `web/package.json` already has `build` / `start`. The proxy route is already `force-dynamic` with `maxDuration = 120`.

---

## 6. Railway — Postgres (pgvector)

1. New project, e.g. `myntra-discovery`.
2. **New → Template → pgvector** (official `pgvector/pgvector` image + volume). Do not use the default Postgres plugin.
3. Wait until the service is running. Copy variables:
   - `DATABASE_URL` — private, for `api` / `worker`
   - `DATABASE_PUBLIC_URL` — laptop pipeline / `psql` only
4. If a client complains about SSL, append `?sslmode=require` to the public URL.
5. Do **not** create tables by hand. The app migrations own the schema, including `CREATE EXTENSION vector`.

Sizing: start with the template default disk. 1024-d vectors plus raw/normalized text grow with corpus size; 1–5 GB is enough for a research pull.

---

## 7. Railway — API service

1. **New → GitHub repo** (this project). Root directory = repository root (not `web/`).
2. Builder: Dockerfile from §5, or Nixpacks with an explicit start command (Dockerfile is preferred because of PyTorch).
3. **Generate a public domain** (`xxx.up.railway.app`). Without a public domain Railway may treat the container as a one-shot job and stop it.
4. Attach a **volume** at `/data`.
5. Variables (see §10). Minimum to boot:

   | Name | Value |
   | --- | --- |
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (reference the pgvector service) |
   | `API_HOST` | `0.0.0.0` (image default) |
   | `REQUIRE_POSTGRES` | `true` (image default) |
   | `API_SHARED_SECRET` | long random string |
   | `AUTHOR_HMAC_SECRET` | long random string (stable) |
   | `GROQ_API_KEY` | Groq key |
   | `HF_HOME` | `/data/models` |
   | `RAW_STORE_PATH` | `/data/raw` |
   | `REPORTS_PATH` | `/data/reports` |
   | `LOCK_PATH` | `/data/locks` |
   | `LOCAL_STORE_PATH` | `/data/local_store.pkl` (must not be the live store) |

6. Replica size: **8 GB RAM** if Copilot should load BGE; **2 GB** is acceptable for a metrics-only demo (expect Copilot retrieve to skip vectors).
7. Health check path: `/health`. Expected JSON: `{"status":"ok","store":"postgres"}`. If `store` is `memory`, Postgres was not reachable — fix `DATABASE_URL` before sharing the frontend.
8. After the first successful deploy, confirm OpenAPI at `https://<railway>/docs` (optional; still behind CORS).

### Nixpacks fallback (if you skip Docker)

Start command:

```text
pip install -e . && python -m src.cli serve --migrate --host 0.0.0.0
```

Install CPU torch in a Nixpacks install phase or the image will try a default wheel. Docker is the less painful path.

---

## 8. Bootstrap data

An empty migrated database serves Overview with zeros / empty themes. Populate it **before** calling the deploy “done.”

### Option A — Laptop writes to Railway Postgres (recommended)

On the machine that can reach Play Store / Reddit:

```powershell
# Point local .env at the PUBLIC Railway URL only for this session
$env:DATABASE_URL = "postgresql://...@turn.proxy.rlwy.net:xxxxx/railway?sslmode=require"
$env:GROQ_API_KEY = "gsk_..."   # same as Railway
$env:AUTHOR_HMAC_SECRET = "<same as Railway>"

python -m src.cli migrate
python -m src.cli pipeline --sources all --max-items 50 --limit 50 --cluster
python -m src.cli ngrams
python -m src.cli report
python -m src.cli source status
```

HMAC and Groq model ids must match the API service. Frozen constants stay as in `.env.example` (`C_MAX=200`, `S_MAX=4`, `GROQ_MODEL=openai/gpt-oss-120b`, `BGE_MODEL_ID=BAAI/bge-m3`).

Copy generated PDFs is unnecessary if `report` ran with `REPORTS_PATH` on the API volume. If you generated reports locally, either re-run `python -m src.cli report` as a Railway one-off so files land on `/data/reports`, or accept JSON report metadata without a downloadable PDF.

### Option B — Railway one-off / cron worker

Same image as `api`, **no public domain**, cron e.g. daily 02:00 UTC:

```text
python -m src.cli pipeline --sources all --cluster && python -m src.cli ngrams && python -m src.cli report
```

Give the worker the **same** `/data` volume if you want shared BGE weights and report PDFs. Overlapping runs take `LOCK_PATH/pipeline.lock` and skip (`EC-IN-16`). Pick **one** scheduler (Railway cron **or** laptop Task Scheduler **or** n8n) — not two (`EC-OP-06`).

Existing wrappers: `ops/cron/discovery.crontab`, `ops/windows/Register-PipelineTask.ps1`, `ops/n8n/discovery-pipeline.json`. Point their `DATABASE_URL` at Railway if you keep them.

### Option C — Dump/restore an existing local DB

```powershell
docker exec <local-pg> pg_dump -U discovery -Fc discovery > discovery.dump
# restore into Railway using DATABASE_PUBLIC_URL
pg_restore --no-owner --no-acl -d $env:DATABASE_PUBLIC_URL discovery.dump
```

Only valid if the local DB already used `vector(1024)` BGE-M3 (no dim mix).

---

## 9. Vercel — frontend

1. [vercel.com](https://vercel.com) → **Add New → Project** → same GitHub repo.
2. **Root Directory:** `web` (Edit, not the repo root).
3. Framework preset: Next.js. Build `npm run build`, output default.
4. Environment variables (Production + Preview):

   | Name | Value | Exposed to browser? |
   | --- | --- | --- |
   | `API_BASE_URL` | `https://<api>.up.railway.app` (no trailing slash) | **No** — server proxy only |
   | `API_SHARED_SECRET` | identical to Railway | **No** |

   The proxy injects `X-API-Key` from `API_SHARED_SECRET` when the browser does not send one (`web/app/api/query/[...path]/route.ts`). With both set, users should **not** see the AuthGate. If you omit the secret on Vercel but set it on Railway, the unlock screen appears and the value is stored in `sessionStorage` only.

5. Region: pick the Vercel region closest to the Railway region (e.g. both `us-east` or both `asia-southeast`) so Copilot’s two hops stay short.
6. Deploy. Open `https://<project>.vercel.app`. Routes to check: `/overview`, `/themes`, `/evidence`, `/copilot`.

Preview deployments: either allow `https://*.vercel.app` in Railway `API_CORS_ORIGINS`, or rely on the server-side proxy (CORS does not apply to the proxy hop). CORS only matters for **browser → Railway** (OpenAPI, curl from a webpage). The product UI does not do that.

---

## 10. Environment reference

Copy from `.env.example`. Secrets stay in the host dashboards, never in git.

### 10.1 Railway `api` — required

| Variable | Production notes |
| --- | --- |
| `DATABASE_URL` | Private pgvector URL. `sslmode=require` if needed |
| `GROQ_API_KEY` | Generation only. `GROQ_BASE_URL=https://api.groq.com/openai/v1` |
| `GROQ_MODEL` | Frozen `openai/gpt-oss-120b` |
| `GROQ_MODEL_LIGHT` | Frozen `openai/gpt-oss-20b` |
| `BGE_MODEL_ID` | `BAAI/bge-m3` |
| `EMBEDDING_DIM` | `1024` |
| `HF_HOME` | `/data/models` |
| `AUTHOR_HMAC_SECRET` | Required for real ingest; keep stable |
| `API_HOST` | `0.0.0.0` (image default) |
| `REQUIRE_POSTGRES` | `true` (image default — do not turn off) |
| `API_SHARED_SECRET` | Required (public bind) |
| `API_CORS_ORIGINS` | `https://<vercel-prod>,http://localhost:3000` |
| `RAW_STORE_PATH` | `/data/raw` |
| `REPORTS_PATH` | `/data/reports` |
| `LOCK_PATH` | `/data/locks` |
| `C_MAX` / `S_MAX` | `200` / `4` |

Railway sets `PORT`. Uvicorn must use `${PORT}`. You do not need `API_PORT` if the Dockerfile uses `$PORT`.

### 10.2 Railway `api` — connectors (optional)

Same names as `.env.example`: `PLAY_STORE_*`, `APP_STORE_*`, `REDDIT_*`, `YOUTUBE_*`, `X_*`. Disable with `PLAY_STORE_ENABLED=false` (etc.) rather than inventing zeros.

If the API replica will **not** ingest, you can leave connectors enabled in env; they only matter when `python -m src.cli ingest` / `pipeline` runs.

### 10.3 Vercel `web` — required

| Variable | Production notes |
| --- | --- |
| `API_BASE_URL` | Railway public HTTPS origin |
| `API_SHARED_SECRET` | Same string as Railway |

Nothing else from the Python `.env` belongs on Vercel.

---

## 11. Order of operations (checklist)

Do these in order. Do not attach Vercel until `/health` reports `store=postgres`.

- [ ] Repo on GitHub; `.env` not committed
- [ ] Deploy the branch that contains `Dockerfile` / `railway.toml` / `web/vercel.json`
- [ ] Railway project + **pgvector** template
- [ ] Railway `api` service + public domain + `/data` volume
- [ ] Set Railway env (§10.1); deploy; `/health` → `postgres`
- [ ] `python -m src.cli migrate` (boot CMD or one-off)
- [ ] Bootstrap corpus (laptop pipeline or worker) (§8)
- [ ] Confirm `GET https://<railway>/metrics/overview` with `X-API-Key` returns themes/counts
- [ ] Vercel project, root `web`, env `API_BASE_URL` + `API_SHARED_SECRET`
- [ ] Set Railway `API_CORS_ORIGINS` to the Vercel URL
- [ ] Browser: Overview SoV matches a curl to the API; Copilot citations open the evidence drawer
- [ ] `python -m src.cli source status` — unavailable sources listed, not imputed
- [ ] Optional: one cron for pipeline; disable local Task Scheduler if cron is on

---

## 12. Smoke tests after go-live

```powershell
# API (expect store=postgres)
curl https://<railway>/health

# Authenticated overview
curl -H "X-API-Key: <secret>" https://<railway>/metrics/overview

# Frontend proxy (from a logged-out browser, or)
curl https://<vercel>/api/query/health
curl https://<vercel>/api/query/metrics/overview
```

In the UI:

1. Overview corpus counts and unavailable badges.
2. One theme card → evidence drawer → source URL or “link unavailable”.
3. Copilot: a Q1–Q9 paraphrase; answer must not invent a % that is missing from tool JSON.
4. Reports: JSON loads; PDF only if `/data/reports` has the file.
5. Disable a source (`python -m src.cli source disable play_store` against prod DB) and confirm Overview + Copilot show **unavailable**, not last week’s volume.

---

## 13. Resource and cost sketch

| Resource | Why it exists | Ballpark |
| --- | --- | --- |
| Railway pgvector | Persistent SQL + vectors | Small always-on Postgres |
| Railway `api` 8 GB | FastAPI + optional BGE | Dominant compute cost |
| Railway volume ~5–10 GB | Weights + reports + raw | Cheap vs RAM |
| Vercel Hobby/Pro | Next.js + 120 s proxy | UI + Copilot hop |
| Groq TPM | Extract, labels, Copilot, report narrative | Same as local; `GROQ_MAX_TPM=8000` |

BGE is not billed; it is RAM + disk. Groq 429: raise `GROQ_MIN_INTERVAL_SECONDS`, lower `--limit`. Do not point `GROQ_BASE_URL` at OpenAI ([Runbook.md](./Runbook.md)).

---

## 14. Security

- Shared secret is prototype auth, not SSO. Anyone with the Vercel URL **and** a Vercel-injected secret can read the corpus. Treat the Vercel URL as internal unless you add real auth later.
- `API_SHARED_SECRET` on Vercel is server-only. Never `NEXT_PUBLIC_API_SHARED_SECRET`.
- Evidence CSV is scrubbed; `RAW_STORE_PATH` may still have pre-scrub fields — volume access = operator-only (`EC-SEC-06`).
- Railway public API is reachable from the internet. The secret is the gate. Rotate it in **both** dashboards together.
- Do not log `GROQ_API_KEY` or the shared secret. Do not paste `.env` into Vercel “plain text in build logs.”

---

## 15. Failure modes (deploy-specific)

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Railway deploy healthy, UI empty, `/health` `store=memory` | `DATABASE_URL` wrong or pg not up at boot | Private URL reference; restart after Postgres is live |
| `CREATE EXTENSION vector` fails | Standard Postgres, not pgvector image | Use pgvector template |
| Health check 502 / 0 replicas | Bind `127.0.0.1` or port 8000 instead of `$PORT` | Dockerfile CMD in §5.2 |
| `API_SHARED_SECRET is required when binding…` | Used `src.cli serve` on `0.0.0.0` without secret | Set secret |
| Vercel 502 `Query API unreachable` | `API_BASE_URL` trailing path, `http` vs `https`, or API asleep | Origin only, HTTPS, public domain |
| Vercel 401 AuthGate | Secret on Railway only | Set the same secret on Vercel, or type it in the gate |
| Copilot 504 | Railway cold start + BGE load + Groq > 45 s proxy | Keep replica warm; 8 GB RAM; retry |
| OOMKilled on first Copilot question | BGE load on a small replica | Scale RAM or accept retrieve skip |
| Report PDF 404 | `REPORTS_PATH` not on a volume / report ran elsewhere | Re-run `report` on `api` with `/data/reports` |
| Play Store ingest `failed` on Railway | Datacenter 403 | Run ingest from laptop; mark source unavailable |
| Themes look like a different corpus | Laptop pickle store vs Postgres | Confirm both use the same `DATABASE_URL` |

Operator playbook for Groq, clustering, and source pause remains [Runbook.md](./Runbook.md).

---

## 16. Rollback

- **Vercel:** Deployments → previous Production. Instant.
- **Railway `api`:** Deployments → Redeploy previous image. Volume `/data` is unchanged.
- **Migrations:** forward-only (`schema_migrations`). There is no down migration. Restore a `pg_dump` taken before `migrate` if you must unwind SQL.
- **Frontend/backend contract:** keep API and `web/` on the same git SHA when possible. The UI must not re-aggregate if an old frontend hits a new API, but missing fields can blank a view.

---

## 17. Out of scope (this plan)

- Production scraper scale, multi-region HA, SSO
- Instagram / Facebook / Quora / on-site Myntra Q&A
- Switching embedding or chat hosts
- Putting the Next.js app on Railway, or FastAPI on Vercel (Python + BGE + pgvector do not fit Vercel’s model)
- Changing frozen constants without a git commit + `python -m src.cli eval`

---

## 18. In-repo deploy hooks (done)

1. `Dockerfile`, `railway.toml`, `.dockerignore`, `web/vercel.json`.
2. `REQUIRE_POSTGRES=true` (API image default) makes `connect_store` wait, then fail — never `local_store.pkl`.
3. `python -m src.cli serve` uses platform `PORT` when `--port` is omitted; `--migrate` applies SQL before uvicorn.
