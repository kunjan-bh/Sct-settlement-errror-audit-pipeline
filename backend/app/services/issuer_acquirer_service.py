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
