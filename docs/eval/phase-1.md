# Phase 1 eval — Play Store ingest + normalize / PII

**Plan:** [ImplementationPlan.md](../ImplementationPlan.md) Phase 1  
**Index:** [eval.md](../eval.md)  
**Edge cases:** [edge-case.md](../edge-case.md)

**Layer:** Backend  
**Depends on:** Phase 0 pass  
**Plan goal:** Play Store → raw → normalized, privacy-safe, relevant Myntra reviews only. First closed loop of the pipeline (Architecture §22.1).  
**Eval question:** Can we ingest Myntra Play reviews twice without dupes, scrub PII, and reject off-topic — without emptying the corpus?

---

## Plan exit criteria (must all be true)

- [ ] Sample corpus of Play Store reviews in `raw_documents` and `normalized_documents`
- [ ] Re-running ingest does not duplicate `(play_store, source_id)`
- [ ] Failed or empty pulls recorded on `ingest_runs`
- [ ] Operator can disable the source conceptually (flag on `ingest_runs` / config) without inventing metrics

---

## Pass bar

Must ingest checks + **20-row PII/relevance spot-check** signed off. Empty-pull and failed-pull both recorded on `ingest_runs`.

---

## How to run

```powershell
python -m src.cli ingest play_store --max-reviews 200
python -m src.cli normalize --since-run <ingest_run_uuid>
pytest tests/test_eval_phase1.py
```

Save CLI logs and a 20-row sample under `evals/runs/1/<date>/`.

---

## Checks

| ID | Kind | Pri | Check | Pass if |
| --- | --- | --- | --- | --- |
| **EV-1-01** | live | Must | `ingest play_store` for Myntra app | `raw_documents` count > 0, `source_type=play_store` |
| **EV-1-02** | live | Must | Run ingest **twice** | `COUNT(*)` unchanged; `fetched_at` may update |
| **EV-1-03** | auto | Must | Fixture: same `(play_store, source_id)` twice | One row |
| **EV-1-04** | live | Must | `normalize --since-run <id>` | `normalized_documents` populated; rejects have reason |
| **EV-1-05** | manual | Must | 20 random normalized rows | No email/phone/order-id/plaintext username; `author_hash` only |
| **EV-1-06** | auto | Must | Fixture: body contains email + order id | Scrubbed before normalize text used downstream |
| **EV-1-07** | auto | Must | Fixture: emoji-only / empty | Not in eligible normalized set (or quality 0 + documented) |
| **EV-1-08** | auto | Must | Fixture: exact duplicate whitespace | One survivor |
| **EV-1-09** | auto | Must | Fixture: Hinglish sizing/wishlist sentence | `language=hinglish` (or `hi`); `text_original` kept, not replaced by English |
| **EV-1-10** | auto | Must | Fixture: off-topic “myntra” as unrelated | `myntra_relevance=reject`; not normalized as insight |
| **EV-1-11** | manual | Must | Generic “app crash” reviews | Auditable reject **or** kept in raw with reason; corpus not 100% rejected |
| **EV-1-12** | auto | Must | Missing category | `product_category=unknown` (or equivalent), never null-forced |
| **EV-1-13** | auto | Must | Simulated 403/timeout | `ingest_runs.status=failed`; no success+empty lie |
| **EV-1-14** | auto | Must | Simulated 200 + 0 rows | `status=success`, `rows_fetched=0`, source still available |
| **EV-1-15** | live | Should | Incremental watermark | Second run fetches fewer/newer than first full pull |
| **EV-1-16** | auto | Should | Snapshot file exists when `payload_uri` set | File readable or row flagged if write failed |

Also run **EV-X-02**, **EV-X-04** from [eval.md](../eval.md).

---

## Spot-check rubric (EV-1-05)

| Item | Fail if |
| --- | --- |
| PII | Any `@`, 10-digit phone, `order`+id pattern in `text_original` |
| Username | Handle in analysis columns |
| Relevance | Obvious non-Myntra shopping thread in normalized |
| Language | Hinglish stored only as `en` with original discarded |

---

## Fail immediately

- Dupes on re-ingest
- Usernames on normalized rows
- Failed pull marked success

---

## Edge cases

EC-IN-01, EC-IN-05, EC-IN-06, EC-NO-01–03, EC-NO-05, EC-NO-07, EC-NO-09, EC-NO-10, EC-NO-12, EC-G-03, EC-G-10

---

## Out of scope for this gate

Groq extraction, BGE embeddings, other sources, dashboard, Instagram / Facebook. Near-dup MinHash is stretch, not a Phase 1 Must.
