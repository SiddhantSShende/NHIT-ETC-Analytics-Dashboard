# NHIT — ETC Analytics Dashboard

Plaza-level analytics over IHMCL VC-Wise monthly toll reports. The dashboard is a static frontend that reads pre-built JSON files.

## Architecture

```
downloads/{vc_monthly,ETC_Monthly_Data,Monthly_Annual_Pass_Report}/*.pdf
                              │  scripts/build_json_export.py  (one-time, ~7 min)
                              ▼
        downloads/json/_index.json
        downloads/json/_taxonomy.json   ◀── built from data/Project details.xlsx
        downloads/json/monthly/<YYYY-MM>.json
        downloads/json/plazas/<slug>.json
                              │
                              ▼
        index.html + app.js + data-layer.js  (Chart.js dashboard)
                              │  fetch() + Cache Storage API
                              ▼
                          Browser cache

        server.py  ── /api/health (Flask, serves static files)
```

The browser fetches JSON files directly from `downloads/json/` and aggregates in JS. The Flask server is only needed to serve the static files locally; any static-file host (Vercel, S3, GitHub Pages, etc.) works equally well.

## Files

| File | Purpose |
| ---- | ------- |
| `constants.py`            | Canonical NHIT plaza list + month tables. |
| `parser.py`               | PDF table extraction (vc_monthly + ETC_Monthly_Data layout). Pure functions. |
| `ingestor.py`             | Plaza-name normalisation + alias map. |
| `taxonomy.py`             | Reads `data/Project details.xlsx`, fuzzy-matches Excel plaza names to canonical names. |
| `scripts/build_json_export.py` | Build script. Reads every PDF, writes `downloads/json/{_index,_taxonomy,monthly/*,plazas/*}.json`. Validates every plaza's per-category sums against the PDF's `TOTAL_CNT`/`TOTAL_AMT` cells. |
| `data-layer.js`           | Browser-side data layer: fetches `downloads/json/*`, caches via Cache Storage API + in-memory map, ports `analytics.py`'s aggregation logic to JS. Exposes `window.DataLayer`. |
| `static_loader.py`        | Loads `downloads/json/*` into a SNAPSHOT-shaped dict. |
| `analytics.py`            | Aggregation helpers (`aggregate_plazas_for_month`, `aggregate_plazas_for_range`). |
| `server.py`               | Flask app. One route: `/api/health`. Serves static files. |
| `index.html`, `app.js`, `styles.css` | Frontend. |
| `data/Project details.xlsx` | SPV / Round / Project taxonomy source. |
| `downloads/json/`         | Generated. Per-month + per-plaza JSON files served as static assets. |

## Usage

```bash
# 1. Drop monthly PDFs into:
#      downloads/vc_monthly/                       (e.g. Jan-2025.pdf)
#      downloads/Monthly_Annual_Pass_Report/<year>/ (annual-pass PDFs)
#      downloads/ETC_Monthly_Data/<year>/          (alternate VC-wise PDFs)

# 2. Rebuild the JSON files:
python scripts/build_json_export.py
#    -> writes downloads/json/{_index,_taxonomy,monthly/*,plazas/*}.json

# 3. Start a local server to view the dashboard:
python server.py
#    -> http://localhost:5051
```

For pure-static hosting (Vercel etc.) the dashboard works with just the static files — `server.py` is only needed locally.

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET`  | `/api/health` | Liveness + plaza/month counts + which directory is the data source. |

All other historical `/api/*` endpoints (`/api/meta`, `/api/aggregate`, …) have been retired — the frontend reads JSON files directly.

## Data legitimacy

`scripts/build_json_export.py` validates each plaza's per-category sums against the PDF's `TOTAL_CNT`/`TOTAL_AMT` columns (0.5 % tolerance) and refuses to silently emit data that disagrees. The current pipeline reports **0 validation warnings** across all 15 monthly PDFs.

## Adding a new month

1. Drop the new PDFs into the correct `downloads/<source>/` subfolder using the same naming convention as existing files (`Jun-2026.pdf`, `Sep-2026-ETC-Data.pdf`, `Sep-2026-Annual-Pass-Data.pdf`).
2. Run `python scripts/build_json_export.py`.
3. Hard-refresh the browser (or the Cache Storage API will keep serving the previous month's data).
