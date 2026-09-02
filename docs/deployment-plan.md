# Deployment Plan

**Project:** Myntra Discovery Engine  
**Target:** FastAPI Query API + Postgres on **Railway**, Next.js dashboard on **Vercel**  
**Companion:** [Architecture.md](./Architecture.md), [Runbook.md](./Runbook.md), [ImplementationPlan.md](./ImplementationPlan.md)

This is a research prototype, not production scraper HA. The goal is a public (or shareable) dashboard whose numbers still come from the Query API — the UI never computes SoV, impact, or confidence.

---

## 1. What we are deploying


| Piece                                                     | Lives in               | Host                                         | Role                                                           |
| --------------------------------------------------------- | ---------------------- | -------------------------------------------- | -------------------------------------------------------------- |
| Query API + Copilot (`src/api`)                           | repo root              | **Railway** web service                      | Metrics, evidence, reports, `POST /copilot/query`              |
| Postgres + **pgvector** `vector(1024)`                    | migrations `001`–`007` | **Railway pgvector** (not default Postgres)  | Source of truth                                                |
| Next.js App Router (`web/`)                               | `web/`                 | **Vercel**                                   | Product UI; browsers talk only to Vercel                       |
| Pipeline CLI (`ingest` → `cluster` → `ngrams` → `report`) | repo root              | Laptop (recommended), or a Railway **cron**  | Writes the corpus. Not required for the API process to stay up |


The frontend already proxies every API call through a Next.js route:

```
Browser  →  https://<vercel>/api/query/...
         →  https://<api>.up.railway.app/{metrics,evidence,copilot,...}
```

Implemented in `web/app/api/query/[...path]/route.ts`. The browser never needs the Railway URL. Do **not** prefix `API_BASE_URL` or `API_SHARED_SECRET` with `NEXT_PUBLIC_`.

