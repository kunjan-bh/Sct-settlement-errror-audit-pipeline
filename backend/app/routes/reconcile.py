"""
Reconciliation endpoints.

Read-only against the switch. See services/reconcile_service.py for what the
buckets mean and why the join is what it is.
"""

import io
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request, send_file

from app.services.core_db import ReadOnlyViolation
from app.services.reconcile_service import build_reconciliation

reconcile_bp = Blueprint("reconcile", __name__, url_prefix="/api/reconcile")

# Every extra day multiplies both sides of the match, and the per-payment join
# already takes over a minute on one day.
MAX_RANGE_DAYS = 7


def _parse(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{field}' must be a date as YYYY-MM-DD (got {value!r}).")


def _range_from_request():
    today = date.today()
    raw_from = request.args.get("from") or str(today - timedelta(days=1))
    raw_to = request.args.get("to") or str(today - timedelta(days=1))
    d_from = _parse(raw_from, "from")
    d_to = _parse(raw_to, "to")
    if d_from > d_to:
        raise ValueError("'from' is after 'to'.")
    span = (d_to - d_from).days + 1
    if span > MAX_RANGE_DAYS:
        raise ValueError(
            f"Range is {span} days; the maximum is {MAX_RANGE_DAYS}. "
            "Reconciliation matches every payment to its settlement, which is slow over long ranges."
        )
    return d_from, d_to


@reconcile_bp.get("")
def reconcile():
    """Full reconciliation for a date range."""
    try:
        d_from, d_to = _range_from_request()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        return jsonify(build_reconciliation(d_from, d_to))
    except ReadOnlyViolation as exc:
        return jsonify({"error": f"Refused a non-read query: {exc}"}), 500
    except Exception as exc:  # noqa: BLE001 - the switch being slow or down is normal
        return jsonify({
            "error": f"Could not reconcile: {str(exc).strip().splitlines()[0][:300]}"
        }), 502


@reconcile_bp.get("/report")
def report():
    """The reconciliation as a workbook, exception sheet included."""
    from app.services.reconcile_report import generate_reconcile_xlsx

    try:
        d_from, d_to = _range_from_request()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        data = build_reconciliation(d_from, d_to)
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "error": f"Could not reconcile: {str(exc).strip().splitlines()[0][:300]}"
        }), 502

    xlsx = generate_reconcile_xlsx(data, datetime.now().strftime("%Y-%m-%d %H:%M"))
    name = f"reconciliation_{d_from}_to_{d_to}.xlsx"
    return send_file(
        io.BytesIO(xlsx),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=name,
    )
