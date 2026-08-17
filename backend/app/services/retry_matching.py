"""
Retry matching: a settlement that failed and was later reprocessed successfully.

The pattern, straight from the real files (MID 054000000101524, 11 Aug 2026):

    10:08  Real Time  50,000 -> HAMRONEPAL 9849296394   Unable to process/failed.
    10:10  Real Time  50,000 -> HAMRONEPAL 9849296394   Unable to process/failed.
    14:51  On Call    50,000 -> HAMRONEPAL 9849296394   Success
    14:51  On Call    50,000 -> HAMRONEPAL 9849296394   Success

Someone reprocessed the two failures on call and they went through. The retry
is a NEW row with a fresh STAN / CRRN / CR Transaction ID -- the original
failed row is never updated -- so there is no id to join on. What ties them
together is the settlement obligation itself:

    (MID, txn amount, beneficiary account)

matched in time order, with the success strictly after the failure.

Two deliberate constraints, both measured against the 11-12 Aug file
(1,387 failures, 148 candidate matches):

1. **The covering success must be settled "On Call" or "System Default".**
   Those are the two reprocessing modes. A later *Real Time* success is a
   fresh customer payment that happens to share an amount and a wallet, not a
   reprocess of the failure -- treating it as one would silently erase real
   failures. This excludes 16 of the 148.

2. **One success covers at most one failure.** Two failures of 50,000 to the
   same wallet need two successes to both clear. Greedy earliest-first
   pairing; the leftovers stay failures.

Matching runs across batches, not just within one: a settlement that failed
on the 11th is commonly reprocessed on the 12th, which is a separate upload.
After any ingest we re-reconcile a window around the new batch, which can
resolve failures in batches that were uploaded days earlier.

Resolved rows are never deleted or edited -- only flagged, so every count
that hides them can also show them on demand.
"""
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

from app.extensions import db
from app.models.transaction import Transaction
from app.services.status_utils import normalize_txn_status

# Settlement modes that represent a reprocess of an earlier attempt.
RETRY_SETTLE_MODES = {"on call", "system default"}

# How far after a failure a reprocess is still considered the same
# obligation. Observed gaps in real data: median 6h, p90 21h, max 24h.
RETRY_WINDOW_DAYS = 3

# "Tue 11 Aug, 2026 10:21 AM" -- the format the settlement export uses.
_DATE_FORMATS = ("%a %d %b, %Y %I:%M %p", "%a %d %b, %Y %I:%M%p", "%d %b, %Y %I:%M %p")


def parse_txn_datetime(raw):
    """Parse the export's Transaction Date. Returns None if unparseable."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, pd.Timestamp):
        return raw.to_pydatetime()

    text = str(raw).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(text, errors="coerce")
        return None if pd.isna(parsed) else parsed.to_pydatetime()
    except Exception:
        return None


def parse_amount(raw):
    """'50,000.00' / 50000.0 -> Decimal-safe float. None if not a number."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return None if pd.isna(raw) else float(raw)
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _amount_key(amount) -> int | None:
    """Amounts compare as integer minor units -- never as floats."""
    if amount is None:
        return None
    return int(round(float(amount) * 100))


def _match_key(txn: Transaction):
    amount = _amount_key(txn.txn_amount)
    if not txn.mid or amount is None or not txn.beneficiary_id:
        return None
    return (txn.mid, amount, txn.beneficiary_id)


def txn_identity(txn: Transaction):
    """
    What makes a row *the same real transaction* across uploads.

    This matters because the daily exports overlap: the 12 Aug file covers
    11 Aug 10:00 through 12 Aug 21:59, so the 13 Aug file will re-report much
    of the 12th. The same settlement attempt therefore exists as several
    Transaction rows in several batches.

    Without this, two duplicate rows of one success would "cover" two
    different failures, inventing a resolution that never happened. Keyed on
    the acquirer's own identifiers (STAN / CRRN / CR Transaction ID), which
    are regenerated per attempt and so identify an attempt exactly.
    """
    extra = txn.extra_data or {}
    stan = str(extra.get("STAN") or "").strip()
    crrn = str(extra.get("CRRN") or "").strip()
    cr_id = str(extra.get("CR Transaction ID") or "").strip()
    if stan or crrn or cr_id:
        return ("txn", stan, crrn, cr_id)
    return ("row", txn.id)  # no identifiers: treat the row as unique


def is_retry_settlement(settled_by) -> bool:
    return (settled_by or "").strip().lower() in RETRY_SETTLE_MODES


