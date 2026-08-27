"""
Operator-editable settings, backed by the app_settings table.

Every setting is declared in _DEFINITIONS with its default and type, so the
Settings page can render the form from the API rather than hardcoding fields,
and a new setting needs one entry here and nothing else.

The SMTP password is the one value never sent to the browser: reads return a
placeholder, and a write that still carries the placeholder is treated as
"unchanged" rather than overwriting the real password with the mask. That is
the whole reason writes go through set_settings instead of a plain upsert.
"""
from app.extensions import db
from app.models.app_setting import AppSetting

# Sent instead of the real secret, and recognised on the way back in.
SECRET_MASK = "••••••••"

# key -> (default, type, secret?)
#   type is one of "str" | "int" | "bool"
_DEFINITIONS = {
    # --- who the batch summary comes from and goes to ---
    "mail_from": ("kunjan.bhatta@sct.com.np", "str", False),
    "mail_from_name": ("SCT QR Settlement", "str", False),
    "mail_to": ("ashok.koirala@sct.com.np", "str", False),
    "mail_cc": ("", "str", False),
    "mail_subject": ("QR Transaction Analysis Report – Daily Summary", "str", False),
    # Sending over SMTP bypasses the Outlook client entirely, so the signature
    # configured there is never applied. This is where it goes instead: HTML,
    # appended below the body (and below the chart) on every summary email.
    "mail_signature_html": ("", "str", False),
    # --- SMTP transport ---
    "smtp_host": ("", "str", False),
    "smtp_port": ("587", "int", False),
    "smtp_username": ("", "str", False),
    "smtp_password": ("", "str", True),
    "smtp_use_tls": ("true", "bool", False),
    "smtp_timeout": ("30", "int", False),
}

_TRUE = {"1", "true", "yes", "on"}


def _coerce(raw, kind, default):
    if raw is None:
        raw = default
    if kind == "int":
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return int(default)
    if kind == "bool":
        return str(raw).strip().lower() in _TRUE
    return "" if raw is None else str(raw)


def get_settings() -> dict:
    """Every setting, coerced to its declared type, defaults filled in. Includes
    real secret values -- for sending, not for the API. See public_settings."""
    stored = {row.key: row.value for row in AppSetting.query.all()}
    return {
        key: _coerce(stored.get(key), kind, default)
        for key, (default, kind, _secret) in _DEFINITIONS.items()
    }


def public_settings() -> dict:
    """What the Settings page gets: same shape, but secrets replaced by a mask
    (or left empty when never set, so the form can show a real empty field)."""
    values = get_settings()
    for key, (_default, _kind, secret) in _DEFINITIONS.items():
        if secret:
            values[key] = SECRET_MASK if values.get(key) else ""
    return values


def set_settings(updates: dict) -> dict:
    """
    Write the subset of known keys present in `updates` and return
    public_settings(). Unknown keys are ignored rather than stored, so a stale
    or hand-rolled client cannot pollute the table.

    A secret arriving as SECRET_MASK means "leave it alone" -- the browser was
    never given the real value, so echoing the mask back must not erase it.
    """
    for key, (_default, kind, secret) in _DEFINITIONS.items():
        if key not in updates:
            continue
        raw = updates[key]
        if secret and raw == SECRET_MASK:
            continue
        if kind == "bool":
            raw = "true" if (raw is True or str(raw).strip().lower() in _TRUE) else "false"
        else:
            raw = "" if raw is None else str(raw).strip()

        row = db.session.get(AppSetting, key)
        if row is None:
            db.session.add(AppSetting(key=key, value=raw))
        else:
            row.value = raw

    db.session.commit()
    return public_settings()


def settings_schema() -> list[dict]:
    """Field metadata so the Settings page can build its form from the API."""
    labels = {
        "mail_from": ("From address", "mail"),
        "mail_from_name": ("From name", "mail"),
        "mail_to": ("To (comma-separated)", "mail"),
        "mail_cc": ("Cc (comma-separated)", "mail"),
        "mail_subject": ("Default subject", "mail"),
        "mail_signature_html": ("Signature (HTML)", "mail"),
        "smtp_host": ("SMTP host", "smtp"),
        "smtp_port": ("SMTP port", "smtp"),
        "smtp_username": ("SMTP username", "smtp"),
        "smtp_password": ("SMTP password", "smtp"),
        "smtp_use_tls": ("Use STARTTLS", "smtp"),
        "smtp_timeout": ("Timeout (seconds)", "smtp"),
    }
    schema = []
    for key, (default, kind, secret) in _DEFINITIONS.items():
        label, group = labels.get(key, (key, "mail"))
        schema.append({
            "key": key, "label": label, "group": group,
            "type": kind, "secret": secret, "default": default,
        })
    return schema
