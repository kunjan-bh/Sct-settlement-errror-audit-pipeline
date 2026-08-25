"""
Document Analysis: a Settlement Type breakdown for ONE uploaded file that
never becomes a batch. The regular Settlement Type report (settlement_type_
service.py) answers "of everything settled in this date range, how did it
settle" over batches already sitting in the pipeline. This answers the same
question for a file someone just wants to check standalone -- a sample, a
one-off reconciliation, a file they don't want mixed into batch history.

No Batch, Transaction, or IssueStatus row is created. Classification still
uses the live RuleEngine/PartnerResolver (same rules and partner mappings as
every real batch), so the numbers are directly comparable -- only the
persistence is skipped.
"""
from dataclasses import dataclass
from typing import Optional

from app.services.classification_service import RuleEngine, PartnerResolver
from app.services.error_classification import _entity_of
from app.services.excel_ingest import read_settlement_dataframe, _clean
from app.services.retry_matching import parse_amount, parse_txn_datetime
from app.services.settlement_type_service import (
    _EMPTY_METHOD_COUNTS,
    _EMPTY_METHOD_AMOUNTS,
    _METHOD_KEYS,
    _method_key,
)
from app.services.status_utils import normalize_txn_status


@dataclass
class _ClassifiedRow:
    """Just enough shape for _entity_of -- mirrors the Transaction attributes
    it reads, without needing a real (persisted) Transaction instance."""

    error_side: str
    partner_name: str
    partner_type: Optional[str]


def build_adhoc_settlement_type_full(file_path: str, file_name: str) -> dict:
    """
    Does the full pass over the file once and returns both the on-page
    summary (kpis/method_breakdown/entities -- what /adhoc-analysis returns)
    and the per-MID rows the report's per-entity sheets need. Kept as one
    function so a report download doesn't reclassify a 10k+-row file twice;
    build_adhoc_settlement_type_report below just throws the mid_rows half
    away for callers that only want the summary.
    """
    df = read_settlement_dataframe(file_path)

    engine = RuleEngine.load()
    resolver = PartnerResolver.load()

    method_breakdown: dict[str, int] = dict(_EMPTY_METHOD_COUNTS)
    method_amount_breakdown: dict[str, float] = dict(_EMPTY_METHOD_AMOUNTS)
    entity_acc: dict[str, dict] = {}
    mid_rows: list[dict] = []
    total_rows = 0
    total_settled = 0
    total_amount_settled = 0.0
    # The cycle this settlement file covers -- reported so a zero trace rate
    # against a transaction file can be read against it (see
    # services/transaction_reconcile.py).
    window_from = None
    window_to = None

    for _, row in df.iterrows():
        total_rows += 1

        when = parse_txn_datetime(_clean(row.get("Transaction Date")))
        if when is not None:
            if window_from is None or when < window_from:
                window_from = when
            if window_to is None or when > window_to:
                window_to = when

        status_val = _clean(row.get("Status"))
        if normalize_txn_status(status_val) != "success":
            continue

        mid = _clean(row.get("MID"))
        remark = _clean(row.get("Remarks 1"))
        result = engine.classify_row(remark)
        partner_name, bucket = resolver.resolve(mid)

        total_settled += 1
        amount = parse_amount(row.get("Txn Amount")) or 0.0
        total_amount_settled += float(amount)

        settled_by_raw = _clean(row.get("Settled By"))
        method = _method_key(settled_by_raw)
        method_breakdown[method] += 1
        method_amount_breakdown[method] += float(amount)

        classified = _ClassifiedRow(error_side=result.side, partner_name=partner_name, partner_type=bucket)
        entity, entity_type = _entity_of(classified)
        ea = entity_acc.setdefault(entity, {
            "entity": entity, "entity_type": entity_type, "total": 0, "amount": 0.0,
            **_EMPTY_METHOD_COUNTS,
        })
        ea["total"] += 1
        ea["amount"] += float(amount)
        ea[method] += 1

        mid_rows.append({
            "entity": entity,
            "entity_type": entity_type,
            "mid": mid,
            "merchant_name": _clean(row.get("Merchant Name")) or "",
            "acquirer_name": _clean(row.get("Acquirer Name")) or "",
            "bank_wallet_name": _clean(row.get("Bank/Wallet Name")) or "",
            "settlement_type": settled_by_raw if settled_by_raw in _METHOD_KEYS else "",
            "amount": float(amount),
            "service_charge": parse_amount(row.get("Service Charge")) or 0.0,
            # Trace keys -- only read when a Transaction List is uploaded
            # alongside; see services/transaction_reconcile.py.
            "ref_id": _clean(row.get("Ref ID")) or "",
            "stan": _clean(row.get("STAN")) or "",
            "crrn": _clean(row.get("CRRN")) or "",
            "remark": remark or "",
        })

    for ea in entity_acc.values():
        ea["amount"] = round(ea["amount"], 2)
    entities = sorted(entity_acc.values(), key=lambda e: (-e["total"], e["entity"]))
    mid_rows.sort(key=lambda r: (r["entity"], r["mid"] or ""))

    return {
        "summary": {
            "file_name": file_name,
            "row_count": total_rows,
            "window": _window_text(window_from, window_to),
            "kpis": {
                "total_settled": total_settled,
                "total_amount_settled": round(total_amount_settled, 2),
                "real_time": method_breakdown["real_time"],
                "system_default": method_breakdown["system_default"],
                "on_call": method_breakdown["on_call"],
                "unknown": method_breakdown["unknown"],
            },
            "method_breakdown": method_breakdown,
            "method_amount_breakdown": {k: round(v, 2) for k, v in method_amount_breakdown.items()},
            "entities": entities,
        },
        "mid_rows": mid_rows,
    }


def _window_text(start, end) -> str:
    if start is None or end is None:
        return ""
    fmt = "%d %b %Y %I:%M %p"
    return f"{start.strftime(fmt)} to {end.strftime(fmt)}"


def build_adhoc_settlement_type_report(file_path: str, file_name: str) -> dict:
    """Used by /adhoc-analysis (the Document Analysis page) -- summary only,
    no per-MID detail, per spec ("don't show that in analysis")."""
    return build_adhoc_settlement_type_full(file_path, file_name)["summary"]
