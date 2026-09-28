# Estimator AI

Window-shade estimating for Direct Shades & Blinds. Upload a project's Excel
workbooks (or drawings) and get a shade count, a price, and an audit trail that cites
the sheet and row behind every number.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env.local          # add GEMINI_API_KEY or OPENAI_API_KEY
python main.py                      # http://localhost:3000
```

Health check: `GET /api/health` reports the provider, the JSON parser, the pipeline
graph, and the pricing policy in effect.

## What it reads

| Workbook | Role |
| --- | --- |
| Window Matrix | **Shade count authority** — the TOTAL row's TOTAL SHADES column. Its Blind QTY UNITS sheets supply per-opening W×H, system, and room. |
| Material Summary | Fabric and system selection per tab (UNITS, BLK, COM-*). |
| Bid Summary | **Reference pricing only.** Dollars are used to anchor and cross-check the total; they never change the count. |

Upload all three together. PDFs, images, CSV, Word, and PowerPoint also work.

## Two routes

The server picks a route per upload and reports it as `projectSummary.route`.

**Workbook fast path** — an Excel-only upload whose Window Matrix has a TOTAL row.
Vision and the retrieval index are skipped because the workbooks already state the
count and every dimension. Take-off and pricing run as deterministic Python, so the
same files always produce the same estimate; the model is used only for the client
email prose, and a template covers it if the model fails.

**Full analysis** — anything with a drawing, or without a Matrix TOTAL row. Vision
reads every page, the document is chunked and embedded, and the model performs the
take-off. Workbook rows still win over model rows wherever they overlap.

## Reading the result

Start with `projectSummary`. `matchStatus` is the one field worth checking first:

- `match` — take-off and schedule both equal the Matrix TOTAL.
- `matrix_only` — the total follows the Matrix TOTAL; some markings are rolled up
  rather than itemised.
- `mismatch` — the take-off disagrees with the Matrix. The Matrix wins; reconcile
  before sending.
- `no_matrix` — no TOTAL row was found, so the count comes from the take-off itself.

Then `estimationAudit` shows the count steps, the price steps, and one row per line
with its count basis, size math, price formula, and source cell reference.

## Pricing

With a Bid Summary, the project total is the bid grand total and per-line catalogue
prices are calibrated to it; the calibration factor appears in every line formula.
Without one, prices are `catalogue list × size factor + labor`, then overhead and
profit. Tune the knobs in `.env.local` (see `.env.example`).

## Layout

```
main.py                     FastAPI app, upload handling, response assembly
pipeline/graph.py           LangGraph topology and the fast-path router
pipeline/nodes.py           One node per agent, SSE progress
agents/                     Vision, context, take-off, estimation, validation
domain/direct_shades_workbooks.py  Excel parsing (matrix, Blind QTY, bid, material)
domain/workbook_takeoff.py  Workbook rows → take-off, authority rules
domain/pricing.py           Deterministic catalogue + bid-anchored pricing
pipeline/project_summary.py Count/price reconciliation object
pipeline/estimation_audit.py Formula-and-reference audit
public/                     Single-page UI
```

## Railway

Connect this repo in Railway. The start command is in the `Procfile` and listens on Railway’s `PORT`.

Set these variables on the service before the first request:

- `GEMINI_API_KEY` — required
- `LLM_PROVIDER` = `gemini`
- `GEMINI_MODEL` = `gemini-2.5-flash`

Do not upload `.env` or `.env.local`. Railway injects the variables itself.

Health check: `GET /api/health`.

## Tests

```bash
python -m unittest discover -s tests
```

The suite builds synthetic Direct Shades workbooks in memory and never calls a model,
so it runs in well under a second.
