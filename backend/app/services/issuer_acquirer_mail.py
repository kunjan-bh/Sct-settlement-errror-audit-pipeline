"""
The issuing-versus-acquiring summary as an email.

Same shape of message as the aggregator note: a sentence and a table in the
body, no attachment, because whoever reads it will paste it into their own
sheet or reply to a line of it.

Only acquirers whose variance needs explaining are listed. On a normal day 266
of roughly 300 settle in full, and a table that repeats "Settled in full" 266
times buries the thirty that do not.
"""

from __future__ import annotations

from datetime import date

from app.services.issuer_acquirer_service import build_issuer_acquirer_from_range
from app.services.settings_service import get_settings

# Balanced acquirers are left out of the table. They are still in the totals,
# and the message says how many were dropped so the omission is visible.
_NOTEWORTHY = ("missing_entries", "pending_settlement", "earlier_days")


def _escape(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _money(value) -> str:
    return f"{float(value or 0):,.2f}"


_COLUMNS = (
    ("Acquirer", "name", "left"),
    ("Transacted", "txn_amount", "right"),
    ("Settled", "settled_amount", "right"),
    ("Variance", "variance_amount", "right"),
    ("Why", "reason", "left"),
)


def _table_html(rows: list[dict]) -> str:
    """Inline borders throughout -- mail clients strip stylesheets."""
    head = "".join(
        f'<th style="border:1px solid #cccccc;padding:5px 9px;background:#f3f4f6;'
        f'text-align:{align};font-size:12px;">{label}</th>'
        for label, _key, align in _COLUMNS
    )
    body = []
    for r in rows:
        cells = "".join(
            f'<td style="border:1px solid #dddddd;padding:5px 9px;'
            f'text-align:{align};font-size:12px;">'
            + (_money(r.get(key)) if key.endswith("amount") else _escape(r.get(key) or ""))
            + "</td>"
            for _label, key, align in _COLUMNS
        )
        body.append(f"<tr>{cells}</tr>")
    return (
        '<table style="border-collapse:collapse;margin:0 0 16px 0;">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _summary_html(t: dict) -> str:
    def row(label: str, value: str) -> str:
        return (
            "<tr>"
            f"<td style='padding:4px 16px 4px 0;color:#444444;font-size:13px;'>{label}</td>"
            f"<td style='padding:4px 0;font-weight:600;text-align:right;font-size:13px;'>{value}</td>"
            "</tr>"
        )

    return (
        "<table style='border-collapse:collapse;margin:0 0 18px 0;'>"
        + row("Payments taken", f"{t['txn_rows']:,}")
        + row("Transacted", _money(t["txn_amount"]))
        + row("Settled", _money(t["settled_amount"]))
        + row("Variance", _money(t["variance_amount"]))
        + row("Payments with no settlement entry", f"{t['missing_entries']:,}")
        + row("Value of those", _money(t["missing_amount"]))
        + "</table>"
    )


def build_issuer_acquirer_email(date_from: date, date_to: date) -> dict:
    """
    The draft: subject and recipients from settings, body a summary and the
    acquirers whose variance needs a word. Editable before it goes.
    """
    data = build_issuer_acquirer_from_range(date_from, date_to)
    settings = get_settings()
    t = data["totals"]

    rows = [r for r in data["acquiring"] if r.get("reason_code") in _NOTEWORTHY]
    balanced = len(data["acquiring"]) - len(rows)

    span = (
        f"for {date_from.isoformat()}" if date_from == date_to
        else f"for {date_from.isoformat()} to {date_to.isoformat()}"
    )
    missing_line = (
        f"<p style='margin:0 0 14px 0;'><strong>{t['missing_entries']:,} payments worth "
        f"{_money(t['missing_amount'])} have no settlement entry at all</strong> — nothing was "
        "ever raised to pay them out. These are the ones needing action.</p>"
        if t["missing_entries"] else
        "<p style='margin:0 0 14px 0;'>Every payment has a settlement entry against it.</p>"
    )

    body = (
        "<p style='margin:0 0 14px 0;'>Dear Sir,</p>"
        f"<p style='margin:0 0 14px 0;'>Please find below the issuing and acquiring "
        f"reconciliation {span}.</p>"
        + _summary_html(t)
        + missing_line
        + (
            f"<p style='margin:0 0 8px 0;font-size:13px;'>Acquirers whose variance needs "
            f"explaining ({len(rows)} of {len(data['acquiring'])}; the remaining {balanced} "
            "settled in full):</p>" + _table_html(rows)
            if rows else
            "<p style='margin:0 0 14px 0;'>Every acquirer settled in full.</p>"
        )
    )

    return {
        "to": settings["mail_to"],
        "cc": settings["mail_cc"],
        "from_addr": settings["mail_from"],
        "from_name": settings["mail_from_name"],
        "subject": (
            "Issuing & Acquiring reconciliation — "
            f"{date_from.isoformat()} to {date_to.isoformat()}"
        ),
        "body_html": body,
        "signature_html": settings["mail_signature_html"],
        "entity": "Issuing & Acquiring",
        "count": len(rows),
        "amount": round(t["missing_amount"], 2),
        "breakdown": {
            "need explaining": len(rows),
            "settled in full": balanced,
            "no settlement entry": t["missing_entries"],
        },
        "smtp_configured": bool(settings["smtp_host"]),
    }
