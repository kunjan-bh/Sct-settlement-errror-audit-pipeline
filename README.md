# SmartQR Failed Settlement Operations — Layer 1, 2, 3 & 4

This operations tool processes SmartQR failed settlement Excel reports: upload → auto-classify every row (whose fault: SCT / Aggregator / Bank) and (which partner: aggregator-grouped cooperative or direct bank/wallet) → dashboard → operations team marks each issue Pending/In Progress/Solved with a comment → finish batch → generate a 3-sheet Excel report.

## Completed Features

- **Robust Ingestion Pipeline**:
  - Features dynamic header detection scanning the first 15 rows for the `"MID"` cell.
  - Strict validation ensures required columns (`MID`, `Remarks 1`) exist, returning a 422 with a descriptive error message on failure.
- **Dynamic Rule Engine & Partner Resolver**:
  - Classification rule matching (contains/starts_with/exact + priority) is fully DB-editable.
  - Partner resolution is based on exact 3-char member-code lookups from MIDs (`mid[:3]`), utilizing automatic left-zero-padding.
- **Flat Issue Table**:
  - Replaced the click-to-expand accordions on the dashboard with a flat, always-visible table (`IssueTable`).
  - Ops status dropdowns (`Pending`, `In Progress`, `Solved`), MIDs lists, and comment textareas are directly visible and auto-save on change/blur.
- **Two views per batch** (tabs on the dashboard):
  - **Solve Batch** — the ops workflow: per-partner issue tables, status dropdowns, comments, MID overrides.
  - **Error Classification** — what actually broke and how often, per aggregator / bank-wallet / SCT, with ranked charts, a per-entity category breakdown, and a "Download Raw Excel" button (numbers only, no charts). Counts every non-success transaction *regardless of ops status*, so excluded/solved issues still count — an aggregator's real error rate shouldn't shrink because someone triaged it.
- **Excel Report Generator**:
  - Produces structured Excel workbooks using `openpyxl` with:
    - **Sheet 1 "Overview"**: Batch metadata, partner summary table (aggregators + banks/wallets), SCT summary table.
    - **Sheet 2 "Processed Records"**: Preserves all original columns and appends aggregator name, issue category, solved status, remarks, batch, and processing timestamp.
    - **Sheet 3 "Issue Summary"**: Grouped issue summaries with txn counts and comments for both Aggregator and SCT sections.
    - **Sheet 4 "Lo Status"**: Rows that arrived as "In progress", plus anything ops manually moved to Lo Progress.
    - **Sheet 5 "Error Classify"**: The same table as the Error Classification download, appended as the last sheet.
- **Retry matching** ([retry_matching.py](backend/app/services/retry_matching.py)): a settlement that failed and was later reprocessed successfully is not an outstanding error. The reprocess is a *new row* with a fresh STAN/CRRN — the original failed row is never updated — so failures are matched to a later success by `(MID, txn amount, beneficiary account)`, 1:1, within 3 days.
  - The covering success must be settled **On Call** or **System Default** (the two reprocessing modes). A later *Real Time* success is a fresh customer payment that happens to share an amount, not a retry.
  - Matching runs **across batches**: a failure uploaded on the 11th is commonly settled on the 12th, which is a separate upload. Re-reconciliation happens after every ingest over a window around the new batch.
  - Rows are deduped by acquirer identity (STAN/CRRN/CR Transaction ID) first, because the daily exports overlap — the 12 Aug file covers 11 Aug 10:00 → 12 Aug 21:59, so consecutive uploads re-report the same transactions.
  - Matched rows are **flagged, never deleted**: excluded from issue tables, charts and the Error Classify sheet, counted as `retry_resolved`, listed in full on the Error Classification tab, and marked `Retry Settled = Yes` in the report's Processed Records sheet.
- **Transaction status normalization** ([status_utils.py](backend/app/services/status_utils.py)): the source files spell the third bucket `"In progress"` while ops calls it "LO". One normalizer maps the variants so ingest, dashboard and report always agree — that key is part of the `IssueStatus` identity, so a mismatch silently loses rows.
- **Upload & Lifecycles**:
  - GLowing drag-and-drop file uploader.
  - Reopen previous batches or complete current open batches (completing locks batch edits and starts downloading the report).

## Run the backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python seed_rules.py            # loads classification rules + bank/wallet entities
python run.py                   # starts on http://localhost:5000
```

## Run the frontend

```bash
cd frontend
npm install
npm run dev                     # starts dev server on http://localhost:5173
```

To build for production on older Node versions (e.g. Node 18):
```bash
NODE_OPTIONS="-r ./node-compat.cjs" npm run build
```

## Known gaps (next layers)

- No Issue Classification CRUD page (rules are DB-editable via models/seed file, but no frontend admin UI yet)
- Batches page has no search/filter (by date, aggregator, issue) yet — just a plain list
- No charts on the Solve tab (the Error Classification tab has them)
- No auth


