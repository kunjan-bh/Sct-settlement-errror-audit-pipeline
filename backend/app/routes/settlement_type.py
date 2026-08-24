"""
Settlement Type Report endpoint -- the success-side counterpart to
/api/analytics. Same date-range scope (any batch whose processing date
falls in range, open or finished), not scoped to a single batch_id.
"""
from datetime import datetime

from flask import Blueprint, request, jsonify

from app.services.settlement_type_service import build_settlement_type_report

settlement_type_bp = Blueprint("settlement_type", __name__, url_prefix="/api/settlement-type")


@settlement_type_bp.get("")
def get_settlement_type_report():
    """
    GET /api/settlement-type?from=YYYY-MM-DD&to=YYYY-MM-DD
    """
    raw_from = request.args.get("from")
    raw_to = request.args.get("to")

    if not raw_from or not raw_to:
        return jsonify({"error": "Both 'from' and 'to' query params are required (YYYY-MM-DD)."}), 400

    try:
        date_from = datetime.strptime(raw_from, "%Y-%m-%d").date()
        date_to = datetime.strptime(raw_to, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "'from'/'to' must be YYYY-MM-DD."}), 400

    if date_from > date_to:
        return jsonify({"error": "'from' must not be after 'to'."}), 400

    return jsonify(build_settlement_type_report(date_from, date_to))
