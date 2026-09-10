"""
Issuer / Acquirer reconciliation endpoints.

Takes one transaction export and (optionally) one settlement export, reconciles
them, and returns either the on-page analysis or the three-sheet workbook.

Nothing is persisted: both uploads are written to a temp path, read, and
removed, exactly like Document Analysis. See
services/issuer_acquirer_service.py for what the numbers mean.
"""
import io
from datetime import date
import os
import uuid
from contextlib import contextmanager

from flask import Blueprint, current_app, jsonify, request, send_file

from app.services.mail_service import MailError, send_batch_email
from app.services.issuer_acquirer_mail import build_issuer_acquirer_email
from app.services.issuer_acquirer_service import (
    build_issuer_acquirer,
    build_issuer_acquirer_from_range,
)
from app.services.report_generator import generate_issuer_acquirer_report_bytes
from app.services.transaction_reconcile import TXN_ALLOWED_EXTENSIONS

issuer_acquirer_bp = Blueprint("issuer_acquirer", __name__, url_prefix="/api/issuer-acquirer")

SETTLEMENT_EXTENSIONS = {".xlsx", ".xls"}

# Both sides are aggregates, so a range is cheap -- but not unbounded.
MAX_RANGE_DAYS = 31


class _BadUpload(Exception):
    pass


@contextmanager
def _saved(field: str, allowed: set, required: bool):
    """Save one uploaded file for the life of the block, always removing it
    after. Yields (None, "") for an absent optional field so the caller can
    treat "no settlement file" as a normal case rather than an error."""
    file = request.files.get(field)
    if file is None or file.filename == "":
        if not required:
            yield None, ""
            return
        raise _BadUpload(f"No {field.replace('_', ' ')} provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed:
        exts = sorted(allowed)
        expected = " or ".join([", ".join(exts[:-1]), exts[-1]] if len(exts) > 1 else exts)
        raise _BadUpload(f"Unsupported file type '{ext}' for {field}. Expected {expected}")

    path = os.path.join(current_app.config["UPLOAD_DIR"], f"ia_{uuid.uuid4().hex}{ext}")
    file.save(path)
    try:
        yield path, file.filename
    finally:
        if os.path.exists(path):
            os.remove(path)


@contextmanager
def _both_uploads():
    with _saved("txn_file", TXN_ALLOWED_EXTENSIONS, required=True) as txn, \
            _saved("settlement_file", SETTLEMENT_EXTENSIONS, required=False) as settle:
        yield txn, settle


@issuer_acquirer_bp.post("/analyze")
def analyze():
    """multipart/form-data: txn_file (required), settlement_file (optional)."""
    try:
        with _both_uploads() as ((txn_path, txn_name), (settle_path, settle_name)):
            data = build_issuer_acquirer(txn_path, settle_path)
            data["files"] = {"transaction": txn_name, "settlement": settle_name}
    except _BadUpload as e:
        return jsonify({"error": str(e)}), 400
    except ValueError as e:
        # Shape problems in the uploaded file -- the message names the columns
        # it looked for, so it is worth showing verbatim.
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        current_app.logger.exception("Issuer/acquirer analysis failed")
        return jsonify({"error": f"Failed to process files: {e}"}), 422
    return jsonify(data)


@issuer_acquirer_bp.post("/report")
def report():
    """
    Same uploads as /analyze, plus an optional `reasons` field: a JSON object
    of {acquirer name: note} captured on the page. The files are re-sent rather
    than re-read from disk because nothing from the first request was kept.
    """
    import json

    raw = request.form.get("reasons") or "{}"
    try:
        reasons = json.loads(raw)
        if not isinstance(reasons, dict):
            reasons = {}
    except ValueError:
        reasons = {}

    try:
        with _both_uploads() as ((txn_path, _txn_name), (settle_path, _settle_name)):
            data = build_issuer_acquirer(txn_path, settle_path)
            report_bytes = generate_issuer_acquirer_report_bytes(data, reasons)
    except _BadUpload as e:
        return jsonify({"error": str(e)}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        current_app.logger.exception("Issuer/acquirer report failed")
        return jsonify({"error": f"Failed to build report: {e}"}), 422

    return send_file(
        io.BytesIO(report_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="Issuer_Acquirer_Reconciliation.xlsx",
    )


def _range_from_args():
    """from/to as inclusive dates."""
    raw_from = (request.args.get("from") or "").strip()
    raw_to = (request.args.get("to") or "").strip()
    try:
        d_from = date.fromisoformat(raw_from)
        d_to = date.fromisoformat(raw_to)
    except ValueError:
        raise ValueError("'from' and 'to' must be dates as YYYY-MM-DD.")
    if d_from > d_to:
        raise ValueError("'from' is after 'to'.")
    if (d_to - d_from).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"Range is longer than {MAX_RANGE_DAYS} days.")
    return d_from, d_to


@issuer_acquirer_bp.get("")
def analyze_range():
    """Issuing and acquiring for a date range, read from the switch."""
    try:
        d_from, d_to = _range_from_args()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        return jsonify(build_issuer_acquirer_from_range(d_from, d_to))
    except Exception as exc:  # noqa: BLE001 - the switch being down is normal
        return jsonify({
            "error": f"Could not read the switch: {str(exc).strip().splitlines()[0][:300]}"
        }), 502


@issuer_acquirer_bp.route("/range-report", methods=["GET", "POST"])
def report_range():
    """
    The same analysis as a workbook.

    POST carries the operator's notes. Every acquirer they did not annotate
    gets the reason the data implies, so the report explains all three hundred
    rather than the handful somebody had time to type.
    """
    try:
        d_from, d_to = _range_from_args()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        data = build_issuer_acquirer_from_range(d_from, d_to)
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            "error": f"Could not read the switch: {str(exc).strip().splitlines()[0][:300]}"
        }), 502

    notes = ((request.get_json(silent=True) or {}).get("reasons") or {})         if request.method == "POST" else {}
    reasons = {
        r["name"]: (notes.get(r["name"]) or r.get("reason") or "")
        for r in data.get("acquiring", [])
    }
    xlsx = generate_issuer_acquirer_report_bytes(data, reasons)
    return send_file(
        io.BytesIO(xlsx),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"issuer_acquirer_{d_from}_to_{d_to}.xlsx",
    )


@issuer_acquirer_bp.get("/email")
def email_preview():
    """The reconciliation as an editable email draft for the chosen dates."""
    try:
        d_from, d_to = _range_from_args()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        return jsonify(build_issuer_acquirer_email(d_from, d_to))
    except Exception as exc:  # noqa: BLE001 - the switch being slow is normal
        return jsonify({
            "error": f"Could not read the switch: {str(exc).strip().splitlines()[0][:300]}"
        }), 502


@issuer_acquirer_bp.post("/email")
def email_send():
    """
    Send what the operator edited, not a re-derived copy -- so the figures they
    checked are the figures that go out.
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