```
Public sources
    → CLI pipeline (laptop or cron)
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

`src/api/serve.py` and `Settings.require_api_secret_if_public()` refuse to bind anything other than localhost unless `API_SHARED_SECRET` is set. Railway must bind `0.0.0.0` and `$PORT`.

Prototype auth is header `X-API-Key` (or `Authorization: Bearer …`). `/health` and `/` are unauthenticated — use `/health` as the Railway health check. `/health` returns **200** while the process is up (`store=pending` until Postgres attaches, then `{"status":"ok","store":"postgres"}`). Metrics stay 503 until the store is ready so Railway does not mark the deploy failed mid-handshake.

### 2.2 Postgres fallback is a local-dev trap

`src/db/connect.py` falls back to `data/local_store.pkl` if Postgres is not listening. On Railway that file is **ephemeral** unless you mount a volume, and the dashboard would look empty after every restart.

Set `REQUIRE_POSTGRES=true` on the API. Production `DATABASE_URL` must be the **private** Railway URL (`*.railway.internal`). The laptop pipeline is the exception — it must use `DATABASE_PUBLIC_URL` (`*.proxy.rlwy.net` or `*.rlwy.net`) with `sslmode=require`.

If a leftover laptop `DATABASE_URL=localhost` is present, `connect.py` ignores it on Railway and uses `DATABASE_PRIVATE_URL` / `DATABASE_PUBLIC_URL` / `PGHOST` instead.

### 2.3 pgvector is required

`migrations/001_init.sql` runs `CREATE EXTENSION IF NOT EXISTS vector` and `chunks.embedding vector(1024)`.

**Railway’s default Postgres plugin does not include pgvector.** Deploy the **pgvector** template in the same project (official `pgvector/pgvector` image). After the database is up, `python -m src.api --migrate` runs `CREATE EXTENSION vector` if it is not already there.

Do not use a generic Postgres image without the extension.

### 2.4 BGE-M3 is local and heavy

Embeddings never leave the machine (`BAAI/bge-m3`, dim **1024**). Weights are ~2 GB under `HF_HOME`. Copilot `search_chunks` loads Sentence-Transformers on first use (`src/api/copilot.py`). Dashboard metrics work without loading BGE; Copilot vector search does not.

Budget **≥8 GB RAM** for the API service if Copilot retrieval should work. A 512 MB replica will OOM on first Copilot retrieve (metrics-only still works; Copilot then uses tagged quotes / Groq tools only, with `embed_error` in tool JSON).

Do not switch to OpenAI embeddings to “make Railway cheaper.” That violates Architecture §5.1.

### 2.5 Ephemeral disk

Railway containers lose the filesystem on each deploy unless a **volume** is attached. Persist:


| Path            | Env              | Why                                            |
| --------------- | ---------------- | ---------------------------------------------- |
| `/data/models`  | `HF_HOME`        | BGE weights (~2 GB); skip re-download          |
| `/data/reports` | `REPORTS_PATH`   | Weekly PDF bytes served by `GET /reports/{id}` |
| `/data/raw`     | `RAW_STORE_PATH` | Redacted ingest snapshots                      |
| `/data/locks`   | `LOCK_PATH`      | Pipeline overlap lock (`EC-IN-16`)             |


Postgres data lives on the **pgvector service volume**, not this API volume.

A Railway volume is attached to **one** service. Cron jobs do **not** see `/data`. To write report PDFs onto the volume, run `python -m src.cli report` from the **API service** (Railway CLI `railway run` against that service, or a one-off start command), not from a separate job unless it mounts the same volume.

### 2.6 Cloud IPs vs connectors

Play Store / public Reddit / Nitter-style hosts often **403/429 from datacenter IPs**. The runbook says: pause the source, do not scrape around the block, do not impute volume.

**Recommended split:** run ingest (and optionally the full pipeline) from a laptop against Railway Postgres via **`DATABASE_PUBLIC_URL`**. Keep the Railway web service as a **read path** (API + Copilot). If you do run ingest on Railway, expect Play Store `failed` / `unavailable` and treat that as honest, not a bug to bypass.

### 2.7 Copilot latency

`web/app/api/query/[...path]/route.ts` already sets `maxDuration = 120` and a 20 s upstream abort with retries; the client waits up to 60 s. Vercel Fluid compute allows this on Hobby (max 300 s). First Copilot turn after a cold BGE load can still be slow — that is expected. Keep the Railway API replica on so it does not sleep between Copilot turns.

---

## 3. Target Railway project

One Railway **project** (same environment), two services, plus Vercel for the UI:


| Service              | Type                                                    | Public?                                                          |
| -------------------- | ------------------------------------------------------- | ---------------------------------------------------------------- |
| `pgvector` (or similar name) | Railway **pgvector** template, Postgres 16+     | No (private URL for `api`). Public TCP proxy for laptop pipeline |
| `discovery-api`      | GitHub → this repo, **root directory = repo root**      | Yes (`*.up.railway.app`)                                         |
| volume on `api`      | Mounted at `/data`                                      | —                                                                |
| `discovery-pipeline` (optional) | Railway cron, same image / start command     | No                                                               |


Do **not** point the Vercel project at the repo root. Vercel’s root directory is `web/`.

Do **not** add a second Railway service for `web/` — the dashboard stays on Vercel.

Region: pick **one** Railway region for both pgvector and the API (private networking is same-project / same-environment). Closest to India is **Southeast Asia** (`southeast-asia`) when available. Vercel: `sin1`.

`render.yaml` in this repo is a leftover from an earlier host. This plan does **not** use it.

---

## 4. Prerequisites

- GitHub repo with this project (Railway and Vercel both deploy from git). `.env` and `script.md` stay gitignored.
- Railway account, Vercel account, Groq key (`GROQ_API_KEY`).
- A long random `API_SHARED_SECRET` and `AUTHOR_HMAC_SECRET`. Generate once; **do not rotate HMAC after real ingest** or `author_hash` values diverge.
- Python 3.11+ locally if you bootstrap the corpus from the laptop.
- Optional: `YOUTUBE_API_KEY`, Reddit PRAW pair, `X_BEARER_TOKEN`. Empty keys already have public fallbacks; Instagram / Facebook / Quora stay unavailable.

---

## 5. Files in the repo

These are in git. Railway builds the **repo root** (`Dockerfile` or Nixpacks + `requirements-api.txt`). Vercel uses `web/` (including `web/vercel.json`). Do not bind `127.0.0.1:8000` on Railway.

### 5.1 Railway build and start

Prefer the repo-root **Dockerfile** (Railway uses it automatically when present):

- Install: `pip install -r requirements-api.txt && pip install --no-deps -e .`
- Command: `python -m src.api --migrate --host 0.0.0.0`

Do **not** `pip install -e .` with default extras on Railway — that pulls Sentence-Transformers / torch and a 15–20 minute build. `requirements-api.txt` is the Query API only (no torch). Metrics and Copilot tools work; vector retrieve loads BGE only if those weights exist under `HF_HOME`.

If you switch the service to **Nixpacks / Railpack** instead of Docker, set:

| Setting        | Value                                                                  |
| -------------- | ---------------------------------------------------------------------- |
| Build command  | `pip install -r requirements-api.txt && pip install --no-deps -e .`    |
| Start command  | `python -m src.api --migrate --host 0.0.0.0`                           |
| Python version | `3.11.11` (repo `.python-version`)                                     |

`python -m src.api` reads Railway’s `PORT`, listens immediately, then attaches Postgres and applies migrations (`schema_migrations` is idempotent).

If migrate-on-boot feels too tight, run it once and drop `--migrate`:

```text
python -m src.cli migrate
```

**Do not** bind `127.0.0.1` or hard-code port 8000 on Railway. Health checks will fail.

### 5.2 `.dockerignore` (repo root)

Keeps the API image small. `web/` is excluded so Railway does not copy the Next.js app into the API image.

### 5.3 `web/vercel.json`

Framework preset **Next.js**, Vercel **Root Directory =** `web`. `web/package.json` already has `build` / `start`. The proxy route is already `force-dynamic` with `maxDuration = 120`.

---

## 6. Railway — Postgres (pgvector)

1. In the Railway project: **New → Template** (or **Database**) and pick **Postgres with pgvector**, not the plain Postgres plugin.
2. PostgreSQL **16+** with the `vector` extension. Same environment as `discovery-api`.
3. Attach a **volume** to the database service. For PG18 templates, mount at `/var/lib/postgresql` (not the old `/var/lib/postgresql/data` path).
4. Wait until the database is running. Variables tab:
   - **Private** `DATABASE_URL` — host like `postgres.railway.internal` — for the API
   - **Public** `DATABASE_PUBLIC_URL` — host like `*.proxy.rlwy.net` — laptop pipeline / `psql` only
5. Append `?sslmode=require` to the public URL if the client does not add TLS itself. Private `.railway.internal` connections use `sslmode=disable` (`src/db/connect.py`).
6. Do **not** create tables by hand. The app migrations own the schema, including `CREATE EXTENSION vector`.

On the **API service**, reference the database instead of pasting a secret:

```text
DATABASE_URL=${{pgvector.DATABASE_URL}}
```

Use the **private** variable from the pgvector service (often `DATABASE_URL` or `DATABASE_PRIVATE_URL`). Do not paste the public `rlwy.net` URL onto the API.

Sizing: start with 1 GB RAM / 5–10 GB volume. 1024-d vectors plus raw/normalized text grow with corpus size.

---

## 7. Railway — API service

1. **New → GitHub Repo** → this repo. Root directory = repository root (not `web/`).
2. Builder: **Dockerfile** at repo root (default if the file exists). Watchtower / start command can stay empty so the image `CMD` runs.
3. **Settings → Networking → Generate domain.** Public URL is `https://<service>.up.railway.app`. Copy this origin into Vercel `API_BASE_URL`.
4. **Settings → Health check path:** `/health`.
5. **Volume** at `/data` (10 GB is enough for BGE + reports). Plan **≥8 GB RAM** for Copilot + BGE; **2 GB** is acceptable for a metrics-only demo (expect Copilot retrieve to skip vectors).
6. Variables (see §10). Minimum to boot:


