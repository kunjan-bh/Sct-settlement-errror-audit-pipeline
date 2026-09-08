"""
Office sessions.

A session is a batch with no spreadsheet behind it. Someone sits down to work,
opens a session, and every dispute they solve or pick up while it is open is
recorded against it automatically. At the end of the day they close it and the
session's report is what went out that day.

The point is that nobody files anything by hand: working the disputes list IS
filling in the batch. The old flow needed an export, an upload, and then the
same decisions typed a second time.

Sessions share the batches table with the old upload batches (Batch.kind), so
notes, the report and the summary email all work the same for both.
"""

from __future__ import annotations

from datetime import date, datetime

from app.extensions import db
from app.models.batch import Batch
from app.models.dispute_status import DisputeStatus

SESSION_KIND = "session"
OPEN = "open"
CLOSED = "finished"


def _default_session_name(when: date | None = None) -> str:
    """
    "Session 2026-09-08", with _2, _3 for a second session the same day.

    Someone who closes a session before lunch and opens another after it has
    two genuinely separate pieces of work, not one interrupted one, so this
    numbers them rather than refusing.
    """
    base = f"Session {(when or date.today()).isoformat()}"
    if not Batch.query.filter_by(name=base).first():
        return base
    n = 2
    while Batch.query.filter_by(name=f"{base}_{n}").first():
        n += 1
    return f"{base}_{n}"


def current_session() -> Batch | None:
    """The open session, if there is one. At most one is ever open."""
    return (
        Batch.query
        .filter_by(kind=SESSION_KIND, status=OPEN)
        .order_by(Batch.created_at.desc())
        .first()
    )


def open_session(name: str | None = None) -> Batch:
    """
    Start a session, or return the one already running.

    Deliberately idempotent: opening a session twice is a double-click or a
    second browser tab, not a request for two batches. Returning the running
    one is what the operator meant either way.
    """
    existing = current_session()
    if existing:
        return existing

    desired = (name or "").strip()
    if desired and Batch.query.filter_by(name=desired).first():
        n = 2
        while Batch.query.filter_by(name=f"{desired}_{n}").first():
            n += 1
        desired = f"{desired}_{n}"

    batch = Batch(
        name=desired or _default_session_name(),
        kind=SESSION_KIND,
        status=OPEN,
    )
    db.session.add(batch)
    db.session.commit()
    return batch


def close_session(batch_id: int, notes: str | None = None) -> Batch:
    """
    End a session and stamp when it finished.

    Closing does not lock anything -- the decisions stay editable, exactly as
    finishing an upload batch never locked it. What closing means is "this is
    the day's work, generate the report for it".
    """
    batch = Batch.query.get_or_404(batch_id)
    batch.status = CLOSED
    batch.finished_at = datetime.utcnow()
    if notes is not None:
        batch.notes = notes.strip() or None
    db.session.commit()
    return batch


def attach_to_current_session(row: DisputeStatus) -> Batch | None:
    """
    Stamp a dispute decision with the session it was taken in.

    Opens a session if none is running rather than dropping the link. Someone
    who starts working before clicking "Start session" has still done the work,
    and silently losing it from the day's report is worse than opening a
    session they did not ask for -- which is, in any case, what they were
    about to do.

    A decision keeps the session it was first recorded in: changing your mind
    at 4pm about something you looked at at 10am is the same piece of work, and
    re-stamping it would move it out of the report that already described it.
    """
    if row.batch_id:
        return Batch.query.get(row.batch_id)

    batch = current_session() or open_session()
    row.batch_id = batch.id
    return batch


def session_activity(batch_id: int) -> dict:
    """
    What was handled during one session: every dispute decision stamped with
    it, split by what was decided.

    Excluded decisions are counted apart and listed nowhere, the same rule the
    rest of the app follows -- an exclusion is not work done, it is work judged
    not ours.
    """
    batch = Batch.query.get_or_404(batch_id)
    rows = DisputeStatus.query.filter_by(batch_id=batch_id).all()

    per_status: dict[str, list[dict]] = {"solved": [], "in_progress": [], "pending": [], "exclude": []}
    for r in rows:
        per_status.setdefault(r.status, []).append(r.to_dict())

    handled = per_status["solved"] + per_status["in_progress"]
    return {
        "batch": batch.to_dict(),
        "totals": {
            "handled": len(handled),
            "solved": len(per_status["solved"]),
            "in_progress": len(per_status["in_progress"]),
            "excluded": len(per_status["exclude"]),
            "amount_handled": round(sum(d["amount"] or 0 for d in handled), 2),
            "amount_solved": round(sum(d["amount"] or 0 for d in per_status["solved"]), 2),
        },
        "solved": sorted(per_status["solved"], key=lambda d: -(d["amount"] or 0)),
        "in_progress": sorted(per_status["in_progress"], key=lambda d: -(d["amount"] or 0)),
    }
