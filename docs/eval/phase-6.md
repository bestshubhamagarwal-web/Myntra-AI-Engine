# Phase 6 eval — Product frontend (Next.js)

**Plan:** [ImplementationPlan.md](../ImplementationPlan.md) Phase 6  
**Index:** [eval.md](../eval.md)  
**Edge cases:** [edge-case.md](../edge-case.md)

**Layer:** Frontend  
**Depends on:** Phase 5 OpenAPI + contract tests **pass**. Do not start this phase early.  
**Plan goal:** A **single, high-quality research product** for PM / Insights: all Architecture §12 views plus Copilot chat, with the evidence loop **theme stat → quotes → source URL** as the primary interaction.  
**Eval question:** Can a PM use the app without a walkthrough, and do on-screen numbers **equal** the Query API for the same filters?

---

## Plan exit criteria (must all be true)

- [ ] PM can open the app, see corpus + ranked themes, drill to quotes and the source URL
- [ ] Every Architecture §12 view exists and uses the global filters
- [ ] Dashboard numbers match the Query API (and therefore SQL) for the same filters
- [ ] No theme without a drill-down path
- [ ] Copilot citations open the evidence drawer; Copilot counts match the API for the same slice
- [ ] Quality bar in the Implementation Plan is met (shell, tokens, empty/unavailable, URL filters, no Streamlit)

---

## Pass bar

Quality bar in Implementation Plan Phase 6 (shell, tokens, URL filters, empty/unavailable, evidence drawer). View checklist complete. Copilot citation chips open the same drawer. No Streamlit. No client-side SoV math.

This phase is not “charts exist.” It is **usable by a PM without a walkthrough**.

---

## How to run

```powershell
python -m src.cli serve
cd web
copy .env.example .env.local
npm install
npm run dev
pytest tests/test_eval_phase6.py
```

Open [http://localhost:3000](http://localhost:3000). Manual QA: three themes, SoV matches API; Copilot count matches theme card for the same filters; unavailable Play Store visible on overview **and** Copilot.

Save screenshots (overview, theme explorer, drawer, Copilot) under `evals/runs/6/<date>/`.

---

## View checklist (Must)

| ID | View | Pass if |
| --- | --- | --- |
| **EV-6-01** | Corpus overview | Counts, histogram, last ingest, unavailable badges |
| **EV-6-02** | Category breakdown | Volume + sentiment by `product_category` |
| **EV-6-03** | Theme explorer | Rank, SoV, impact, confidence; drill to drawer |
| **EV-6-04** | Word/phrase frequency | Table + cloud; **filter required** for cloud |
| **EV-6-05** | Sentiment trend | Overall + per theme/category |
| **EV-6-06** | Segment comparison | Heatmap includes **unknown** |
| **EV-6-07** | Source/platform breakdown | Theme mix by `source_type` |
| **EV-6-08** | Raw evidence | Search/filter; scrubbed CSV |
| **EV-6-09** | Automated reporting | Artifact list + download |
| **EV-6-10** | Copilot chat | Same app; citations + confidence |

---

## Other checks

| ID | Kind | Pri | Check | Pass if |
| --- | --- | --- | --- | --- |
| **EV-6-11** | manual | Must | UI: theme → drawer → external URL | 3 themes; links open or “link unavailable” |
| **EV-6-12** | auto | Must | No metric math in `web/` (grep SoV/impact compute) | Only API fields rendered |
| **EV-6-13** | manual | Must | Global filters in URL; all views agree | Change filter; overview + evidence match |
| **EV-6-14** | manual | Must | Copilot chip → same `document_id` as evidence | Drawer opens |
| **EV-6-15** | manual | Must | Copilot count = theme card for same slice | Exact |
| **EV-6-16** | manual | Must | Play ingest failed fixture | Unavailable on overview **and** Copilot chrome |
| **EV-6-17** | manual | Must | Quality bar: shell, tokens, skeletons, ~1280px layout | Met |
| **EV-6-18** | manual | Should | Sparkline with 1 point | No fake up-arrow |
| **EV-6-19** | auto | Must | Not Streamlit / not Metabase app | `web/` is Next.js |

Also run **EV-X-03**.

---

## Quality bar reminder (Must)

From Implementation Plan Phase 6:

- Persistent sidebar (Overview, Themes, Evidence, Categories, Trends, Segments, Sources, Phrases, Reports, Copilot)
- Global filters; **URL query string is source of truth**
- Theme explorer is the hero; click → evidence drawer (not a dead-end modal)
- Distinct empty / error / unavailable states
- Copilot in the same app; citation chips open the **same** drawer
- Desktop-first (~1280px+); usable ~768px; no broken table overflow
- Skeletons on first load; no layout jump when filters apply
- Denominator label on SoV; `unknown` segment visible; bookmark vs stall not merged

---

## Fail immediately

- UI `reduce`/`value_counts` ≠ API
- Theme without drill-down
- Failed source shown as this week’s volume with no label
- Streamlit (or equivalent) shipped as the product UI
- Copilot citation not in evidence table

---

## Edge cases

EC-UI-01–12, EC-Q-13, EC-G-02, EC-X-01–03, EC-CO-18

---

## Out of scope for this gate

Consumer-grade marketing site, Myntra shopper UI, SSO beyond shared secret. Re-implementing metric formulas in the client. Email of reports. Fine-tuning; full Q1–Q9 gold harness (Phase 7).
