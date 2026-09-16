"""
Add-terminal endpoints.

The only part of this application that writes to the switch. Live writes need
the MID typed back, and every terminal created is recorded locally -- see
services/terminal_service.py for why the write path is kept apart from the
read-only one.
"""

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models.terminal_log import TerminalLog
from app.services.core_db import ENVIRONMENTS, active_environment
from app.services.terminal_service import (
    TerminalError,
    create_terminals,
    plan_terminals,
    suggest_names,
)

terminals_bp = Blueprint("terminals", __name__, url_prefix="/api/terminals")

# Adding hundreds in one go is far more likely to be a mistake than a plan.
MAX_PER_REQUEST = 50


@terminals_bp.get("/plan")
def plan():
    """
    What would be created for a MID, without creating it: the terminal being
    copied, what the merchant already has, and the new names and ids.
    """
    mid = (request.args.get("mid") or "").strip()
    try:
        count = int(request.args.get("count") or 1)
    except ValueError:
        return jsonify({"error": "'count' must be a number."}), 400
    if not 1 <= count <= MAX_PER_REQUEST:
        return jsonify({"error": f"Count must be between 1 and {MAX_PER_REQUEST}."}), 400

    raw_names = [n.strip() for n in (request.args.get("names") or "").split(",") if n.strip()]
    try:
        names = raw_names or suggest_names(mid, count)
        return jsonify(plan_terminals(mid, names[:count]))
    except TerminalError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - the switch being down is normal
        return jsonify({
            "error": f"Could not read the switch: {str(exc).strip().splitlines()[0][:300]}"
        }), 502


@terminals_bp.post("")
def create():
    """
    Create the terminals. Writes to whichever switch the toggle is on.

    Live asks for the MID to be typed back. It is the only irreversible thing
    this application does, and a mis-click on the wrong merchant would put
    terminals on a real one.
    """
    payload = request.get_json(silent=True) or {}
    mid = (payload.get("mid") or "").strip()
    names = payload.get("names") or []
    environment = (payload.get("environment") or active_environment()).strip().lower()

    if environment not in ENVIRONMENTS:
        return jsonify({"error": f"Unknown environment {environment!r}."}), 400
    if environment != active_environment():
        # Otherwise a stale tab could write to the database it was showing
        # before somebody flipped the toggle.
        return jsonify({
            "error": f"The app is on {active_environment().upper()} but the request said "
                     f"{environment.upper()}. Reload and try again."
        }), 409
    if not isinstance(names, list) or not names:
        return jsonify({"error": "At least one terminal name is required."}), 400
    if len(names) > MAX_PER_REQUEST:
        return jsonify({"error": f"At most {MAX_PER_REQUEST} terminals at a time."}), 400

    if environment == "live" and (payload.get("confirm") or "").strip() != mid:
        return jsonify({
            "error": "Writing to live: type the MID to confirm."
        }), 428

    try:
        result = create_terminals(mid, names, environment)
    except TerminalError as exc:
        # The steps it got through travel with the error, so the screen can show
        # what ran and that it was undone instead of just the message.
        return jsonify({"error": str(exc), "steps": exc.steps}), 400
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator as text
        return jsonify({
            "error": f"Could not create the terminals: {str(exc).strip().splitlines()[0][:300]}",
            "steps": [],
        }), 502

    for t in result["created"]:
        db.session.add(TerminalLog(
            environment=environment, mid=mid, terminal_name=t["name"],
            pag_id=t["id"], outlet_id=t["outlet_id"],
        ))
    db.session.commit()
    return jsonify(result), 201


@terminals_bp.get("/log")
def log():
    """Everything this application has added, newest first."""
    rows = (
        TerminalLog.query
        .order_by(TerminalLog.created_at.desc())
        .limit(int(request.args.get("limit") or 100))
        .all()
    )
    return jsonify([r.to_dict() for r in rows])
