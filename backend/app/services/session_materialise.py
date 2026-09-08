"""
Turn a closed session's dispute decisions into Transaction rows.

Everything downstream of a batch -- the dashboard, the five-sheet report with
its Error Classify charts, analytics, and the summary email with its donut and
volume bars -- reads Transaction rows. A session had none, so all of it
rendered zero.

Rather than write a second dashboard, a second report and a second email for
sessions, a closed session materialises the settlements it handled as ordinary
transactions on that batch. Every existing view then works on a session exactly
as it does on an uploaded batch, which is what was asked for: the same report,
not a different one.

The switch stays the source of truth for what the settlement *was*; what we add
is what the operator decided about it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.extensions import db
from app.models.batch import Batch
from app.models.dispute_status import DisputeStatus
from app.models.issue_status import IssueStatus
from app.models.transaction import Transaction
from app.services.classification_service import PartnerResolver, RuleEngine
from app.services.core_db import run_query
from app.services.dispute_service import _CARRIED_SQL, _reason
from app.services.status_utils import issue_partner_key, normalize_txn_status

# fund_transfer_logs statuses mapped onto the batch flow's vocabulary.
_STATUS_MAP = {"FAILED": "Failed", "PENDING": "Pending", "SUCCESS": "Success"}

# The whole day's settlements, not just the ones someone touched. An upload
# batch was a settlement export -- every row for the period, with the failures
# picked out of it -- so a session has to be the same or "Total Txns" reports
# the size of the operator's to-do list instead of the day's volume.
_DAY_SQL = """
WITH bal AS (
    SELECT merchant_code, member_code, total_balance, hold_balance
    FROM supports.get_merchant_balance()
)
SELECT
    f.id, f.merchant_code, f.merchant_name, f.crrn, f.stan, f.ref_id,
    f.amount, f.service_charge, f.date, f.date_time,
    f.status, f.current_status, f.status_code, f.remarks, f.remark_two,
    f.partner, f.acquirer_name, f.bank_name_or_wallet_name,
    f.settlement_frequency, f.creditor_account, f.creditor_name,
    b.total_balance, b.hold_balance
