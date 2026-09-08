"""
Disputes: failed settlements whose money is still sitting on the merchant.

A settlement that failed is not automatically a dispute. It becomes one when
the amount never went anywhere -- the merchant's balance is still holding it.
That pairing is what makes a case worth chasing, and it is only visible by
reading the switch and the balance function together, which is why this
cannot be built from the settlement export alone.

Everything here reads the live switch through core_db, which is read-only at
the server. Nothing in this module writes to it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.dispute_status import DisputeStatus
from app.services.classification_service import PartnerResolver
from app.services.core_db import run_query
from app.services.settings_service import get_settings

# A failed settlement carries the failure reason in two places that disagree:
# `status` is the coarse verdict and `current_status` is where in the transfer
# it stopped. Ops needs the second one -- "FUND_TRANSFER_PENDING" and
# "VALIDATION_FAILED" are chased completely differently.
_FAILED_STATUSES = ("FAILED", "PENDING")

# Held money plus one of these remarks is the dangerous combination: the far
# end may well have paid out, so the merchant could be paid twice if this is
# simply retried. Kept in step with the operator-editable verify list.
_DEFAULT_DOUBLE_PAY_HINTS = ("connection reset", "connection was closed")


def _double_pay_hints() -> tuple[str, ...]:
    """The verify-before-retry patterns the operator maintains in Settings,
    falling back to the built-in pair."""
    try:
        raw = (get_settings() or {}).get("verify_remark_patterns") or ""
    except Exception:  # noqa: BLE001 - settings must never break a read
        raw = ""
    pats = tuple(p.strip().lower() for p in str(raw).replace(";", ",").split(",") if p.strip())
    return pats or _DEFAULT_DOUBLE_PAY_HINTS


# Days after the filter window in which a retry still counts as "this one was
# reprocessed". Matches RETRY_WINDOW_DAYS in the batch flow -- a settlement
# retried on the next working day is the same money, not a new failure.
REPROCESS_WINDOW_DAYS = 3

# One row per failed settlement, with the merchant's live balance beside it and
# a flag for whether the same money later went through.
#
# The reprocess test is (merchant_code, amount) with a later timestamp, because
# nothing else links a retry to its original: CRRN is unique per row, and the
# ref_id chain only ever runs SUCCESS->SUCCESS or FAILED->FAILED, never
# FAILED->SUCCESS. Both were checked against the live switch before settling on
# this. It is the same rule the batch flow already retries on.
#
# `ok` is grouped up front rather than written as a correlated EXISTS: the
# correlated form scans fund_transfer_logs once per failed row and blew the
# statement timeout on a two-day window.
_DISPUTE_SQL = """
WITH bal AS (
    SELECT merchant_code, member_code, total_balance, hold_balance
    FROM supports.get_merchant_balance()
),
ok AS (
    SELECT merchant_code, amount, min(date_time) AS first_ok
    FROM operators.fund_transfer_logs
    WHERE date BETWEEN %(date_from)s AND %(reprocess_to)s
      AND status = 'SUCCESS'
    GROUP BY merchant_code, amount
)
SELECT
    f.id, f.merchant_code, f.merchant_name, f.crrn, f.stan, f.ref_id,
    f.amount, f.service_charge, f.date, f.date_time,
    f.status, f.current_status, f.status_code,
    f.remarks, f.remark_two,
    f.partner, f.acquirer_name, f.bank_name_or_wallet_name, f.wallet_code,
    f.institution_id, f.settlement_frequency, f.medium,
    f.creditor_name, f.creditor_account, f.creditor_mobile,
    f.bank_id, f.branch_id, f.partner_ref_id,
    b.member_code, b.total_balance, b.hold_balance,
    (ok.first_ok IS NOT NULL AND ok.first_ok > f.date_time) AS reprocessed_ok,
    ok.first_ok AS reprocessed_at
FROM operators.fund_transfer_logs f
LEFT JOIN bal b ON b.merchant_code = f.merchant_code
LEFT JOIN ok ON ok.merchant_code = f.merchant_code AND ok.amount = f.amount
WHERE f.date BETWEEN %(date_from)s AND %(date_to)s
  AND f.status = ANY(%(statuses)s)
