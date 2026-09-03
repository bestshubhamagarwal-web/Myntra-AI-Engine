# Phase 3 eval — Multi-source ingest (4–5 types)

**Plan:** [ImplementationPlan.md](../ImplementationPlan.md) Phase 3  
**Index:** [eval.md](../eval.md)  
**Edge cases:** [edge-case.md](../edge-case.md)

**Layer:** Backend  
**Depends on:** Phase 2 pass (same normalize / extract / embed path)  
**Plan goal:** Meet the ingest deliverable. Same envelope, same normalize/extract path (Architecture §22.3, §5 source list).  
**Eval question:** Are four Myntra-relevant sources live, and are Instagram/Facebook (and any skipped fifth) **unavailable** rather than faked?

---

## Plan exit criteria (must all be true)

- [ ] **≥ 4 source_types** with non-zero `normalized_documents`
- [ ] Unique constraint holds across all sources
- [ ] `source_status` lists live vs unavailable (no imputed volumes)
- [ ] Sample includes wishlist/sizing/returns-like language (qualitative check)

---

## Pass bar

`COUNT(DISTINCT source_type)` on `normalized_documents` **≥ 4**. Unique constraint holds in a cross-source id collision test. `source_status` lists live vs unavailable. Qualitative sample contains wishlist/sizing/returns language.

---

## How to run

Ingest each live connector (Play already done), then normalize + extract + embed **new docs only**. Confirm `source_status`.

```powershell
pytest tests/test_eval_phase3.py
python -m src.cli dump   # optional scrubbed dump under ./data/review/phase3/
```

Save `source_status` output and a 15-row mixed sample under `evals/runs/3/<date>/`.

---

## Checks

| ID | Kind | Pri | Check | Pass if |
| --- | --- | --- | --- | --- |
| **EV-3-01** | live | Must | Play + App Store + Reddit + YouTube (or documented substitute) | ≥4 types with count > 0 |
| **EV-3-02** | auto | Must | Insert `play_store`/`123` and `reddit`/`123` | Both rows exist |
| **EV-3-03** | auto | Must | `source_status` view/table | Every Architecture source type is `live`, `failed`, or `unavailable` — never missing |
| **EV-3-04** | auto | Must | Instagram + Facebook | `unavailable` (unless a real public connector shipped) |
| **EV-3-05** | auto | Must | No connector targeting AJIO/Nykaa/Flipkart/Meesho **app pages** | Grep/allowlist of apps = Myntra only |
| **EV-3-06** | live | Must | Reddit: Myntra-filtered | Spot 10 posts: Myntra shopping-relevant or `reject` |
| **EV-3-07** | live | Must | YouTube: `parent_context` has video title | Non-null on youtube rows |
| **EV-3-08** | auto | Must | YouTube off-topic fixture | `reject` before normalize |
| **EV-3-09** | auto | Must | `[deleted]` / empty reddit body | Not clustered later; reject reason `removed` |
| **EV-3-10** | live | Must | New docs only: extract+embed incremental | Unchanged hashes skipped |
| **EV-3-11** | manual | Must | 15-row mixed-source sample | Wishlist or sizing or returns language appears at least a few times (not all app-crash) |
| **EV-3-12** | live | Should | Fifth source **or** explicit unavailable in `source_status` | Honest gap |
| **EV-3-13** | auto | Should | Per-source `ingest_runs` | Failures don’t mark other sources failed |

Also run **EV-X-03**.

---

## Fail immediately

- 3 sources only with a comment “YouTube later” and no unavailable flag
- Competitor Play Store ingest
- Collided ids across sources

---

## Edge cases

EC-IN-08–11, EC-IN-13, EC-IN-15, EC-G-09, EC-G-01

---

## Out of scope for this gate

Instagram / Facebook groups (remain unavailable). Production-grade scraper HA. n8n can wait until Phase 7 if cron is enough.
