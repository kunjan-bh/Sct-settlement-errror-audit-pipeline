"""
Reconciliation endpoints.

Read-only against the switch. See services/reconcile_service.py for what the
buckets mean and why the join is what it is.
"""

import io
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, request, send_file

from app.services.core_db import ReadOnlyViolation
from app.services.job_runner import job_status, start_job
from app.services.reconcile_service import RECONCILE_STEPS, build_reconciliation

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


@reconcile_bp.post("/start")
def start():
    """
    Begin a reconciliation and return a job id to poll.

    Matching a day takes about a minute; holding the request open for that
    reads as a hung page, and gives no way to say which stage it is on.
    """
    try:
        d_from, d_to = _range_from_request()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    app = current_app._get_current_object()
    job_id = start_job(
        app,
        lambda progress: build_reconciliation(d_from, d_to, progress=progress),
        RECONCILE_STEPS,
    )
    return jsonify({"job_id": job_id, "steps": RECONCILE_STEPS,
                    "range": {"from": str(d_from), "to": str(d_to)}}), 202


@reconcile_bp.get("/status/<job_id>")
def status(job_id):
    """Where a running reconciliation has got to, with the result once done."""
    job = job_status(job_id)
    if job is None:
        return jsonify({"error": "No such job — it may have expired."}), 404
    return jsonify(job)
