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
from app.services.status_utils import normalize_txn_status
from app.services.retry_matching import parse_amount, parse_txn_datetime, reconcile_retries

# The same export also ships as a raw snake_case dump (header on row 1, no
# metadata lines). Rather than teach every downstream consumer two vocabularies,
# the reader renames those columns to the Title Case names the pipeline already
# speaks, so ingest, classification, the dashboard and the Settlement Type
# report all keep working unchanged.
#
# ref_id has no Title Case counterpart because the older export simply does not
# carry it -- it is the switch transaction id, and it is what lets a settlement
# be traced to its transaction (see services/transaction_reconcile.py).
SNAKE_COLUMN_MAP = {
    "merchant_code": "MID",
    "merchant_name": "Merchant Name",
    "remarks": "Remarks 1",
    "remark_two": "Remarks 2",
    "status": "Status",
    "status_code": "Status Code",
    "amount": "Txn Amount",
    "service_charge": "Service Charge",
    "settlement_frequency": "Settled By",
    "stan": "STAN",
    "crrn": "CRRN",
    "date_time": "Transaction Date",
    "acquirer_name": "Acquirer Name",
    "bank_name_or_wallet_name": "Bank/Wallet Name",
    "creditor_account": "Bank Account/Wallet ID",
    "creditor_name": "Account Name",
    "partner_ref_id": "CR Transaction ID",
    "ref_id": "Ref ID",
}

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


# Either spelling of the merchant-id column marks the header row.
_HEADER_MARKERS = ("MID", "merchant_code")


def _find_header_index(file_path: str, max_rows: int = 15) -> int:
    """
    Scans the first `max_rows` rows of the Excel file for a row containing a
    cell naming the merchant-id column ('MID', or 'merchant_code' in the raw
    dump). Returns the 0-based row index.
    """
    preview = pd.read_excel(file_path, header=None, nrows=max_rows)
    for idx, row in preview.iterrows():
        for val in row.values:
            if pd.notna(val) and str(val).strip() in _HEADER_MARKERS:
                return int(idx)
    markers = " or ".join(f"'{m}'" for m in _HEADER_MARKERS)
    raise ValueError(
        f"Could not find a header row containing {markers} within the first {max_rows} rows."
    )


def read_settlement_dataframe(file_path: str) -> pd.DataFrame:
    """
    Shared by ingest_excel (persisted batches) and the ad-hoc, non-persisted
    Settlement Type analysis (services/adhoc_settlement_service.py) -- same
    file shape, same header-detection quirks, so the parsing rules can never
    drift between the two call sites.
    """
    header_idx = _find_header_index(file_path)

    # dtype={"MID": str} is critical: without it, pandas reads MID as a
    # number and silently strips leading zeros (096... becomes 96...),
    # which corrupts every 3-char member code that starts with 0. This bit
    # us on the very first real-file test run -- 0 rows resolved to a
    # partner because every leading-zero MID was shifted by one digit.
    df = pd.read_excel(
        file_path, header=header_idx, dtype={"MID": str, "merchant_code": str}
    )
    df.columns = [str(c).strip() if pd.notna(c) else c for c in df.columns]
    df = df.dropna(how="all")  # drop fully-blank trailing rows, if any
    df = _normalize_settlement_columns(df)

    required_cols = ["MID", "Remarks 1"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Expected required columns MID and Remarks 1, found: {list(df.columns)}")

    return df


def _normalize_settlement_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename raw-dump columns to the pipeline's Title Case names, and normalize
    the values that changed spelling with them.

    A rename never overwrites a column the file already has under the target
    name, so a file carrying both vocabularies keeps the canonical one.
    """
    existing = set(df.columns)
    renames = {
        snake: title
        for snake, title in SNAKE_COLUMN_MAP.items()
        if snake in existing and title not in existing
    }
    if renames:
        df = df.rename(columns=renames)

    # REAL_TIME -> "Real Time" etc. The dump screams its enum; the rest of the
    # codebase (and the report's dropdown) uses the display spelling.
    if "Settled By" in df.columns:
        df["Settled By"] = df["Settled By"].map(_normalize_settled_by)

    return df


def _normalize_settled_by(value):
    """'REAL_TIME'/'real time' -> 'Real Time'. Unrecognized values pass through
    untouched so a new settlement method is visible rather than silently
    flattened."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    text = str(value).strip()
    if not text:
        return value
    key = text.replace("-", " ").replace("_", " ").lower()
    return {
        "real time": "Real Time",
        "system default": "System Default",
        "on call": "On Call",
    }.get(key, text)


def ingest_excel(file_path: str, batch_date: date | None = None) -> Batch:
    """
    Reads the Excel at file_path, creates a Batch, classifies every row,
    and persists Transaction + IssueStatus rows. Returns the Batch.

    batch_date defaults to today (the date you're doing the upload/ops
    work), NOT a date parsed out of the filename -- your report filename
    has the settlement date, but the batch is about when YOU processed it,
    which is what "Batch_YYYY_MM_DD" naming is for in your spec.
    """
    df = read_settlement_dataframe(file_path)

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
        txn_status = normalize_txn_status(status_val)

        # Promoted out of extra_data because retry matching joins on them
        # across batches -- see services/retry_matching.py.
        beneficiary = _clean(row.get("Bank Account/Wallet ID"))

        txn = Transaction(
            batch_id=batch.id,
            mid=mid,
            merchant_name=_clean(row.get("Merchant Name")),
            status=status_val,
            status_code=_clean(row.get("Status Code")),
            remark=remark,
            extra_data=extra_data,
            txn_amount=parse_amount(row.get("Txn Amount")),
            settled_by=_clean(row.get("Settled By")),
            beneficiary_id=beneficiary,
            txn_datetime=parse_txn_datetime(row.get("Transaction Date")),
            retry_resolved=False,
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

    # Must run after the commit (it re-queries to get assigned ids). Scoped to
    # a window around this batch, so today's reprocessed successes can also
    # resolve failures from a file uploaded yesterday.
    reconcile_retries(around_batch_id=batch.id)

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
