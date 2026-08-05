"""
Builds the dashboard data shape for a batch: top-level totals, aggregator
summary cards, bank/wallet summary cards, SCT summary, and the full issue
list (each issue = one expandable card with a status dropdown + comment,
per your spec).

Kept as pure aggregation over already-classified Transaction rows -- no
re-classification happens here, that already happened at ingest time.
"""
from collections import defaultdict

from app.extensions import db
from app.models.transaction import Transaction
from app.models.issue_status import IssueStatus


def build_dashboard(batch_id: int) -> dict:
    transactions = Transaction.query.filter_by(batch_id=batch_id).all()
    issue_statuses = {
        (i.side, i.partner_name, i.category, i.txn_status): i
        for i in IssueStatus.query.filter_by(batch_id=batch_id).all()
    }

    totals = _build_totals(transactions, issue_statuses)
    aggregator_summary = _build_partner_summary(transactions, issue_statuses, bucket="aggregator", batch_id=batch_id)
    bank_summary = _build_partner_summary(transactions, issue_statuses, bucket="bank_wallet", batch_id=batch_id)
    sct_summary = _build_sct_summary(transactions, issue_statuses, batch_id=batch_id)

    return {
        "totals": totals,
        "aggregator_summary": aggregator_summary,
        "bank_summary": bank_summary,
        "sct_summary": sct_summary,
    }


def _get_last_solved_comment(side: str, partner_name: str | None, category: str, txn_status: str, current_batch_id: int) -> str | None:
    prev = (
        IssueStatus.query
        .filter(
            IssueStatus.side == side,
            IssueStatus.partner_name == partner_name,
            IssueStatus.category == category,
            IssueStatus.txn_status == txn_status,
            IssueStatus.status == "solved",
            IssueStatus.batch_id != current_batch_id,
            IssueStatus.comment != None,
            IssueStatus.comment != ""
        )
        .order_by(IssueStatus.updated_at.desc())
        .first()
    )
    return prev.comment if prev else None


def _build_totals(transactions: list[Transaction], issue_statuses: dict) -> dict:
    total = len(transactions)
    # Count by transaction status instead of issue_statuses? The user wants to see how many transactions failed/pending.
    pending = sum(1 for t in transactions if (t.status or "").lower() == "pending")
    failed = sum(1 for t in transactions if (t.status or "").lower() == "failed")
    lo_progress = sum(1 for t in transactions if (t.status or "").lower() in ["lo progress", "lo", "lo_progress"])
    success_count = sum(1 for t in transactions if (t.status or "").lower() == "success")

    settlement_failed = sum(1 for t in transactions if t.error_side in ("aggregator", "bank") and (t.status or "").lower() != "success")
    transaction_failed = sum(1 for t in transactions if t.error_side == "sct" and (t.status or "").lower() != "success")
    no_aggregator = sum(1 for t in transactions if t.partner_name == "No Aggregator")
    total_aggregators = len({t.partner_name for t in transactions if t.partner_type == "aggregator"})

    return {
        "total_transactions": total,
        "pending": pending,
        "failed": failed,
        "settlement_failed": settlement_failed,
        "transaction_failed": transaction_failed,
        "no_aggregator": no_aggregator,
        "total_aggregators": total_aggregators,
        "lo_progress": lo_progress,
        "success_issues": success_count,
    }


