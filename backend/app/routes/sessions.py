"""
Office session endpoints.

A session is the batch. It opens when someone starts work, collects every
dispute decision made while it is open, and closes at the end of the day with
a report of what was handled.
"""

from flask import Blueprint, jsonify, request

from app.services.session_service import (
    close_session, current_session, open_session, session_activity,
)

sessions_bp = Blueprint("sessions", __name__, url_prefix="/api/sessions")


@sessions_bp.get("/current")
def get_current():
    """The running session, or null. The UI polls this to decide whether it is
    showing a Start or a Close button."""
    batch = current_session()
    return jsonify(batch.to_dict() if batch else None)


@sessions_bp.post("")
def start():
    """Open a session. Safe to call twice -- returns the running one."""
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if len(name) > 120:
        return jsonify({"error": "Name is too long (max 120 characters)."}), 400
    return jsonify(open_session(name or None).to_dict()), 201


@sessions_bp.post("/<int:batch_id>/close")
def close(batch_id):
    """
    End the session. Its report becomes available immediately -- that is the
    point of closing rather than just walking away.
    """
    payload = request.get_json(silent=True) or {}
    notes = payload.get("notes")
    batch = close_session(batch_id, notes if isinstance(notes, str) else None)
    return jsonify({**batch.to_dict(), "activity": session_activity(batch_id)["totals"]})


@sessions_bp.get("/<int:batch_id>/activity")
def activity(batch_id):
    """Everything handled during one session, for the report and the email."""
    return jsonify(session_activity(batch_id))
