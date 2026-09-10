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
from app.services.classification_service import PartnerResolver
from app.services.core_db import run_query

# raw Settled By value -> response key. Anything else (blank, unrecognized)
# falls into "unknown" -- counted, never silently dropped.
_METHOD_KEYS = {
    "Real Time": "real_time",
    "System Default": "system_default",
    "On Call": "on_call",
}

# The switch spells the same three modes in its own way.
_SWITCH_METHOD_KEYS = {
    "REAL_TIME": "real_time",
    "SYSTEM_DEFAULT": "system_default",
    "ON_CALL": "on_call",
}
_SWITCH_METHOD_LABELS = {
    "REAL_TIME": "Real Time",
    "SYSTEM_DEFAULT": "System Default",
    "ON_CALL": "On Call",
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


_SETTLED_SQL = """
SELECT merchant_code, merchant_name, settlement_frequency,
       amount, service_charge, bank_name_or_wallet_name, acquirer_name
FROM operators.fund_transfer_logs
WHERE date BETWEEN %(date_from)s AND %(date_to)s
  AND status = 'SUCCESS'
"""


def _switch_rows(date_from: date, date_to: date) -> list[dict]:
    """Every successful settlement in range, straight from the switch."""
    return run_query(_SETTLED_SQL, {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
    })


def build_settlement_type_report(date_from: date, date_to: date) -> dict:
    """
    How the day's settlements were made -- Real Time, System Default or On
    Call -- and by which aggregator or wallet.

    Read from the switch by date rather than from ingested batches. The batch
    version could only answer for days somebody had happened to upload, and
    reported nothing at all for any other date, which is a confusing way for a
    date picker to behave. The switch has every day.
    """
    rows = _switch_rows(date_from, date_to)
    if not rows:
        return _empty_result(date_from, date_to)

    resolver = PartnerResolver.load()

    method_breakdown: dict[str, int] = dict(_EMPTY_METHOD_COUNTS)
    method_amount_breakdown: dict[str, float] = dict(_EMPTY_METHOD_AMOUNTS)
    entity_acc: dict[str, dict] = {}
    total_settled = 0
    total_amount_settled = 0.0

    for row in rows:
        total_settled += 1
        amount = _num(row.get("amount"))
        total_amount_settled += amount

        method = _SWITCH_METHOD_KEYS.get(
            (row.get("settlement_frequency") or "").strip().upper(), "unknown"
        )
        method_breakdown[method] += 1
        method_amount_breakdown[method] += amount

        mid = (row.get("merchant_code") or "").strip()
        entity, bucket = resolver.resolve(mid)
        entity_type = "aggregator" if bucket == "aggregator" else "bank_wallet"
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
            # Kept so the response shape does not change; nothing is read from
            # batches any more, so there are none to count.
            "batches_included": 0,
        },
        "method_breakdown": method_breakdown,
        "method_amount_breakdown": {k: round(v, 2) for k, v in method_amount_breakdown.items()},
        "entities": entities,
    }


def build_settlement_type_mid_rows(date_from: date, date_to: date) -> list[dict]:
    """
    One row per successful settlement -- MID, merchant, the switch's own
    acquirer and bank/wallet names, and how it settled. Feeds the report's
    per-entity MID sheets.

    A separate call from build_settlement_type_report, which runs on every date
    change; this one only runs when a report is downloaded.
    """
    rows = _switch_rows(date_from, date_to)
    if not rows:
        return []

    resolver = PartnerResolver.load()
    out = []
    for row in rows:
        mid = (row.get("merchant_code") or "").strip()
        entity, bucket = resolver.resolve(mid)
        out.append({
            "entity": entity,
            "entity_type": "aggregator" if bucket == "aggregator" else "bank_wallet",
            "mid": mid,
            "merchant_name": row.get("merchant_name") or "",
            "acquirer_name": row.get("acquirer_name") or "",
            "bank_wallet_name": row.get("bank_name_or_wallet_name") or "",
            "settlement_type": _SWITCH_METHOD_LABELS.get(
                (row.get("settlement_frequency") or "").strip().upper(), ""
            ),
            "amount": _num(row.get("amount")),
            "service_charge": _num(row.get("service_charge")),
        })

    out.sort(key=lambda r: (r["entity"], r["mid"] or ""))
    return out
