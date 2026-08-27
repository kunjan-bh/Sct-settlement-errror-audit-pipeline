"""
Cross-batch analytics: same "what broke" and "is it solved" logic as the
single-batch report and Error Classification views (build_error_classification,
report_generator.eff_status), aggregated across every batch whose processing
date (Batch.created_at) falls in a requested range, bucketed for a trend chart.

Deliberately reuses _entity_of, normalize_txn_status and ISSUE_BUCKETS instead
of reclassifying anything -- same definitions everywhere in the app. The one
thing this does NOT reuse is dashboard_service's per-issue
_get_last_solved_comment lookup (N+1 queries) or its full-ORM-object loading:
a date range can span many batches at once, so this only pulls the columns it
actually needs.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models.batch import Batch
from app.models.transaction import Transaction
from app.models.issue_status import IssueStatus
from app.services.error_classification import _entity_of
from app.services.status_utils import ISSUE_BUCKETS, normalize_txn_status

BUCKET_CHOICES = ("day", "week", "month")

# Ops statuses that close an issue out as done.
SOLVED_OPS_STATUSES = frozenset({"solved", "success"})

# "exclude" is neither solved nor unsolved -- it is set aside. Ops marking an
# issue exclude ("not needed", "not ours") is a decision that it will not be
# worked, so counting it as unsolved understates the resolution rate, but
# calling it solved would claim work that never happened. It is counted in its
# own bucket and dropped from the resolution denominator, so a batch that is
# entirely solved-or-excluded reports 100%.
#
# Excluded issues stay in total_errors: the error still happened, per
# error_classification.py. Exclude changes whether there is work left, not
# whether it occurred.
EXCLUDED_OPS_STATUS = "exclude"

_EMPTY_ENTITY_COUNTS = {
    "failed": 0, "pending": 0, "lo_progress": 0, "solved": 0, "unsolved": 0, "excluded": 0,
}


def _bucket_key(d: date, bucket: str) -> tuple[str, date]:
    """(sort key, bucket_start date) for a batch's date, in the given granularity."""
    if bucket == "day":
        return d.isoformat(), d
    if bucket == "week":
        start = d - timedelta(days=d.weekday())  # Monday
        return start.isoformat(), start
    start = d.replace(day=1)  # month
    return start.isoformat(), start


def _bucket_label(bucket_start: date, bucket: str) -> str:
    if bucket == "day":
        return bucket_start.strftime("%b %d")
    if bucket == "week":
        return f"Wk of {bucket_start.strftime('%b %d')}"
    return bucket_start.strftime("%b %Y")


def _empty_result(date_from: date, date_to: date, bucket: str) -> dict:
    return {
        "range": {"from": date_from.isoformat(), "to": date_to.isoformat(), "bucket": bucket},
        "kpis": {
            "total_transactions": 0, "total_errors": 0, "solved": 0, "unsolved": 0,
            "excluded": 0,
            "resolution_rate": 0.0, "batches_included": 0,
        },
        "trend": [],
        "status_breakdown": {b: 0 for b in ISSUE_BUCKETS},
        "resolution_breakdown": {"solved": 0, "unsolved": 0, "excluded": 0},
        "top_entities": [],
        "top_categories": [],
        "available_entities": [],
    }