| Name                 | Value                                                                 |
| -------------------- | --------------------------------------------------------------------- |
| `DATABASE_URL`       | `${{pgvector.DATABASE_URL}}` (private, `.railway.internal`)           |
| `API_HOST`           | `0.0.0.0`                                                             |
| `REQUIRE_POSTGRES`   | `true`                                                                |
| `API_SHARED_SECRET`  | long random string (same value on Vercel)                             |
| `AUTHOR_HMAC_SECRET` | long random string (stable)                                           |
| `GROQ_API_KEY`       | Groq key                                                              |
| `HF_HOME`            | `/data/models`                                                        |
| `RAW_STORE_PATH`     | `/data/raw`                                                           |
| `REPORTS_PATH`       | `/data/reports`                                                       |
| `LOCK_PATH`          | `/data/locks`                                                         |
| `LOCAL_STORE_PATH`   | `/data/local_store.pkl` (must not be the live store)                  |


7. Expected `/health` JSON: `{"status":"ok","store":"postgres"}`. If `store` is `pending`, the process is up and still attaching Postgres (liveness is 200). If `store` is `memory`, Postgres was not reachable — fix `DATABASE_URL` before sharing the frontend. If logs show `*.railway.internal` DNS failure, set `DATABASE_URL` to the private reference from the pgvector service (same project + region). If the API was given the public `rlwy.net` URL, `connect.py` still accepts it with `sslmode=require`.
8. After the first successful deploy, confirm OpenAPI at `https://<api>.up.railway.app/docs` (optional; still behind CORS).

