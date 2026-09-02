# Deployment Plan

**Project:** Myntra Discovery Engine  
**GitHub:** [bestshubhamagarwal-web/Myntra-AI-Engine](https://github.com/bestshubhamagarwal-web/Myntra-AI-Engine)  
**Target:** FastAPI Query API **and** Next.js dashboard on **Vercel**; Postgres + **pgvector** on **Neon**  
**Companion:** [Architecture.md](./Architecture.md), [Runbook.md](./Runbook.md), [ImplementationPlan.md](./ImplementationPlan.md)

This is a research prototype, not production scraper HA. The goal is a public (or shareable) dashboard whose numbers still come from the Query API — the UI never computes SoV, impact, or confidence.

---

## 1. What we are deploying


| Piece                                                     | Lives in               | Host                                        | Role                                                          |
| --------------------------------------------------------- | ---------------------- | ------------------------------------------- | ------------------------------------------------------------- |
| Query API + Copilot (`src/api`, entry `api/index.py`)     | repo root              | **Vercel** project, root directory `.`      | Metrics, evidence, reports JSON, `POST /copilot/query`        |
| Postgres + **pgvector** `vector(1024)`                    | migrations `001`–`007` | **Neon** (Vercel Marketplace or neon.tech)  | Source of truth                                               |
| Next.js App Router (`web/`)                               | `web/`                 | **Vercel** project, root directory `web`    | Product UI; browsers talk only to this origin                 |
| Pipeline CLI (`ingest` → `cluster` → `ngrams` → `report`) | repo root              | Laptop (required for BGE + scrapers)        | Writes the corpus. Not the Vercel function                    |


Two Vercel projects, one GitHub repo. The dashboard proxies every API call through a Next.js route:

```
Browser  →  https://<web>.vercel.app/api/query/...
         →  https://<api>.vercel.app/{metrics,evidence,copilot,...}
```

Implemented in `web/app/api/query/[...path]/route.ts`. The browser never needs the FastAPI URL. Do **not** prefix `API_BASE_URL` or `API_SHARED_SECRET` with `NEXT_PUBLIC_`.

```
Public sources
    → CLI pipeline (laptop)
    → Neon Postgres (pgvector)
    → Vercel FastAPI  (Query API + Copilot + Groq)
         ↑
    Vercel Next.js  (proxy + dashboard)
         ↑
    Browser
```

Vercel does not run Postgres. Neon (or another host with `CREATE EXTENSION vector`) is the database. Do not paste a Neon URL into the dashboard project's `API_BASE_URL`.

---

## 2. Constraints that shape this plan

These are not optional footnotes. They decide function size, timeouts, and where the pipeline runs.

### 2.1 Two Vercel projects, one repo

Vercel’s **Root Directory** can be only one folder per project.

| Project (name them clearly) | Root Directory | Framework | Entry |
| --------------------------- | -------------- | --------- | ----- |
| Query API                   | **`.`** (repository root) | **FastAPI** | `api/index.py` exports `app` |
| Dashboard                   | **`web`** | **Next.js** | `web/package.json` |

Do **not** import the GitHub repo once and leave Root Directory empty — Vercel may treat the repo as Next.js *or* FastAPI and skip the other app.

`web/vercel.json` is only seen by the dashboard project. Repo-root `vercel.json` is only seen by the API project.

### 2.2 Auth

Prototype auth is header `X-API-Key` (or `Authorization: Bearer …`). `/health` and `/` are unauthenticated. Metrics and Copilot require `API_SHARED_SECRET` on the hosted API.

Set the **same** secret on both Vercel projects. The dashboard proxy injects `X-API-Key` when the browser does not send one. With both set, users should **not** see the AuthGate.

`src/api/serve.py` still refuses a public bind without a secret when you run `python -m src.api` locally. Vercel does not bind a socket; it loads `api/index.py`.

### 2.3 Postgres fallback is a local-dev trap

`src/db/connect.py` falls back to `data/local_store.pkl` if Postgres is not listening. On Vercel that file is **ephemeral** (`/tmp`) and the dashboard would look empty after every cold start.

The API project sets `REQUIRE_POSTGRES=true` automatically when `VERCEL=1`. Production `DATABASE_URL` / `POSTGRES_URL` must be the Neon (or other hosted pgvector) URL with `sslmode=require`.

If a leftover laptop `DATABASE_URL=localhost` is present, `connect.py` ignores it on Vercel and uses `POSTGRES_URL` / `POSTGRES_URL_NON_POOLING` / `PGHOST` instead.

### 2.4 pgvector is required

`migrations/001_init.sql` runs `CREATE EXTENSION IF NOT EXISTS vector` and `chunks.embedding vector(1024)`.

**Neon includes pgvector.** After the first API deploy, boot `--migrate` (already on in `api/index.py`) applies it. If `CREATE EXTENSION vector` fails, run it once in the Neon SQL Editor, then redeploy.

Do not use a generic Postgres that cannot load the extension.

### 2.5 BGE-M3 does not fit Vercel Functions

Embeddings never leave the machine (`BAAI/bge-m3`, dim **1024**). Weights are ~2 GB. Vercel’s FastAPI bundle is capped (500 MB standard; Large Functions up to 5 GB on Fluid, still too small and too slow for a first Copilot retrieve).

The API install is `requirements.txt` (no torch). Copilot `search_chunks` skips vectors when Sentence-Transformers is missing (`embed_error` in tool JSON) and continues with tagged quotes + Groq tools. **Dashboard metrics do not need BGE.**

Do not switch to OpenAI embeddings to “make Vercel work.” That violates Architecture §5.1. Run `python -m src.cli embed` on the laptop against Neon.

### 2.6 Ephemeral disk (`/tmp` only)

Vercel Functions can write **`/tmp`**. `load_settings()` maps `HF_HOME`, `RAW_STORE_PATH`, `REPORTS_PATH`, `LOCK_PATH`, and `LOCAL_STORE_PATH` there when `VERCEL=1`.

Report **JSON** is served from Postgres. Report **PDFs** written on a laptop (or a previous invocation) are not on the function filesystem — `GET /reports/{id}` JSON still works; PDF download returns 404 unless you generate the file in that isolate. That is expected.

Postgres data lives on **Neon**, not on Vercel disk.

### 2.7 Cloud IPs vs connectors

Play Store / public Reddit / Nitter-style hosts often **403/429 from datacenter IPs**. The runbook says: pause the source, do not scrape around the block, do not impute volume.

**Required split:** run ingest (and the full pipeline, including BGE) from a laptop against Neon via the Neon connection string. Keep Vercel as the **read path** (API + Copilot). Do not run `python -m src.cli ingest` inside the Vercel function.

### 2.8 Copilot latency

`web/app/api/query/[...path]/route.ts` sets `maxDuration = 120`. The proxy waits 110 s on POST (Copilot) and 20 s on GET (with retries). The API `vercel.json` sets `maxDuration` 120 on `api/index.py`. Vercel Fluid compute allows this on Hobby (max 300 s). First Copilot turn after a cold isolate + Groq can still be slow — that is expected.

### 2.9 Python version

Vercel’s Python runtime is **3.12** (also 3.13 / 3.14). Repo `.python-version` is `3.12`. Local and Docker (`python:3.11-slim` in the leftover `Dockerfile`) may stay on 3.11; `requires-python = ">=3.11"`.

Do **not** `pip install -e .` with default extras on Vercel — `pyproject.toml` still lists Sentence-Transformers for laptop work. The API project `installCommand` is `pip install -r requirements.txt && pip install --no-deps -e .`.

---

## 3. Target Vercel + Neon layout

One GitHub repo, two Vercel projects, one Neon database:


| Piece | Type | Public? |
| ----- | ---- | ------- |
| Neon Postgres 16+ with pgvector | Vercel **Storage → Neon**, or neon.tech project | No (dashboard). Connection string for API + laptop |
| `discovery-api` | GitHub → repo, **Root Directory = `.`**, framework **FastAPI** | Yes (`https://<api>.vercel.app`) |
| `discovery-web` | GitHub → same repo, **Root Directory = `web`**, framework **Next.js** | Yes (`https://<web>.vercel.app`) |


Region: put **Neon** and both Vercel projects in the same area. Closest to India is **Singapore** (`sin1` on Vercel; Neon `ap-southeast-1` when available).

`Dockerfile` / `railway.toml` / `render.yaml` stay in git as optional leftovers. **This plan does not use them.**

---

## 4. Prerequisites

- GitHub repo: [bestshubhamagarwal-web/Myntra-AI-Engine](https://github.com/bestshubhamagarwal-web/Myntra-AI-Engine) (both Vercel projects deploy from this remote). `.env` and `script.md` stay gitignored.
- Vercel account (two projects), Neon project with pgvector, Groq key (`GROQ_API_KEY`).
- A long random `API_SHARED_SECRET` and `AUTHOR_HMAC_SECRET`. Generate once; **do not rotate HMAC after real ingest** or `author_hash` values diverge.
- Python 3.11+ locally if you bootstrap the corpus from the laptop (BGE + scrapers).
- Optional: `YOUTUBE_API_KEY`, Reddit PRAW pair, `X_BEARER_TOKEN`. Empty keys already have public fallbacks; Instagram / Facebook / Quora stay unavailable.

---

## 5. Files in the repo

These are in git.

### 5.1 FastAPI on Vercel (repo root)

| File | Role |
| ---- | ---- |
| `api/index.py` | Exports FastAPI `app = create_app(migrate_on_boot=True)` |
| `vercel.json` | Framework `fastapi`, `maxDuration` 120, excludes `web/` / tests |
| `requirements.txt` | Query API only (no torch) |
| `.python-version` | `3.12` |
| `pyproject.toml` `[tool.vercel]` | `entrypoint = "api.index:app"` |
| `.vercelignore` | Drops `web/`, `tests/`, `docs/`, `data/` from the API bundle |

Vercel detects FastAPI from `api/index.py` + `fastapi` in `requirements.txt`. Every request hits that single function (Fluid compute).

`python -m src.api --migrate` is still the local/long-running server. You do not run uvicorn on Vercel.

### 5.2 Next.js on Vercel (`web/`)

Framework preset **Next.js**, **Root Directory = `web`**. `web/package.json` already has `build` / `start`. The proxy route is `force-dynamic` with `maxDuration = 120`.

### 5.3 Optional Docker

`Dockerfile` + `requirements-api.txt` remain for a container host if you ever need BGE in the API process. Not part of the Vercel go-live.

---

## 6. Neon — Postgres (pgvector)

1. In Vercel: **Storage → Create Database → Neon**, or create a project on [neon.tech](https://neon.tech) and paste the URL.
2. PostgreSQL **16+**. Enable / confirm **pgvector** (Neon: `CREATE EXTENSION vector` in the SQL Editor if the first migrate fails).
3. Same Neon project for the API and the laptop pipeline. Copy:
   - **Pooled** `POSTGRES_URL` (or `DATABASE_URL`) — fine for Query API reads
   - **Direct** `POSTGRES_URL_NON_POOLING` (or Neon “direct” connection) — preferred for `migrate`
4. Append `?sslmode=require` if the client does not add TLS. `src/db/connect.py` adds `sslmode=require` for `*.neon.tech` and strips Prisma’s `pgbouncer=true` query flag.
5. Do **not** create tables by hand. The app migrations own the schema, including `CREATE EXTENSION vector`.

On the **API** Vercel project, set `DATABASE_URL` to the Neon URL (or rely on the Neon integration’s `POSTGRES_URL` — the API reads both).

Sizing: Neon free / launch is enough to start. 1024-d vectors plus raw/normalized text grow with corpus size.

---

## 7. Vercel — API project (`discovery-api`)

1. [vercel.com](https://vercel.com) → **Add New → Project** → [bestshubhamagarwal-web/Myntra-AI-Engine](https://github.com/bestshubhamagarwal-web/Myntra-AI-Engine).
2. **Root Directory:** `.` (leave the repository root; do **not** set `web`).
3. Framework preset: **FastAPI** (or Other if FastAPI is detected from `api/index.py`).
4. Install command should match `vercel.json`: `pip install -r requirements.txt && pip install --no-deps -e .`
5. Environment variables (Production + Preview):


| Name | Value |
| ---- | ----- |
| `DATABASE_URL` | Neon URL (`postgresql://…@*.neon.tech/…?sslmode=require`) |
| `POSTGRES_URL` | Optional; Neon integration often sets this instead |
| `POSTGRES_URL_NON_POOLING` | Optional; used if `DATABASE_URL` is empty |
| `API_SHARED_SECRET` | long random string (same value on the dashboard project) |
| `AUTHOR_HMAC_SECRET` | long random string (stable) |
| `GROQ_API_KEY` | Groq key |
| `API_CORS_ORIGINS` | `https://<web>.vercel.app,http://localhost:3000` |


`REQUIRE_POSTGRES`, `HF_HOME`, and `/tmp` paths are set in code when `VERCEL=1`. You do not need `API_HOST` / `API_PORT`.

6. Deploy. Expected `GET https://<api>.vercel.app/health` JSON: `{"status":"ok","store":"postgres"}`. If `store` is `pending`, the isolate is up and still attaching Postgres — retry. If `store` is `memory`, Postgres was not reachable — fix `DATABASE_URL` before attaching the frontend.
7. Confirm OpenAPI at `https://<api>.vercel.app/docs` (optional; still behind CORS). Authenticated check: `GET /metrics/overview` with `X-API-Key`.

The API install is `requirements.txt` (no torch). Run ingest/embed from the laptop against Neon.

---

## 8. Bootstrap data

An empty migrated database serves Overview with zeros / empty themes. Populate it **before** calling the deploy “done.”

### Option A — Laptop writes to Neon (required for a real corpus)

On the machine that can reach Play Store / Reddit and can load BGE-M3:

```powershell
# Point local .env at Neon only for this session
$env:DATABASE_URL = "postgresql://…@ep-….ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
$env:GROQ_API_KEY = "gsk_..."   # same as Vercel API project
$env:AUTHOR_HMAC_SECRET = "<same as Vercel>"

python -m src.cli migrate
python -m src.cli pipeline --sources all --max-items 50 --limit 50 --cluster
python -m src.cli ngrams
python -m src.cli report
python -m src.cli source status
```

HMAC and Groq model ids must match the API project. Frozen constants stay as in `.env.example` (`C_MAX=200`, `S_MAX=4`, `GROQ_MODEL=openai/gpt-oss-120b`, `BGE_MODEL_ID=BAAI/bge-m3`).

Report PDFs stay on the laptop disk. The hosted API serves report JSON from Postgres; PDF download on Vercel is best-effort.

### Option B — Dump/restore an existing local DB

```powershell
docker exec <local-pg> pg_dump -U discovery -Fc discovery > discovery.dump
pg_restore --no-owner --no-acl -d $env:DATABASE_URL discovery.dump
```

Only valid if the local DB already used `vector(1024)` BGE-M3 (no dim mix). Then `CREATE EXTENSION vector` must already exist on Neon.

Do **not** schedule the pipeline as a Vercel Cron that shells out to ingest — no Play Store from function IPs, no BGE in the bundle.

Existing wrappers: `ops/cron/discovery.crontab`, `ops/windows/Register-PipelineTask.ps1`, `ops/n8n/discovery-pipeline.json`. Point their `DATABASE_URL` at Neon if you keep them. Pick **one** scheduler (`EC-OP-06`).

---

## 9. Vercel — dashboard project (`discovery-web`)

1. [vercel.com](https://vercel.com) → **Add New → Project** → same GitHub repo.
2. **Root Directory:** `web` (Edit, not the repo root).
3. Framework preset: Next.js. Build `npm run build`, output default.
4. Environment variables (Production + Preview):

  | Name                | Value                                                    | Exposed to browser?        |
  | ------------------- | -------------------------------------------------------- | -------------------------- |
  | `API_BASE_URL`      | `https://<api>.vercel.app` (no trailing slash)           | **No** — server proxy only |
  | `API_SHARED_SECRET` | identical to the FastAPI project                         | **No**                     |

   The proxy injects `X-API-Key` from `API_SHARED_SECRET` when the browser does not send one (`web/app/api/query/[...path]/route.ts`). If you omit the secret on the dashboard but set it on the API, the unlock screen appears and the value is stored in `sessionStorage` only.
5. Region: same as the API project (`sin1` if Neon is Singapore).
6. Deploy. Open `https://<web>.vercel.app`. Routes to check: `/overview`, `/themes`, `/evidence`, `/copilot`.

Preview deployments: the API already allows `https://*.vercel.app` via origin regex. CORS only matters for **browser → FastAPI** (OpenAPI, curl from a webpage). The product UI does not do that.

---

## 10. Environment reference

Copy from `.env.example`. Secrets stay in the Vercel / Neon dashboards, never in git.

### 10.1 Vercel `discovery-api` — required


| Variable             | Production notes                                                                                         |
| -------------------- | -------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`       | Neon URL (`*.neon.tech`). Prefer the direct host for migrate                                             |
| `POSTGRES_URL`       | Set by the Vercel Neon integration if you skip `DATABASE_URL`                                            |
| `GROQ_API_KEY`       | Generation only. `GROQ_BASE_URL=https://api.groq.com/openai/v1`                                          |
| `GROQ_MODEL`         | Frozen `openai/gpt-oss-120b`                                                                             |
| `GROQ_MODEL_LIGHT`   | Frozen `openai/gpt-oss-20b`                                                                              |
| `BGE_MODEL_ID`       | `BAAI/bge-m3` (laptop embed only; unused in the function bundle)                                         |
| `EMBEDDING_DIM`      | `1024`                                                                                                   |
| `AUTHOR_HMAC_SECRET` | Required for real ingest; keep stable                                                                    |
| `API_SHARED_SECRET`  | Required (hosted API)                                                                                    |
| `API_CORS_ORIGINS`   | `https://<web>.vercel.app,http://localhost:3000`                                                         |
| `C_MAX` / `S_MAX`    | `200` / `4`                                                                                              |


`connect.py` also reads `POSTGRES_URL_NON_POOLING`, `DATABASE_URL_UNPOOLED`, `PGHOST` / `PGUSER` / `PGPASSWORD` / `PGDATABASE` if `DATABASE_URL` is missing or still `localhost`.

### 10.2 Vercel `discovery-api` — connectors (optional)

Same names as `.env.example`: `PLAY_STORE_*`, `APP_STORE_*`, `REDDIT_*`, `YOUTUBE_*`, `X_*`. Disable with `PLAY_STORE_ENABLED=false` (etc.) rather than inventing zeros.

The Vercel replica will **not** ingest; connectors only matter on the laptop.

### 10.3 Vercel `discovery-web` — required


| Variable            | Production notes                         |
| ------------------- | ---------------------------------------- |
| `API_BASE_URL`      | FastAPI Vercel HTTPS origin              |
| `API_SHARED_SECRET` | Same string as the API project           |


Nothing else from the Python `.env` belongs on the dashboard project.

---

## 11. Order of operations (checklist)

Do these in order. Do not attach the dashboard until `/health` reports `store=postgres`.

- [ ] Repo on GitHub: [bestshubhamagarwal-web/Myntra-AI-Engine](https://github.com/bestshubhamagarwal-web/Myntra-AI-Engine); `.env` not committed
- [ ] Deploy `main` (contains `api/index.py` / `vercel.json` / `requirements.txt` / `web/vercel.json`)
- [ ] Neon project, region aligned with Vercel (`ap-southeast-1` / `sin1` when possible)
- [ ] Confirm `CREATE EXTENSION vector` is allowed (SQL Editor if needed)
- [ ] Vercel project `discovery-api`, root `.`, FastAPI, env `DATABASE_URL` + `API_SHARED_SECRET` + `AUTHOR_HMAC_SECRET` + `GROQ_API_KEY`
- [ ] Deploy; `/health` → `postgres`
- [ ] Migrations applied on boot (`schema_migrations`) or `python -m src.cli migrate` from the laptop
- [ ] Bootstrap corpus (laptop pipeline against Neon) (§8)
- [ ] Confirm `GET https://<api>.vercel.app/metrics/overview` with `X-API-Key` returns themes/counts
- [ ] Vercel project `discovery-web`, root `web`, env `API_BASE_URL` + `API_SHARED_SECRET`
- [ ] Set API `API_CORS_ORIGINS` to the dashboard URL
- [ ] Browser: Overview SoV matches a curl to the API; Copilot citations open the evidence drawer
- [ ] `python -m src.cli source status` — unavailable sources listed, not imputed
- [ ] Optional: one laptop / n8n scheduler for pipeline

---

## 12. Smoke tests after go-live

```powershell
# API (expect store=postgres)
curl https://<api>.vercel.app/health

# Authenticated overview
curl -H "X-API-Key: <secret>" https://<api>.vercel.app/metrics/overview

# Frontend proxy (from a logged-out browser, or)
curl https://<web>.vercel.app/api/query/health
curl https://<web>.vercel.app/api/query/metrics/overview
```

In the UI:

1. Overview corpus counts and unavailable badges.
2. One theme card → evidence drawer → source URL or “link unavailable”.
3. Copilot: a Q1–Q9 paraphrase; answer must not invent a % that is missing from tool JSON. Vector retrieve may show `embed_error` — quotes + metrics tools still work.
4. Reports: JSON loads; PDF only if the function happens to have the file under `/tmp`.
5. Disable a source (`python -m src.cli source disable play_store` against Neon) and confirm Overview + Copilot show **unavailable**, not last week’s volume.

---

## 13. Resource and cost sketch


| Resource                      | Why it exists                              | Ballpark                           |
| ----------------------------- | ------------------------------------------ | ---------------------------------- |
| Neon Postgres + storage       | Persistent SQL + vectors                   | Always-on Postgres                 |
| Vercel FastAPI Fluid function | Query API + Copilot (no BGE)               | Pay per invocation                 |
| Vercel Next.js                | Dashboard + 120 s proxy                    | UI + Copilot hop                   |
| Groq TPM                      | Extract, labels, Copilot, report narrative | Same as local; `GROQ_MAX_TPM=8000` |
| Laptop (pipeline)             | Ingest + BGE-M3                            | Not billed by Vercel               |


BGE is RAM + disk on the laptop, not a Vercel line item. Groq 429: raise `GROQ_MIN_INTERVAL_SECONDS`, lower `--limit`. Do not point `GROQ_BASE_URL` at OpenAI ([Runbook.md](./Runbook.md)).

---

## 14. Security

- Shared secret is prototype auth, not SSO. Anyone with the dashboard URL **and** a Vercel-injected secret can read the corpus. Treat the dashboard URL as internal unless you add real auth later.
- `API_SHARED_SECRET` on Vercel is server-only. Never `NEXT_PUBLIC_API_SHARED_SECRET`.
- Evidence CSV is scrubbed; laptop `RAW_STORE_PATH` may still have pre-scrub fields (`EC-SEC-06`).
- The FastAPI origin (`*.vercel.app`) is reachable from the internet. The secret is the gate. Rotate it in **both** Vercel projects together.
- Do not log `GROQ_API_KEY` or the shared secret. Do not paste `.env` into Vercel “plain text in build logs.”
- Laptop `DATABASE_URL` is the Neon URL. Do not commit it.

---

## 15. Failure modes (deploy-specific)


| Symptom                                                        | Likely cause                                                         | Fix                                                                 |
| -------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------- |
| API healthy, UI empty, `/health` `store=memory`                | `DATABASE_URL` wrong or Neon not up                                  | Neon `POSTGRES_URL` / `DATABASE_URL`; `sslmode=require`             |
| `/health` stuck `store=pending`                                | Neon unreachable or pooled URL blocking migrate                      | Direct `POSTGRES_URL_NON_POOLING`; retry the function               |
| `CREATE EXTENSION vector` fails                                | Extension not enabled on the database                                | Neon SQL Editor: `CREATE EXTENSION vector`; migrate again           |
| `API_SHARED_SECRET is not set on the hosted Query API`         | Secret missing on the API project                                    | Set it; redeploy                                                    |
| Dashboard 502 `Query API unreachable`                          | `API_BASE_URL` trailing path, `http` vs `https`, or wrong project    | Origin only, HTTPS, `https://<api>.vercel.app` (not Neon)           |
| Dashboard 401 AuthGate                                         | Secret on API project only                                           | Set the same secret on the dashboard project, or type it in the gate |
| Copilot 504                                                    | Cold start + Groq > proxy budget                                     | Retry; Fluid keeps the isolate warm                                 |
| Copilot `embed_error` / no vector quotes                       | No BGE in the function (by design)                                   | Metrics + tagged quotes still valid                                 |
| Report PDF 404                                                 | PDF is not on `/tmp` in this isolate                                 | Use JSON; generate PDFs on the laptop                               |
| Play Store ingest `failed` if you try it on Vercel             | Datacenter 403                                                       | Run ingest from laptop; mark source unavailable                     |
| Themes look like a different corpus                            | Laptop pickle store vs Neon                                          | Confirm both use the same Neon database                             |
| `API_BASE_URL` error about `neon.tech` / `rlwy.net`            | Pasted the database URL into the dashboard project                   | Use the **FastAPI** `https://<api>.vercel.app`                      |
| Build installs torch / exceeds bundle size                     | `pip install -e .` without `--no-deps`                               | Use `requirements.txt` then `pip install --no-deps -e .`            |
| Dashboard project builds FastAPI / API project builds Next.js  | Wrong Root Directory                                                 | API = `.` ; dashboard = `web`                                       |


Operator playbook for Groq, clustering, and source pause remains [Runbook.md](./Runbook.md).

---

## 16. Rollback

- **Either Vercel project:** Deployments → previous Production. Instant.
- **Migrations:** forward-only (`schema_migrations`). There is no down migration. Restore a `pg_dump` taken before `migrate` if you must unwind SQL.
- **Frontend/backend contract:** keep API and `web/` on the same git SHA when possible. The UI must not re-aggregate if an old frontend hits a new API, but missing fields can blank a view.

---

## 17. Out of scope (this plan)

- Production scraper scale, multi-region HA, SSO
- Instagram / Facebook / Quora / on-site Myntra Q&A
- Switching embedding or chat hosts
- Loading BGE-M3 inside the Vercel function
- Putting the pipeline CLI on Vercel Cron
- Changing frozen constants without a git commit + `python -m src.cli eval`

---

## 18. In-repo deploy hooks (done)

1. `api/index.py`, repo-root `vercel.json`, `requirements.txt`, `.python-version` (3.12), `web/vercel.json`.
2. `REQUIRE_POSTGRES=true` on Vercel makes `connect_store` wait, then fail — never `local_store.pkl`.
3. `create_app(migrate_on_boot=True)` applies SQL after Postgres attaches on each cold start (`schema_migrations` is idempotent).
4. `src/db/connect.py` treats Neon hosts (`*.neon.tech`, `sslmode=require`) and still understands leftover Railway/Render DSNs.
5. Writable paths on Vercel default to `/tmp`.
6. Dashboard proxy talks to `https://<api>.vercel.app` via `API_BASE_URL` (server-only).
