# web/ — Discovery product UI (Phase 6)

Next.js App Router app for PM / Insights. All numbers are **read from the Phase 5 Query API**. The client does not compute share of voice, impact, or confidence.

## Run

From the repo root, start the API:

```powershell
python -m src.cli serve
```

In `web/`:

```powershell
copy .env.example .env.local
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Default API proxy target is `http://127.0.0.1:8000`. Set `API_BASE_URL` and `API_SHARED_SECRET` in `web/.env.local` to match the backend. If the API requires a secret and the Next.js env is empty, the unlock screen asks for the same value and stores it in session storage.

## Views

| Route | Source |
| --- | --- |
| `/overview` | `GET /metrics/overview` + top themes |
| `/themes` | `GET /metrics/themes` — ranked cards, evidence drawer |
| `/evidence` | `GET /evidence` — table + scrubbed CSV |
| `/categories` | `GET /metrics/segments?dimension=product_category` |
| `/trends` | `GET /metrics/trends` |
| `/segments` | `GET /metrics/segments` (heatmap includes `unknown`) |
| `/sources` | overview `counts_by_source` + source mix |
| `/phrases` | `GET /metrics/ngrams` — cloud only when `cloud_eligible` |
| `/reports` | `GET /reports` + PDF download |
| `/copilot` | `POST /copilot/query` — citation chips open the drawer |

Global filters live in the URL (`date_from`, `date_to`, `source_type`, `product_category`, `intent_mode`, `theme_id`, …) so every view shares the same slice.

Visual system follows `docs/stitch_discovery_wishlist_analytics/.../discovery/DESIGN.md` (Myntra rose, Inter, 240px sidebar, 480px evidence drawer).