FROM operators.fund_transfer_logs f
LEFT JOIN bal b ON b.merchant_code = f.merchant_code
WHERE f.date BETWEEN %(date_from)s AND %(date_to)s
"""


def _txn_status_for(raw: str | None) -> str:
    return _STATUS_MAP.get((raw or "").strip().upper(), (raw or "Failed").title())


def _session_span(batch: Batch, keys: list[str]) -> tuple[str, str]:
    """
    The days a session covers: the span of the settlements it ruled on, or the
    day it ran if it ruled on nothing. Derived rather than stored, because the
    operator never says "I am working on the 7th" -- they just work.
    """
    if keys:
        r = run_query(
            "SELECT min(date) AS lo, max(date) AS hi FROM operators.fund_transfer_logs "
            "WHERE id = ANY(%(ids)s)",
            {"ids": keys},
        )
        if r and r[0].get("lo"):
            return str(r[0]["lo"]), str(r[0]["hi"])
    day = (batch.created_at or datetime.utcnow()).date()
    return str(day), str(day)


def materialise_session(batch_id: int) -> dict:
    """
    Rebuild a session batch from the switch: every settlement for the days it
    covers, with the operator's decisions applied on top.

    This is the ingest step the upload used to do, sourced from the database
    the export came from. Safe to run repeatedly -- it clears what it wrote
    last time, so closing and re-closing a session cannot double it up.
    """
    batch = Batch.query.get_or_404(batch_id)
    decisions = (
        DisputeStatus.query
        .filter(DisputeStatus.batch_id == batch_id, DisputeStatus.scope_type.is_(None))
        .all()
    )
    by_key = {d.dispute_key: d for d in decisions}

    # Standing wallet/aggregator rules count too. An aggregator excluded on the
    # Disputes page is excluded here as well, or the report calls 1,109
    # settlements unsolved that were deliberately taken off the list.
    scopes = {
        (s.scope_type, (s.scope_value or "").lower()): s
        for s in DisputeStatus.query.filter(DisputeStatus.scope_type.isnot(None)).all()
    }
    date_from, date_to = _session_span(batch, list(by_key.keys()))

    rows = run_query(_DAY_SQL, {"date_from": date_from, "date_to": date_to})

    # The Disputes page rules a failure out when it was reprocessed, or when
    # the merchant's balance shows the money went out. Those are settled, not
    # outstanding. Without this the batch called 127 of them unsolved while the
    # Disputes page listed 2 as open -- the same day's work, two answers.
    #
    # They land on `retry_resolved`, which is exactly this idea in the batch
    # flow ("Reprocessed & Settled") and is already excluded from every count
    # and chart.
    from app.services.dispute_service import build_disputes

    resolved_ids = {
        str(d["id"])
        for d in build_disputes(date_from, date_to)["disputes"]
        if d["reprocessed_ok"] or d["likely_settled"]
        or (d["settled_clear"] and not d["negative_hold"])
    }
    # A settlement someone ruled on keeps their decision. Solving a dispute is
    # what makes the money leave, so sweeping it into "reprocessed & settled"
    # would credit the switch for work the operator did and report 15 solved
    # where 77 were.
    resolved_ids -= set(by_key.keys())

    Transaction.query.filter_by(batch_id=batch_id).delete(synchronize_session=False)
    IssueStatus.query.filter_by(batch_id=batch_id).delete(synchronize_session=False)
    db.session.flush()

    rules = RuleEngine.load()
    resolver = PartnerResolver.load()
    issues: dict[tuple, IssueStatus] = {}
    overrides: dict[tuple, dict] = {}
    comments: dict[tuple, dict] = {}
    txns = []

    for r in rows:
        mid = (r.get("merchant_code") or "").strip()
        remark = r.get("remarks") or _reason(r)
        result = rules.classify_row(remark)
        partner_name, bucket = resolver.resolve(mid)
        txn_status_label = _txn_status_for(r.get("status"))
        txn_status = normalize_txn_status(txn_status_label)

        txns.append(Transaction(
            batch_id=batch_id,
            mid=mid,
            merchant_name=r.get("merchant_name"),
            status=txn_status_label,
            status_code=r.get("status_code"),
            remark=remark,
            txn_amount=r.get("amount"),
            settled_by=(r.get("settlement_frequency") or "").replace("_", " ").title() or None,
            beneficiary_id=r.get("creditor_account"),
            txn_datetime=r.get("date_time"),
            partner_name=partner_name,
            partner_type=bucket,
            error_side=result.side,
            error_category=result.category,
            matched_rule_id=result.matched_rule_id,
            retry_resolved=str(r.get("id")) in resolved_ids,
            extra_data={
                "CRRN": r.get("crrn"),
                "STAN": r.get("stan"),
                "Ref ID": r.get("ref_id"),
                "Stopped At": r.get("current_status"),
                "Bank/Wallet": r.get("bank_name_or_wallet_name"),
                "Hold Balance": str(r.get("hold_balance") or ""),
                "Total Balance": str(r.get("total_balance") or ""),
            },
        ))

        if txn_status == "success" or str(r.get("id")) in resolved_ids:
            continue

        key = (result.side, issue_partner_key(partner_name, bucket), result.category, txn_status)
        if key not in issues:
            issues[key] = IssueStatus(
                batch_id=batch_id, side=key[0], partner_name=key[1],
                category=key[2], txn_status=key[3], status="pending", mid_overrides={},
            )
            overrides[key] = {}
            comments[key] = {}

        decision = by_key.get(str(r.get("id")))
        if decision is None and scopes:
            # Same precedence as the Disputes page: most specific first.
            for scope_type, value in (
                ("mapped_partner", partner_name),
                ("partner", r.get("partner")),
                ("bank_or_wallet", r.get("bank_name_or_wallet_name")),
                ("acquirer", r.get("acquirer_name")),
                ("mid", mid),
            ):
                decision = scopes.get((scope_type, (value or "").lower()))
                if decision is not None:
                    break

        # Every failure in the group gets an effective status, decided or not.
        # Knowing the undecided ones is what lets the card take a real status
        # below instead of sitting at pending with a pile of overrides.
        overrides[key][mid] = decision.status if decision is not None else "pending"
        if decision is not None and decision.comment:
            comments[key][mid] = decision.comment
            if not issues[key].comment:
                issues[key].comment = decision.comment

    db.session.bulk_save_objects(txns)
    for key, issue in issues.items():
        per_mid = overrides[key]

        # The card takes whatever most of its MIDs are, and only the ones that
        # differ get an override. Lifting only on unanimity left a group where
        # four of five were solved sitting at "pending" behind four overrides,
        # so the cards read pending all day however much had been done.
        counts: dict[str, int] = {}
        for status in per_mid.values():
            counts[status] = counts.get(status, 0) + 1
        # Ties go to the more advanced status: a group half solved reads better
        # as solved-with-exceptions than as pending-with-exceptions.
        rank = {"pending": 0, "in_progress": 1, "exclude": 2, "solved": 3}
        issue.status = max(counts, key=lambda s: (counts[s], rank.get(s, 0)))

        issue.mid_overrides = {
            mid: {"status": status, "comment": comments[key].get(mid)}
            for mid, status in per_mid.items()
            if status != issue.status
        }
        db.session.add(issue)

    batch.finished_at = batch.finished_at or datetime.utcnow()
    db.session.commit()
    return {
        "range": f"{date_from}..{date_to}",
        "transactions": len(txns),
        "issues": len(issues),
        "decisions_applied": sum(len(v) for v in overrides.values()),
    }
