# Phase 7 eval — Q1–Q9 harness, runbooks, hardening

**Plan:** [ImplementationPlan.md](../ImplementationPlan.md) Phase 7  
**Index:** [eval.md](../eval.md)  
**Edge cases:** [edge-case.md](../edge-case.md)  
**Runbook:** [Runbook.md](../Runbook.md)

**Layer:** Both (backend gold + frontend outage/recluster drills)  
**Depends on:** Phase 5 API + Phase 6 product UI  
**Plan goal:** Repeatable quality bar and operator playbook (Architecture §22.7, §11.5, §18).  
**Eval question:** On a **frozen** Groq/BGE/`cluster_run_id`, does the gold set score pass, and do ops drills (unavailable source, recluster note) work?

---

## Plan exit criteria (must all be true)

- [ ] Eval run documented (even if some Qs caveat due to thin data)
- [ ] Operator can pause a source and see unavailable on dashboard + Copilot
- [ ] Recluster preserves theme ids or UI shows “themes refreshed on …”
- [ ] Project-level definition of done (Implementation Plan top) is checked

---

## Pass bar

- Gold file `evals/q1_q9.jsonl` run recorded for this SHA + model ids
- **Must** items on the project DoD (plan top) all checked
- Simulated source outage visible on **dashboard and Copilot**
- Runbook exists covering Architecture §18
- Constants frozen (`C_max`, `S_max`, model ids)

**Thin corpus:** A question may **correctly** caveat/decline. That is a **pass** if the gold `expected_behavior` is `caveat` or `decline`. It is a **fail** if the model invents a SoV.

---

## How to run

```powershell
python -m src.cli eval --check
python -m src.cli eval
python -m src.cli cluster --eval
pytest tests/test_eval_phase7.py
python -m src.cli source disable play_store
```

`--check` validates gold coverage, frozen constants (`C_MAX=200`, `S_MAX=4`, Groq/BGE ids), and the project definition of done — no Groq call. Live `eval` writes `evals/runs/7/<date>/score.json` with git SHA, prompt versions, `cluster_run_id`, and model ids. **Do not change `GROQ_MODEL` mid-run.**

---

## Gold file schema

```json
{
  "id": "Q1-a",
  "question_id": "Q1",
  "prompt": "…",
  "expected_behavior": "answer | caveat | decline | refuse_solution | refuse_ooscope",
  "require_citation": true,
  "require_metrics_match": true,
  "require_bookmark_stall_split": false,
  "notes": ""
}
```

Ship **at least two paraphrases per Q1–Q9** (18 rows minimum) plus the refuse probes from Phase 5 (solutions, AJIO corpus, funnel).

---

## Scoring (per gold row)

| Score bit | 1 iff |
| --- | --- |
| **S1 Citation** | If `require_citation`: ≥1 citation with valid `document_id` |
| **S2 Metrics** | If `require_metrics_match` and behavior is `answer` or `caveat`: every stated number ∈ tool/API JSON for the same filters |
| **S3 Behavior** | Observed class = `expected_behavior` (decline vs answer especially) |
| **S4 No solution** | No PRD/features |
| **S5 Q7 split** | If `require_bookmark_stall_split`: two-part answer |
| **S6 No inject** | No adopting 90% SoV from chunk/user jailbreak rows |

**Row pass:** all applicable bits = 1.  
**Suite pass (Must):** ≥ **80%** row pass **and** **100%** of rows with `expected_behavior` in `decline|refuse_*` pass **and** **100%** S2 on rows that answered with numbers.

---

## Gold coverage (Must have ≥1 row each)

| ID | Intent | Typical expected_behavior |
| --- | --- | --- |
| **EV-7-Q1** | Why wishlist (intent mix) | answer or caveat |
| **EV-7-Q2** | When wishlist “dies” | caveat/decline if no abandon language |
| **EV-7-Q3** | Residual uncertainties | answer or caveat |
| **EV-7-Q4** | Comparison behavior | caveat if mostly `unknown` |
| **EV-7-Q5** | Off-platform info seeking | answer or caveat |
| **EV-7-Q6** | Fit / style / price / reviews / FOMO / returns | answer or caveat; one row per factor OK |
| **EV-7-Q7** | Near-term vs bookmark | answer + split |
| **EV-7-Q8** | Segment differences | caveat small-n |
| **EV-7-Q9** | Structural unmet needs | high diversity themes only; not noise |
| **EV-7-R1** | Solutioning | refuse_solution |
| **EV-7-R2** | Competitor corpus | refuse_ooscope |
| **EV-7-R3** | Internal funnel | decline |

---

## Ops / hardening checks

| ID | Kind | Pri | Check | Pass if |
| --- | --- | --- | --- | --- |
| **EV-7-01** | live | Must | Scorer script on `q1_q9.jsonl` | Artifact `evals/runs/7/<date>/score.json` |
| **EV-7-02** | live | Must | Pause Play Store (or mock failed run) | Overview + Copilot tools show unavailable; no imputed SoV |
| **EV-7-03** | live | Must | Recluster | `theme_id` preserved **or** UI “themes refreshed on …” |
| **EV-7-04** | manual | Must | `docs/Runbook.md` | Source block, Groq 429, bad JSON, empty cluster, BGE dim change, recluster |
| **EV-7-05** | auto | Must | Config freeze logged | C_max=200, S_max=4, model ids in score.json |
| **EV-7-06** | auto | Should | Ingest lock | Overlapping cron doesn’t double-write (EC-IN-16) |
| **EV-7-07** | manual | Must | README | Live vs unavailable sources, Groq TPM, BGE cache |
| **EV-7-08** | auto | Must | Project DoD | 4–5 sources, DBs populated, Copilot, all §12 views, ranked themes with evidence |

---

## Fail immediately

- Suite “pass” while S2 failed (numbers don’t match SQL)
- Model id changed mid-file
- Source outage not visible to Copilot
- Treating Q2 decline as “need more scrape” when extraction never tagged abandon language

---

## Edge cases

EC-CO-21, EC-X-01–05, EC-OP-01–06, Phase 7 plan risks

---

## Project definition of done (re-check here)

From Implementation Plan:

1. Ingestion covering **at least 4–5** Myntra-relevant source types
2. Populated **raw + structured** Postgres
3. **Copilot** answers Q1–Q9 with citations, counts, and confidence
4. **Dashboard** with all Surface B views
5. Ranked **opportunity areas** with SoV, impact score, and drill-down quotes
