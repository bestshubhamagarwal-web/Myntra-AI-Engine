# Deployment Plan

**Project:** Myntra Discovery Engine  
**Target:** FastAPI Query API on **Render**, Next.js dashboard on **Vercel**  
**Companion:** [Architecture.md](./Architecture.md), [Runbook.md](./Runbook.md), [ImplementationPlan.md](./ImplementationPlan.md)

This is a research prototype, not production scraper HA. The goal is a public (or shareable) dashboard whose numbers still come from the Query API — the UI never computes SoV, impact, or confidence.

---

## 1. What we are deploying


| Piece                                                     | Lives in               | Host                                       | Role                                                           |
| --------------------------------------------------------- | ---------------------- | ------------------------------------------ | -------------------------------------------------------------- |
| Query API + Copilot (`src/api`)                           | repo root              | **Render** web service                     | Metrics, evidence, reports, `POST /copilot/query`              |
| Postgres + **pgvector** `vector(1024)`                    | migrations `001`–`007` | **Render Postgres** (managed)              | Source of truth                                                |
| Next.js App Router (`web/`)                               | `web/`                 | **Vercel**                                 | Product UI; browsers talk only to Vercel                       |
| Pipeline CLI (`ingest` → `cluster` → `ngrams` → `report`) | repo root              | Laptop (recommended), or a Render **cron** | Writes the corpus. Not required for the API process to stay up |


The frontend already proxies every API call through a Next.js route:

```
Browser  →  https://<vercel>/api/query/...
         →  https://<api>.onrender.com/{metrics,evidence,copilot,...}
```

Implemented in `web/app/api/query/[...path]/route.ts`. The browser never needs the Render URL. Do **not** prefix `API_BASE_URL` or `API_SHARED_SECRET` with `NEXT_PUBLIC_`.

```
Public sources
    → CLI pipeline (laptop or cron)
    → Render Postgres (pgvector)
    → Render FastAPI  (Query API + Copilot + Groq)
         ↑
    Vercel Next.js  (proxy + dashboard)
         ↑
    Browser
```

---

## 2. Constraints that shape this plan

These are not optional footnotes. They decide machine size, start command, and where the pipeline runs.

### 2.1 Auth and bind address

`src/cli.py serve` and `Settings.require_api_secret_if_public()` refuse to bind anything other than localhost unless `API_SHARED_SECRET` is set. Render must bind `0.0.0.0` and `$PORT`.

Prototype auth is header `X-API-Key` (or `Authorization: Bearer …`). `/health` and `/` are unauthenticated — use `/health` as the Render health check. `/health` returns **200** while the process is up (`store=pending` until Postgres attaches, then `{"status":"ok","store":"postgres"}`). Metrics stay 503 until the store is ready so Render does not restart the API mid-handshake.

### 2.2 Postgres fallback is a local-dev trap

`src/db/connect.py` falls back to `data/local_store.pkl` if Postgres is not listening. On Render that file is **ephemeral** unless you mount a disk, and the dashboard would look empty after every restart.

Production `DATABASE_URL` must be reachable at process start. Prefer the **internal** Render URL (`fromDatabase.connectionString` in `render.yaml`), not the External Database URL. The laptop pipeline is the exception — it must use the **external** URL.

### 2.3 pgvector is required

`migrations/001_init.sql` runs `CREATE EXTENSION IF NOT EXISTS vector` and `chunks.embedding vector(1024)`. Render Postgres **includes pgvector**. Do not use a generic Docker Postgres image without the extension. After the database is up, `python -m src.cli migrate` (the API boot CMD) runs `CREATE EXTENSION vector`.

### 2.4 BGE-M3 is local and heavy

Embeddings never leave the machine (`BAAI/bge-m3`, dim **1024**). Weights are ~2 GB under `HF_HOME`. Copilot `search_chunks` loads Sentence-Transformers on first use (`src/api/copilot.py`). Dashboard metrics work without loading BGE; Copilot vector search does not.

Budget **≥8 GB RAM** for the API service if Copilot retrieval should work. Render plan `**2c-8g`** (2 CPU / 8 GB). A 512 MB free instance will OOM on first Copilot retrieve (metrics-only still works; Copilot then uses tagged quotes / Groq tools only, with `embed_error` in tool JSON).

Do not switch to OpenAI embeddings to “make Render cheaper.” That violates Architecture §5.1.

Do **not** use Render’s **free** web plan: 512 MB, spins down after idle, and **cannot** attach a persistent disk.

### 2.5 Ephemeral disk