The API install is `requirements-api.txt` (no torch). Run ingest/embed from the laptop against `DATABASE_PUBLIC_URL`.

---

## 8. Bootstrap data

An empty migrated database serves Overview with zeros / empty themes. Populate it **before** calling the deploy “done.”

### Option A — Laptop writes to Railway Postgres (recommended)

On the machine that can reach Play Store / Reddit:

```powershell
# Point local .env at the PUBLIC Railway URL only for this session
$env:DATABASE_URL = "postgresql://postgres:...@xxxxx.proxy.rlwy.net:xxxxx/railway?sslmode=require"
$env:GROQ_API_KEY = "gsk_..."   # same as Railway
$env:AUTHOR_HMAC_SECRET = "<same as Railway>"

python -m src.cli migrate
python -m src.cli pipeline --sources all --max-items 50 --limit 50 --cluster
python -m src.cli ngrams
python -m src.cli report
python -m src.cli source status
```

HMAC and Groq model ids must match the API service. Frozen constants stay as in `.env.example` (`C_MAX=200`, `S_MAX=4`, `GROQ_MODEL=openai/gpt-oss-120b`, `BGE_MODEL_ID=BAAI/bge-m3`).

If you generated reports locally, re-run `python -m src.cli report` on the API service so files land on `/data/reports`, or accept JSON report metadata without a downloadable PDF.

### Option B — Railway cron

Same image as `api`, schedule e.g. daily 02:00 UTC:

```text
python -m src.cli pipeline --sources all --cluster && python -m src.cli ngrams && python -m src.cli report
```

The cron **does not** share `/data` with `api` unless you attach the same volume. Pick **one** scheduler (Railway cron **or** laptop Task Scheduler **or** n8n) — not two (`EC-OP-06`).

Existing wrappers: `ops/cron/discovery.crontab`, `ops/windows/Register-PipelineTask.ps1`, `ops/n8n/discovery-pipeline.json`. Point their `DATABASE_URL` at Railway **`DATABASE_PUBLIC_URL`** if you keep them.

### Option C — Dump/restore an existing local DB

