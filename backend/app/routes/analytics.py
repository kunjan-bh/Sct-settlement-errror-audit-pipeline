"""
Cross-batch analytics endpoint -- backs the Analytics nav page. Unlike every
other route in this app, this one is not scoped to a single batch_id; it
spans whatever batches fall inside the requested date range.
"""
from datetime import datetime

from flask import Blueprint, request, jsonify

from app.services.analytics_service import build_analytics, BUCKET_CHOICES

analytics_bp = Blueprint("analytics", __name__, url_prefix="/api/analytics")


@analytics_bp.get("")
def get_analytics():
    """
    GET /api/analytics?from=YYYY-MM-DD&to=YYYY-MM-DD&bucket=day|week|month
        &exclude_entities=Name+One&exclude_entities=Name+Two

    exclude_entities may be repeated to drop more than one aggregator/bank
    (or "SCT" / "No Aggregator") entirely from every number in the response.
    """
    raw_from = request.args.get("from")
    raw_to = request.args.get("to")
    bucket = request.args.get("bucket", "day")
    exclude_entities = set(request.args.getlist("exclude_entities"))

    if not raw_from or not raw_to:
        return jsonify({"error": "Both 'from' and 'to' query params are required (YYYY-MM-DD)."}), 400

    try:
        date_from = datetime.strptime(raw_from, "%Y-%m-%d").date()
        date_to = datetime.strptime(raw_to, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "'from'/'to' must be YYYY-MM-DD."}), 400

    if date_from > date_to:
        return jsonify({"error": "'from' must not be after 'to'."}), 400

    if bucket not in BUCKET_CHOICES:
        return jsonify({"error": f"'bucket' must be one of {BUCKET_CHOICES}."}), 400

    return jsonify(build_analytics(date_from, date_to, bucket, exclude_entities))
