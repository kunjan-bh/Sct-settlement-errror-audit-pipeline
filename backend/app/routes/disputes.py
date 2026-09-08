"""
Disputes endpoints.

Failed settlements read straight from the live switch, paired with the
merchant's current hold balance. There is no upload here: the point of this
tab is that the data comes from the source, so nothing has to be exported,
mailed around and re-imported before anyone can see it.

Read-only throughout -- see services/core_db.py.
"""

import io
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request, send_file

from app.extensions import db
from app.models.dispute_status import DISPUTE_STATUSES, DisputeStatus
from app.services.core_db import ReadOnlyViolation, core_db_status
from app.services.dispute_export import generate_dispute_xlsx
from app.services.dispute_service import build_disputes
from app.services.session_service import attach_to_current_session

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


@disputes_bp.put("/<path:dispute_key>/status")
def set_dispute_status(dispute_key):
    """
    Record what an operator decided about one failed settlement.

    Writes to *our* database, never the switch. The dispute itself stays where
    it is; all we keep is the decision and the note that goes with it.
    """
    payload = request.get_json(silent=True) or {}
    status = (payload.get("status") or "").strip().lower()
    if status not in DISPUTE_STATUSES:
        return jsonify({
            "error": f"status must be one of {', '.join(DISPUTE_STATUSES)}"
        }), 400

    row = DisputeStatus.query.filter_by(dispute_key=dispute_key).first()
    if not row:
        row = DisputeStatus(dispute_key=dispute_key)
        db.session.add(row)

    row.status = status
    if "comment" in payload:
        row.comment = (payload.get("comment") or "").strip() or None
    # Denormalised so the decision stays readable once this settlement has
    # aged out of whatever range is being viewed.
    for field in ("mid", "crrn", "partner_name"):
        if payload.get(field):
            setattr(row, field, str(payload[field])[:128])
    if payload.get("amount") is not None:
        try:
            row.amount = float(payload["amount"])
        except (TypeError, ValueError):
            pass

    # Working the list is what fills in the batch -- no separate filing step.
    attach_to_current_session(row)

    db.session.commit()
    return jsonify(row.to_dict())


@disputes_bp.put("/scope/<scope_type>/<path:scope_value>")
def set_scope_status(scope_type, scope_value):
    """
    Apply a decision to a whole entity -- a wallet, an aggregator, a bank, or
    one merchant -- instead of a single settlement.

    This is what makes "exclude" usable: an aggregator that settles on its own
    schedule produces the same failure every day, and clicking forty rows to
    say so is not a workflow.
    """
    if scope_type not in ("mapped_partner", "partner", "bank_or_wallet", "acquirer", "mid"):
        return jsonify({
            "error": "scope_type must be mapped_partner, partner, bank_or_wallet, acquirer or mid"
        }), 400

    payload = request.get_json(silent=True) or {}
    status = (payload.get("status") or "").strip().lower()
    if status not in DISPUTE_STATUSES:
        return jsonify({
            "error": f"status must be one of {', '.join(DISPUTE_STATUSES)}"
        }), 400

    key = f"scope:{scope_type}:{scope_value.lower()}"
    row = DisputeStatus.query.filter_by(dispute_key=key).first()

    if status == "pending":
        # Clearing an entity-level decision, rather than storing "pending" as
        # if it were one -- otherwise the scope keeps overriding every row.
        if row:
            db.session.delete(row)
            db.session.commit()
        return jsonify({"cleared": True, "scope_type": scope_type, "scope_value": scope_value})

    if not row:
        row = DisputeStatus(dispute_key=key)
        db.session.add(row)
    row.scope_type = scope_type
    row.scope_value = scope_value
    row.status = status
    if "comment" in payload:
        row.comment = (payload.get("comment") or "").strip() or None

    db.session.commit()
    return jsonify(row.to_dict())


@disputes_bp.get("/scopes")
def list_scopes():
    """Entity-level decisions currently in force, so the UI can show what is
    being suppressed rather than leaving it a mystery."""
    rows = DisputeStatus.query.filter(DisputeStatus.scope_type.isnot(None)).all()
    return jsonify([r.to_dict() for r in rows])


@disputes_bp.post("/export")
def export_disputes():
    """
    Excel of the disputes currently on screen.

    Takes the ids the page is showing rather than re-deriving the filter here:
    the tab, the aggregator dropdown and the search box all live in the
    browser, and reimplementing them server-side would give two versions of
    "what is showing" that could disagree. The rows themselves are re-read from
    the switch, so the sheet carries live figures rather than whatever the page
    had cached.
    """
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "Nothing to export."}), 400

    try:
        date_from = _parse(payload.get("from") or "", "from")
        date_to = _parse(payload.get("to") or "", "to")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    wanted = set(str(i) for i in ids)
    try:
        data = build_disputes(date_from, date_to)
    except Exception as exc:  # noqa: BLE001 - the switch being down is normal
        return jsonify({
            "error": f"Could not read the switch: {str(exc).strip().splitlines()[0][:300]}"
        }), 502

    by_id = {str(d["id"]): d for d in data["disputes"]}
    # Keep the page's order: the operator sorted or filtered to get here.
    rows = [by_id[i] for i in (str(x) for x in ids) if i in by_id]
    if not rows:
        return jsonify({"error": "None of those disputes are in this date range any more."}), 404

    xlsx = generate_dispute_xlsx(rows, {
        "date_from": str(date_from),
        "date_to": str(date_to),
        "filter_label": str(payload.get("filter_label") or "")[:120],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })

    name = f"disputes_{date_from}_to_{date_to}.xlsx"
    return send_file(
        io.BytesIO(xlsx),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=name,
    )
