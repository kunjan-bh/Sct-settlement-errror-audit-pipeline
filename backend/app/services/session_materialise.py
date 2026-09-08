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
_STATUS_MAP = {"FAILED": "Failed", "PENDING": "Pending"}


def _txn_status_for(raw: str | None) -> str:
    return _STATUS_MAP.get((raw or "").strip().upper(), (raw or "Failed").title())


def materialise_session(batch_id: int) -> dict:
    """
    Rebuild a session batch's transactions and issue statuses from its
    decisions. Safe to run repeatedly -- it clears what it wrote last time
    first, so closing, reopening and re-closing a session cannot double it up.
    """
    batch = Batch.query.get_or_404(batch_id)
    decisions = (
        DisputeStatus.query
        .filter(DisputeStatus.batch_id == batch_id, DisputeStatus.scope_type.is_(None))
        .all()
    )
    by_key = {d.dispute_key: d for d in decisions}
    if not by_key:
        return {"transactions": 0, "issues": 0}

    # The switch holds the settlement itself; we only stored the decision.
    rows = run_query(
        _CARRIED_SQL,
        {
            "ids": list(by_key.keys()),
            "ok_from": str(date.today() - timedelta(days=120)),
            "reprocess_to": str(date.today() + timedelta(days=1)),
        },
    )

    Transaction.query.filter_by(batch_id=batch_id).delete(synchronize_session=False)
    IssueStatus.query.filter_by(batch_id=batch_id).delete(synchronize_session=False)
    db.session.flush()

    rules = RuleEngine.load()
    resolver = PartnerResolver.load()
    issues: dict[tuple, IssueStatus] = {}
    made = 0

    for r in rows:
        decision = by_key.get(str(r.get("id")))
        if decision is None:
            continue

        mid = (r.get("merchant_code") or "").strip()
        remark = r.get("remarks") or _reason(r)
        result = rules.classify_row(remark)
        partner_name, bucket = resolver.resolve(mid)
        txn_status_label = _txn_status_for(r.get("status"))
        txn_status = normalize_txn_status(txn_status_label)

        db.session.add(Transaction(
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
            retry_resolved=False,
            # Keeps CRRN and the balances in the report's raw-row sheet, which
            # is what gets forwarded to an aggregator.
            extra_data={
                "CRRN": r.get("crrn"),
                "STAN": r.get("stan"),
                "Ref ID": r.get("ref_id"),
                "Hold Balance": str(r.get("hold_balance") or ""),
                "Total Balance": str(r.get("total_balance") or ""),
                "Stopped At": r.get("current_status"),
                "Bank/Wallet": r.get("bank_name_or_wallet_name"),
            },
        ))
        made += 1

        key = (result.side, issue_partner_key(partner_name, bucket), result.category, txn_status)
        issue = issues.get(key)
        if issue is None:
            issue = IssueStatus(
                batch_id=batch_id, side=key[0], partner_name=key[1],
                category=key[2], txn_status=key[3],
                status="pending", mid_overrides={},
            )
            issues[key] = issue
            db.session.add(issue)

        # The decision was taken per settlement, but IssueStatus is per issue
        # group. Record it as a per-MID override so a group holding two
        # different decisions keeps both instead of one silently winning.
        overrides = dict(issue.mid_overrides or {})
        overrides[mid] = {"status": decision.status, "comment": decision.comment}
        issue.mid_overrides = overrides
        if decision.comment and not issue.comment:
            issue.comment = decision.comment

    # Where every MID in a group agrees, lift it to the group so the dashboard
    # shows one decided card rather than a pending card full of overrides.
    for issue in issues.values():
        statuses = {v.get("status") for v in (issue.mid_overrides or {}).values()}
        if len(statuses) == 1:
            issue.status = statuses.pop()

    batch.finished_at = batch.finished_at or datetime.utcnow()
    db.session.commit()
    return {"transactions": made, "issues": len(issues)}