def build_analytics(
    date_from: date, date_to: date, bucket: str, exclude_entities: set[str] | None = None
) -> dict:
    """
    date_from/date_to are inclusive calendar dates. Every batch whose
    created_at date falls in [date_from, date_to] is included, open or
    finished -- an error happened the day it happened regardless of whether
    ops has clicked "Finish" on that batch yet.

    exclude_entities: aggregator/bank/"SCT"/"No Aggregator" names to drop
    entirely from every number below -- as if those transactions never
    existed, not just hidden from the top-entities list. This is a page-level
    filter, separate from the per-issue "exclude" ops status used in the
    Solve tab (which Error Classification/Analytics deliberately still count,
    per error_classification.py's docstring -- an issue someone marked
    exclude still happened).

    solved/unsolved split those errors by whether ops has finished them.
    Excluded issues are reported separately as `excluded` and belong to
    neither: they are still in total_errors (they happened) but are left out
    of the resolution denominator, so resolution_rate = solved / (solved +
    unsolved) and a batch that is entirely solved-or-excluded reports 100%.
    """
    if bucket not in BUCKET_CHOICES:
        raise ValueError(f"bucket must be one of {BUCKET_CHOICES}")

    range_start = datetime.combine(date_from, datetime.min.time())
    range_end_exclusive = datetime.combine(date_to + timedelta(days=1), datetime.min.time())

    batches = Batch.query.filter(
        Batch.created_at >= range_start, Batch.created_at < range_end_exclusive
    ).all()
    if not batches:
        return _empty_result(date_from, date_to, bucket)

    batch_date = {b.id: b.created_at.date() for b in batches}
    batch_ids = list(batch_date.keys())

    # Column-projected query, not full ORM hydration -- a date range can span
    # many batches (tens of thousands of rows), and this only needs the
    # columns the classification/solved logic actually reads.
    txn_rows = (
        db.session.query(
            Transaction.batch_id,
            Transaction.mid,
            Transaction.status,
            Transaction.error_side,
            Transaction.partner_name,
            Transaction.partner_type,
            Transaction.error_category,
            Transaction.retry_resolved,
        )
        .filter(Transaction.batch_id.in_(batch_ids))
        .all()
    )

    issue_status_map = {
        (i.batch_id, i.side, i.partner_name, i.category, i.txn_status): i
        for i in IssueStatus.query.filter(IssueStatus.batch_id.in_(batch_ids)).all()
    }

    exclude_entities = exclude_entities or set()

    total_transactions = 0
    total_errors = 0
    solved = 0
    excluded = 0
    status_breakdown: dict[str, int] = defaultdict(int)
    trend_buckets: dict[str, dict] = {}
    entity_acc: dict[str, dict] = {}
    category_acc: dict[tuple, dict] = {}
    all_entities_seen: dict[str, str] = {}  # entity -> entity_type, ignores exclude_entities

    for row in txn_rows:
        # Entity is resolved for every row regardless of status (classification
        # runs on every ingested row, not just failures -- see excel_ingest.py),
        # so the exclude filter and available_entities both need it up front.
        entity, entity_type = _entity_of(row)  # Row supports attribute access by column name
        all_entities_seen.setdefault(entity, entity_type)
        if entity in exclude_entities:
            continue

        total_transactions += 1

        if row.retry_resolved:
            continue
        txn_status = normalize_txn_status(row.status)
        if txn_status == "success":
            continue

        total_errors += 1
        status_breakdown[txn_status] += 1

        side = row.error_side or "unknown"
        category = row.error_category or "Unclassified"

        issue_key = (
            row.batch_id, side, row.partner_name if side != "sct" else None,
            category, txn_status,
        )
        issue_obj = issue_status_map.get(issue_key)

        override_obj = None
        if issue_obj and issue_obj.mid_overrides and row.mid in issue_obj.mid_overrides:
            raw = issue_obj.mid_overrides[row.mid]
            override_obj = raw if isinstance(raw, dict) else {"status": raw, "remark": ""}

        eff_status = (override_obj["status"] if override_obj else None) or (
            issue_obj.status if issue_obj else "pending"
        )
        is_excluded = eff_status == EXCLUDED_OPS_STATUS
        is_solved = eff_status in SOLVED_OPS_STATUSES
        if is_excluded:
            excluded += 1
        elif is_solved:
            solved += 1

        # --- trend bucket (keyed by the batch's date, not per-row) ---
        d = batch_date[row.batch_id]
        key, bucket_start = _bucket_key(d, bucket)
        tb = trend_buckets.setdefault(key, {
            "bucket_start": bucket_start, "total": 0, "failed": 0, "pending": 0,
            "lo_progress": 0, "solved": 0, "unsolved": 0, "excluded": 0,
        })
        tb["total"] += 1
        if txn_status in tb:
            tb[txn_status] += 1
        tb["excluded" if is_excluded else ("solved" if is_solved else "unsolved")] += 1

        # --- top entities ---
        ea = entity_acc.setdefault(entity, {
            "entity": entity, "entity_type": entity_type, "total": 0, **_EMPTY_ENTITY_COUNTS,
        })
        ea["total"] += 1
        if txn_status in ea:
            ea[txn_status] += 1
        ea["excluded" if is_excluded else ("solved" if is_solved else "unsolved")] += 1

        # --- top categories ---
        cat_key = (category, side)
        ca = category_acc.setdefault(cat_key, {"category": category, "side": side, "count": 0})
        ca["count"] += 1

    trend = [
        {
            "bucket_label": _bucket_label(trend_buckets[k]["bucket_start"], bucket),
            "bucket_start": trend_buckets[k]["bucket_start"].isoformat(),
            "total": trend_buckets[k]["total"],
            "failed": trend_buckets[k]["failed"],
            "pending": trend_buckets[k]["pending"],
            "lo_progress": trend_buckets[k]["lo_progress"],
            "solved": trend_buckets[k]["solved"],
            "unsolved": trend_buckets[k]["unsolved"],
            "excluded": trend_buckets[k]["excluded"],
        }
        for k in sorted(trend_buckets.keys())
    ]

    top_entities = sorted(entity_acc.values(), key=lambda e: (-e["total"], e["entity"]))[:10]

    top_categories = sorted(category_acc.values(), key=lambda c: -c["count"])[:10]
    for c in top_categories:
        c["share_of_batch"] = round(c["count"] * 100.0 / total_errors, 2) if total_errors else 0.0

    # Excluded issues sit outside the split entirely -- neither worked nor
    # outstanding -- so they are not in `unsolved` and not in the denominator.
    unsolved = total_errors - solved - excluded
    decidable = solved + unsolved
    resolution_rate = round(solved * 100.0 / decidable, 2) if decidable else 0.0

    return {
        "range": {"from": date_from.isoformat(), "to": date_to.isoformat(), "bucket": bucket},
        "kpis": {
            "total_transactions": total_transactions,
            "total_errors": total_errors,
            "solved": solved,
            "unsolved": unsolved,
            "excluded": excluded,
            "resolution_rate": resolution_rate,
            "batches_included": len(batch_ids),
        },
        "trend": trend,
        "status_breakdown": {b: status_breakdown.get(b, 0) for b in ISSUE_BUCKETS},
        "resolution_breakdown": {"solved": solved, "unsolved": unsolved, "excluded": excluded},
        "top_entities": top_entities,
        "top_categories": top_categories,
        "available_entities": [
            {"entity": name, "entity_type": all_entities_seen[name]}
            for name in sorted(all_entities_seen)
        ],
    }