def reconcile_retries(around_batch_id: int | None = None, all_time: bool = False) -> dict:
    """
    Re-run retry matching and persist the flags.

    Scope: the time window spanned by `around_batch_id` widened by
    RETRY_WINDOW_DAYS on both sides (so a new upload can resolve failures in
    batches ingested earlier), or every transaction when `all_time` is set.

    Idempotent: flags inside the scope are cleared and recomputed, so
    re-running never double-counts and can also *un*-resolve a row if the
    data changed.
    """
    query = Transaction.query
    window = None

    if not all_time and around_batch_id is not None:
        bounds = (
            db.session.query(
                db.func.min(Transaction.txn_datetime), db.func.max(Transaction.txn_datetime)
            )
            .filter(Transaction.batch_id == around_batch_id)
            .first()
        )
        if bounds and bounds[0] and bounds[1]:
            window = (
                bounds[0] - timedelta(days=RETRY_WINDOW_DAYS),
                bounds[1] + timedelta(days=RETRY_WINDOW_DAYS),
            )
            query = query.filter(
                Transaction.txn_datetime >= window[0], Transaction.txn_datetime <= window[1]
            )

    rows = query.filter(Transaction.txn_datetime.isnot(None)).all()

    # Grouped by match key, then collapsed to one entry per real transaction
    # so duplicate rows from overlapping uploads cannot each claim a match.
    failures: dict[tuple, dict[tuple, list[Transaction]]] = defaultdict(lambda: defaultdict(list))
    successes: dict[tuple, dict[tuple, Transaction]] = defaultdict(dict)

    for txn in rows:
        # Clear first so a re-run is a clean recompute, not an accumulation.
        txn.retry_resolved = False
        txn.retry_resolved_by_id = None

        key = _match_key(txn)
        if key is None:
            continue

        bucket = normalize_txn_status(txn.status)
        identity = txn_identity(txn)
        if bucket == "failed":
            failures[key][identity].append(txn)
        elif bucket == "success" and is_retry_settlement(txn.settled_by):
            existing = successes[key].get(identity)
            if existing is None or txn.txn_datetime < existing.txn_datetime:
                successes[key][identity] = txn

    resolved_rows = 0
    resolved_txns = 0
    cross_batch = 0

    for key, failed_by_identity in failures.items():
        covering = sorted(successes.get(key, {}).values(), key=lambda t: t.txn_datetime)
        if not covering:
            continue

        # Each real failure once, earliest first; all its duplicate rows share
        # whatever verdict it gets.
        ordered = sorted(
            failed_by_identity.values(), key=lambda group: min(t.txn_datetime for t in group)
        )
        taken = set()

        for group in ordered:
            failed_at = min(t.txn_datetime for t in group)
            for idx, success in enumerate(covering):
                if idx in taken or success.txn_datetime <= failed_at:
                    continue
                if success.txn_datetime - failed_at > timedelta(days=RETRY_WINDOW_DAYS):
                    break  # sorted: everything further out is out of window too
                taken.add(idx)
                for txn in group:
                    txn.retry_resolved = True
                    txn.retry_resolved_by_id = success.id
                    resolved_rows += 1
                    if success.batch_id != txn.batch_id:
                        cross_batch += 1
                resolved_txns += 1
                break

    db.session.commit()
    return {
        "scanned": len(rows),
        "resolved": resolved_txns,       # distinct real failures resolved
        "resolved_rows": resolved_rows,  # rows flagged (duplicates included)
        "cross_batch": cross_batch,
        "window": [window[0].isoformat(), window[1].isoformat()] if window else None,
    }


def resolved_details(batch_id: int) -> list[dict]:
    """
    The audit list behind the "reprocessed & settled" count: which failure was
    covered by which success. Nothing is hidden without being inspectable.
    """
    failures = (
        Transaction.query.filter_by(batch_id=batch_id, retry_resolved=True)
        .order_by(Transaction.txn_datetime)
        .all()
    )
    if not failures:
        return []

    covering_ids = {f.retry_resolved_by_id for f in failures if f.retry_resolved_by_id}
    covering = {
        t.id: t for t in Transaction.query.filter(Transaction.id.in_(covering_ids)).all()
    } if covering_ids else {}

    rows = []
    for failure in failures:
        success = covering.get(failure.retry_resolved_by_id)
        extra = failure.extra_data or {}
        rows.append({
            "mid": failure.mid,
            "merchant_name": failure.merchant_name,
            "partner_name": failure.partner_name,
            "category": failure.error_category,
            "amount": float(failure.txn_amount) if failure.txn_amount is not None else None,
            "beneficiary_id": failure.beneficiary_id,
            "failed_at": failure.txn_datetime.isoformat() if failure.txn_datetime else None,
            "failed_settled_by": failure.settled_by,
            "failed_stan": str(extra.get("STAN") or ""),
            "failed_remark": failure.remark,
            "settled_at": success.txn_datetime.isoformat() if success and success.txn_datetime else None,
            "settled_by": success.settled_by if success else None,
            "settled_stan": str((success.extra_data or {}).get("STAN") or "") if success else "",
            "same_batch": bool(success and success.batch_id == failure.batch_id),
        })
    return rows
