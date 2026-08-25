"""
Settlement Type Report: the success-side counterpart to Analytics. Instead of
"what broke", this answers "of what settled, how did it settle" -- Real Time
vs System Default vs On Call -- broken down by aggregator/bank/wallet/SCT.

Same batch-date-range scaffold as analytics_service.build_analytics (batches
by Batch.created_at in range, column-projected Transaction query, no full ORM
hydration), and reuses _entity_of for entity resolution -- error_side/
partner_name/partner_type are populated for every row at ingest time
(excel_ingest.py classifies and resolves partners unconditionally, not just
for failures), so the same resolution works unchanged on success rows.
"""
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models.batch import Batch
from app.models.transaction import Transaction
from app.services.error_classification import _entity_of
from app.services.status_utils import normalize_txn_status

# raw Settled By value -> response key. Anything else (blank, unrecognized)
# falls into "unknown" -- counted, never silently dropped.
_METHOD_KEYS = {
    "Real Time": "real_time",
    "System Default": "system_default",
    "On Call": "on_call",
}

_EMPTY_METHOD_COUNTS = {"real_time": 0, "system_default": 0, "on_call": 0, "unknown": 0}
_EMPTY_METHOD_AMOUNTS = {"real_time": 0.0, "system_default": 0.0, "on_call": 0.0, "unknown": 0.0}


def _method_key(raw) -> str:
    text = (raw or "").strip()
    return _METHOD_KEYS.get(text, "unknown")


def _method_label(raw) -> str:
    """Raw 'Settled By' -> one of the three canonical labels, or "" if blank/
    unrecognized -- for the report's MID sheets, where a blank cell gets a
    dropdown (see report_generator._write_entity_mid_sheets) instead of a
    guessed value."""
    text = (raw or "").strip()
    return text if text in _METHOD_KEYS else ""


def _num(value) -> float:
    """extra_data keeps whatever JSON type the source cell had; Service Charge
    can arrive as a string. 0.0 when absent or unparseable -- a blank charge is
    genuinely zero here, not unknown."""
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def _empty_result(date_from: date, date_to: date) -> dict:
    return {
        "range": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "kpis": {
            "total_settled": 0, "total_amount_settled": 0.0, "real_time": 0, "system_default": 0,
            "on_call": 0, "unknown": 0, "batches_included": 0,
        },
        "method_breakdown": dict(_EMPTY_METHOD_COUNTS),
        "method_amount_breakdown": dict(_EMPTY_METHOD_AMOUNTS),
        "entities": [],
    }


def build_settlement_type_report(date_from: date, date_to: date) -> dict:
    """
    date_from/date_to are inclusive calendar dates. Every batch whose
    created_at date falls in [date_from, date_to] is included, open or
    finished -- same batch scope as Analytics.
    """
    range_start = datetime.combine(date_from, datetime.min.time())
    range_end_exclusive = datetime.combine(date_to + timedelta(days=1), datetime.min.time())

    batches = Batch.query.filter(
        Batch.created_at >= range_start, Batch.created_at < range_end_exclusive
    ).all()
    if not batches:
        return _empty_result(date_from, date_to)

    batch_ids = [b.id for b in batches]

    txn_rows = (
        db.session.query(
            Transaction.status,
            Transaction.settled_by,
            Transaction.error_side,
            Transaction.partner_name,
            Transaction.partner_type,
            Transaction.txn_amount,
        )
        .filter(Transaction.batch_id.in_(batch_ids))
        .all()
    )

    method_breakdown: dict[str, int] = dict(_EMPTY_METHOD_COUNTS)
    method_amount_breakdown: dict[str, float] = dict(_EMPTY_METHOD_AMOUNTS)
    entity_acc: dict[str, dict] = {}
    total_settled = 0
    total_amount_settled = 0.0

    for row in txn_rows:
        if normalize_txn_status(row.status) != "success":
            continue

        total_settled += 1
        amount = float(row.txn_amount) if row.txn_amount is not None else 0.0
        total_amount_settled += amount

        method = _method_key(row.settled_by)
        method_breakdown[method] += 1
        method_amount_breakdown[method] += amount

        entity, entity_type = _entity_of(row)  # Row supports attribute access by column name
        ea = entity_acc.setdefault(entity, {
            "entity": entity, "entity_type": entity_type, "total": 0, "amount": 0.0,
            **_EMPTY_METHOD_COUNTS,
        })
        ea["total"] += 1
        ea["amount"] += amount
        ea[method] += 1

    for ea in entity_acc.values():
        ea["amount"] = round(ea["amount"], 2)
    entities = sorted(entity_acc.values(), key=lambda e: (-e["total"], e["entity"]))

    return {
        "range": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "kpis": {
            "total_settled": total_settled,
            "total_amount_settled": round(total_amount_settled, 2),
            "real_time": method_breakdown["real_time"],
            "system_default": method_breakdown["system_default"],
            "on_call": method_breakdown["on_call"],
            "unknown": method_breakdown["unknown"],
            "batches_included": len(batch_ids),
        },
        "method_breakdown": method_breakdown,
        "method_amount_breakdown": {k: round(v, 2) for k, v in method_amount_breakdown.items()},
        "entities": entities,
    }


def build_settlement_type_mid_rows(date_from: date, date_to: date) -> list[dict]:
    """
    One row per successful settlement in range -- MID, merchant, whatever
    issuer/acquirer names the source file carried, and settlement type.
    Feeds the report's per-entity MID sheets only (report_generator.py);
    the on-page report and its cache never see this.

    Deliberately a SEPARATE query from build_settlement_type_report, which
    runs on every page view/date change and stays column-projected for that
    reason. This one also pulls extra_data (for Acquirer Name / Bank-Wallet
    Name) and only runs when a report is actually downloaded, so it doesn't
    need to stay as light.
    """
    range_start = datetime.combine(date_from, datetime.min.time())
    range_end_exclusive = datetime.combine(date_to + timedelta(days=1), datetime.min.time())

    batches = Batch.query.filter(
        Batch.created_at >= range_start, Batch.created_at < range_end_exclusive
    ).all()
    if not batches:
        return []

    batch_ids = [b.id for b in batches]
    transactions = Transaction.query.filter(Transaction.batch_id.in_(batch_ids)).all()

    rows = []
    for txn in transactions:
        if normalize_txn_status(txn.status) != "success":
            continue

        entity, entity_type = _entity_of(txn)
        extra = txn.extra_data or {}
        rows.append({
            "entity": entity,
            "entity_type": entity_type,
            "mid": txn.mid,
            "merchant_name": txn.merchant_name,
            "acquirer_name": extra.get("Acquirer Name") or "",
            "bank_wallet_name": extra.get("Bank/Wallet Name") or "",
            "settlement_type": _method_label(txn.settled_by),
            "amount": float(txn.txn_amount) if txn.txn_amount is not None else 0.0,
            "service_charge": _num(extra.get("Service Charge")),
        })

    rows.sort(key=lambda r: (r["entity"], r["mid"] or ""))
    return rows
