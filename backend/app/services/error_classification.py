"""
Error Classification view: "which error, how often, on whose side".

This is deliberately NOT the ops view. The dashboard's Solve section answers
"what does the team still have to fix" and is filtered by ops status
(excluded issues disappear, solved ones are marked). This answers a
different question: of everything that did not settle in this batch, what
broke and how frequently, grouped per aggregator / bank-wallet / SCT.

So it counts every non-success transaction regardless of what ops later
marked it -- an issue someone marked "exclude" still happened, and hiding it
would understate an aggregator's real error frequency. Same data feeds the
dashboard charts, the standalone Excel extract, and the "Error Classify"
sheet appended to the full batch report, so all three can never disagree.

The one exclusion is a failure that a later reprocess actually settled (see
services/retry_matching.py): that settlement did go through, so counting it
as an outstanding error would overstate the failure rate instead. Those rows
are counted in totals.retry_resolved and listed in retry_resolved_rows, so
the exclusion can always be audited.

Grouping rule (matches how ops reasons about fault):
  error_side == "sct"  -> entity "SCT"          (our own problem, no partner)
  otherwise            -> entity = partner_name (the aggregator/bank at fault)
"""
from collections import defaultdict

from app.models.batch import Batch
from app.models.transaction import Transaction
from app.services.retry_matching import resolved_details
from app.services.status_utils import ISSUE_BUCKETS, normalize_txn_status

SCT_ENTITY = "SCT"
UNMAPPED_ENTITY = "No Aggregator"


def _entity_of(txn: Transaction) -> tuple[str, str]:
    """(entity name, entity type) for one transaction."""
    if txn.error_side == "sct":
        return SCT_ENTITY, "sct"

    name = txn.partner_name or UNMAPPED_ENTITY
    if name == UNMAPPED_ENTITY or not txn.partner_type:
        return UNMAPPED_ENTITY, "unmapped"
    return name, txn.partner_type  # "aggregator" | "bank_wallet"


def _pct(part: int, whole: int) -> float:
    return round(part * 100.0 / whole, 2) if whole else 0.0


def build_error_classification(batch_id: int) -> dict:
    """
    Returns the full classification tree:

      {
        "batch": {...},
        "totals": {"total_errors", "entities", "categories", "by_status": {...}},
        "entities": [ {entity, entity_type, total, failed, pending, lo_progress,
                       share_of_batch, categories: [ {category, side, count,
                       failed, pending, lo_progress, share_of_entity,
                       share_of_batch} ]} ],
        "categories": [ {category, side, count, entity_count, share_of_batch} ],
      }

    Entities and categories are both sorted by descending count, which is the
    order the charts and the Excel extract render in -- worst offender first.
    """
    batch = Batch.query.get_or_404(batch_id)
    transactions = Transaction.query.filter_by(batch_id=batch_id).all()

    # (entity, entity_type, category, side) -> {bucket: count}
    grouped: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    entity_types: dict[str, str] = {}
    total_errors = 0
    retry_resolved = 0
    by_status: dict[str, int] = defaultdict(int)

    for txn in transactions:
        bucket = normalize_txn_status(txn.status)
        if bucket == "success":
            continue

        # A failure that a later reprocess settled is not an outstanding
        # error. It is counted separately and listed in `retry_resolved_rows`
        # so the suppression is always auditable, never silent.
        if txn.retry_resolved:
            retry_resolved += 1
            continue

        entity, entity_type = _entity_of(txn)
        category = txn.error_category or "Unclassified"
        side = txn.error_side or "unknown"

        grouped[(entity, category, side)][bucket] += 1
        entity_types[entity] = entity_type
        by_status[bucket] += 1
        total_errors += 1

    # --- roll up per entity ---
    per_entity: dict[str, dict] = {}
    for (entity, category, side), buckets in grouped.items():
        count = sum(buckets.values())
        row = per_entity.setdefault(entity, {
            "entity": entity,
            "entity_type": entity_types[entity],
            "total": 0,
            "categories": [],
            **{b: 0 for b in ISSUE_BUCKETS},
        })
        row["total"] += count
        for b in ISSUE_BUCKETS:
            row[b] += buckets.get(b, 0)
        row["categories"].append({
            "category": category,
            "side": side,
            "count": count,
            **{b: buckets.get(b, 0) for b in ISSUE_BUCKETS},
        })

    entities = []
    for row in per_entity.values():
        for cat in row["categories"]:
            cat["share_of_entity"] = _pct(cat["count"], row["total"])
            cat["share_of_batch"] = _pct(cat["count"], total_errors)
        row["categories"].sort(key=lambda c: (-c["count"], c["category"]))
        row["share_of_batch"] = _pct(row["total"], total_errors)
        entities.append(row)
    entities.sort(key=lambda e: (-e["total"], e["entity"]))

    # --- roll up per category across all entities ---
    cat_totals: dict[tuple, dict] = {}
    for (entity, category, side), buckets in grouped.items():
        agg = cat_totals.setdefault((category, side), {
            "category": category, "side": side, "count": 0, "entity_count": 0,
        })
        agg["count"] += sum(buckets.values())
        agg["entity_count"] += 1

    categories = sorted(cat_totals.values(), key=lambda c: (-c["count"], c["category"]))
    for cat in categories:
        cat["share_of_batch"] = _pct(cat["count"], total_errors)

    return {
        "batch": batch.to_dict(),
        "totals": {
            "total_errors": total_errors,
            "entities": len(entities),
            "categories": len(categories),
            "by_status": {b: by_status.get(b, 0) for b in ISSUE_BUCKETS},
            "by_side": _side_totals(grouped),
            "retry_resolved": retry_resolved,
        },
        "entities": entities,
        "categories": categories,
        "retry_resolved_rows": resolved_details(batch_id),
    }


def _side_totals(grouped: dict) -> dict:
    totals: dict[str, int] = defaultdict(int)
    for (_entity, _category, side), buckets in grouped.items():
        totals[side] += sum(buckets.values())
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


ENTITY_TYPE_LABELS = {
    "aggregator": "Aggregator",
    "bank_wallet": "Bank / Wallet",
    "sct": "SCT",
    "unmapped": "Unmapped MID",
}

SIDE_LABELS = {
    "sct": "SCT",
    "aggregator": "Aggregator",
    "bank": "Bank",
    "unknown": "Unknown",
}
