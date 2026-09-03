# Phase 0 eval — Foundation

**Plan:** [ImplementationPlan.md](../ImplementationPlan.md) Phase 0  
**Index:** [eval.md](../eval.md) (scorecard, EV-X-*, evidence layout)  
**Edge cases:** [edge-case.md](../edge-case.md)

**Layer:** Backend  
**Plan goal:** A runnable repo and empty database that later phases plug into without rewriting contracts.  
**Eval question:** Can a clean machine migrate, talk to Groq, and get 1024-d BGE vectors — with no OpenAI?

---

## Plan exit criteria (must all be true)

From Implementation Plan Phase 0:

- [ ] Fresh machine: clone, env, migrate, empty tables queryable
- [ ] Raw envelope schema frozen and documented in code
- [ ] Groq reachable with `GROQ_API_KEY`; BGE-M3 loads and returns 1024-d vectors
- [ ] `.env.example` has Groq + BGE vars only — no OpenAI embedding/chat keys

---

## Pass bar

All Must pass. Live Groq/BGE smoke may be skipped on CI **only** if a labeled `evals/runs/0/` artifact from a developer machine exists for this SHA.

---

## How to run

```powershell
python -m src.cli migrate
python -m src.cli smoke
pytest tests/test_eval_phase0.py
```

Skip live pieces while iterating: `python -m src.cli smoke --skip-bge` or `--skip-groq --skip-bge`.

Save output under `evals/runs/0/<date>/`.

---

## Checks

| ID | Kind | Pri | Check | Pass if |
| --- | --- | --- | --- | --- |
| **EV-0-01** | auto | Must | Layout: `src/ingest`, `normalize`, `extract`, `embed`, `cluster`, `metrics`, `api`, `prompts/`; `web/` stub OK | Paths exist |
| **EV-0-02** | auto | Must | `migrate` CLI against empty Postgres | Tables `raw_documents`, `normalized_documents`, `ingest_runs`, `ingest_queries` exist |
| **EV-0-03** | auto | Must | Unique `(source_type, source_id)` | Constraint present |
| **EV-0-04** | auto | Must | pgvector; `chunks.embedding` planned or created as `vector(1024)` | Dimension 1024 in migration/docs/code |
| **EV-0-05** | auto | Must | `.env.example` has `GROQ_API_KEY`, `GROQ_BASE_URL`, `GROQ_MODEL`, `GROQ_MODEL_LIGHT`, `BGE_MODEL_ID`, `EMBEDDING_DIM` | No `OPENAI_API_KEY` for chat/embed |
| **EV-0-06** | auto | Must | `ingest_queries` seeds include wishlist, cart, sizing, returns, Myntra vs AJIO | Rows present; AJIO is query text only |
| **EV-0-07** | live | Must | Groq 1-token chat or `models.list` with `GROQ_BASE_URL=https://api.groq.com/openai/v1` | HTTP 200 |
| **EV-0-08** | live | Must | Load `BAAI/bge-m3`, encode one sentence | `len(vector) == 1024` |
| **EV-0-09** | auto | Must | Raw envelope fields documented in code (Architecture §6.1) | Type/schema includes `source_type`, `source_id`, `url`, `raw_text`, `author_hash`, `payload_uri`, `myntra_relevance` |
| **EV-0-10** | auto | Should | README: Postgres, migrate, HF cache / local BGE path | New clone can follow it |

Also run **EV-X-01** and **EV-X-02** from [eval.md](../eval.md).

---

## Fail immediately

- BGE dim ≠ 1024
- Envelope still “TBD”
- OpenAI used as the default LLM or embed host

---

## Edge cases to exercise

EC-EM-01, EC-EM-08, EC-SEC-01, EC-G-04 (config only)

---

## Risks (from the plan — confirm eval covers them)

- Changing the envelope after Phase 1 causes connector rewrites — **EV-0-09** freeze.
- Hugging Face download blocked — vendor `BAAI/bge-m3` under `./data/models`; smoke still asserts dim 1024.
