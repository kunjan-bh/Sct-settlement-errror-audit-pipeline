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
- **3-Sheet Excel Report Generator**:
  - Produces structured Excel workbooks using `openpyxl` with:
    - **Sheet 1 "Overview"**: Batch metadata, aggregator summary table, SCT summary table.
    - **Sheet 2 "Processed Records"**: Preserves all original columns and appends aggregator name, issue category, solved status, remarks, batch, and processing timestamp.
    - **Sheet 3 "Issue Summary"**: Grouped issue summaries with txn counts and comments for both Aggregator and SCT sections.
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
- No charts (pie/bar/line) yet
- No auth


