"""
Routes for the batch lifecycle: upload -> process -> dashboard -> ops
updates -> finish. Report generation (Finish Batch -> 3-sheet Excel) is
NOT in this file yet -- that's the next piece after this dashboard layer
is confirmed working.
"""
import os
import uuid
import io

from flask import Blueprint, request, jsonify, current_app, send_file

from app.extensions import db
from app.models.batch import Batch
from app.models.issue_status import IssueStatus
from app.services.excel_ingest import ingest_excel
from app.services.dashboard_service import build_dashboard
from app.services.report_generator import generate_report_bytes

batches_bp = Blueprint("batches", __name__, url_prefix="/api/batches")

ALLOWED_EXTENSIONS = {".xlsx", ".xls"}


@batches_bp.post("/upload")
def upload_batch():
    """
    multipart/form-data with a "file" field. Saves the file to
    storage/uploads with a collision-proof name, runs it through the
    ingestion pipeline (classify + persist), and returns the new batch's
    dashboard data in one round trip -- the frontend doesn't need a
    separate "now fetch the dashboard" call right after upload.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Unsupported file type '{ext}'. Expected .xlsx or .xls"}), 400

    # uuid prefix avoids collisions if two files share a name -- the batch
    # itself (not the filename) is the durable identifier going forward.
    safe_name = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    save_path = os.path.join(current_app.config["UPLOAD_DIR"], safe_name)
    file.save(save_path)

    try:
        batch = ingest_excel(save_path)
    except Exception as e:
        # Ingestion failed partway -- surface the real error instead of a
        # generic 500, since "which column was missing" is exactly what
        # you need to fix a malformed upload.
        current_app.logger.exception("Ingestion failed")
        return jsonify({"error": f"Failed to process file: {e}"}), 422

    return jsonify({
        "batch": batch.to_dict(),
        "dashboard": build_dashboard(batch.id),
    }), 201


@batches_bp.get("")
def list_batches():
    """Powers the 'Previous Batches' search page."""
    batches = Batch.query.order_by(Batch.created_at.desc()).all()
    return jsonify([b.to_dict() for b in batches])


@batches_bp.get("/<int:batch_id>")
def get_batch(batch_id):
    batch = Batch.query.get_or_404(batch_id)
    return jsonify({
        "batch": batch.to_dict(),
        "dashboard": build_dashboard(batch_id),
    })


@batches_bp.patch("/<int:batch_id>/notes")
def update_notes(batch_id):
    batch = Batch.query.get_or_404(batch_id)
    data = request.get_json(force=True)
    batch.notes = data.get("notes", "")
    db.session.commit()
    return jsonify(batch.to_dict())


@batches_bp.post("/<int:batch_id>/finish")
def finish_batch(batch_id):
    """
    Flips status to finished.
    """
    from datetime import datetime

    batch = Batch.query.get_or_404(batch_id)
    batch.status = "finished"
    batch.finished_at = datetime.utcnow()
    db.session.commit()
    return jsonify(batch.to_dict())


@batches_bp.get("/<int:batch_id>/report")
def download_report(batch_id):
    """
    Generates and returns the 3-sheet Excel report as a file response.
    """
    batch = Batch.query.get_or_404(batch_id)
    report_bytes = generate_report_bytes(batch.id)

    filename = f"SmartQR_Settlement_Report_{batch.name}.xlsx"
    return send_file(
        io.BytesIO(report_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@batches_bp.patch("/<int:batch_id>/issues/<int:issue_id>")
def update_issue(batch_id, issue_id):
    """
    Body: { "status": "solved" | "pending" | "in_progress", "comment": "..." }
    This is the dropdown + comment box per issue card.
    """
    issue = IssueStatus.query.filter_by(id=issue_id, batch_id=batch_id).first_or_404()
    data = request.get_json(force=True)

    if "status" in data:
        if data["status"] not in ("pending", "in_progress", "solved", "exclude"):
            return jsonify({"error": "status must be pending, in_progress, solved, or exclude"}), 400
        issue.status = data["status"]
    if "comment" in data:
        issue.comment = data["comment"]

    db.session.commit()
    return jsonify(issue.to_dict())


@batches_bp.delete("/<int:batch_id>")
def delete_batch(batch_id):
    """
    Deletes the batch and all of its associated transactions and issue statuses.
    """
    from app.models.transaction import Transaction
    batch = Batch.query.get_or_404(batch_id)

    Transaction.query.filter_by(batch_id=batch.id).delete()
    IssueStatus.query.filter_by(batch_id=batch.id).delete()

    db.session.delete(batch)
    db.session.commit()

    return "", 204
