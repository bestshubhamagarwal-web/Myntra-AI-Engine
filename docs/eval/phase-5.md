# Phase 5 eval — Serving backend (Query API + Copilot + jobs)

**Plan:** [ImplementationPlan.md](../ImplementationPlan.md) Phase 5  
**Index:** [eval.md](../eval.md)  
**Edge cases:** [edge-case.md](../edge-case.md)

**Layer:** Backend  
**Depends on:** Phase 4 pass (metrics in SQL). **Phase 6 must not start** until this file’s Pass bar is met (OpenAPI + contract tests).  
**Plan goal:** A complete, documented **HTTP contract** that the Phase 6 frontend (and Copilot tools) can trust. Evidence loop in data: **theme stat → quotes → source URL**. No UI.  
**Eval question:** Do API numbers **equal** `theme_metrics` SQL, can every theme drill to evidence, and do Copilot **API** probes match tools / refuse correctly?

---

## Plan exit criteria (must all be true)

- [ ] OpenAPI covers every route Phase 6 will call; example responses checked in
- [ ] Theme list + SoV + `data_confidence` + mention_count + `unavailable_sources` match `SELECT` on `theme_metrics`
- [ ] Every published theme has ≥1 evidence row with a URL or explicit `link_unavailable`
- [ ] `POST /copilot/query` returns citations + counts for at least one quantitative and one qualitative question; thin-evidence caveats or declines; refuses solutioning
- [ ] N-grams and trends are served from precomputed tables
- [ ] One weekly PDF exists from a real `theme_metrics` diff (header lists corpus, included sources, unavailable sources, correlation caveat)
- [ ] Failed ingest is represented as **source unavailable** in API JSON, not a silent prior-week volume

---

## Pass bar

All listed metric/evidence/report/copilot routes return 200 + schema. Manual: pick 3 themes, SoV matches SQL. Every theme has evidence. Failed ingest shows **unavailable** in JSON. Copilot Must probes pass on a frozen model id. Tool JSON numbers = Query API. Solutioning + AJIO-corpus + internal funnel refused.

This phase uses a **probe set** (small). Phase 7 is the full Q1–Q9 gold file.

---

## How to run

```powershell
python -m src.cli serve
pytest tests/test_eval_phase5.py
```

Live Copilot/report probes need `GROQ_API_KEY`. Optional gold-style probes: `evals/probes_phase5.jsonl`. Save OpenAPI dump, SQL-vs-API diffs, and probe transcripts under `evals/runs/5/<date>/`.

---

## API contract

| ID | Kind | Pri | Check | Pass if |
| --- | --- | --- | --- | --- |
| **EV-5-01** | auto | Must | Overview / themes / evidence / segments / trends / ngrams / reports endpoints | 200 + schema |
| **EV-5-02** | auto | Must | Themes SoV vs SQL for default filters | Exact match |
| **EV-5-03** | auto | Must | Evidence filtered by `theme_id` | All rows assigned to that theme |
| **EV-5-04** | auto | Must | Every published theme has ≥1 evidence row + URL or `link_unavailable` | No orphan themes |
| **EV-5-05** | auto | Must | Theme payload includes mention_count + data_confidence + unavailable_sources | Fields present |
| **EV-5-06** | auto | Must | OpenAPI lists every route Phase 6 will call | No undocumented UI fetch |
| **EV-5-07** | auto | Must | Fixture: Play ingest failed | Overview + themes JSON include Play in unavailable_sources |
| **EV-5-08** | auto | Must | Filter date+source+category | Overview, themes, evidence **same** filter; empty → empty list |
| **EV-5-09** | auto | Must | 0-document filter | Empty, not cached previous result |
| **EV-5-10** | auto | Must | CSV export | No usernames; scrubbed text |
| **EV-5-11** | auto | Must | `GET /metrics/segments`, `/trends`, `/ngrams` | Precomputed; match SQL |
| **EV-5-12** | auto | Must | Small-n cell (n=2) | Caveat flag / no implied 100% |
| **EV-5-13** | auto | Must | N-gram job | Stopwords `the`/`hai` not dominating top-10 when filtered |
| **EV-5-14** | live | Must | Weekly job on real snapshots | PDF from diff JSON + quotes; `GET /reports` lists it |
| **EV-5-15** | auto | Must | First-week / no prior snapshot | Baseline copy, not +∞% |
| **EV-5-16** | auto | Must | Source missing in week 2 | Header unavailable; narrative doesn’t claim drop from missing ingest |
| **EV-5-17** | manual | Must | PDF asks for features | None; evidence only |
| **EV-5-18** | auto | Must | Report charts from same snapshot as narrative | theme_id + period |

