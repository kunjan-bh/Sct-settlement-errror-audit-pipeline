"""
Telling an aggregator which of their settlements were not settled in real time.

Real-time settlement is the normal path and completes in seconds. Anything that
went out On Call or on the System Default sweep was reprocessed -- someone or
something had to push it -- and the aggregator generally wants the list so they
can reconcile their own side.

The settlements go in the message as a plain table rather than an attachment.
Whoever reads it is going to paste it into their own sheet or reply to it line
by line, and neither is possible with a file they have to open first.
"""

from __future__ import annotations

from datetime import date

from app.services.settings_service import get_settings
from app.services.settlement_type_service import build_settlement_type_mid_rows

# What counts as reprocessed. Real Time is excluded by definition -- it is the
# path that needed no intervention at all.
REPROCESSED_TYPES = ("On Call", "System Default")


def _escape(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _money(value) -> str:
    return f"{float(value or 0):,.2f}"


def entity_settlement_rows(
    entity: str,
    date_from: date,
    date_to: date,
    types: tuple[str, ...] = REPROCESSED_TYPES,
) -> list[dict]:
    """Settlements for one aggregator or wallet that did not settle in real time."""
    rows = [
        r for r in build_settlement_type_mid_rows(date_from, date_to)
        if r["entity"] == entity and r["settlement_type"] in types
    ]
    rows.sort(key=lambda r: (r.get("date_time") or r.get("date") or "", -(r.get("amount") or 0)))
    return rows


_COLUMNS = (
    # Full timestamp, not just the day. An aggregator matching these against
    # their own log needs the time to tell apart several settlements for the
    # same merchant on the same date.
    ("Date & time", "date_time", "left"),
    ("MID", "mid", "left"),
    ("Merchant", "merchant_name", "left"),
    ("CRRN", "crrn", "left"),
    ("Amount", "amount", "right"),
    ("Settled by", "settlement_type", "left"),
)


def _table_html(rows: list[dict]) -> str:
    """
    The settlements as a table that survives being pasted into a spreadsheet.

    Every border and alignment is inline. Mail clients strip stylesheets, and a
    forty-row table that loses its grid in Outlook is unreadable.
    """
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
            f'{_money(r.get(key)) if key == "amount" else _escape(r.get(key) or "")}</td>'
            for _label, key, align in _COLUMNS
        )
        body.append(f"<tr>{cells}</tr>")

    total = sum(float(r.get("amount") or 0) for r in rows)
    body.append(
        '<tr><td colspan="4" style="border:1px solid #cccccc;padding:5px 9px;'
        'font-weight:600;font-size:12px;">Total</td>'
        f'<td style="border:1px solid #cccccc;padding:5px 9px;text-align:right;'
        f'font-weight:600;font-size:12px;">{_money(total)}</td>'
        '<td style="border:1px solid #cccccc;"></td></tr>'
    )

    return (
        '<table style="border-collapse:collapse;margin:0 0 16px 0;">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def build_entity_email(
    entity: str,
    date_from: date,
    date_to: date,
    types: tuple[str, ...] = REPROCESSED_TYPES,
) -> dict:
    """
    The draft for one aggregator: subject and sender from settings, body a
    sentence and the table. Editable in the overlay before it goes.
    """
    rows = entity_settlement_rows(entity, date_from, date_to, types)
    settings = get_settings()
    total = sum(float(r.get("amount") or 0) for r in rows)

    span = (
        f"on {date_from.isoformat()}" if date_from == date_to
        else f"between {date_from.isoformat()} and {date_to.isoformat()}"
    )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["settlement_type"]] = counts.get(r["settlement_type"], 0) + 1
    breakdown = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items())) or "none"

    table = (
        _table_html(rows) if rows
        else '<p style="margin:0 0 14px 0;">No such settlements in this period.</p>'
    )
    body = (
        '<p style="margin:0 0 14px 0;">Dear Team,</p>'
        '<p style="margin:0 0 14px 0;">The settlements below were not settled in real '
        f'time {span} — {breakdown}. Please reconcile these against your records.</p>'
        f"{table}"
        '<p style="margin:0 0 14px 0;">Please let us know if any of these do not match '
        "your side.</p>"
    )

    return {
        # Deliberately empty. An aggregator's address is not something this
        # system holds, and guessing one is worse than asking for it.
        "to": "",
        "cc": "",
        "from_addr": settings["mail_from"],
        "from_name": settings["mail_from_name"],
        "subject": (
            f"Reprocessed QR settlements — {entity} — "
            f"{date_from.isoformat()} to {date_to.isoformat()}"
        ),
        "body_html": body,
        "signature_html": settings["mail_signature_html"],
        "entity": entity,
        "count": len(rows),
        "amount": round(total, 2),
        "breakdown": counts,
        "smtp_configured": bool(settings["smtp_host"]),
    }
