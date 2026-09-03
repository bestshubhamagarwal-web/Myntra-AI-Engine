# Eval

**Project:** AI-Powered Discovery Engine for Myntra Wishlist Behavior  
**Companion docs:** [ImplementationPlan.md](./ImplementationPlan.md), [Architecture.md](./Architecture.md), [edge-case.md](./edge-case.md), [problemStatement.md](./problemStatement.md)

This is the **phase gate index**. A phase is not done when the code exists; it is done when that phase’s **Pass bar** in its eval file is met. Do not start the next phase until the previous phase passes.

| Phase | Layer | Eval file |
| --- | --- | --- |
| 0 | Backend | [eval/phase-0.md](./eval/phase-0.md) — Foundation |
| 1 | Backend | [eval/phase-1.md](./eval/phase-1.md) — Play Store + normalize / PII |
| 2 | Backend | [eval/phase-2.md](./eval/phase-2.md) — Groq extract + BGE |
| 3 | Backend | [eval/phase-3.md](./eval/phase-3.md) — Multi-source ingest |
| 4 | Backend | [eval/phase-4.md](./eval/phase-4.md) — Cluster + metrics + impact |
| 5 | Backend | [eval/phase-5.md](./eval/phase-5.md) — Query API + Copilot API + jobs |
| 6 | Frontend | [eval/phase-6.md](./eval/phase-6.md) — Next.js product UI |
| 7 | Both | [eval/phase-7.md](./eval/phase-7.md) — Q1–Q9 gold + runbooks |

Phase 7 is the project-level Copilot / Q1–Q9 harness. Phases 0–6 are earlier gates so that harness is not evaluating a broken pipeline.

---

## How to use a phase eval

| Rule | Meaning |
| --- | --- |
| **Gate** | All **Must** checks pass. **Should** failures are logged; they do not block unless tagged P0 in [edge-case.md](./edge-case.md). |
| **Must / Should** | Must = ship blocker for that phase. Should = quality; fix before claiming the phase “healthy”. |
| **Kind** | `auto` = script/SQL/unit. `manual` = human spot-check. `live` = needs Groq / source APIs. |
| **Evidence** | Save command output, query results, or screenshots under `evals/runs/<phase>/<date>/`. |
| **Models** | Record `GROQ_MODEL`, `GROQ_MODEL_LIGHT`, `BGE_MODEL_ID` (+ revision) on every `live` run. Do not change mid-run. |
| **Regressions** | CI runs `auto` checks for the **current phase and all prior phases**. |

### Scorecard template (copy into `evals/runs/<phase>/<date>/scorecard.md`)

```
phase: N
date:
git_sha:
GROQ_MODEL:
GROQ_MODEL_LIGHT:
BGE_MODEL_ID:
result: pass | fail
must_failed: []
should_failed: []
notes:
```

### Cross-cutting (every phase, including 0)

Run these whenever code that could violate them lands.

| ID | Must | Check |
| --- | --- | --- |
| **EV-X-01** | Must | No OpenAI (or non-Groq) chat/embed keys required in `.env.example`. Grep: no `api.openai.com` embed/chat calls. |
| **EV-X-02** | Must | No secrets in git (`GROQ_API_KEY`, Reddit/YouTube tokens). |
| **EV-X-03** | Must | Unavailable source ≠ imputed count (once `source_status` / metrics exist). |
| **EV-X-04** | Must | PII not in `normalized_documents` / chunks / chat logs (from Phase 1 on). |

---

## Suggested command layout

```
evals/
  q1_q9.jsonl              # Phase 7 gold
  probes_phase5.jsonl      # Phase 5 Copilot API probes
  fixtures/                # PII, Hinglish, injection, dupes
  runs/
    0/ … 7/
docs/eval/
  phase-0.md … phase-7.md
```

```powershell
pytest tests/test_eval_phase0.py   # through test_eval_phase7.py
```

Live Groq/BGE smokes are opt-in (`GROQ_API_KEY` / `RUN_LIVE_BGE=1` / `RUN_LIVE_EXTRACT=1`). CI may skip `live`; record a developer-machine artifact under `evals/runs/<phase>/` for the SHA.

---

## Phase-to-plan traceability

| Phase | Implementation Plan exit criteria | Eval IDs (primary) |
| --- | --- | --- |
| 0 | Migrate, envelope, Groq, BGE 1024, no OpenAI keys | EV-0-* |
| 1 | Play corpus, no dupes, ingest_runs, no invented metrics | EV-1-* |
| 2 | Majority ok extract, retry failed JSON, tag SELECT, sizing kNN, no OpenAI path | EV-2-* |
| 3 | ≥4 sources, unique, source_status, qualitative language | EV-3-* |
| 4 | Ranked themes, quotes, unavailable_sources, bookmark split, hypothesis | EV-4-* |
| 5 | Query + Copilot API, SQL match, n-grams/reports jobs, no UI | EV-5-* |
| 6 | Next.js product UI, all §12 views, Copilot chat, API-only numbers | EV-6-* |
| 7 | Gold run, outage drill, recluster note, project DoD | EV-7-* |

---

## When eval fails, what to fix first

| Symptom | Likely layer | Do not |
| --- | --- | --- |
| Invented SoV | Copilot tools / prompt (Phase 5, 7) | Scrape more |
| Bad tags on Hinglish | Extract prompt / keep original text (Phase 2) | Translate-then-discard |
| Themes are generic app crashes | Relevance / clustering exclude (Phase 1, 4) | Relabel noise as opportunities |
| Dashboard ≠ Copilot | API as single path (Phase 5–6) | Dual aggregation |
| Missing Play volume “recovered” | unavailable_sources (Phase 3–7) | Interpolate |
| PII in citations | Normalize + quote validate (Phase 1–2) | Ship CSV |
