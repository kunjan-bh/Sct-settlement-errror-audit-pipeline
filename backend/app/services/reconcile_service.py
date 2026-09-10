"""
Reconciliation engine: does everything that came in go back out?

Incoming is a QR payment (operators.transactions) -- money taken from a
customer. Outgoing is a settlement (operators.fund_transfer_logs) -- that money
paid to the merchant. Over a long enough window the two should agree, and every
rupee of difference should have a name.

Two things make this harder than one join, and both were established against
the live switch before anything here was written.

First, the link. CRRN does NOT join the two tables -- the namespaces are
disjoint, and matching on it produces nothing. Neither does transactions.
settlement_id, despite the name. The key is
`fund_transfer_logs.ref_id = transactions.network_txn_id`.

Second, not every merchant settles per transaction. Where
`is_real_time_merchant_settled` is true each payment gets its own settlement
row and matches one to one. Where it is false the merchant is settled in
batches, so their payments have no individual settlement and must be
reconciled on totals instead. On 8 Sep that was 11,777 real-time payments
matching one to one, against 3,260 batch payments with no row each -- reported
as missing, they would have looked like NPR 3.7m gone astray.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.services.classification_service import PartnerResolver
from app.services.core_db import run_query

# How long a settlement may reasonably take before its absence is a finding
# rather than a wait. Settlement can legitimately run a day or two late, and
# occasionally a week when an aggregator is holding things up.
GRACE_DAYS = 2

# How far past the window to look for settlements. A payment on the last day of
# the range may settle a day or two later. Kept tight because every extra day
# widens the settlement side of the join: at 14 days the per-payment match took
# 112s and hit the statement timeout when the balance join was still inside it.
SETTLE_LOOKAHEAD_DAYS = 3


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# One row per incoming payment, with its settlement beside it where there is
# one. Left join, because a payment with no settlement is the whole point.
# Both sides are fetched separately and joined in Python.
#
# As one server-side join this took 49s on a single day and tipped over the 60s
# statement timeout the moment anything else was querying -- ref_id is not
# indexed, so Postgres hashed a whole date range of settlements against every
# payment. Two date-filtered reads and a dict do the same work in a fraction of
# the time, and the row counts are small enough to carry (about 15k payments
# and 60k settlements for one day).
_TXN_SQL = """
SELECT
    t.txn_id, t.network_txn_id, t.crrn, t.stan, t.merchant_code,
    t.txn_amount, t.txn_date, t.txn_date_time, t.txn_mode,
    t.institution_name, t.issuer_institution_name,
    t.is_real_time_merchant_settled AS real_time
FROM operators.transactions t
WHERE t.txn_date BETWEEN %(date_from)s AND %(date_to)s
  AND t.payment_status = 'SUCCESS'
"""

_SETTLE_SQL = """
SELECT
    f.id, f.ref_id, f.merchant_code, f.amount, f.service_charge,
    f.status, f.current_status, f.status_code, f.remarks,
    f.date AS settle_date, f.date_time AS settle_date_time,
    f.settlement_frequency, f.bank_name_or_wallet_name, f.crrn AS settle_crrn
FROM operators.fund_transfer_logs f
WHERE f.date BETWEEN %(date_from)s AND %(settle_to)s
  AND f.ref_id IS NOT NULL
"""

_BALANCE_SQL = """
SELECT merchant_code, total_balance, hold_balance
FROM supports.get_merchant_balance()
"""

# Batch-settled merchants get no per-payment row, so they reconcile on totals:
# everything they took in against everything paid out to them.
_BATCH_SQL = """
WITH took AS (
    SELECT merchant_code, count(*) AS txns, sum(txn_amount) AS amount
    FROM operators.transactions
    WHERE txn_date BETWEEN %(date_from)s AND %(date_to)s
      AND payment_status = 'SUCCESS'
      AND is_real_time_merchant_settled IS NOT TRUE
    GROUP BY merchant_code
),
paid AS (
    SELECT merchant_code,
           count(*) FILTER (WHERE status = 'SUCCESS') AS settlements,
           sum(amount) FILTER (WHERE status = 'SUCCESS') AS amount,
           count(*) FILTER (WHERE status <> 'SUCCESS') AS failed
    FROM operators.fund_transfer_logs
    -- Same days as the take side. A batch merchant's settlements cannot be
    -- matched to individual payments, so the only honest comparison is
    -- like-for-like over one period; a wider window here counts payouts for
    -- days the take side never saw.
    WHERE date BETWEEN %(date_from)s AND %(date_to)s
    GROUP BY merchant_code
)
SELECT took.merchant_code, took.txns, took.amount AS took_amount,
       COALESCE(paid.settlements, 0) AS settlements,
       COALESCE(paid.amount, 0) AS paid_amount,
       COALESCE(paid.failed, 0) AS failed_settlements