def _build_partner_summary(transactions: list[Transaction], issue_statuses: dict, bucket: str, batch_id: int) -> list[dict]:
    by_partner: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        if t.partner_type == bucket:
            by_partner[t.partner_name].append(t)

    result = []
    for partner_name, rows in sorted(by_partner.items()):
        by_txn_status: dict[str, list[Transaction]] = defaultdict(list)
        for r in rows:
            s = (r.status.lower().replace(" ", "_") if r.status else "unknown")
            by_txn_status[s].append(r)

        def build_issues_for_status(txn_status_name, status_rows):
            by_category: dict[str, list[Transaction]] = defaultdict(list)
            for r in status_rows:
                by_category[r.error_category].append(r)

            issues = []
            for category, cat_rows in sorted(by_category.items()):
                side = cat_rows[0].error_side
                status_row = issue_statuses.get((side, partner_name, category, txn_status_name))
                # Auto-create IssueStatus row if it doesn't exist yet and not success
                if not status_row and txn_status_name != "success":
                    status_row = IssueStatus(
                        batch_id=batch_id,
                        side=side,
                        partner_name=partner_name,
                        category=category,
                        txn_status=txn_status_name,
                        status="pending",
                    )
                    db.session.add(status_row)
                    db.session.flush()  # get the id
                    issue_statuses[(side, partner_name, category, txn_status_name)] = status_row
                
                if status_row:
                    issues.append({
                        "id": status_row.id,
                        "side": side,
                        "category": category,
                        "txn_status": txn_status_name,
                        "count": len(cat_rows),
                        "affected_mids": sorted({r.mid for r in cat_rows if r.mid}),
                        "status": status_row.status,
                        "comment": status_row.comment,
                        "mid_overrides": status_row.mid_overrides or {},
                        "last_solved_comment": _get_last_solved_comment(side, partner_name, category, txn_status_name, batch_id),
                    })
            return sorted(issues, key=lambda i: -i["count"])

        failed_count = len(by_txn_status.get("failed", []))
        pending_count = len(by_txn_status.get("pending", []))
        lo_progress_count = len(by_txn_status.get("lo_progress", []))

        result.append({
            "partner_name": partner_name,
            "failed": failed_count,
            "pending": pending_count,
            "lo_progress": lo_progress_count,
            "issues_failed": build_issues_for_status("failed", by_txn_status.get("failed", [])),
            "issues_pending": build_issues_for_status("pending", by_txn_status.get("pending", [])),
            "issues_lo_progress": build_issues_for_status("lo_progress", by_txn_status.get("lo_progress", [])),
        })

    # Commit any newly created IssueStatus rows
    db.session.commit()

    return sorted(result, key=lambda p: -(p["failed"] + p["pending"] + p["lo_progress"]))


def _build_sct_summary(transactions: list[Transaction], issue_statuses: dict, batch_id: int) -> dict:
    sct_rows = [t for t in transactions if t.error_side == "sct"]

    by_txn_status: dict[str, list[Transaction]] = defaultdict(list)
    for r in sct_rows:
        s = (r.status.lower().replace(" ", "_") if r.status else "unknown")
        by_txn_status[s].append(r)
        
    def build_issues_for_status(txn_status_name, status_rows):
        by_category: dict[str, list[Transaction]] = defaultdict(list)
        for r in status_rows:
            by_category[r.error_category].append(r)

        issues = []
        for category, cat_rows in sorted(by_category.items()):
            side = cat_rows[0].error_side
            status_row = issue_statuses.get((side, None, category, txn_status_name))
            # Auto-create IssueStatus row if it doesn't exist yet and not success
            if not status_row and txn_status_name != "success":
                status_row = IssueStatus(
                    batch_id=batch_id,
                    side=side,
                    partner_name=None,
                    category=category,
                    txn_status=txn_status_name,
                    status="pending",
                )
                db.session.add(status_row)
                db.session.flush()  # get the id
                issue_statuses[(side, None, category, txn_status_name)] = status_row
            
            if status_row:
                issues.append({
                    "id": status_row.id,
                    "side": side,
                    "category": category,
                    "txn_status": txn_status_name,
                    "count": len(cat_rows),
                    "affected_mids": sorted({r.mid for r in cat_rows if r.mid}),
                    "status": status_row.status,
                    "comment": status_row.comment,
                    "mid_overrides": status_row.mid_overrides or {},
                    "last_solved_comment": _get_last_solved_comment(side, None, category, txn_status_name, batch_id),
                })
        return sorted(issues, key=lambda i: -i["count"])

    # Commit any newly created IssueStatus rows
    db.session.commit()

    return {
        "total_issues": len(sct_rows),
        "issues_failed": build_issues_for_status("failed", by_txn_status.get("failed", [])),
        "issues_pending": build_issues_for_status("pending", by_txn_status.get("pending", [])),
        "issues_lo_progress": build_issues_for_status("lo_progress", by_txn_status.get("lo_progress", [])),
    }