Render containers lose the filesystem on each deploy unless a **persistent disk** is attached. Persist:


| Path            | Env              | Why                                            |
| --------------- | ---------------- | ---------------------------------------------- |
| `/data/models`  | `HF_HOME`        | BGE weights (~2 GB); skip re-download          |
| `/data/reports` | `REPORTS_PATH`   | Weekly PDF bytes served by `GET /reports/{id}` |
| `/data/raw`     | `RAW_STORE_PATH` | Redacted ingest snapshots                      |
| `/data/locks`   | `LOCK_PATH`      | Pipeline overlap lock (`EC-IN-16`)             |


Postgres data is on the **database**, not this disk.

A Render disk is attached to **one** service instance. Cron jobs and one-off shells do **not** see `/data`. To write report PDFs onto the volume, run `python -m src.cli report` from the **API service Shell** (Dashboard → service → Shell), not from a one-off job.

Attaching a disk disables zero-downtime deploys (brief downtime on each deploy). That is expected.

### 2.6 Cloud IPs vs connectors

Play Store / public Reddit / Nitter-style hosts often **403/429 from datacenter IPs**. The runbook says: pause the source, do not scrape around the block, do not impute volume.

**Recommended split:** run ingest (and optionally the full pipeline) from a laptop against Render Postgres via the **External Database URL**. Keep the Render web service as a **read path** (API + Copilot). If you do run ingest on Render, expect Play Store `failed` / `unavailable` and treat that as honest, not a bug to bypass.

### 2.7 Copilot latency

`web/app/api/query/[...path]/route.ts` already sets `maxDuration = 120` and a 20 s upstream abort with retries; the client waits up to 60 s. Vercel Fluid compute allows this on Hobby (max 300 s). First Copilot turn after a cold BGE load can still be slow — that is expected. Keep the Render service on a paid plan so it does not spin down.

---

## 3. Target Render workspace

One Render Blueprint (`render.yaml`), two (or three) resources:


| Service                         | Type                                               | Public?                                                       |
| ------------------------------- | -------------------------------------------------- | ------------------------------------------------------------- |
| `discovery-db`                  | Render Postgres 16 + pgvector                      | No (internal URL for `api`). External URL for laptop pipeline |
| `discovery-api`                 | GitHub → this repo, **root directory = repo root** | Yes (`*.onrender.com`)                                        |
| disk on `api`                   | Persistent disk mounted at `/data`                 | —                                                             |
| `discovery-pipeline` (optional) | Cron, same Docker image                            | No                                                            |


Do **not** point the Vercel project at the repo root. Vercel’s root directory is `web/`.

Region: pick **one** Render region for both Postgres and the API (private networking is same-region only). Closest to India is **Singapore**. Vercel: `sin1`. Region cannot be changed after the Blueprint first creates the resources.

---

## 4. Prerequisites

- GitHub repo with this project (Render and Vercel both deploy from git). `.env` and `script.md` stay gitignored.
- Render account, Vercel account, Groq key (`GROQ_API_KEY`).
- A long random `API_SHARED_SECRET` and `AUTHOR_HMAC_SECRET`. Generate once; **do not rotate HMAC after real ingest** or `author_hash` values diverge.
- Python 3.11+ locally if you bootstrap the corpus from the laptop.
- Optional: `YOUTUBE_API_KEY`, Reddit PRAW pair, `X_BEARER_TOKEN`. Empty keys already have public fallbacks; Instagram / Facebook / Quora stay unavailable.

---

## 5. Files in the repo

These are in git. Render uses the Dockerfile via `render.yaml`; Vercel uses `web/` (including `web/vercel.json`). Do not let a native Python runtime guess `python -m src.cli serve` on `127.0.0.1:8000`.

### 5.1 `render.yaml` (repo root)

Blueprint: Docker web service + managed Postgres. Secrets use `sync: false` so you paste them in the Render Dashboard — they never go in git.

```yaml
databases:
  - name: discovery-db
    plan: 0.5c-1g
    postgresMajorVersion: "16"
    databaseName: discovery
    user: discovery
    region: singapore

services:
  - type: web
    name: discovery-api
    runtime: docker
    plan: 2c-8g
    region: singapore
    healthCheckPath: /health
    dockerfilePath: ./Dockerfile
    disk:
      name: api-data
      mountPath: /data
      sizeGB: 10
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: discovery-db
          property: connectionString
      - key: API_SHARED_SECRET
        sync: false
      - key: AUTHOR_HMAC_SECRET
        sync: false
      - key: GROQ_API_KEY
        sync: false
```

