# Phase 4 eval — Clustering, quantification, impact score

**Plan:** [ImplementationPlan.md](../ImplementationPlan.md) Phase 4  
**Index:** [eval.md](../eval.md)  
**Edge cases:** [edge-case.md](../edge-case.md)

**Layer:** Backend  
**Depends on:** Phase 2 (required); Phase 3 preferred before a “final” cluster  
**Plan goal:** Named **opportunity areas** with shared SQL metrics (Architecture §22.4, §8.3–8.6). Analytical core.  
**Eval question:** Can we rank themes with SoV, confidence, impact, quotes, honest unavailable sources — without forcing k=10 on noise?

---

## Plan exit criteria (must all be true)

- [ ] Ranked theme list with SoV, confidence, impact_score
- [ ] Every theme joins to ≥1 document and verbatim quote
- [ ] Missing source appears in `unavailable_sources`, not in SoV numerator/denominator as a fake series
- [ ] Bookmark vs stall is a theme field and/or `intent_mode` cut, not mixed into one blob
- [ ] Hypothesis flag set on correlation-looking clusters

---

## Pass bar

Every published theme has ≥1 document + verbatim quote. Impact formula matches Architecture §8.6 (spot-check 3 themes by hand). `unavailable_sources` populated when a source is down. Noise is not in the ranked opportunity list. Bookmark vs stall visible on themes and/or `intent_mode` cut.

**Dev cluster on Play-only is allowed** for pipeline debug; **gate for Phase 5 (API)** prefers a cluster run **after** Phase 3 multi-source. If Play-only, label the run `cluster_runs.corpus=play_only` and do not treat ranks as product-ready.

---

## How to run

```powershell
python -m src.cli cluster
pytest tests/test_eval_phase4.py
```

Save cluster_run id, top theme table, and three hand-calculated impact rows under `evals/runs/4/<date>/`. Record `GROQ_MODEL_LIGHT` used for labels.

---

## Checks

| ID | Kind | Pri | Check | Pass if |
| --- | --- | --- | --- | --- |
| **EV-4-01** | live | Must | Cluster CLI on `extraction_status=ok` | `cluster_runs` row; algorithm + params stored |
| **EV-4-02** | auto | Must | `not_applicable` / empty friction+intent | Excluded from cluster members |
| **EV-4-03** | auto | Must | Noise points | Not in `themes` opportunity ranking |
| **EV-4-04** | auto | Must | Tiny-corpus fixture (n small, all noise) | 0–few themes; no forced 10 labels |
| **EV-4-05** | live | Must | Groq labels | Each theme has name, description, `hypothesis_flag`, `bookmark_vs_stall` |
| **EV-4-06** | auto | Must | Theme → documents → quote | `NOT EXISTS` theme without evidence |
| **EV-4-07** | auto | Must | `theme_metrics` global snapshot | mention_count, share_of_voice, source_diversity, data_confidence, impact_score, unavailable_sources |
| **EV-4-08** | auto | Must | SoV = mention_count / eligible_corpus_count | SQL identity (±1e-6) |
| **EV-4-09** | auto | Must | Simulated missing Play Store | Play in `unavailable_sources`; SoV denominator does not invent Play volume |
| **EV-4-10** | auto | Must | Impact = SoV × sentiment_severity × segment_breadth × data_confidence | Hand calc 3 rows |
| **EV-4-11** | auto | Must | Monetary/coupon theme fixture | Still present (not filtered) |
| **EV-4-12** | auto | Must | `unknown` segment in slice metrics | Column/value present |
| **EV-4-13** | auto | Must | Failed extractions | Absent from mention_count |
| **EV-4-14** | auto | Must | Chunk-level embeddings | mention_count uses **distinct document_id** |
| **EV-4-15** | auto | Should | Trend with 1 bucket | `trend_direction` null/unknown, not `flat` |
| **EV-4-16** | auto | Should | Recluster twice on same data | `theme_id` stable via centroid match **or** documented new run + “refreshed on” |
| **EV-4-17** | auto | Should | Incremental kNN | `assignment_method` set |
| **EV-4-18** | manual | Should | Top 5 names | Specific opportunity areas, not “Customer issues” |
| **EV-4-19** | manual | Must | Causal-sounding clusters | `hypothesis_flag=true` where no funnel proof |

Also run **EV-X-03**.

---

## Confidence bands (unit)

| ID | Input | Expected policy (for Phase 5 Copilot API) |
| --- | --- | --- |
| **EV-4-20** | confidence 0.60 | answer |
| **EV-4-21** | 0.35 | caveat |
| **EV-4-22** | 0.34 | decline quantified |

---

## Fail immediately

- Ranked theme with no quotes
- Interpolated missing source
- Forced k-means “10 opportunities” on noise
- Bookmark mixed into stall-only theme with no field

---

## Edge cases

EC-CL-01–11, EC-Q-01–10, EC-G-01, EC-G-07, EC-G-08

---

## Out of scope for this gate

Dashboard / Copilot UI (Phase 6). N-grams job and remaining metric endpoints (Phase 5). Copilot API (Phase 5).
