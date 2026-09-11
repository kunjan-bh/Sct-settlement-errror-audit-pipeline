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

# Every status that means the money has not reached the merchant. Named for
# what they have in common rather than "failed", because IN_PROGRESS is not a
# failure -- it is a settlement stuck part-way, which is exactly the thing
# worth chasing when the amount is sitting in the merchant's hold.
#
# `status` is the coarse verdict and `current_status` is where in the transfer
# it stopped. Ops needs the second one -- "FUND_TRANSFER_PENDING" and
# "VALIDATION_FAILED" are chased completely differently.
_UNSETTLED_STATUSES = ("FAILED", "PENDING", "IN_PROGRESS")

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
_SELECT_COLUMNS = """
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
"""

# Settlements someone is still working on, pulled in by id no matter how old
# they are. Without this, an unfinished dispute silently drops off the list as
# soon as the date window moves past it -- the work is still outstanding, it is
# just invisible, which is the worst of both.
_CARRIED_SQL = f"""
WITH bal AS (
    SELECT merchant_code, member_code, total_balance, hold_balance
    FROM supports.get_merchant_balance()
),
ok AS (
    SELECT merchant_code, amount, min(date_time) AS first_ok
    FROM operators.fund_transfer_logs
    WHERE date BETWEEN %(ok_from)s AND %(reprocess_to)s AND status = 'SUCCESS'
    GROUP BY merchant_code, amount
)
SELECT {_SELECT_COLUMNS}
FROM operators.fund_transfer_logs f
LEFT JOIN bal b ON b.merchant_code = f.merchant_code
LEFT JOIN ok ON ok.merchant_code = f.merchant_code AND ok.amount = f.amount
WHERE f.id = ANY(%(ids)s)
"""

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
    # Some rows carry a pipe-joined trace (mid|stan|rrn) where the message
    # should be. That is an identifier, not a reason, and reads as noise in a
    # list someone is triaging.
    looks_like_trace = "|" in remark and " " not in remark
    if remark and not looks_like_trace and remark.lower() not in ("failed", "-", "n/a", "null"):
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
    Decide, per merchant, which of their failures the money still sitting on
    them accounts for -- setting `held`, `partially_held` and `likely_settled`
    in place.

    The pot is `total_balance`, not `hold_balance`. The switch only sometimes
    flags unsettled money as held: across a two-day range, 82 failures had a
    zero hold while the merchant's total balance was at least the failed
    amount, and in 72 of those the total matched the failed amount to the
    rupee. A merchant's ordinary float does not equal a failed settlement
    exactly 72 times -- that is the unsettled money, sitting in the balance
    without the hold flag. `hold_balance` stays on the row as the stronger
    signal when the switch does set it.

    The balance is one pot per merchant, not one per failure: two failed
    settlements of NPR 1,000 against a NPR 1,000 balance is one outstanding
    failure and one that already went through. Allocation runs most recent
    first, because an older failure has had longer to be retried, swept by a
    system-default run, or settled on call. Whatever the pot does not reach is
    marked `likely_settled` and drops out.

    This is inference, not something the switch states -- nothing links a
    balance to the settlement that caused it.
    """
    by_mid: dict[str, list[dict]] = {}
    for d in disputes:
        by_mid.setdefault(d["mid"], []).append(d)

    for rows in by_mid.values():
        # Excluded rows do not consume the pot -- they are not being chased.
        #
        # Reprocessed ones DO. "Reprocessed" is inferred from a later success
        # for the same merchant and amount, and two settlements of the same
        # size on one day are common: MID 008000001384524 had a PENDING 2,500
        # at 07:19 and a SUCCESS 2,500 at 07:25, and was ruled out -- while
        # still holding the 2,500. The balance is the harder evidence, so it
        # gets to overrule the guess.
        live = [
            d for d in rows
            # Entity-level exclusions (a whole wallet) drop out entirely. A
            # row someone excluded by hand still holds money, so it still
            # consumes the pot and still gets its flags -- it belongs in the
            # Excluded section, not nowhere.
            if not (d["op_status"] == "exclude" and d["op_scope"])
        ]
        for d in rows:
            d.setdefault("held", False)
            d.setdefault("partially_held", False)
            d.setdefault("likely_settled", False)
            d.setdefault("negative_hold", False)
        if not live:
            continue

        # A negative hold is not "no money held", it is a broken ledger: in
        # every case seen the hold is exactly minus the total balance, which
        # looks like a hold released twice. Certainly not evidence the
        # settlement went through, so these surface rather than being ruled out.
        if live[0]["hold_balance"] < 0:
            for d in live:
                d["negative_hold"] = True
            continue

        # Nothing sitting anywhere -- every settlement for this merchant went
        # out. This is the case `settled_clear` records.
        pot = live[0]["total_balance"]
        if pot <= 0:
            continue

        # Anything an operator has already ruled on keeps its claim on the pot,
        # ahead of date order. Without this a failure arriving this afternoon
        # takes the balance from one someone spent the morning investigating,
        # and that case disappears off the list mid-investigation.
        remaining = pot
        ordered = sorted(
            live,
            key=lambda x: (x["op_status"] != "pending", x["date_time"]),
            reverse=True,
        )
        for d in ordered:
            if remaining <= 0:
                d["likely_settled"] = True
                continue
            if remaining >= d["amount"]:
                d["held"] = True
                remaining -= d["amount"]
            else:
                # The pot covers part of this one: still outstanding, but not
                # all of it is still sitting there.
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
    reprocess_to = str(_as_date(date_to) + timedelta(days=REPROCESS_WINDOW_DAYS))
    rows = run_query(
        _DISPUTE_SQL,
        {
            "date_from": str(date_from),
            "date_to": str(date_to),
            "reprocess_to": reprocess_to,
            "statuses": list(_UNSETTLED_STATUSES),
        },
    )

    # Carry forward anything still being worked on. A dispute picked up
    # yesterday is still outstanding today, and letting the date window hide it
    # loses the work rather than finishing it. Only unfinished decisions are
    # carried: solved and excluded ones are done, and dragging them along
    # forever would grow the list without end.
    in_range = {str(r.get("id")) for r in rows}
    carried_keys = [
        s.dispute_key
        for s in DisputeStatus.query.filter(
            DisputeStatus.status == "in_progress",
            DisputeStatus.scope_type.is_(None),
        ).all()
        if s.dispute_key not in in_range
    ]
    carried_ids = set()
    if carried_keys:
        extra = run_query(
            _CARRIED_SQL,
            {
                "ids": carried_keys,
                # The reprocess check needs to reach back to when these failed,
                # not just into the window being viewed.
                "ok_from": str(min(_as_date(date_from), date.today() - timedelta(days=90))),
                "reprocess_to": reprocess_to,
            },
        )
        carried_ids = {str(r.get("id")) for r in extra}
        rows = rows + extra

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
            "carried_over": str(r.get("id")) in carried_ids,
            "reprocessed_ok": bool(r.get("reprocessed_ok")),
            "reprocessed_at": str(r.get("reprocessed_at") or ""),
            **_decision_for(r, decisions, scoped),
        })

    _allocate_holds(disputes)

    # The balance overrules the reprocess guess. If the hold still reaches a
    # settlement, the money is on the merchant whatever a later same-amount
    # success suggests, so it goes back on the list.
    for d in disputes:
        if d["reprocessed_ok"] and (d["held"] or d["partially_held"]):
            d["reprocessed_ok"] = False
            d["why_listed"] = (
                "A later settlement of the same amount suggested this was "
                "reprocessed, but the merchant is still holding the money."
            )

    # Excluded and already-reprocessed settlements are not outstanding work:
    # one was judged not ours to chase, the other already went through. Both
    # are still returned and counted separately so nothing vanishes silently.
    # Two different things wear the "exclude" status. A standing rule about a
    # wallet removes its rows from the page; a decision about one settlement
    # files it under Excluded where it stays visible.
    entity_excluded = [d for d in disputes if d["op_status"] == "exclude" and d["op_scope"]]
    excluded = entity_excluded
    # These three count what was ruled out and is therefore NOT listed. A row
    # someone has ruled on is listed in its own section, so counting it here
    # too would double it and the figures would stop adding up.
    def _ruled_out(d) -> bool:
        return d["op_status"] == "pending" and not (d["op_status"] == "exclude" and d["op_scope"])

    reprocessed = [d for d in disputes if d["reprocessed_ok"] and _ruled_out(d)]
    live = [
        d for d in disputes
        if not (d["op_status"] == "exclude" and d["op_scope"])
        and (
            # A settlement someone has ruled on stays visible in its section,
            # whatever the balance says now. Solving a dispute is what makes
            # the money leave, so the balance check would rule out every
            # dispute the moment it was solved and the Solved tab would sit
            # empty however much work had gone through it.
            d["op_status"] != "pending"
            or (
                not d["reprocessed_ok"]
                and not d.get("likely_settled")
                # Hold and total both zero: nothing is sitting anywhere, so
                # every settlement for this merchant went out.
                and not (d["settled_clear"] and not d["negative_hold"])
            )
        )
    ]

    held_rows = [
        d for d in live
        if d["held"] or d["partially_held"] or d["negative_hold"] or d["op_status"] != "pending"
    ]
    negative_hold = [d for d in live if d["negative_hold"]]

    # The work queue: one bucket per operator decision, so clicking a status
    # moves a row from one section to the next instead of leaving it in place
    # for someone to re-read.
    def _bucket(status: str) -> list[dict]:
        return [d for d in held_rows if d["op_status"] == status]

    pending_rows = _bucket("pending")
    # Outstanding work is what is still to be done. A solved dispute is not a
    # dispute any more and its money has left the merchant; an excluded one was
    # judged not ours. Counting either as outstanding overstates both the queue
    # and the money at stake.
    outstanding_rows = [d for d in held_rows if d["op_status"] in ("pending", "in_progress")]
    in_progress_rows = _bucket("in_progress")
    solved_rows = _bucket("solved")
    row_excluded_rows = _bucket("exclude")
    # Still needing aggregator verification -- once solved or excluded it does
    # not, so those drop off this count.
    at_risk = [
        d for d in held_rows
        if d["double_pay_risk"] and d["op_status"] in ("pending", "in_progress")
    ]
    solved = solved_rows
    in_progress = in_progress_rows

    # Money at risk is what the hold actually covers, summed over the
    # settlements being chased -- not the raw hold balance, which can exceed
    # the failures it is standing against (a merchant holding 120 against a
    # single failed 100 is 100 in dispute, not 120).
    held_by_mid = {
        d["mid"]: d["hold_balance"] for d in outstanding_rows
    }  # kept for the merchant count only
    likely_settled = [
        d for d in disputes
        if d.get("likely_settled") and _ruled_out(d) and not d["reprocessed_ok"]
    ]
    # Nothing sitting on the merchant at all. Counted so the figures reconcile:
    # every row from the switch is either listed or in one of these buckets.
    settled_clear = [
        d for d in disputes
        if d["settled_clear"] and not d["negative_hold"] and _ruled_out(d)
        and not d["reprocessed_ok"] and not d.get("likely_settled")
    ]

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
    counted = outstanding_rows

    return {
        "range": {"from": str(date_from), "to": str(date_to)},
        "totals": {
            "failed": len(counted),
            "merchants": len({d["mid"] for d in counted}),
            "failed_amount": round(sum(d["amount"] for d in counted), 2),
            "held_count": len(outstanding_rows),
            "listed_count": len(held_rows),
            "held_merchants": len(held_by_mid),
            "held_amount": round(sum(
                d["amount"] if d["held"] else d.get("covered_amount", 0.0) if not d["negative_hold"] else 0.0
                for d in outstanding_rows
            ), 2),
            "at_risk_count": len(at_risk),
            "at_risk_amount": round(sum(d["amount"] for d in at_risk), 2),
            "excluded_count": len(excluded),
            "likely_settled_count": len(likely_settled),
            "settled_clear_count": len(settled_clear),
            "negative_hold_count": len(negative_hold),
            "carried_over_count": len([d for d in held_rows if d["carried_over"]]),
            "reprocessed_count": len(reprocessed),
            "solved_count": len(solved),
            "in_progress_count": len(in_progress),
            "pending_count": len(pending_rows),
            "row_excluded_count": len(row_excluded_rows),
        },
        "by_partner": sorted(by_partner.values(), key=lambda b: -b["amount"]),
        "disputes": disputes,
    }