Set remaining frozen constants and path env in the same file (already in git). Render injects `PORT`.

Apply with **Dashboard → New → Blueprint** and the GitHub repo, or `render blueprint apply`. First apply prompts for the three secrets.

### 5.2 `Dockerfile` (repo root)

CPU PyTorch only. A CUDA wheel will bloat the image and fail on Render.

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

The image sets `API_HOST=0.0.0.0` and `REQUIRE_POSTGRES=true`. `serve` reads Render’s `PORT`, waits for Postgres, then applies migrations (`schema_migrations` is idempotent).

If migrate-on-boot feels too tight, run it once from the API **Shell** and drop `--migrate` from the start command:

```text
python -m src.cli migrate
```

**Do not** bind `127.0.0.1` or hard-code port 8000 on Render. Health checks will fail.

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

Framework preset **Next.js**, Vercel **Root Directory =** `web`. `web/package.json` already has `build` / `start`. The proxy route is already `force-dynamic` with `maxDuration = 120`.

---

## 6. Render — Postgres (pgvector)

1. Created by the Blueprint (`discovery-db`), or **Dashboard → New → Postgres**.
2. PostgreSQL **16** (or 17). Same region as `discovery-api`.
3. Wait until status is **Available**. Connect menu:
  - **Internal Database URL** — for `api` (`DATABASE_URL` via `fromDatabase`)
  - **External Database URL** — laptop pipeline / `psql` only
4. Append `?sslmode=require` to the external URL if the client does not add TLS itself.
5. Do **not** create tables by hand. The app migrations own the schema, including `CREATE EXTENSION vector`.

Sizing: start with `0.5c-1g` and 5–10 GB disk. 1024-d vectors plus raw/normalized text grow with corpus size. Free Render Postgres expires after 30 days — do not use it for this corpus.

---

## 7. Render — API service

1. Blueprint creates `discovery-api` from this repo. Root directory = repository root (not `web/`).
2. Runtime: **Docker**. Dockerfile from §5.2.
3. Public URL is `https://discovery-api.onrender.com` (or the name you chose). Copy this origin into Vercel `API_BASE_URL`.
4. Disk at `/data` (Blueprint). Plan `**2c-8g**` for Copilot + BGE; `**1c-2g**` is acceptable for a metrics-only demo (expect Copilot retrieve to skip vectors).
5. Variables (see §10). Minimum to boot:

  | Name                 | Value                                                               |
  | -------------------- | ------------------------------------------------------------------- |
  | `DATABASE_URL`       | Internal connection string (Blueprint `fromDatabase`)               |
  | `API_HOST`           | `0.0.0.0` (image default)                                           |
  | `REQUIRE_POSTGRES`   | `true` (image default)                                              |
  | `API_SHARED_SECRET`  | long random string (paste on first Blueprint apply; copy to Vercel) |
  | `AUTHOR_HMAC_SECRET` | long random string (stable)                                         |
  | `GROQ_API_KEY`       | Groq key                                                            |
  | `HF_HOME`            | `/data/models`                                                      |
  | `RAW_STORE_PATH`     | `/data/raw`                                                         |
  | `REPORTS_PATH`       | `/data/reports`                                                     |
  | `LOCK_PATH`          | `/data/locks`                                                       |
  | `LOCAL_STORE_PATH`   | `/data/local_store.pkl` (must not be the live store)                |

6. Health check path: `/health`. Expected JSON: `{"status":"ok","store":"postgres"}`. If `store` is `pending`, the process is up and still attaching Postgres (liveness is 200). If `store` is `memory`, Postgres was not reachable — fix `DATABASE_URL` before sharing the frontend. If `store` stays `pending` or logs show `SSL connection has been closed unexpectedly`, the API retries `dpg-….singapore-postgres.render.com` with `sslnegotiation=direct`. Confirm API + database share a region (Singapore).
7. After the first successful deploy, confirm OpenAPI at `https://<api>.onrender.com/docs` (optional; still behind CORS).

First Docker build (CPU torch + Sentence-Transformers) can take 10–20 minutes. That is a **build**, not a hung deploy.

---

## 8. Bootstrap data

An empty migrated database serves Overview with zeros / empty themes. Populate it **before** calling the deploy “done.”

### Option A — Laptop writes to Render Postgres (recommended)

On the machine that can reach Play Store / Reddit:

