"""
Issuer vs Acquirer reconciliation for one day (or range).

Every QR transaction has two sides. The customer's bank or wallet debits them
-- the ISSUER. The merchant's institution is paid -- the ACQUIRER. Inside the
transaction export those two always balance to the rupee: measured on a real
120,237-row file, issuing and acquiring both totalled NPR 129,926,900.72,
because every row contributes its amount to exactly one of each.

So the interesting gap is not issuer vs acquirer. It is what the switch
transacted versus what actually settled:

    transaction file  ->  what was authorised   (issuing and acquiring sides)
    settlement file   ->  what was paid out     (acquiring side only)

A shortfall there is usually pending settlement rather than a break -- a
transaction late in the day settles the next morning, an On Call batch has not
run yet. That is why every variance row carries a free-text reason: the
number alone cannot say whether it is timing or a real problem, and only ops
knows which.

Nothing is persisted. Both files are read, reconciled and discarded, same as
Document Analysis.
"""
import pandas as pd

from app.services.excel_ingest import read_settlement_dataframe, _clean
from app.services.retry_matching import parse_amount
from app.services.status_utils import normalize_txn_status
from app.services.transaction_reconcile import read_transaction_dataframe

# Columns the transaction export can name these under. The Title Case ones are
# the "Transaction List" shape, the snake_case the raw dump.
_ISSUER_COLUMNS = ("issuer_institution_name", "Issuer Name")
_ACQUIRER_COLUMNS = ("institution_name", "Acquirer Name")
_AMOUNT_COLUMNS = ("txn_amount", "Transaction Amount")
_DATE_COLUMNS = ("txn_date_time", "txn_date", "Date & Time")

UNKNOWN = "(unnamed)"


def _pick(columns, candidates):
    for name in candidates:
        if name in columns:
            return name
    return None


def _accumulate(rows, name_col, amount_col):
    """{name: [count, amount]} over a dataframe, blank names bucketed together
    rather than dropped -- a missing institution is a data problem worth
    seeing, not something to quietly discard."""
    acc: dict[str, list] = {}
    for name, amount in zip(rows[name_col], rows[amount_col]):
        key = (str(name).strip() if pd.notna(name) else "") or UNKNOWN
        entry = acc.setdefault(key, [0, 0.0])
        entry[0] += 1
        entry[1] += float(amount or 0)
    return acc


def _rows_from(acc, count_key, amount_key, total_amount):
    out = [
        {
            "name": name,
            count_key: c,
            amount_key: round(a, 2),
            "share": round(a * 100.0 / total_amount, 2) if total_amount else 0.0,
        }
        for name, (c, a) in acc.items()
    ]
    out.sort(key=lambda r: -r[amount_key])
    return out


