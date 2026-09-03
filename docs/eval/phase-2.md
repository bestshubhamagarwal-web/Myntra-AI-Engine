# Phase 2 eval — Groq extraction + BGE embeddings

**Plan:** [ImplementationPlan.md](../ImplementationPlan.md) Phase 2  
**Index:** [eval.md](../eval.md)  
**Edge cases:** [edge-case.md](../edge-case.md)

**Layer:** Backend  
**Depends on:** Phase 1 pass  
**Plan goal:** Every normalized document can become tags + chunks + BGE vectors, without clustering yet (Architecture §22.2, §5.1).  
**Eval question:** Do ≥50% (target: **majority**) of normalized Play docs extract validly, and does sizing retrieval return related chunks?

**Suggested sample:** 50 docs for the smoke rubric (plan task 4); full corpus can be a follow-on job.

---

## Plan exit criteria (must all be true)

- [ ] Majority of normalized Play Store docs have `extraction_status=ok` via Groq
- [ ] Invalid Groq JSON is retryable and does not crash the batch
- [ ] `SELECT` by `friction_tag` / `intent_mode` / `maps_to_questions` works
- [ ] pgvector cosine on BGE vectors returns related chunks for a sizing query
- [ ] No OpenAI (or other) embedding/chat calls in this path

---

## Pass bar

Schema validity on the 50-doc sample **≥ 80% `ok` after retries**. Quote-span check **100% of `ok` rows** (invalid spans discarded or row failed). Retrieval smoke pass. Grep shows Groq base URL only for generation.

---

## How to run

```powershell
python -m src.cli migrate
python -m src.cli extract --limit 50
python -m src.cli embed --limit 50
python -m src.cli extract-eval --limit 50
python -m src.cli search "Myntra size too small / runs small" -k 8
pytest tests/test_eval_phase2.py
```

Live sample (EV-2-01 / 10 / 12) is opt-in (`RUN_LIVE_EXTRACT=1` / `RUN_LIVE_BGE=1`). Save `extract-eval` output under `evals/runs/2/<date>/`. Record `GROQ_MODEL` and `BGE_MODEL_ID` on the scorecard.

---

## Checks

| ID | Kind | Pri | Check | Pass if |
| --- | --- | --- | --- | --- |
| **EV-2-01** | live | Must | Batch extract 50 normalized docs | `extraction_status=ok` ≥ 80% (majority of **full** Play corpus still required as phase exit: ≥ 50% of all normalized) |
| **EV-2-02** | auto | Must | Invalid JSON fixture (mock Groq) | Retries then `failed`; document still in evidence; batch continues |
| **EV-2-03** | auto | Must | Pydantic schema matches Architecture §8.2 | Required enums; nulls allowed |
| **EV-2-04** | auto | Must | For each `ok` row: quote `span` is substring of `text_original`; offsets consistent or repaired | No dangling highlights |
| **EV-2-05** | auto | Must | `intent_mode` column ≠ copy of `friction_tag` | Distinct fields; fixture bookmark text ≠ stall-only |
| **EV-2-06** | auto | Must | Multi-friction fixture (fit + returns) | Both tags present |
| **EV-2-07** | auto | Must | Guessing fixture (“nice dress”) | `unknown` / `not_applicable`, not `price_watch` |
| **EV-2-08** | auto | Must | Re-run extract, same content hash | Groq not called (cache); token log shows skip |
| **EV-2-09** | auto | Must | Resume after kill | Continues from last id; cached rows not re-billed |
| **EV-2-10** | live | Must | Embed all chunks of sample | `vector_dims(embedding)=1024`; `embedding_model` contains `bge-m3` |
| **EV-2-11** | auto | Must | L2 norm ~ 1.0 | `abs(\|v\|-1) < 1e-3` |
| **EV-2-12** | live | Must | Query embed “Myntra size too small / runs small” with **same** BGE, **no** en-v1.5 prefix | Top-k includes size/fit chunks if present in sample |
| **EV-2-13** | auto | Must | HTTP/SDK: generation → Groq; embed → local | No OpenAI embed endpoint |
| **EV-2-14** | auto | Must | Failed extraction **not** eligible for later theme metrics (flag or view) | Query documents the exclusion |
| **EV-2-15** | auto | Should | Chunk metadata has tags after extract | `friction_tag` / `intent_mode` on chunk |
| **EV-2-16** | auto | Should | Long-doc chunking | Overlap; SoV later must be distinct `document_id` (unit test of counter) |
| **EV-2-17** | auto | Should | `severity` clipped to 0–1 | Out-of-range rejected |

Also run **EV-X-01**, **EV-X-04**.

---

## Full-corpus exit (plan)

| ID | Check | Pass if |
| --- | --- | --- |
| **EV-2-18** | All normalized Play docs | `ok` **majority** (>50%); remainder `failed`/`pending` explicit |

---

## Fail immediately

- Batch crash on one bad JSON
- Quote not in source text
- Embed via Groq/OpenAI
- `intent_mode` collapsed into friction

---

## Edge cases

EC-EX-01–08, EC-EX-11–13, EC-EM-01–06, EC-EM-10, EC-G-04, EC-G-06

---

## Out of scope for this gate

HDBSCAN, `themes`, dashboard, Copilot UI, hosted embedding APIs. Full segment Groq tagging may stay keyword-only until Phase 4.