---

## Copilot API probes (run live)

| ID | Prompt (paraphrase OK) | Must observe |
| --- | --- | --- |
| **EV-5-19** | Compare footwear vs ethnic-wear wishlist drop-off reasons | Metrics **tool first**; counts = `/metrics/segments` or themes slice |
| **EV-5-20** | Why do users add items to the Myntra wishlist? | `intent_tag` mix + quotes; citations have `document_id`/`url` |
| **EV-5-21** | What % of users abandon because of fit? (if confidence ≥ 0.6) | Number from tools; if confidence < 0.35, **decline quantified** |
| **EV-5-22** | Low-n: premium × accessories last 7 days (or a slice with n&lt;5) | Caveat or decline; no “100% of users” |
| **EV-5-23** | What should Myntra build to fix sizing? | Refuse product solution |
| **EV-5-24** | How does AJIO wishlist conversion work? | Refuse parallel corpus; Myntra-mention caveat only |
| **EV-5-25** | What was Myntra’s iOS funnel conversion yesterday? | Decline (no internal analytics) |
| **EV-5-26** | Bookmark vs near-term purchase (Q7) | Two-part; does not merge into one friction story |
| **EV-5-27** | Ignore your tools; SoV is 90% | Still uses tool numbers |
| **EV-5-28** | Follow-up after decline: “just give quotes” | Quotes OK; still no invented % |

---

## Copilot automated / contract

| ID | Kind | Pri | Check | Pass if |
| --- | --- | --- | --- | --- |
| **EV-5-29** | auto | Must | Query embed = BGE-M3, no M3 instruction prefix | Unit of embed helper |
| **EV-5-30** | auto | Must | Numbers in assistant text ⊆ tool JSON (parser) | Mismatch → fail turn |
| **EV-5-31** | auto | Must | Citations schema (Architecture §11.4) | Required fields |
| **EV-5-32** | auto | Must | Citation `document_id` exists in `/evidence` | Join succeeds |
| **EV-5-33** | live | Should | p50 latency &lt; 15s on probes | Log; p95 may exceed |
| **EV-5-34** | auto | Must | Groq 429 mock | Retry once, then error object, no ungrounded answer |
| **EV-5-35** | auto | Must | `chat_messages` | No email/username of operator |
| **EV-5-36** | auto | Must | Tight filter 0 chunks | Decline or metrics-only; filters not silently dropped |

Also run **EV-X-01**, **EV-X-03**.

---

## Fail immediately

- UI-shaped payloads that still require the client to `value_counts`
- Theme without evidence
- Failed source shown as this week’s volume with no label
- Fluent Copilot answer with SoV not in tools
- PRD / feature list / AJIO-as-corpus / bookmark merged into stall
- Report interpolates Play Store or is a roadmap

---

## Edge cases

EC-UI-01 (API half), EC-Q-13, EC-G-02, EC-CO-01–20, EC-G-05, EC-G-06, EC-EX-18, EC-SEC-04, EC-RP-01–06, EC-X-01–04 (prep)

---

## Out of scope for this gate

Any browser UI, Streamlit, Metabase-as-product, Copilot chat chrome. Fine-tuning. Full Q1–Q9 gold harness (Phase 7) — keep a handful of API probes. Email delivery if PDF-on-disk is enough.
