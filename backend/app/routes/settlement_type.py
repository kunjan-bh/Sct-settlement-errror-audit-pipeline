"""
Settlement Type Report endpoint -- the success-side counterpart to
/api/analytics. Same date-range scope (any batch whose processing date
falls in range, open or finished), not scoped to a single batch_id.
"""
import io
import os
import uuid
from contextlib import contextmanager
from datetime import datetime

from flask import Blueprint, request, jsonify, current_app, send_file

from app.services.settlement_type_service import build_settlement_type_report
from app.services.adhoc_settlement_service import build_adhoc_settlement_type_report
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


@settlement_type_bp.post("/adhoc-analysis")
def adhoc_settlement_analysis():
    """
    multipart/form-data with a "file" field -- the "Document Analysis"
    section of the Settlement Type Report page. Runs one uploaded file
    through the same classification rules and partner mappings as a real
    batch, but nothing is persisted: no Batch, Transaction, or IssueStatus
    row is created, and the file itself is deleted right after processing.
    For a one-off file someone wants to check without adding it to batch
    history.
    """
    try:
        with _saved_upload() as (tmp_path, filename):
            report = build_adhoc_settlement_type_report(tmp_path, filename)
    except _BadUpload as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.exception("Ad-hoc settlement analysis failed")
        return jsonify({"error": f"Failed to process file: {e}"}), 422

    return jsonify(report)


@settlement_type_bp.post("/adhoc-analysis/report")
def download_adhoc_settlement_report():
    """
    Same settlement file upload as /adhoc-analysis, but returns the Excel
    report instead of JSON -- the "Download Report" button in Document
    Analysis. The file is re-sent (the frontend keeps the original File
    object around after analyzing), not re-fetched from disk, since nothing
    from the first request was persisted.

    An optional second file field, "txn_file", takes that day's Transaction
    List. When present, every settled MID in the per-entity sheets is traced
    to the transaction it settled; when absent the report is exactly what it
    was before.
    """
    try:
        with _saved_upload() as (tmp_path, filename), \
                _saved_transaction_upload() as (txn_path, txn_filename):
            report_bytes = generate_adhoc_settlement_type_report_bytes(
                tmp_path, filename, txn_path, txn_filename
            )
    except _BadUpload as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.exception("Ad-hoc settlement report generation failed")
        return jsonify({"error": f"Failed to process file: {e}"}), 422

    safe_name = os.path.splitext(filename)[0].replace(" ", "_")
    return send_file(
        io.BytesIO(report_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"Settlement_Type_Report_{safe_name}.xlsx",
    )