```powershell
# Point local .env at the EXTERNAL Render URL only for this session
$env:DATABASE_URL = "postgresql://discovery:...@dpg-xxxxx-a.singapore-postgres.render.com/discovery?sslmode=require"
$env:GROQ_API_KEY = "gsk_..."   # same as Render
$env:AUTHOR_HMAC_SECRET = "<same as Render>"

python -m src.cli migrate
python -m src.cli pipeline --sources all --max-items 50 --limit 50 --cluster
python -m src.cli ngrams
python -m src.cli report
python -m src.cli source status
```

HMAC and Groq model ids must match the API service. Frozen constants stay as in `.env.example` (`C_MAX=200`, `S_MAX=4`, `GROQ_MODEL=openai/gpt-oss-120b`, `BGE_MODEL_ID=BAAI/bge-m3`).

Copy generated PDFs is unnecessary if `report` ran on the API disk. If you generated reports locally, either re-run `python -m src.cli report` in the **API Shell** so files land on `/data/reports`, or accept JSON report metadata without a downloadable PDF.

### Option B — Render cron

Same Docker image as `api`, cron e.g. daily 02:00 UTC:

```text
python -m src.cli pipeline --sources all --cluster && python -m src.cli ngrams && python -m src.cli report
```

The cron **does not** share `/data` with `api`. Overlapping runs take `LOCK_PATH/pipeline.lock` only if that path exists on the cron instance (it will not persist). Pick **one** scheduler (Render cron **or** laptop Task Scheduler **or** n8n) — not two (`EC-OP-06`).

Existing wrappers: `ops/cron/discovery.crontab`, `ops/windows/Register-PipelineTask.ps1`, `ops/n8n/discovery-pipeline.json`. Point their `DATABASE_URL` at the Render **external** URL if you keep them.

### Option C — Dump/restore an existing local DB

```powershell
docker exec <local-pg> pg_dump -U discovery -Fc discovery > discovery.dump
# restore into Render using the External Database URL
pg_restore --no-owner --no-acl -d $env:DATABASE_URL discovery.dump
```

Only valid if the local DB already used `vector(1024)` BGE-M3 (no dim mix).

---

## 9. Vercel — frontend