def build_issuer_acquirer(txn_path: str, settle_path: str | None = None) -> dict:
    """
    Reconcile one transaction export against one settlement export.

    The settlement file is optional: with only transactions you still get both
    sides of the switch's own view, which is worth seeing on its own. The
    settled column and every variance simply stay empty.
    """
    txn = read_transaction_dataframe(txn_path)
    cols = set(txn.columns)

    issuer_col = _pick(cols, _ISSUER_COLUMNS)
    acquirer_col = _pick(cols, _ACQUIRER_COLUMNS)
    amount_col = _pick(cols, _AMOUNT_COLUMNS)
    date_col = _pick(cols, _DATE_COLUMNS)

    if amount_col is None:
        raise ValueError(
            f"Transaction file has no amount column (looked for {list(_AMOUNT_COLUMNS)}); "
            f"found: {sorted(cols)}"
        )
    if issuer_col is None and acquirer_col is None:
        raise ValueError(
            "Transaction file names neither an issuer nor an acquirer institution "
            f"(looked for {list(_ISSUER_COLUMNS)} / {list(_ACQUIRER_COLUMNS)})."
        )

    txn = txn.copy()
    txn["_amt"] = [parse_amount(v) or 0.0 for v in txn[amount_col]]
    txn_total = float(txn["_amt"].sum())
    txn_count = len(txn)

    issuing = _rows_from(
        _accumulate(txn, issuer_col, "_amt") if issuer_col else {},
        "txn_count", "txn_amount", txn_total,
    )
    acquiring_txn = _accumulate(txn, acquirer_col, "_amt") if acquirer_col else {}

    # ---- settlement side ----
    settled_by_acquirer: dict[str, list] = {}
    settled_total = 0.0
    settled_count = 0
    settle_rows = 0
    if settle_path:
        settle = read_settlement_dataframe(settle_path)
        acq = "Acquirer Name" if "Acquirer Name" in settle.columns else None
        settle_rows = len(settle)
        for _, row in settle.iterrows():
            if normalize_txn_status(_clean(row.get("Status"))) != "success":
                continue
            amount = float(parse_amount(row.get("Txn Amount")) or 0.0)
            name = (_clean(row.get(acq)) if acq else None) or UNKNOWN
            entry = settled_by_acquirer.setdefault(name, [0, 0.0])
            entry[0] += 1
            entry[1] += amount
            settled_total += amount
            settled_count += 1

    # ---- acquiring: transacted vs settled, side by side ----
    names = set(acquiring_txn) | set(settled_by_acquirer)
    acquiring = []
    for name in names:
        t_count, t_amount = acquiring_txn.get(name, (0, 0.0))
        s_count, s_amount = settled_by_acquirer.get(name, (0, 0.0))
        acquiring.append({
            "name": name,
            "txn_count": t_count,
            "txn_amount": round(t_amount, 2),
            "settled_count": s_count,
            "settled_amount": round(s_amount, 2),
            # Positive = transacted more than settled, i.e. still to be paid
            # out. Negative means the settlement file covers transactions this
            # export does not, which is normal when the two cover different
            # windows.
            "variance_amount": round(t_amount - s_amount, 2),
            "variance_count": t_count - s_count,
        })
    acquiring.sort(key=lambda r: -max(r["txn_amount"], r["settled_amount"]))

    window = ""
    if date_col is not None:
        values = [str(v)[:19] for v in txn[date_col].dropna()]
        if values:
            window = f"{min(values)} to {max(values)}"

    return {
        "totals": {
            "txn_rows": txn_count,
            "txn_amount": round(txn_total, 2),
            "settlement_rows": settle_rows,
            "settled_count": settled_count,
            "settled_amount": round(settled_total, 2),
            "variance_amount": round(txn_total - settled_total, 2),
            "variance_count": txn_count - settled_count,
            "settled_pct": round(settled_total * 100.0 / txn_total, 2) if txn_total else 0.0,
            "window": window,
            "has_settlement": bool(settle_path),
        },
        "issuing": issuing,
        "acquiring": acquiring,
    }


# --- straight from the switch ------------------------------------------------
#
# The upload version reconciles whatever two files someone happens to have.
# This one asks the database the files came from, so a date is all it needs and
# the two sides always cover the same window -- which is where most of the
# spurious variance in the file version came from.

# Both sides are pulled once and everything derived in Python. Separate GROUP
# BYs would each re-scan the same two tables, and explaining a variance needs
# the row-level ref_id link anyway.
_TXN_SQL = """
SELECT network_txn_id, institution_name, issuer_institution_name, txn_amount,
       is_real_time_merchant_settled AS real_time, merchant_code
FROM operators.transactions
WHERE txn_date BETWEEN %(date_from)s AND %(date_to)s
  AND payment_status = 'SUCCESS'
"""

_SETTLE_SQL = """
SELECT ref_id, acquirer_name, amount, status, date
FROM operators.fund_transfer_logs
WHERE date BETWEEN %(date_from)s AND %(settle_to)s
"""

# A settlement raised a day or two after the payment is normal, so the
# settlement side reaches past the window being reported on.
_LOOKAHEAD_DAYS = 3


