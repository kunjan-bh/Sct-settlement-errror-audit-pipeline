"""
Operator-editable settings, backed by the app_settings table.

Every setting is declared in _DEFINITIONS with its default and type, so the
Settings page can render the form from the API rather than hardcoding fields,
and a new setting needs one entry here and nothing else.

The SMTP transport is configured in backend/.env and is NOT exposed here.
public_settings() drops every smtp_* key and settings_schema() omits them, so
the host, username and password never reach the browser at all -- the Settings
page gets smtp_status() instead, which says only whether the transport is
usable. Server credentials have no business being round-tripped through a web
form, and there is nothing to leak from a page that never received them.

set_settings() still refuses to overwrite a stored secret with the mask, which
matters because the key is writable by any client even though our own UI no
longer offers it.
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
# same block can be edited field by field in .env. The layout below mirrors
# the standard SCT block exactly -- see _signature_from_env:
#
#     Regards,
#     Kunjan Bhatta                                  (bold)
#     Tech Operation Department                      (bold)
#     Mobile: 9768785577
#     Smart Choice Technologies Ltd. (SCT)
#     5th Floor, RS Sadan, Panipokhari, Kathmandu, Nepal
#     Toll Free: 1660-0144155 | www.sct.com.np       (site is a link)
#     [logo]
_SIGNATURE_CLOSING = "Regards,"

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

# Configured in .env only. Never sent to the browser, never rendered as a
# form field -- see smtp_status() for what the Settings page gets instead.
ENV_ONLY_PREFIX = "smtp_"

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
    Build the signature HTML from the MAIL_SIGNATURE_* variables, in the
    standard SCT layout (see _SIGNATURE_CLOSING above).

    Used when no signature has been saved on the Settings page, so the common
    case is "fill in .env once and never touch the UI". Any part left unset is
    skipped rather than rendered as a blank line -- a signature with no
    toll-free number closes up instead of leaving a gap.

    The logo is NOT here: it has to be attached to the message and referenced
    by Content-ID, which only the sender knows. mail_service appends it.
    """
    def env(key):
        return (os.environ.get(key) or "").strip()

    name, title = env("MAIL_SIGNATURE_NAME"), env("MAIL_SIGNATURE_TITLE")
    phone, company = env("MAIL_SIGNATURE_PHONE"), env("MAIL_SIGNATURE_COMPANY")
    address, toll_free = env("MAIL_SIGNATURE_ADDRESS"), env("MAIL_SIGNATURE_TOLL_FREE")
    website = env("MAIL_SIGNATURE_WEBSITE")

    if not any((name, title, phone, company, address, toll_free, website)):
        return ""

    bold = "font-weight:700;color:#000000;"
    plain = "color:#000000;"
    lines = [f'<div style="{plain}">{_escape(_SIGNATURE_CLOSING)}</div>']

    if name:
        lines.append(f'<div style="{bold}">{_escape(name)}</div>')
    if title:
        lines.append(f'<div style="{bold}">{_escape(title)}</div>')
    if phone:
        lines.append(f'<div style="{plain}">Mobile: {_escape(phone)}</div>')
    if company:
        lines.append(f'<div style="{plain}">{_escape(company)}</div>')
    if address:
        lines.append(f'<div style="{plain}">{_escape(address)}</div>')

    # Toll free and website share the last line, separated by a pipe, with the
    # site as the only link in the block.
    tail = []
    if toll_free:
        tail.append(f"Toll Free: {_escape(toll_free)}")
    if website:
        url = website if website.startswith(("http://", "https://")) else f"https://{website}"
        tail.append(
            f'<a href="{_escape(url)}" style="color:#1155cc;text-decoration:underline;">'
            f"{_escape(website)}</a>"
        )
    if tail:
        lines.append(f'<div style="{plain}">' + " | ".join(tail) + "</div>")

    return (
        '<div style="font-family:Calibri,Segoe UI,Arial,sans-serif;'
        'font-size:14px;line-height:1.4;">' + "".join(lines) + "</div>"
    )


# Shipped with the app so the signature works on a fresh checkout with no
# configuration. MAIL_SIGNATURE_LOGO overrides it; pointing that at a file
# that does not exist falls back here rather than silently dropping the logo.
_BUNDLED_LOGO = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "signature_logo.png")


def signature_logo_path() -> str:
    """Path to the signature logo image, or "" if there is none to use.
    Existence is checked here so a stale override degrades to the bundled
    logo, and a missing bundle degrades to no logo, rather than breaking a
    send that is otherwise fine."""
    raw = (os.environ.get("MAIL_SIGNATURE_LOGO") or "").strip()
    if raw and os.path.isfile(raw):
        return raw
    return _BUNDLED_LOGO if os.path.isfile(_BUNDLED_LOGO) else ""


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
    """
    What the Settings page gets. Every smtp_* key is removed rather than
    masked: a mask still tells the browser a value exists and invites a form
    field for it, and none of the transport belongs in the UI.
    """
    values = get_settings()
    return {
        key: value
        for key, value in values.items()
        if not key.startswith(ENV_ONLY_PREFIX)
    }


def smtp_status() -> dict:
    """
    Whether the mail transport is usable, without revealing any of it.

    `configured` is what gates sending: a host is the minimum, and if a
    username is set then a password must be too, otherwise the login fails at
    send time rather than here.
    """
    values = get_settings()
    host = bool((values["smtp_host"] or "").strip())
    username = bool((values["smtp_username"] or "").strip())
    password = bool((values["smtp_password"] or "").strip())
    return {
        "configured": host and (not username or password),
        "host_set": host,
        "credentials_set": username and password,
        # Enough to tell one .env from another when something looks wrong,
        # without handing over the address itself.
        "from_domain": (values["mail_from"] or "").split("@")[-1] if values["mail_from"] else "",
    }


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
        if key.startswith(ENV_ONLY_PREFIX):
            continue  # configured in .env; no form field for it
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