```powershell
docker exec <local-pg> pg_dump -U discovery -Fc discovery > discovery.dump
# restore into Railway using DATABASE_PUBLIC_URL
pg_restore --no-owner --no-acl -d $env:DATABASE_URL discovery.dump
```

Only valid if the local DB already used `vector(1024)` BGE-M3 (no dim mix).

---

## 9. Vercel — frontend

1. [vercel.com](https://vercel.com) → **Add New → Project** → same GitHub repo.
2. **Root Directory:** `web` (Edit, not the repo root).
3. Framework preset: Next.js. Build `npm run build`, output default.
4. Environment variables (Production + Preview):

  | Name                | Value                                               | Exposed to browser?        |
  | ------------------- | --------------------------------------------------- | -------------------------- |
  | `API_BASE_URL`      | `https://<service>.up.railway.app` (no trailing slash) | **No** — server proxy only |
  | `API_SHARED_SECRET` | identical to Railway                                | **No**                     |

   The proxy injects `X-API-Key` from `API_SHARED_SECRET` when the browser does not send one (`web/app/api/query/[...path]/route.ts`). With both set, users should **not** see the AuthGate. If you omit the secret on Vercel but set it on Railway, the unlock screen appears and the value is stored in `sessionStorage` only.
5. Region: pick the Vercel region closest to the Railway region (Southeast Asia → `sin1`, US West → `sfo1`) so Copilot’s two hops stay short.
6. Deploy. Open `https://<project>.vercel.app`. Routes to check: `/overview`, `/themes`, `/evidence`, `/copilot`.

Preview deployments: either allow `https://*.vercel.app` in Railway `API_CORS_ORIGINS`, or rely on the server-side proxy (CORS does not apply to the proxy hop). CORS only matters for **browser → Railway** (OpenAPI, curl from a webpage). The product UI does not do that. The API already allows `https://*.vercel.app` via origin regex.

---

## 10. Environment reference

Copy from `.env.example`. Secrets stay in the host dashboards, never in git.

### 10.1 Railway `discovery-api` — required


| Variable             | Production notes                                                                                         |
| -------------------- | -------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`       | **Private** URL only (`*.railway.internal`). Prefer `${{pgvector.DATABASE_URL}}`                         |
| `GROQ_API_KEY`       | Generation only. `GROQ_BASE_URL=https://api.groq.com/openai/v1`                                          |
| `GROQ_MODEL`         | Frozen `openai/gpt-oss-120b`                                                                             |
| `GROQ_MODEL_LIGHT`   | Frozen `openai/gpt-oss-20b`                                                                              |
| `BGE_MODEL_ID`       | `BAAI/bge-m3`                                                                                            |
| `EMBEDDING_DIM`      | `1024`                                                                                                   |
| `HF_HOME`            | `/data/models`                                                                                           |
| `AUTHOR_HMAC_SECRET` | Required for real ingest; keep stable                                                                    |
| `API_HOST`           | `0.0.0.0`                                                                                                |
| `REQUIRE_POSTGRES`   | `true` (do not turn off)                                                                                 |
| `API_SHARED_SECRET`  | Required (public bind)                                                                                   |
| `API_CORS_ORIGINS`   | `https://<vercel-prod>,http://localhost:3000`                                                            |
| `RAW_STORE_PATH`     | `/data/raw`                                                                                              |
| `REPORTS_PATH`       | `/data/reports`                                                                                          |
| `LOCK_PATH`          | `/data/locks`                                                                                            |
| `C_MAX` / `S_MAX`    | `200` / `4`                                                                                              |


Railway sets `PORT`. Uvicorn must use `${PORT}`. You do not need `API_PORT` if the start command omits `--port`.

`connect.py` also reads `DATABASE_PRIVATE_URL`, `DATABASE_PUBLIC_URL`, `PGHOST` / `PGUSER` / `PGPASSWORD` / `PGDATABASE` if `DATABASE_URL` is missing or still `localhost`.

### 10.2 Railway `discovery-api` — connectors (optional)

Same names as `.env.example`: `PLAY_STORE_*`, `APP_STORE_*`, `REDDIT_*`, `YOUTUBE_*`, `X_*`. Disable with `PLAY_STORE_ENABLED=false` (etc.) rather than inventing zeros.

If the API replica will **not** ingest, you can leave connectors enabled in env; they only matter when `python -m src.cli ingest` / `pipeline` runs.

### 10.3 Vercel `web` — required


| Variable            | Production notes                    |
| ------------------- | ----------------------------------- |
| `API_BASE_URL`      | Railway public HTTPS origin         |
| `API_SHARED_SECRET` | Same string as Railway              |


Nothing else from the Python `.env` belongs on Vercel.

---

## 11. Order of operations (checklist)

Do these in order. Do not attach Vercel until `/health` reports `store=postgres`.

- [ ] Repo on GitHub; `.env` not committed
- [ ] Deploy the branch that contains `Dockerfile` / `requirements-api.txt` / `web/vercel.json`
- [ ] Railway project, region **southeast-asia** (or one region for both services)
- [ ] Add **pgvector** template (not default Postgres); wait until it is running
- [ ] Add GitHub service `discovery-api` from repo root; Dockerfile build
- [ ] Set `DATABASE_URL=${{pgvector.DATABASE_URL}}` (private)
- [ ] Paste `API_SHARED_SECRET`, `AUTHOR_HMAC_SECRET`, `GROQ_API_KEY`
- [ ] Volume at `/data`; replica RAM ≥8 GB (or 2 GB metrics-only)
- [ ] Generate public domain; health check `/health`
- [ ] Deploy; `/health` → `postgres`
- [ ] `python -m src.cli migrate` (boot `--migrate` or `railway run`)
- [ ] Bootstrap corpus (laptop pipeline or cron) (§8)
- [ ] Confirm `GET https://<api>.up.railway.app/metrics/overview` with `X-API-Key` returns themes/counts
- [ ] Vercel project, root `web`, env `API_BASE_URL` + `API_SHARED_SECRET`
- [ ] Set Railway `API_CORS_ORIGINS` to the Vercel URL
- [ ] Browser: Overview SoV matches a curl to the API; Copilot citations open the evidence drawer
- [ ] `python -m src.cli source status` — unavailable sources listed, not imputed
- [ ] Optional: one cron for pipeline; disable local Task Scheduler if cron is on

---

## 12. Smoke tests after go-live

```powershell
# API (expect store=postgres)
curl https://<api>.up.railway.app/health

# Authenticated overview
curl -H "X-API-Key: <secret>" https://<api>.up.railway.app/metrics/overview

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


| Resource                         | Why it exists                              | Ballpark                           |
| -------------------------------- | ------------------------------------------ | ---------------------------------- |
| Railway pgvector + volume        | Persistent SQL + vectors                   | Always-on Postgres                 |
| Railway `discovery-api` ≥8 GB    | FastAPI + optional BGE                     | Dominant compute cost              |
| Railway volume ~10 GB on API     | Weights + reports + raw                    | Cheap vs RAM                       |
| Vercel Hobby/Pro                 | Next.js + 120 s proxy                      | UI + Copilot hop                   |
| Groq TPM                         | Extract, labels, Copilot, report narrative | Same as local; `GROQ_MAX_TPM=8000` |


BGE is not billed; it is RAM + disk. Groq 429: raise `GROQ_MIN_INTERVAL_SECONDS`, lower `--limit`. Do not point `GROQ_BASE_URL` at OpenAI ([Runbook.md](./Runbook.md)).

---

## 14. Security

- Shared secret is prototype auth, not SSO. Anyone with the Vercel URL **and** a Vercel-injected secret can read the corpus. Treat the Vercel URL as internal unless you add real auth later.
- `API_SHARED_SECRET` on Vercel is server-only. Never `NEXT_PUBLIC_API_SHARED_SECRET`.
- Evidence CSV is scrubbed; `RAW_STORE_PATH` may still have pre-scrub fields — volume access = operator-only (`EC-SEC-06`).
- Railway public API (`*.up.railway.app`) is reachable from the internet. The secret is the gate. Rotate it in **both** dashboards together.
- Do not log `GROQ_API_KEY` or the shared secret. Do not paste `.env` into Vercel “plain text in build logs.”
- Laptop `DATABASE_URL` is the **public** Postgres URL (`DATABASE_PUBLIC_URL`). Do not commit it. Disable the TCP proxy if you do not need ingest from outside Railway.

---

## 15. Failure modes (deploy-specific)


| Symptom                                                        | Likely cause                                                         | Fix                                                                 |
| -------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Railway deploy healthy, UI empty, `/health` `store=memory`     | `DATABASE_URL` wrong or pg not up at boot                            | Private `${{pgvector.DATABASE_URL}}`; restart after DB is running   |
| `/health` stuck `store=pending`                                | Private host unreachable or public URL without TLS                   | Same project; `.railway.internal` internally, `sslmode=require` on `rlwy.net` |
| `failed to resolve host '…railway.internal'`                   | API and DB not in the same Railway environment / project             | Recreate both in one project; use the variable reference            |
| `SSL/TLS required` on `*.rlwy.net`                             | Public proxy requires TLS                                            | `sslmode=require` (code already adds this for `.rlwy.net`)          |
| `CREATE EXTENSION vector` fails                                | Used default Railway Postgres, not the pgvector template             | Deploy the pgvector template; migrate again                         |
| Health check never passes                                      | Bind `127.0.0.1` or port 8000 instead of `$PORT`                     | `python -m src.api` in §5.1                                         |
| `API_SHARED_SECRET is required when binding…`                  | Bound `0.0.0.0` without secret and without platform `PORT`           | Set secret                                                          |
| Vercel 502 `Query API unreachable`                             | `API_BASE_URL` trailing path, `http` vs `https`, or API asleep       | Origin only, HTTPS, `*.up.railway.app` (not the Postgres host)      |
| Vercel 401 AuthGate                                            | Secret on Railway only                                               | Set the same secret on Vercel, or type it in the gate               |
| Copilot 504                                                    | Cold start + BGE load + Groq > proxy budget                          | Keep replica awake; 8 GB RAM; retry                                 |
| OOMKilled on first Copilot question                            | BGE load on a small replica                                          | Scale RAM to ≥8 GB or accept retrieve skip                          |
| Report PDF 404                                                 | `REPORTS_PATH` not on the API volume / report ran on laptop or cron  | Re-run `report` on the API service                                  |
| Play Store ingest `failed` on Railway                          | Datacenter 403                                                       | Run ingest from laptop; mark source unavailable                     |
| Themes look like a different corpus                            | Laptop pickle store vs Postgres                                      | Confirm both use the same Railway database                          |
| `API_BASE_URL` error about `railway.internal` / `rlwy.net`     | Pasted the database URL into Vercel                                  | Use the **web service** `https://<api>.up.railway.app`              |
| Build installs torch / takes 20 minutes                        | `pip install -e .` without `--no-deps`                               | Use `requirements-api.txt` then `pip install --no-deps -e .`        |


Operator playbook for Groq, clustering, and source pause remains [Runbook.md](./Runbook.md).

---

## 16. Rollback

- **Vercel:** Deployments → previous Production. Instant.
- **Railway `discovery-api`:** Deployments → Redeploy previous. Volume `/data` is unchanged.
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

1. `Dockerfile`, `requirements-api.txt`, `.python-version`, `web/vercel.json`.
2. `REQUIRE_POSTGRES=true` makes `connect_store` wait, then fail — never `local_store.pkl`.
3. `python -m src.api` uses platform `PORT` when `--port` is omitted; `--migrate` applies SQL after Postgres attaches.
4. `src/db/connect.py` already treats Railway hosts: `.railway.internal` (private, no TLS) and `.rlwy.net` (public, `sslmode=require`).