FROM took
LEFT JOIN paid ON paid.merchant_code = took.merchant_code
ORDER BY took.amount DESC
"""

# Settlements with no single payment behind them.
#
# Checked against the live switch: on 8 Sep all 195 of these were ON_CALL (109)
# or SYSTEM_DEFAULT (86) and not one was REAL_TIME, and widening the payment
# lookback from 3 days to 30 changed nothing. They are aggregate payouts -- one
# transfer covering many payments -- so no individual transaction corresponds
# to them and none ever will. Reported as fact, not as a finding.
#
# An unmatched REAL_TIME settlement would be a different matter, since those
# are raised one per payment. Those are flagged separately below.
# Written as an anti-join rather than NOT EXISTS: the correlated form re-probed
# operators.transactions once per settlement and blew the statement timeout.
_ORPHAN_SQL = """
WITH paid AS (
    SELECT id, merchant_code, crrn, amount, status, date, remarks,
           bank_name_or_wallet_name, settlement_frequency, ref_id
    FROM operators.fund_transfer_logs
    WHERE date BETWEEN %(date_from)s AND %(date_to)s
      AND status = 'SUCCESS'
      AND ref_id IS NOT NULL
),
took AS (
    SELECT DISTINCT network_txn_id
    FROM operators.transactions
    WHERE txn_date BETWEEN %(txn_from)s AND %(date_to)s
      AND network_txn_id IS NOT NULL
)
SELECT paid.id, paid.merchant_code, paid.crrn, paid.amount, paid.status,
       paid.date, paid.remarks, paid.bank_name_or_wallet_name,
       paid.settlement_frequency, paid.ref_id
