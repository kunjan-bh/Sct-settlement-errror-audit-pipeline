"""
Ingestion pipeline: uploaded Excel -> Batch + Transaction rows + IssueStatus
rows, ready for the dashboard to query.

Built against your real file (Merchant_Settlement_Report_2026-06-26.xlsx):
  - Row 1-2 are export metadata (title, exported date/time) -- not data.
  - Row 3 is the real header.
  - Data starts row 4.
  - Columns: SN, Transaction Date, Acquirer Name, MID, Merchant Name,
    Txn Amount, Service Charge, Settled By, STAN, CRRN, CR Transaction ID,
    Bank/Wallet Name, Bank Account/Wallet ID, Account Name, Remarks 1,
    Remarks 2, Status Code, Status.

Only MID and Remarks 1 are structurally required (they drive partner
resolution and classification). Everything else is preserved -- known
fields map to Transaction columns, everything else goes into extra_data
verbatim, per your "do not delete original information" requirement.
"""
from datetime import datetime, date

import pandas as pd

from app.extensions import db
from app.models.batch import Batch
from app.models.transaction import Transaction
from app.models.issue_status import IssueStatus
from app.services.classification_service import RuleEngine, PartnerResolver

KNOWN_COLUMN_MAP = {
    "MID": "mid",
    "Merchant Name": "merchant_name",
    "Remarks 1": "remark",
    "Status Code": "status_code",
    "Status": "status",
}


def _next_batch_name(base_date: date) -> str:
    """
    Batch_YYYY_MM_DD, with _2/_3... appended if a batch already exists for
    that date -- so uploading two files the same day doesn't collide.
    """
    base = f"Batch_{base_date.strftime('%Y_%m_%d')}"
    if not Batch.query.filter_by(name=base).first():
        return base

    n = 2
    while Batch.query.filter_by(name=f"{base}_{n}").first():
        n += 1
    return f"{base}_{n}"


def _find_header_index(file_path: str, max_rows: int = 15) -> int:
    """
    Scans the first `max_rows` rows of the Excel file for a row containing a cell
    whose value equals 'MID'. Returns the 0-based row index.
    Raises ValueError if 'MID' cell is not found in the first `max_rows` rows.
    """
    preview = pd.read_excel(file_path, header=None, nrows=max_rows)
    for idx, row in preview.iterrows():
        for val in row.values:
            if pd.notna(val) and str(val).strip() == "MID":
                return int(idx)
    raise ValueError(f"Could not find header row containing 'MID' column within the first {max_rows} rows.")


def ingest_excel(file_path: str, batch_date: date | None = None) -> Batch:
    """
    Reads the Excel at file_path, creates a Batch, classifies every row,
    and persists Transaction + IssueStatus rows. Returns the Batch.

    batch_date defaults to today (the date you're doing the upload/ops
    work), NOT a date parsed out of the filename -- your report filename
    has the settlement date, but the batch is about when YOU processed it,
    which is what "Batch_YYYY_MM_DD" naming is for in your spec.
    """
    header_idx = _find_header_index(file_path)

    # dtype={"MID": str} is critical: without it, pandas reads MID as a
    # number and silently strips leading zeros (096... becomes 96...),
    # which corrupts every 3-char member code that starts with 0. This bit
    # us on the very first real-file test run -- 0 rows resolved to a
    # partner because every leading-zero MID was shifted by one digit.
    df = pd.read_excel(file_path, header=header_idx, dtype={"MID": str})
    df.columns = [str(c).strip() if pd.notna(c) else c for c in df.columns]
    df = df.dropna(how="all")  # drop fully-blank trailing rows, if any

    required_cols = ["MID", "Remarks 1"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Expected required columns MID and Remarks 1, found: {list(df.columns)}")

    batch_date = batch_date or date.today()
    batch = Batch(
        name=_next_batch_name(batch_date),
        status="open",
        input_file_path=file_path,
    )
    db.session.add(batch)
    db.session.flush()  # assigns batch.id without committing yet

    engine = RuleEngine.load()
    resolver = PartnerResolver.load()

    # Preserve all non-mapped original columns in extra_data
    known_headers = set(KNOWN_COLUMN_MAP.keys())
    extra_cols = [c for c in df.columns if c not in known_headers]

    # issue_key -> IssueStatus (get-or-create, deduped within this batch)
    issue_cache: dict[tuple, IssueStatus] = {}

    transactions = []
    for _, row in df.iterrows():
        mid = _clean(row.get("MID"))
        remark = _clean(row.get("Remarks 1"))

        result = engine.classify_row(remark)
        partner_name, bucket = resolver.resolve(mid)

        extra_data = {col: _json_safe(row.get(col)) for col in extra_cols}

        status_val = _clean(row.get("Status"))
        txn_status = (status_val.lower().replace(" ", "_") if status_val else "unknown")

        txn = Transaction(
            batch_id=batch.id,
            mid=mid,
            merchant_name=_clean(row.get("Merchant Name")),
            status=status_val,
            status_code=_clean(row.get("Status Code")),
            remark=remark,
            extra_data=extra_data,
            partner_name=partner_name,
            partner_type=bucket,
            error_side=result.side,
            error_category=result.category,
            matched_rule_id=result.matched_rule_id,
        )
        transactions.append(txn)

        # side "bank_wallet" from PartnerResolver vs error_side "bank" from
        # RuleEngine are different dimensions (see classification_rule.py
        # docstring) -- IssueStatus groups by error_side, since that's
        # "whose fault", which is what ops actually resolves.
        if txn_status != "success":
            issue_key = (batch.id, result.side, partner_name if result.side != "sct" else None, result.category, txn_status)
            if issue_key not in issue_cache:
                issue_cache[issue_key] = IssueStatus(
                    batch_id=batch.id,
                    side=result.side,
                    partner_name=issue_key[2],
                    category=result.category,
                    txn_status=txn_status,
                    status="pending",
                )

    db.session.bulk_save_objects(transactions)
    for issue in issue_cache.values():
        db.session.add(issue)

    db.session.commit()
    return batch


def _clean(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return str(value).strip()


def _json_safe(value):
    """pandas/numpy types (Timestamp, int64, nan) aren't JSON-serializable
    as-is -- convert to plain Python types before storing in the JSON
    column."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalar types (int64, float64...)
        return value.item()
    return value
