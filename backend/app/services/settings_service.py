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
import os

from app.extensions import db
from app.models.app_setting import AppSetting

# Sent instead of the real secret, and recognised on the way back in.
SECRET_MASK = "••••••••"

# Where each setting's default comes from in the environment (backend/.env,
# loaded in config.py). Precedence is: saved value in the database, then the
# environment, then the literal default in _DEFINITIONS.
#
# This is why the SMTP password can stay out of the database entirely -- leave
# it unset on the Settings page and it is read from SMTP_PASSWORD every time.
_ENV_KEYS = {
    "mail_from": "SMTP_FROM_EMAIL",
    "mail_from_name": "MAIL_FROM_NAME",
    "mail_to": "MAIL_TO",
    "mail_cc": "MAIL_CC",
    "mail_subject": "MAIL_SUBJECT",
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_username": "SMTP_USERNAME",
    "smtp_password": "SMTP_PASSWORD",
    "smtp_timeout": "SMTP_TIMEOUT",
}

# The signature is assembled from parts rather than pasted as HTML, so the
# same block can be edited field by field in .env. Order is the order it
# renders. See _signature_from_env.
_SIGNATURE_ENV = [
    ("MAIL_SIGNATURE_NAME", "", "bold"),
    ("MAIL_SIGNATURE_TITLE", "", "plain"),
    ("MAIL_SIGNATURE_COMPANY", "", "plain"),
    ("MAIL_SIGNATURE_PHONE", "Mobile: ", "plain"),
    ("MAIL_SIGNATURE_TOLL_FREE", "Toll Free: ", "plain"),
    ("MAIL_SIGNATURE_ADDRESS", "", "muted"),
    ("MAIL_SIGNATURE_WEBSITE", "", "link"),
]

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


def _escape(text) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _signature_from_env() -> str:
    """
    Build the signature HTML from the MAIL_SIGNATURE_* variables.

    Used when no signature has been saved on the Settings page, so the common
    case is "fill in .env once and never touch the UI". Any part left unset is
    simply skipped, so a signature with no toll-free number renders without a
    blank line where it would have been.
    """
    lines = []
    for env_key, prefix, style in _SIGNATURE_ENV:
        value = (os.environ.get(env_key) or "").strip()
        if not value:
            continue
        text = _escape(f"{prefix}{value}")
        if style == "bold":
            lines.append(f'<div style="font-weight:600;color:#111827;">{text}</div>')
        elif style == "muted":
            lines.append(f'<div style="color:#6b7280;font-size:12px;">{text}</div>')
        elif style == "link":
            url = value if value.startswith(("http://", "https://")) else f"https://{value}"
            lines.append(
                f'<div><a href="{_escape(url)}" style="color:#2563eb;">{text}</a></div>'
            )
        else:
            lines.append(f'<div style="color:#374151;">{text}</div>')

    if not lines:
        return ""
    return '<div style="font-size:13px;line-height:1.45;">' + "".join(lines) + "</div>"


def _default_for(key: str, default):
    """The default before the database is consulted: environment first, then
    the literal declared in _DEFINITIONS."""
    if key == "mail_signature_html":
        return _signature_from_env() or default
    env_key = _ENV_KEYS.get(key)
    if env_key:
        raw = os.environ.get(env_key)
        if raw is not None and raw.strip() != "":
            return raw.strip()
    if key == "smtp_use_tls":
        # SMTP_ENCRYPTION mirrors the variable name the other SCT tool uses:
        # tls/starttls -> STARTTLS on the given port, ssl -> implicit SSL
        # (set SMTP_PORT=465 for that), none -> plaintext.
        enc = (os.environ.get("SMTP_ENCRYPTION") or "").strip().lower()
        if enc:
            return "true" if enc in ("tls", "starttls") else "false"
    return default


def get_settings() -> dict:
    """Every setting, coerced to its declared type. A value saved on the
    Settings page wins; otherwise the environment; otherwise the declared
    default. Includes real secret values -- for sending, not for the API."""
    stored = {row.key: row.value for row in AppSetting.query.all()}
    return {
        key: _coerce(stored.get(key), kind, _default_for(key, default))
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
        resolved = _default_for(key, default)
        schema.append({
            "key": key, "label": label, "group": group, "type": kind,
            "secret": secret,
            # What the field falls back to when left blank -- shown as the
            # placeholder, so .env values are visible without being retyped.
            "default": SECRET_MASK if (secret and resolved) else resolved,
            "from_env": bool(_ENV_KEYS.get(key) and os.environ.get(_ENV_KEYS[key])),
        })
    return schema