1. [vercel.com](https://vercel.com) → **Add New → Project** → same GitHub repo.
2. **Root Directory:** `web` (Edit, not the repo root).
3. Framework preset: Next.js. Build `npm run build`, output default.
4. Environment variables (Production + Preview):

  | Name                | Value                                            | Exposed to browser?        |
  | ------------------- | ------------------------------------------------ | -------------------------- |
  | `API_BASE_URL`      | `https://<api>.onrender.com` (no trailing slash) | **No** — server proxy only |
  | `API_SHARED_SECRET` | identical to Render                              | **No**                     |

   The proxy injects `X-API-Key` from `API_SHARED_SECRET` when the browser does not send one (`web/app/api/query/[...path]/route.ts`). With both set, users should **not** see the AuthGate. If you omit the secret on Vercel but set it on Render, the unlock screen appears and the value is stored in `sessionStorage` only.
5. Region: pick the Vercel region closest to the Render region (Singapore → `sin1`, Oregon → `sfo1`) so Copilot’s two hops stay short.
6. Deploy. Open `https://<project>.vercel.app`. Routes to check: `/overview`, `/themes`, `/evidence`, `/copilot`.

Preview deployments: either allow `https://*.vercel.app` in Render `API_CORS_ORIGINS`, or rely on the server-side proxy (CORS does not apply to the proxy hop). CORS only matters for **browser → Render** (OpenAPI, curl from a webpage). The product UI does not do that. The API already allows `https://*.vercel.app` via origin regex.

---

## 10. Environment reference

Copy from `.env.example`. Secrets stay in the host dashboards, never in git.

### 10.1 Render `discovery-api` — required


| Variable             | Production notes                                                                                        |
| -------------------- | ------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`       | **Internal** URL only (`dpg-…`, no `.render.com`). Blueprint also sets `RENDER_DATABASE_URL` + `PGHOST` |
| `GROQ_API_KEY`       | Generation only. `GROQ_BASE_URL=https://api.groq.com/openai/v1`                                         |
| `GROQ_MODEL`         | Frozen `openai/gpt-oss-120b`                                                                            |
| `GROQ_MODEL_LIGHT`   | Frozen `openai/gpt-oss-20b`                                                                             |
| `BGE_MODEL_ID`       | `BAAI/bge-m3`                                                                                           |
| `EMBEDDING_DIM`      | `1024`                                                                                                  |
| `HF_HOME`            | `/data/models`                                                                                          |
| `AUTHOR_HMAC_SECRET` | Required for real ingest; keep stable                                                                   |
| `API_HOST`           | `0.0.0.0` (image default)                                                                               |
| `REQUIRE_POSTGRES`   | `true` (image default — do not turn off)                                                                |
| `API_SHARED_SECRET`  | Required (public bind)                                                                                  |
| `API_CORS_ORIGINS`   | `https://<vercel-prod>,http://localhost:3000`                                                           |
| `RAW_STORE_PATH`     | `/data/raw`                                                                                             |
| `REPORTS_PATH`       | `/data/reports`                                                                                         |
| `LOCK_PATH`          | `/data/locks`                                                                                           |
| `C_MAX` / `S_MAX`    | `200` / `4`                                                                                             |


Render sets `PORT`. Uvicorn must use `${PORT}`. You do not need `API_PORT` if the Dockerfile uses `$PORT`.

### 10.2 Render `discovery-api` — connectors (optional)

Same names as `.env.example`: `PLAY_STORE_*`, `APP_STORE_*`, `REDDIT_*`, `YOUTUBE_*`, `X_*`. Disable with `PLAY_STORE_ENABLED=false` (etc.) rather than inventing zeros.

If the API replica will **not** ingest, you can leave connectors enabled in env; they only matter when `python -m src.cli ingest` / `pipeline` runs.

### 10.3 Vercel `web` — required


| Variable            | Production notes           |
| ------------------- | -------------------------- |
| `API_BASE_URL`      | Render public HTTPS origin |
| `API_SHARED_SECRET` | Same string as Render      |


Nothing else from the Python `.env` belongs on Vercel.

---

## 11. Order of operations (checklist)

Do these in order. Do not attach Vercel until `/health` reports `store=postgres`.

- [ ] Repo on GitHub; `.env` not committed
- [ ] Deploy the branch that contains `Dockerfile` / `render.yaml` / `web/vercel.json`
- [ ] Render Blueprint apply (or Dashboard: Postgres 16 + Docker web service)
- [ ] Paste `API_SHARED_SECRET`, `AUTHOR_HMAC_SECRET`, `GROQ_API_KEY` when prompted
- [ ] Confirm disk at `/data` and plan `2c-8g` (or `1c-2g` metrics-only)
- [ ] Deploy; `/health` → `postgres`
- [ ] `python -m src.cli migrate` (boot CMD or API Shell)
- [ ] Bootstrap corpus (laptop pipeline or cron) (§8)
- [ ] Confirm `GET https://<api>.onrender.com/metrics/overview` with `X-API-Key` returns themes/counts
- [ ] Vercel project, root `web`, env `API_BASE_URL` + `API_SHARED_SECRET`
- [ ] Set Render `API_CORS_ORIGINS` to the Vercel URL
- [ ] Browser: Overview SoV matches a curl to the API; Copilot citations open the evidence drawer
- [ ] `python -m src.cli source status` — unavailable sources listed, not imputed
- [ ] Optional: one cron for pipeline; disable local Task Scheduler if cron is on

---

## 12. Smoke tests after go-live

```powershell
# API (expect store=postgres)
curl https://<api>.onrender.com/health

# Authenticated overview
curl -H "X-API-Key: <secret>" https://<api>.onrender.com/metrics/overview

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


| Resource                       | Why it exists                              | Ballpark                           |
| ------------------------------ | ------------------------------------------ | ---------------------------------- |
| Render Postgres `0.5c-1g`      | Persistent SQL + vectors                   | Small always-on Postgres           |
| Render `discovery-api` `2c-8g` | FastAPI + optional BGE                     | Dominant compute cost              |
| Render disk ~10 GB             | Weights + reports + raw                    | Cheap vs RAM                       |
| Vercel Hobby/Pro               | Next.js + 120 s proxy                      | UI + Copilot hop                   |
| Groq TPM                       | Extract, labels, Copilot, report narrative | Same as local; `GROQ_MAX_TPM=8000` |


BGE is not billed; it is RAM + disk. Groq 429: raise `GROQ_MIN_INTERVAL_SECONDS`, lower `--limit`. Do not point `GROQ_BASE_URL` at OpenAI ([Runbook.md](./Runbook.md)).

---

## 14. Security

- Shared secret is prototype auth, not SSO. Anyone with the Vercel URL **and** a Vercel-injected secret can read the corpus. Treat the Vercel URL as internal unless you add real auth later.
- `API_SHARED_SECRET` on Vercel is server-only. Never `NEXT_PUBLIC_API_SHARED_SECRET`.
- Evidence CSV is scrubbed; `RAW_STORE_PATH` may still have pre-scrub fields — disk access = operator-only (`EC-SEC-06`).
- Render public API (`*.onrender.com`) is reachable from the internet. The secret is the gate. Rotate it in **both** dashboards together.
- Do not log `GROQ_API_KEY` or the shared secret. Do not paste `.env` into Vercel “plain text in build logs.”
- Laptop `DATABASE_URL` is the **external** Postgres URL. Do not commit it. Restrict the database IP allow list if you do not need ingest from arbitrary networks.

---

## 15. Failure modes (deploy-specific)


| Symptom                                                   | Likely cause                                                      | Fix                                                                 |
| --------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------- |
| Render deploy healthy, UI empty, `/health` `store=memory` | `DATABASE_URL` wrong or pg not up at boot                         | Internal URL from Blueprint; restart after Postgres is Available    |
| `/health` stuck `store=pending`                           | Internal host unreachable (wrong region) or TLS                   | Same region (Singapore); public hostname is the DNS fallback        |
| `failed to resolve host 'dpg-…'`                          | Short host is private DNS only; not in public DNS                 | Code retries `dpg-….{region}-postgres.render.com`; keep same region |
| `SSL connection has been closed unexpectedly`             | TLS/channel-binding on the public Postgres hostname               | `sslmode=require` + `channel_binding=disable`; same-region services |
| `CREATE EXTENSION vector` fails                           | Not Render Postgres, or too-old image                             | Use managed Render Postgres 16+                                     |
| Health check never passes                                 | Bind `127.0.0.1` or port 8000 instead of `$PORT`                  | Dockerfile CMD in §5.2                                              |
| `API_SHARED_SECRET is required when binding…`             | Used `src.cli serve` on `0.0.0.0` without secret                  | Set secret                                                          |
| Vercel 502 `Query API unreachable`                        | `API_BASE_URL` trailing path, `http` vs `https`, or API asleep    | Origin only, HTTPS, `*.onrender.com` (not the Postgres host)        |
| Vercel 401 AuthGate                                       | Secret on Render only                                             | Set the same secret on Vercel, or type it in the gate               |
| Copilot 504                                               | Render cold start + BGE load + Groq > proxy budget                | Paid plan (no spin-down); 8 GB RAM; retry                           |
| OOMKilled on first Copilot question                       | BGE load on a small replica                                       | Scale to `2c-8g` or accept retrieve skip                            |
| Report PDF 404                                            | `REPORTS_PATH` not on the API disk / report ran on laptop or cron | Re-run `report` in the API Shell                                    |
| Play Store ingest `failed` on Render                      | Datacenter 403                                                    | Run ingest from laptop; mark source unavailable                     |
| Themes look like a different corpus                       | Laptop pickle store vs Postgres                                   | Confirm both use the same Render `DATABASE_URL`                     |
| `API_BASE_URL` error about `dpg-` / `postgres.render.com` | Pasted the database URL into Vercel                               | Use the **web service** `https://<api>.onrender.com`                |


Operator playbook for Groq, clustering, and source pause remains [Runbook.md](./Runbook.md).

---

## 16. Rollback

- **Vercel:** Deployments → previous Production. Instant.
- **Render `discovery-api`:** Deploys → Redeploy previous image. Disk `/data` is unchanged.
- **Migrations:** forward-only (`schema_migrations`). There is no down migration. Restore a `pg_dump` taken before `migrate` if you must unwind SQL.
- **Frontend/backend contract:** keep API and `web/` on the same git SHA when possible. The UI must not re-aggregate if an old frontend hits a new API, but missing fields can blank a view.

---

## 17. Out of scope (this plan)

- Production scraper scale, multi-region HA, SSO
- Instagram / Facebook / Quora / on-site Myntra Q&A
- Switching embedding or chat hosts
- Putting the Next.js app on Render, or FastAPI on Vercel (Python + BGE + pgvector do not fit Vercel’s model)
- Changing frozen constants without a git commit + `python -m src.cli eval`

---

## 18. In-repo deploy hooks (done)

1. `Dockerfile`, `render.yaml`, `.dockerignore`, `web/vercel.json`.
2. `REQUIRE_POSTGRES=true` (API image default) makes `connect_store` wait, then fail — never `local_store.pkl`.
3. `python -m src.cli serve` uses platform `PORT` when `--port` is omitted; `--migrate` applies SQL before uvicorn.

