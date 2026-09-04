"""
Settings endpoints: the mail/SMTP configuration behind the end-of-batch
summary email, editable from the Settings page.

The SMTP password is never returned -- reads send a mask, and a write that
carries the mask back means "unchanged". See services/settings_service.py.
"""
from flask import Blueprint, current_app, jsonify, request

from app.services.mail_service import MailError, send_batch_email
from app.services.settings_service import (
    public_settings,
    set_settings,
    settings_schema,
    smtp_status,
    sync_verify_rules,
)

settings_bp = Blueprint("settings", __name__, url_prefix="/api/settings")


@settings_bp.get("")
def get_all_settings():
    """Current values plus the field metadata the form is built from, so adding
    a setting needs no frontend change. The SMTP transport is deliberately
    absent -- `smtp` reports only whether it works."""
    return jsonify({
        "values": public_settings(),
        "schema": settings_schema(),
        "smtp": smtp_status(),
    })


@settings_bp.put("")
def update_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Expected a JSON object of setting values."}), 400
    values = set_settings(payload)
    # The rules table is what classifies a row at ingest, so a changed pattern
    # list has to be pushed into it here or editing the setting would look like
    # it worked and change nothing.
    verify = sync_verify_rules() if "verify_remark_patterns" in payload else None
    return jsonify({
        "values": values,
        "schema": settings_schema(),
        "smtp": smtp_status(),
        "verify": verify,
    })


@settings_bp.post("/test-email")
def send_test_email():
    """
    Sends a short message using the saved SMTP settings, so the configuration
    can be proven before a real batch depends on it. Recipients come from the
    request when given, otherwise from the saved settings.
    """
    payload = request.get_json(silent=True) or {}
    values = public_settings()
    try:
        result = send_batch_email(
            subject="SCT QR Settlement — test email",
            from_addr=payload.get("from_addr") or values["mail_from"],
            from_name=payload.get("from_name") or values["mail_from_name"],
            to=payload.get("to") or values["mail_to"],
            cc="",
            body_html=(
                "<p>This is a test message from the SCT QR settlement pipeline.</p>"
                "<p>If you are reading it, the SMTP settings are working.</p>"
            ),
        )
    except MailError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.exception("Test email failed")
        return jsonify({"error": f"Unexpected error sending test email: {e}"}), 500
    return jsonify(result)
