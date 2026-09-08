"""
Disputes endpoints.

Failed settlements read straight from the live switch, paired with the
merchant's current hold balance. There is no upload here: the point of this
tab is that the data comes from the source, so nothing has to be exported,
mailed around and re-imported before anyone can see it.

Read-only throughout -- see services/core_db.py.
"""

from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from app.services.core_db import ReadOnlyViolation, core_db_status
from app.services.dispute_service import build_disputes

disputes_bp = Blueprint("disputes", __name__, url_prefix="/api/disputes")

# The switch holds years of data and this joins against a function that walks
# every merchant, so an unbounded range is a way to hurt production by
# accident rather than a useful feature.
MAX_RANGE_DAYS = 31


def _parse(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{field}' must be a date as YYYY-MM-DD (got {value!r}).")


@disputes_bp.get("/status")
def status():
    """Whether the switch is configured and reachable. Never returns
    credentials -- see core_db.core_db_status."""
    return jsonify(core_db_status())


@disputes_bp.get("")
def list_disputes():
    """
    Failed settlements for a date range, flagged with whether the money is
    still held on the merchant.

    Defaults to yesterday..today, which is the window someone opening this tab
    in the morning almost always wants.
    """
    today = date.today()
    raw_from = request.args.get("from") or str(today - timedelta(days=1))
    raw_to = request.args.get("to") or str(today)

    try:
        date_from = _parse(raw_from, "from")
        date_to = _parse(raw_to, "to")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if date_from > date_to:
        return jsonify({"error": "'from' is after 'to'."}), 400
    span = (date_to - date_from).days + 1
    if span > MAX_RANGE_DAYS:
        return jsonify({
            "error": f"Range is {span} days; the maximum is {MAX_RANGE_DAYS}. "
                     "Narrow the dates -- this reads the live switch."
        }), 400

    try:
        return jsonify(build_disputes(date_from, date_to))
    except ReadOnlyViolation as exc:
        # Should be unreachable: every query in dispute_service is a SELECT.
        # If it fires, the guard has caught a genuine bug before it reached
        # production, and saying so plainly beats a generic 500.
        return jsonify({"error": f"Refused a non-read query: {exc}"}), 500
    except Exception as exc:  # noqa: BLE001 - the switch being down is normal
        return jsonify({
            "error": f"Could not read the switch: {str(exc).strip().splitlines()[0][:300]}"
        }), 502
