"""
Settlement Type Report endpoint -- the success-side counterpart to
/api/analytics. Same date-range scope (any batch whose processing date
falls in range, open or finished), not scoped to a single batch_id.
"""
import io
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime

from flask import Blueprint, request, jsonify, current_app, send_file

from app.services.mail_service import MailError, send_batch_email
from app.services.settlement_type_mail import REPROCESSED_TYPES, build_entity_email
from app.services.settlement_type_service import build_settlement_type_report
from app.services.report_generator import (
    generate_settlement_type_report_bytes,
    generate_adhoc_settlement_type_report_bytes,
)
from app.services.transaction_reconcile import TXN_ALLOWED_EXTENSIONS

settlement_type_bp = Blueprint("settlement_type", __name__, url_prefix="/api/settlement-type")

ALLOWED_EXTENSIONS = {".xlsx", ".xls"}

# The optional Transaction List uploaded beside a settlement file. The switch
# exports it as CSV by default, so .csv is accepted here but not for the
# settlement file itself.
TXN_FIELD = "txn_file"


class _BadUpload(Exception):
    pass


@contextmanager
def _saved_upload(field: str = "file", allowed=ALLOWED_EXTENSIONS, required: bool = True):
    """
    Validates one file field of the current request, saves it to a temp path
    for the duration of the `with` block, and always removes it after --
    shared by every ad-hoc endpoint (JSON analysis and the xlsx report) since
    none of them persists an upload.

    With required=False a missing or empty field yields (None, "") instead of
    raising, which is how the optional Transaction List is handled: a bad
    file is still an error, but no file at all is not.
    """
    file = request.files.get(field)
    if file is None or file.filename == "":
        if not required:
            yield None, ""
            return
        raise _BadUpload("No file provided" if file is None else "No file selected")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed:
        exts = sorted(allowed)
        expected = " or ".join([", ".join(exts[:-1]), exts[-1]] if len(exts) > 1 else exts)
        raise _BadUpload(f"Unsupported file type '{ext}'. Expected {expected}")

    tmp_path = os.path.join(current_app.config["UPLOAD_DIR"], f"adhoc_{uuid.uuid4().hex}{ext}")
    file.save(tmp_path)
    try:
        yield tmp_path, file.filename
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@contextmanager
def _saved_transaction_upload():
    """The optional Transaction List beside a settlement file."""
    with _saved_upload(TXN_FIELD, TXN_ALLOWED_EXTENSIONS, required=False) as saved:
        yield saved


def _parse_range():
    """from/to as inclusive dates, the same pair the page already sends."""
    raw_from = (request.args.get("from") or "").strip()
    raw_to = (request.args.get("to") or "").strip()
    try:
        d_from = date.fromisoformat(raw_from)
        d_to = date.fromisoformat(raw_to)
    except ValueError:
        raise ValueError("'from' and 'to' must be dates as YYYY-MM-DD.")
    if d_from > d_to:
        raise ValueError("'from' is after 'to'.")
    return d_from, d_to


def _requested_types() -> tuple[str, ...]:
    """
    Which settlement types to report. Defaults to everything that was not real
    time, since that is what "reprocessed" means here.
    """
    raw = (request.args.get("types") or "").strip()
    if not raw:
        return REPROCESSED_TYPES
    wanted = tuple(t.strip() for t in raw.split(",") if t.strip())
    return tuple(t for t in wanted if t in REPROCESSED_TYPES) or REPROCESSED_TYPES


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


@settlement_type_bp.get("/report")
def download_settlement_type_report():
    """
    GET /api/settlement-type/report?from=YYYY-MM-DD&to=YYYY-MM-DD
    Downloadable 2-sheet Excel: Summary (settlement method split by count
    and by amount) + Entity Breakdown -- for sharing the date-range report.
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

    report_bytes = generate_settlement_type_report_bytes(date_from, date_to)
    filename = f"Settlement_Type_Report_{date_from.isoformat()}_to_{date_to.isoformat()}.xlsx"
    return send_file(
        io.BytesIO(report_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@settlement_type_bp.get("/entity-email")
def entity_email_preview():
    """
    Draft an email to one aggregator or wallet listing the settlements of
    theirs that were reprocessed rather than settled in real time.
    """
    entity = (request.args.get("entity") or "").strip()
    if not entity:
        return jsonify({"error": "An aggregator or wallet is required."}), 400
    try:
        date_from, date_to = _parse_range()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    types = _requested_types()
    return jsonify(build_entity_email(entity, date_from, date_to, types))


@settlement_type_bp.post("/entity-email")
def send_entity_email():
    """
    Send what the operator edited in the overlay -- not a re-derived copy, so
    what they read is what the aggregator receives.
    """
    payload = request.get_json(silent=True) or {}
    try:
        result = send_batch_email(
            subject=payload.get("subject") or "",
            from_addr=payload.get("from_addr") or "",
            from_name=payload.get("from_name") or "",
            to=payload.get("to") or "",
            cc=payload.get("cc") or "",
            body_html=payload.get("body_html") or "",
            signature_html=payload.get("signature_html"),
        )
    except MailError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
