"""
Builds the dashboard data shape for a batch: top-level totals, aggregator
summary cards, bank/wallet summary cards, SCT summary, and the full issue
list (each issue = one expandable card with a status dropdown + comment,
per your spec).

Kept as pure aggregation over already-classified Transaction rows -- no
re-classification happens here, that already happened at ingest time.
"""
from collections import defaultdict

from app.models.transaction import Transaction
from app.models.issue_status import IssueStatus


def build_dashboard(batch_id: int) -> dict:
    transactions = Transaction.query.filter_by(batch_id=batch_id).all()
    issue_statuses = {
        (i.side, i.partner_name, i.category): i
        for i in IssueStatus.query.filter_by(batch_id=batch_id).all()
    }

    totals = _build_totals(transactions)
    aggregator_summary = _build_partner_summary(transactions, issue_statuses, bucket="aggregator")
    bank_summary = _build_partner_summary(transactions, issue_statuses, bucket="bank_wallet")
    sct_summary = _build_sct_summary(transactions, issue_statuses)

    return {
        "totals": totals,
        "aggregator_summary": aggregator_summary,
        "bank_summary": bank_summary,
        "sct_summary": sct_summary,
    }


def _build_totals(transactions: list[Transaction]) -> dict:
    total = len(transactions)
    # Your real file is pre-filtered to Failed rows only, so "pending" will
    # read 0 against it -- that's correct given the input, not a bug. Once
    # a file with mixed statuses comes through, this counts it properly.
    pending = sum(1 for t in transactions if (t.status or "").lower() == "pending")
    failed = sum(1 for t in transactions if (t.status or "").lower() == "failed")
    settlement_failed = sum(1 for t in transactions if t.error_side in ("aggregator", "bank"))
    transaction_failed = sum(1 for t in transactions if t.error_side == "sct")
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
    }


def _build_partner_summary(transactions: list[Transaction], issue_statuses: dict, bucket: str) -> list[dict]:
    by_partner: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        if t.partner_type == bucket:
            by_partner[t.partner_name].append(t)

    result = []
    for partner_name, rows in sorted(by_partner.items()):
        by_category: dict[str, list[Transaction]] = defaultdict(list)
        for r in rows:
            by_category[r.error_category].append(r)

        issues = []
        for category, cat_rows in sorted(by_category.items()):
            side = cat_rows[0].error_side
            status_row = issue_statuses.get((side, partner_name, category))
            issues.append({
                "id": status_row.id if status_row else None,
                "side": side,
                "category": category,
                "count": len(cat_rows),
                "affected_mids": sorted({r.mid for r in cat_rows if r.mid})[:20],
                "status": status_row.status if status_row else "pending",
                "comment": status_row.comment if status_row else None,
            })

        failed_count = sum(1 for r in rows if (r.status or "").lower() == "failed")
        pending_count = sum(1 for r in rows if (r.status or "").lower() == "pending")

        result.append({
            "partner_name": partner_name,
            "failed": failed_count,
            "pending": pending_count,
            "issue_count": len(issues),
            "issues": sorted(issues, key=lambda i: -i["count"]),
        })

    return sorted(result, key=lambda p: -(p["failed"] + p["pending"]))


def _build_sct_summary(transactions: list[Transaction], issue_statuses: dict) -> dict:
    sct_rows = [t for t in transactions if t.error_side == "sct"]

    by_category: dict[str, list[Transaction]] = defaultdict(list)
    for r in sct_rows:
        by_category[r.error_category].append(r)

    issues = []
    for category, rows in sorted(by_category.items()):
        status_row = issue_statuses.get(("sct", None, category))
        issues.append({
            "id": status_row.id if status_row else None,
            "category": category,
            "count": len(rows),
            "affected_mids": sorted({r.mid for r in rows if r.mid})[:20],
            "status": status_row.status if status_row else "pending",
            "comment": status_row.comment if status_row else None,
        })

    return {
        "total_issues": len(sct_rows),
        "issues": sorted(issues, key=lambda i: -i["count"]),
    }