def _to_float(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _explain(variance, failed_amt, earlier_amt, missing_amt):
    """
    (code, sentence) for one acquirer's variance.

    Nobody is going to write three hundred reasons by hand, and they would be
    the same three sentences every time. The switch already knows which one
    applies: a positive gap is settlements that failed or have not gone out
    yet, a negative one is earlier days' settlements landing in this window.

    What neither explains is a payment with no settlement raised at all. That
    is a settlement entry genuinely missing rather than late, so it is called
    out separately and sorts to the top.
    """
    if abs(variance) < 0.01 and missing_amt < 0.01:
        return "balanced", "Settled in full."

    parts = []
    if missing_amt >= 0.01:
        parts.append(
            "NPR {:,.2f} of payments have no settlement entry at all - nothing was "
            "ever raised to pay them out".format(missing_amt)
        )
    if variance > 0:
        parts.append(
            "NPR {:,.2f} of settlements were raised but failed or are still pending".format(failed_amt)
            if failed_amt >= 0.01
            else "payments taken have not been settled yet"
        )
    elif variance < 0:
        parts.append(
            "NPR {:,.2f} settled here belongs to payments from earlier days".format(earlier_amt)
            if earlier_amt >= 0.01
            else "more was settled than taken here, so it covers earlier days"
        )

    code = (
        "missing_entries" if missing_amt >= 0.01
        else "pending_settlement" if variance > 0
        else "earlier_days"
    )
    sentence = "; ".join(parts)
    return code, sentence[:1].upper() + sentence[1:] + "."


def build_issuer_acquirer_from_range(date_from, date_to) -> dict:
    """
    Issuing and acquiring volumes for a date range, read from the switch, each
    acquirer's variance explained from the data rather than typed by hand.

    Same shape as build_issuer_acquirer so the page and the report do not care
    which way the data arrived.

    Only successful payments count. An issuing volume including declines is not
    one anyone can act on, and the settlement side has no equivalent to compare
    it against.
    """
    from datetime import timedelta

    from app.services.core_db import run_query

    params = {
        "date_from": str(date_from),
        "date_to": str(date_to),
        "settle_to": str(date_to + timedelta(days=_LOOKAHEAD_DAYS)),
    }
    payments = run_query(_TXN_SQL, params)
    settlements = run_query(_SETTLE_SQL, params)

    # --- issuing -----------------------------------------------------------
    issuing_acc = {}
    txn_total = 0.0
    for p in payments:
        amount = _to_float(p.get("txn_amount"))
        txn_total += amount
        e = issuing_acc.setdefault(p.get("issuer_institution_name") or UNKNOWN, [0, 0.0])
        e[0] += 1
        e[1] += amount
    issuing = _rows_from(
        {k: (v[0], v[1]) for k, v in issuing_acc.items()},
        "txn_count", "txn_amount", txn_total,
    )

    # --- acquiring ---------------------------------------------------------
    acq_txn = {}
    payment_of = {}
    for p in payments:
        name = p.get("institution_name") or UNKNOWN
        amount = _to_float(p.get("txn_amount"))
        e = acq_txn.setdefault(name, [0, 0.0])
        e[0] += 1
        e[1] += amount
        ref = p.get("network_txn_id")
        if ref:
            payment_of[ref] = (name, amount, p.get("merchant_code") or "")

    settled, failed, earlier = {}, {}, {}
    matched, seen = set(), set()
    window_to = str(date_to)
    for s in settlements:
        name = s.get("acquirer_name") or UNKNOWN
        amount = _to_float(s.get("amount"))
        ref = s.get("ref_id")
        succeeded = (s.get("status") or "").upper() == "SUCCESS"

        # The lookahead days exist so a payment settled tomorrow still counts
        # as settled. They must not be added to this window's totals, or the
        # settled side covers more days than the payments it is compared with
        # -- which reported 31,048 settlements against 15,038 payments and made
        # every acquirer look enormously over-settled.
        #
        # `seen` is any settlement entry at all, whatever its status; `matched`
        # is one that succeeded. A settlement that failed is a failed
        # settlement, not a missing entry -- an entry exists, it just did not
        # go through, and telling an acquirer nothing was ever raised for it
        # would be wrong.
        if ref and ref in payment_of:
            seen.add(ref)
            if succeeded:
                matched.add(ref)
        if str(s.get("date") or "") > window_to:
            continue

        if succeeded:
            e = settled.setdefault(name, [0, 0.0])
            e[0] += 1
            e[1] += amount
            if ref and ref not in payment_of:
                # Paid out here, for a payment this window does not contain.
                e2 = earlier.setdefault(name, [0, 0.0])
                e2[0] += 1
                e2[1] += amount
        else:
            e3 = failed.setdefault(name, [0, 0.0])
            e3[0] += 1
            e3[1] += amount

    # Payments with no settlement raised at all.
    #
    # Batch-settled merchants are left out: they have no per-payment settlement
    # by design, so counting them here would report NPR 3.7m of missing entries
    # that were never missing. A merchant counts as batch-settled only when
    # none of their payments settle in real time -- the flag is per payment and
    # does sometimes disagree with the merchant's own behaviour.
    realtime_mids = {
        (p.get("merchant_code") or "") for p in payments if p.get("real_time")
    }
    missing = {}
    for ref, (name, amount, mid) in payment_of.items():
        # Missing means no entry of any status was ever raised.
        if ref in seen or mid not in realtime_mids:
            continue
        e = missing.setdefault(name, [0, 0.0])
        e[0] += 1
        e[1] += amount

    settled_count = sum(v[0] for v in settled.values())
    settled_total = sum(v[1] for v in settled.values())

    acquiring = []
    for name in set(acq_txn) | set(settled):
        t_count, t_amount = acq_txn.get(name, [0, 0.0])
        s_count, s_amount = settled.get(name, [0, 0.0])
        variance = round(t_amount - s_amount, 2)
        f_count, f_amount = failed.get(name, [0, 0.0])
        _ec, e_amount = earlier.get(name, [0, 0.0])
        m_count, m_amount = missing.get(name, [0, 0.0])
        code, why = _explain(variance, f_amount, e_amount, m_amount)
        acquiring.append({
            "name": name,
            "txn_count": t_count,
            "txn_amount": round(t_amount, 2),
            "settled_count": s_count,
            "settled_amount": round(s_amount, 2),
            "variance_amount": variance,
            "variance_count": t_count - s_count,
            "failed_settlements": f_count,
            "failed_amount": round(f_amount, 2),
            "earlier_days_amount": round(e_amount, 2),
            "missing_count": m_count,
            "missing_amount": round(m_amount, 2),
            "reason_code": code,
            "reason": why,
        })

    # Missing entries first: they are the only kind the data cannot explain
    # away, so they should not be buried under the biggest acquirers.
    acquiring.sort(key=lambda r: (
        r["reason_code"] != "missing_entries",
        -max(r["txn_amount"], r["settled_amount"]),
    ))

    return {
        "totals": {
            "txn_rows": len(payments),
            "txn_amount": round(txn_total, 2),
            "settlement_rows": settled_count,
            "settled_count": settled_count,
            "settled_amount": round(settled_total, 2),
            "variance_amount": round(txn_total - settled_total, 2),
            "variance_count": len(payments) - settled_count,
            "settled_pct": round(settled_total * 100.0 / txn_total, 2) if txn_total else 0.0,
            "window": "{} to {}".format(date_from, date_to),
            "has_settlement": True,
            "missing_entries": sum(v[0] for v in missing.values()),
            "missing_amount": round(sum(v[1] for v in missing.values()), 2),
        },
        "issuing": issuing,
        "acquiring": acquiring,
    }