ORDER BY f.amount DESC NULLS LAST, f.merchant_code
"""


def _as_date(value: str | date) -> date:
    """A date, whether the caller passed one or an ISO string."""
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _num(value) -> float:
    """Decimal/None from the switch as a plain float, for JSON."""
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _reason(row: dict) -> str:
    """
    One human sentence for why this failed.

    `remarks` is the switch's own message and is usually the useful one, but it
    is bare "Failed" often enough that current_status has to fill in -- an
    operator triaging a list cannot act on "Failed".
    """
    remark = (row.get("remarks") or "").strip()
    current = (row.get("current_status") or "").strip()
    if remark and remark.lower() not in ("failed", "-", "n/a", "null"):
        return remark
    if current:
        return current.replace("_", " ").title()
    return remark or "Unknown"


def _decision_for(row: dict, decisions: dict, scoped: dict) -> dict:
    """
    The operator's decision about this settlement.

    A decision on the row itself wins. Failing that, an entity-level exclusion
    applies -- excluding "Mbank" as a wallet is meant to cover every one of its
    failures, which is the whole point of excluding an entity rather than
    forty rows one at a time.
    """
    key = str(row.get("id") or "")
    own = decisions.get(key)
    if own:
        return {
            "op_status": own.status,
            "op_comment": own.comment,
            "op_scope": None,
            "op_updated_at": own.updated_at.isoformat() if own.updated_at else None,
        }

    for scope_type, value in (
        ("mapped_partner", row.get("_mapped_partner")),
        ("partner", row.get("partner")),
        ("bank_or_wallet", row.get("bank_name_or_wallet_name")),
        ("acquirer", row.get("acquirer_name")),
        ("mid", row.get("merchant_code")),
    ):
        hit = scoped.get((scope_type, (value or "").lower()))
        if hit:
            return {
                "op_status": hit.status,
                "op_comment": hit.comment,
                "op_scope": f"{scope_type}:{value}",
                "op_updated_at": hit.updated_at.isoformat() if hit.updated_at else None,
            }

    return {"op_status": "pending", "op_comment": None, "op_scope": None, "op_updated_at": None}


def _allocate_holds(disputes: list[dict]) -> None:
    """
    Decide, per merchant, which of their failures the held money actually
    covers -- setting `held`, `partially_held` and `likely_settled` in place.

    The hold balance is one pot per merchant, not one per failure. A merchant
    with two failed settlements of NPR 1,000 who holds NPR 1,000 has one
    outstanding failure and one that already went through; checking each row
    against the balance on its own calls both of them held and doubles the
    money at risk.

    Allocation runs most recent first. An older failure has had longer to be
    retried, swept up by a system-default run or settled on call, so when the
    hold only stretches to some of them it is the newer ones that are still
    outstanding. Whatever the hold does not reach is marked `likely_settled`
    and drops out of the dispute list.

    This is an inference, not a fact the switch states: nothing links a hold
    to the settlement that caused it. It is why the flag is named "likely" and
    why these rows stay in the payload rather than being deleted.
    """
    by_mid: dict[str, list[dict]] = {}
    for d in disputes:
        by_mid.setdefault(d["mid"], []).append(d)

    for rows in by_mid.values():
        # Excluded rows do not consume the hold -- they are not being chased.
        live = [d for d in rows if d["op_status"] != "exclude" and not d["reprocessed_ok"]]
        for d in rows:
            d.setdefault("held", False)
            d.setdefault("partially_held", False)
            d.setdefault("likely_settled", False)
            d.setdefault("negative_hold", False)

        pot = live[0]["hold_balance"] if live else 0.0

        # A negative hold is not "no money held", it is a broken ledger: on
        # every case seen so far the hold is exactly minus the total balance,
        # which looks like a hold released twice. Whatever the cause, it is
        # certainly not evidence that the settlement went through, so these
        # surface as disputes needing attention rather than being ruled out
        # for holding nothing.
        if pot < 0:
            for d in live:
                d["negative_hold"] = True
            continue

        # A merchant holding nothing was never a dispute -- those rows are not
        # "settled by allocation", they simply have no money sitting anywhere.
        # Only a hold that runs out part-way through tells us something.
        if pot == 0:
            continue

        remaining = pot
        for d in sorted(live, key=lambda x: x["date_time"], reverse=True):
            if remaining <= 0:
                d["likely_settled"] = True
                continue
            if remaining >= d["amount"]:
                d["held"] = True
                remaining -= d["amount"]
            else:
                # The pot covers part of this one: still outstanding, but the
                # merchant is not holding all of it.
                d["partially_held"] = True
                d["covered_amount"] = round(remaining, 2)
                remaining = 0.0


def build_disputes(date_from: str | date, date_to: str | date) -> dict:
    """
    Failed settlements for a date range, split by whether the money is held.

    Returns every failure, flagged -- rather than only the held ones -- because
    "this failed and the money is NOT held" is the answer to "where did it go",
    and hiding those rows just moves the question somewhere this app cannot
    answer.
    """
    rows = run_query(
        _DISPUTE_SQL,
        {
            "date_from": str(date_from),
            "date_to": str(date_to),
            "reprocess_to": str(_as_date(date_to) + timedelta(days=REPROCESS_WINDOW_DAYS)),
            "statuses": list(_FAILED_STATUSES),
        },
    )

    hints = _double_pay_hints()

    # The switch names the *merchant's* institution ("HAMRONEPAL", "SIMRIK"),
    # not the aggregator that owns it -- all three of those are Mbank. Ops
    # chases the aggregator, so resolve the MID through the same partner
    # mapping the batch flow uses and group on that. It is a dict lookup on the
    # first three digits: 1,315 MIDs resolve in under 2ms, against a 10s switch
    # query, so this costs nothing.
    resolver = PartnerResolver.load()

    # Operator decisions live in our own database, never on the switch. Loaded
    # once here rather than per row: a wide range is thousands of failures.
    decisions = {d.dispute_key: d for d in DisputeStatus.query.all()}
    scoped = {
        (d.scope_type, (d.scope_value or "").lower()): d
        for d in DisputeStatus.query.filter(DisputeStatus.scope_type.isnot(None)).all()
    }

    disputes: list[dict] = []
    seen_mids: set[str] = set()

    for r in rows:
        mid = (r.get("merchant_code") or "").strip()
        amount = _num(r.get("amount"))
        hold = _num(r.get("hold_balance"))
        total = _num(r.get("total_balance"))
        remark_blob = f"{r.get('remarks') or ''} {r.get('remark_two') or ''}".lower()
        double_pay_risk = any(h in remark_blob for h in hints)

        # Balance state, decided per merchant below. A merchant holding
        # nothing at all (hold and total both zero) has no money sitting
        # anywhere, so every settlement of theirs went through -- that is the
        # secondary confirmation that a failure is not a live dispute.
        settled_clear = hold == 0 and total == 0

        mapped_partner, partner_type = resolver.resolve(mid)
        # _decision_for reads this off the raw row, so stamp it there too.
        r["_mapped_partner"] = mapped_partner

        seen_mids.add(mid)
        disputes.append({
            "mapped_partner": mapped_partner,
            "partner_type": partner_type,
            "id": r.get("id"),
            "mid": mid,
            "merchant_name": r.get("merchant_name"),
            "crrn": r.get("crrn"),
            "stan": r.get("stan"),
            "ref_id": r.get("ref_id"),
            "amount": amount,
            "service_charge": _num(r.get("service_charge")),
            "date": str(r.get("date") or ""),
            "date_time": str(r.get("date_time") or ""),
            "status": r.get("status"),
            "current_status": r.get("current_status"),
            "status_code": r.get("status_code"),
            "reason": _reason(r),
            "remarks": r.get("remarks"),
            "remark_two": r.get("remark_two"),
            "partner": r.get("partner"),
            "acquirer_name": r.get("acquirer_name"),
            "bank_or_wallet": r.get("bank_name_or_wallet_name"),
            "wallet_code": r.get("wallet_code"),
            "institution_id": r.get("institution_id"),
            "settlement_frequency": r.get("settlement_frequency"),
            "medium": r.get("medium"),
            "creditor_name": r.get("creditor_name"),
            "creditor_account": r.get("creditor_account"),
            "creditor_mobile": r.get("creditor_mobile"),
            "bank_id": r.get("bank_id"),
            "branch_id": r.get("branch_id"),
            "partner_ref_id": r.get("partner_ref_id"),
            "member_code": r.get("member_code"),
            "total_balance": _num(r.get("total_balance")),
            "hold_balance": hold,
            "settled_clear": settled_clear,
            "double_pay_risk": double_pay_risk,
            "reprocessed_ok": bool(r.get("reprocessed_ok")),
            "reprocessed_at": str(r.get("reprocessed_at") or ""),
            **_decision_for(r, decisions, scoped),
        })

    _allocate_holds(disputes)

    # Excluded and already-reprocessed settlements are not outstanding work:
    # one was judged not ours to chase, the other already went through. Both
    # are still returned and counted separately so nothing vanishes silently.
    excluded = [d for d in disputes if d["op_status"] == "exclude"]
    reprocessed = [d for d in disputes if d["reprocessed_ok"] and d["op_status"] != "exclude"]
    live = [
        d for d in disputes
        if d["op_status"] != "exclude" and not d["reprocessed_ok"] and not d.get("likely_settled")
    ]

    held_rows = [d for d in live if d["held"] or d["partially_held"] or d["negative_hold"]]
    negative_hold = [d for d in live if d["negative_hold"]]
    at_risk = [d for d in held_rows if d["double_pay_risk"]]
    solved = [d for d in held_rows if d["op_status"] == "solved"]
    in_progress = [d for d in held_rows if d["op_status"] == "in_progress"]

    # Money at risk is what the hold actually covers, summed over the
    # settlements being chased -- not the raw hold balance, which can exceed
    # the failures it is standing against (a merchant holding 120 against a
    # single failed 100 is 100 in dispute, not 120).
    held_by_mid = {
        d["mid"]: d["hold_balance"] for d in held_rows
    }  # kept for the merchant count only
    likely_settled = [d for d in disputes if d.get("likely_settled") and d["op_status"] != "exclude"]

    by_partner: dict[str, dict] = {}
    for d in disputes:
        key = d["partner"] or d["acquirer_name"] or "Unmapped"
        b = by_partner.setdefault(key, {"partner": key, "count": 0, "amount": 0.0, "held": 0})
        b["count"] += 1
        b["amount"] += d["amount"]
        if d["held"] or d["partially_held"]:
            b["held"] += 1

    # Every headline number counts only failures still standing. Excluded,
    # already reprocessed, and ruled out by the merchant's balance are all
    # finished business: none is work outstanding, and leaving any of them in a
    # total only invites "these don't add up". `live` is exactly that set.
    counted = live

    return {
        "range": {"from": str(date_from), "to": str(date_to)},
        "totals": {
            "failed": len(counted),
            "merchants": len({d["mid"] for d in counted}),
            "failed_amount": round(sum(d["amount"] for d in counted), 2),
            "held_count": len(held_rows),
            "held_merchants": len(held_by_mid),
            "held_amount": round(sum(
                d["amount"] if d["held"] else d.get("covered_amount", 0.0) if not d["negative_hold"] else 0.0
                for d in held_rows
            ), 2),
            "at_risk_count": len(at_risk),
            "at_risk_amount": round(sum(d["amount"] for d in at_risk), 2),
            "excluded_count": len(excluded),
            "likely_settled_count": len(likely_settled),
            "negative_hold_count": len(negative_hold),
            "reprocessed_count": len(reprocessed),
            "solved_count": len(solved),
            "in_progress_count": len(in_progress),
            "open_count": len(held_rows) - len(solved),
        },
        "by_partner": sorted(by_partner.values(), key=lambda b: -b["amount"]),
        "disputes": disputes,
    }