FROM paid
LEFT JOIN took ON took.network_txn_id = paid.ref_id
WHERE took.network_txn_id IS NULL
ORDER BY paid.amount DESC
"""


def _as_date(v: str | date) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def _classify(row: dict, today: date) -> tuple[str, str]:
    """
    (bucket, why) for one incoming payment.

    The buckets are the answer to "where is this merchant's money", and each
    one is a different thing to do about it -- which is why "unsettled" is not
    one bucket.
    """
    status = (row.get("settle_status") or "").upper()
    txn_day = row.get("txn_date")
    age = (today - txn_day).days if isinstance(txn_day, date) else 0

    if status == "SUCCESS":
        return "settled", "Paid to the merchant."

    if status:
        # There is a settlement and it did not succeed. Whether that is still
        # outstanding is a balance question, not a status one: if the merchant
        # is holding nothing, the money has since gone out by some other route
        # -- a retry, a reprocess, or the next day's run.
        hold = _num(row.get("hold_balance"))
        total = _num(row.get("total_balance"))
        if hold == 0 and total == 0:
            return "settled_later", (
                "Settlement failed, but the merchant holds nothing — "
                "it went out on a retry or a later run."
            )
        return "settlement_failed", (
            f"Settlement {status.lower()}: {row.get('remarks') or row.get('current_status') or 'no reason given'}"
        )

    # No settlement row at all.
    if not row.get("real_time"):
        return "batch_merchant", (
            "Settled in batches, not per payment — reconciled on totals below."
        )
    if age <= GRACE_DAYS:
        return "awaiting_settlement", (
            f"No settlement yet, {age} day{'' if age == 1 else 's'} old — still within normal time."
        )
    return "not_in_settlement_report", (
        f"{age} days old with no settlement record at all. "
        "The payment is in the transaction report but nothing was ever raised to pay it out."
    )


RECONCILE_STEPS = [
    "Reading payments and matching settlements",
    "Reading merchant balances",
    "Classifying every payment",
    "Reconciling batch-settled merchants",
    "Checking settlements with no payment",
]


def build_reconciliation(
    date_from: str | date, date_to: str | date, progress=None,
) -> dict:
    """
    Reconcile incoming payments against outgoing settlements for a date range.

    Everything is read-only from the switch (see core_db). Nothing here writes.
    """
    d_from, d_to = _as_date(date_from), _as_date(date_to)
    today = date.today()
    params = {
        "date_from": str(d_from),
        "date_to": str(d_to),
        "settle_to": str(d_to + timedelta(days=SETTLE_LOOKAHEAD_DAYS)),
    }

    if progress: progress(RECONCILE_STEPS[0], 0)
    payments = run_query(_TXN_SQL, params)
    settlements = run_query(_SETTLE_SQL, params)
    # One settlement per payment where there is one at all. Where a ref_id
    # somehow repeats, the successful one is the answer -- a later success is
    # what actually happened to the money.
    by_ref: dict[str, dict] = {}
    for s in settlements:
        ref = s.get("ref_id")
        if ref is None:
            continue
        prev = by_ref.get(ref)
        if prev is None or (
            (s.get("status") or "").upper() == "SUCCESS"
            and (prev.get("status") or "").upper() != "SUCCESS"
        ):
            by_ref[ref] = s

    rows = []
    for p in payments:
        s = by_ref.get(p.get("network_txn_id")) or {}
        rows.append({
            **p,
            "settle_id": s.get("id"),
            "settle_amount": s.get("amount"),
            "service_charge": s.get("service_charge"),
            "settle_status": s.get("status"),
            "current_status": s.get("current_status"),
            "status_code": s.get("status_code"),
            "remarks": s.get("remarks"),
            "settle_date": s.get("settle_date"),
            "settle_date_time": s.get("settle_date_time"),
            "settlement_frequency": s.get("settlement_frequency"),
            "bank_name_or_wallet_name": s.get("bank_name_or_wallet_name"),
            "settle_crrn": s.get("settle_crrn"),
        })

    if progress: progress(RECONCILE_STEPS[1], 1)
    balances = {
        (b.get("merchant_code") or "").strip(): b
        for b in run_query(_BALANCE_SQL)
    }
    resolver = PartnerResolver.load()

    if progress: progress(RECONCILE_STEPS[2], 2)
    buckets: dict[str, list[dict]] = {}
    incoming_amount = 0.0
    settled_amount = 0.0

    for r in rows:
        bal = balances.get((r.get("merchant_code") or "").strip()) or {}
        r["hold_balance"] = bal.get("hold_balance")
        r["total_balance"] = bal.get("total_balance")
        bucket, why = _classify(r, today)
        amount = _num(r.get("txn_amount"))
        incoming_amount += amount
        if bucket == "settled":
            settled_amount += _num(r.get("settle_amount"))

        mid = (r.get("merchant_code") or "").strip()
        partner, partner_type = resolver.resolve(mid)
        buckets.setdefault(bucket, []).append({
            "txn_id": r.get("txn_id"),
            "network_txn_id": r.get("network_txn_id"),
            "crrn": r.get("crrn"),
            "stan": r.get("stan"),
            "mid": mid,
            "merchant": r.get("institution_name"),
            "issuer": r.get("issuer_institution_name"),
            "partner": partner,
            "partner_type": partner_type,
            "txn_amount": amount,
            "txn_date": str(r.get("txn_date") or ""),
            "txn_date_time": str(r.get("txn_date_time") or ""),
            "settle_id": r.get("settle_id"),
            "settle_crrn": r.get("settle_crrn"),
            "settle_amount": _num(r.get("settle_amount")),
            "settle_status": r.get("settle_status"),
            "settle_date": str(r.get("settle_date") or ""),
            "settlement_frequency": r.get("settlement_frequency"),
            "bank_or_wallet": r.get("bank_name_or_wallet_name"),
            "remarks": r.get("remarks"),
            "hold_balance": _num(r.get("hold_balance")),
            "total_balance": _num(r.get("total_balance")),
            "bucket": bucket,
            "why": why,
        })

    # Batch merchants, reconciled on totals rather than per payment.
    if progress: progress(RECONCILE_STEPS[3], 3)
    batch_rows = run_query(_BATCH_SQL, params)
    batch = []
    batch_took = batch_paid = 0.0
    for b in batch_rows:
        took = _num(b.get("took_amount"))
        paid = _num(b.get("paid_amount"))
        batch_took += took
        batch_paid += paid
        mid = (b.get("merchant_code") or "").strip()
        partner, _t = resolver.resolve(mid)
        variance = round(took - paid, 2)
        bal = balances.get(mid) or {}
        hold = _num(bal.get("hold_balance"))
        batch.append({
            "mid": mid, "partner": partner,
            "txns": b.get("txns"), "took_amount": took,
            "settlements": b.get("settlements"), "paid_amount": paid,
            "failed_settlements": b.get("failed_settlements"),
            "hold_balance": hold, "total_balance": _num(bal.get("total_balance")),
            "variance": variance,
            # A variance the merchant is still holding is money on its way, not
            # money missing. Only the rest is a finding.
            "explained_by_balance": abs(variance) <= max(hold, 0) + 0.01,
        })

    if progress: progress(RECONCILE_STEPS[4], 4)
    orphans = run_query(_ORPHAN_SQL, {
        "date_from": str(d_from), "date_to": str(d_to),
        # An orphan check has to look back before the window, or a payment made
        # yesterday and settled today is called an orphan.
        "txn_from": str(d_from - timedelta(days=SETTLE_LOOKAHEAD_DAYS)),
    })

    orphan_rows = []
    aggregate_orphans = realtime_orphans = 0
    for o in orphans:
        freq = (o.get("settlement_frequency") or "").upper()
        is_realtime = freq == "REAL_TIME"
        if is_realtime:
            realtime_orphans += 1
        else:
            aggregate_orphans += 1
        orphan_rows.append({
            "id": o.get("id"), "mid": o.get("merchant_code"), "crrn": o.get("crrn"),
            "amount": _num(o.get("amount")), "date": str(o.get("date") or ""),
            "remarks": o.get("remarks"),
            "bank_or_wallet": o.get("bank_name_or_wallet_name"),
            "settlement_frequency": o.get("settlement_frequency"),
            "expected_to_match": is_realtime,
            "why": (
                "Real-time settlement with no payment found — should have matched."
                if is_realtime else
                f"{freq.replace('_', ' ').title() or 'Batch'} payout covering several "
                "payments at once, so no single transaction matches it."
            ),
        })

    # The one thing the balance line actually tests.
    #
    # Every payment sits in exactly one bucket and the buckets are a partition
    # of the same population, so "explained" is just every bucket except
    # settled, and "difference" is incoming minus settled. Those cancel by
    # construction. The only quantity that can survive is the gap between what
    # a settled payment took in and what was paid out for it -- so that is what
    # the residual measures, and it deserves to be named rather than dressed up
    # as proof that the whole reconciliation is sound.
    mismatches = [
        x for x in buckets.get("settled", [])
        if abs(x["txn_amount"] - x["settle_amount"]) > 0.01
    ]

    def count(name: str) -> int:
        return len(buckets.get(name, []))

    def amount_of(name: str) -> float:
        return round(sum(x["txn_amount"] for x in buckets.get(name, [])), 2)

    # Exceptions are everything that is not simply done: the report someone
    # actually has to act on.
    exception_buckets = (
        "not_in_settlement_report", "settlement_failed",
        "awaiting_settlement", "settled_later",
    )
    exceptions = [x for name in exception_buckets for x in buckets.get(name, [])]
    exceptions.sort(key=lambda x: -x["txn_amount"])

    return {
        "range": {"from": str(d_from), "to": str(d_to)},
        "totals": {
            "incoming_txns": len(rows),
            "incoming_amount": round(incoming_amount, 2),
            "settled_txns": count("settled"),
            "settled_amount": round(settled_amount, 2),
            "difference": round(incoming_amount - settled_amount, 2),
            "settled_later": count("settled_later"),
            "settled_later_amount": amount_of("settled_later"),
            "settlement_failed": count("settlement_failed"),
            "settlement_failed_amount": amount_of("settlement_failed"),
            "awaiting_settlement": count("awaiting_settlement"),
            "awaiting_amount": amount_of("awaiting_settlement"),
            "not_in_settlement_report": count("not_in_settlement_report"),
            "not_in_report_amount": amount_of("not_in_settlement_report"),
            "batch_merchant_txns": count("batch_merchant"),
            "batch_merchant_amount": amount_of("batch_merchant"),
            "batch_merchants": len(batch),
            "batch_took_amount": round(batch_took, 2),
            "batch_paid_amount": round(batch_paid, 2),
            "batch_unexplained": len([b for b in batch if not b["explained_by_balance"]]),
            "orphan_settlements": len(orphan_rows),
            "orphan_amount": round(sum(x["amount"] for x in orphan_rows), 2),
            "orphan_aggregate": aggregate_orphans,
            "orphan_realtime": realtime_orphans,
            "exceptions": len(exceptions),
            "amount_mismatches": len(mismatches),
            "amount_mismatch_amount": round(
                sum(x["txn_amount"] - x["settle_amount"] for x in mismatches), 2
            ),
        },
        "buckets": {k: v for k, v in buckets.items()},
        "batch": batch,
        "orphans": orphan_rows,
        "orphan_note": (
            f"{len(orphan_rows):,} settlements had no single payment behind them. "
            f"{aggregate_orphans:,} are batch or on-call payouts, which cover many "
            "payments in one transfer, so no individual transaction corresponds to "
            "them — expected, not a finding. "
            + (
                f"{realtime_orphans:,} are real-time settlements, which are raised one "
                "per payment and should have matched; those are worth checking."
                if realtime_orphans else
                "None were real-time, which are the only kind that should match one to one."
            )
        ),
        "exceptions": exceptions,
        "amount_mismatches": mismatches,
        "balance_note": (
            "Every payment is in exactly one line above, so those lines always sum to "
            "the difference. What this check really tests is narrower: whether each "
            "settled payment was paid out at the amount it came in at. "
            + (
                f"{len(mismatches):,} were not."
                if mismatches else
                "All of them were."
            )
        ),
    }
