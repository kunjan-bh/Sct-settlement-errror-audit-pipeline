"""
End-of-batch summary email: compose it, preview it, send it.

Flow, deliberately not fire-and-forget: finishing a batch builds a draft
(build_batch_email) which the frontend shows in an editable overlay, and
nothing leaves the building until someone presses Send. Recipients, subject
and body are all editable at that point; the defaults come from
settings_service so the common case is press-and-go.

The Errors & Resolution ring is rendered server-side (chart_image.py) and
attached inline by Content-ID, so it shows in the body rather than as a
download, and the mail does not depend on the recipient fetching anything
from us.
"""
import base64
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.extensions import db
from app.models.batch import Batch
from app.models.issue_status import IssueStatus
from app.models.transaction import Transaction
from app.services.analytics_service import EXCLUDED_OPS_STATUS, SOLVED_OPS_STATUSES
from app.services.chart_image import render_error_resolution_png
from app.services.settings_service import get_settings
from app.services.status_utils import normalize_txn_status


class MailError(Exception):
    """Anything that stops a send: no SMTP host configured, no recipient, or
    the server refusing us. Carries a message meant to be shown to the user."""


def _split_addresses(raw) -> list[str]:
    """'a@x, b@y; c@z' -> ['a@x', 'b@y', 'c@z']. Blank entries dropped."""
    if not raw:
        return []
    parts = str(raw).replace(";", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def batch_summary_numbers(batch_id: int) -> dict:
    """
    The one batch's errors split two ways: by transaction status, and by
    whether ops finished them.

    Same rules as analytics_service.build_analytics -- retry-resolved rows and
    ops-excluded rows are both out, so failed + pending + lo_progress ==
    solved + unsolved and the two rings describe one population. Scoped to a
    single batch rather than a date range, which is what the email is about.
    """
    rows = (
        db.session.query(
            Transaction.status, Transaction.error_side, Transaction.error_category,
            Transaction.partner_name, Transaction.mid, Transaction.retry_resolved,
        )
        .filter(Transaction.batch_id == batch_id)
        .all()
    )
    issue_map = {
        (i.side, i.partner_name, i.category, i.txn_status): i
        for i in IssueStatus.query.filter_by(batch_id=batch_id).all()
    }

    status_breakdown = {"failed": 0, "pending": 0, "lo_progress": 0}
    solved = unsolved = excluded = retry_resolved = 0
    total_transactions = len(rows)

    for row in rows:
        if row.retry_resolved:
            retry_resolved += 1
            continue
        txn_status = normalize_txn_status(row.status)
        if txn_status == "success":
            continue

        side = row.error_side or "unknown"
        category = row.error_category or "Unclassified"
        issue = issue_map.get(
            (side, row.partner_name if side != "sct" else None, category, txn_status)
        )

        override = None
        if issue and issue.mid_overrides and row.mid in issue.mid_overrides:
            raw = issue.mid_overrides[row.mid]
            override = raw if isinstance(raw, dict) else {"status": raw}

        eff = (override.get("status") if override else None) or (
            issue.status if issue else "pending"
        )
        if eff == EXCLUDED_OPS_STATUS:
            excluded += 1
            continue

        if txn_status in status_breakdown:
            status_breakdown[txn_status] += 1
        if eff in SOLVED_OPS_STATUSES:
            solved += 1
        else:
            unsolved += 1

    total_errors = solved + unsolved
    return {
        "total_transactions": total_transactions,
        "total_errors": total_errors,
        "status_breakdown": status_breakdown,
        "resolution": {"solved": solved, "unsolved": unsolved},
        "excluded": excluded,
        "retry_resolved": retry_resolved,
        "resolution_rate": round(solved * 100.0 / total_errors, 2) if total_errors else 0.0,
    }


def _default_body_html(batch, stats: dict) -> str:
    """
    Default message body: the operator's Batch Notes verbatim, then the same
    figures the ring shows. Notes come first because they are the human part --
    the numbers are already in the picture.

    Editable in the preview overlay, so this is a starting point rather than a
    fixed template.
    """
    notes = (batch.notes or "").strip()
    notes_html = (
        "<br>".join(_escape(line) for line in notes.splitlines())
        if notes
        else "<span style='color:#9ca3af'>(no batch notes were recorded)</span>"
    )
    s = stats["status_breakdown"]
    r = stats["resolution"]

    def row(label, value, color="#111827"):
        return (
            f"<tr>"
            f"<td style='padding:4px 14px 4px 0;color:#6b7280;'>{_escape(label)}</td>"
            f"<td style='padding:4px 0;font-weight:600;color:{color};'>{value:,}</td>"
            f"</tr>"
        )

    return f"""<p style="margin:0 0 14px 0;">Dear Sir,</p>
<p style="margin:0 0 14px 0;">Please find below the QR transaction analysis summary for
<strong>{_escape(batch.name)}</strong>.</p>

<div style="margin:0 0 18px 0;padding:12px 14px;background:#f9fafb;border-left:3px solid #2563eb;">
  {notes_html}
</div>

<table style="border-collapse:collapse;font-size:13px;margin:0 0 18px 0;">
  {row("Total transactions", stats["total_transactions"])}
  {row("Error transactions", stats["total_errors"])}
  {row("Failed", s["failed"], "#dc2626")}
  {row("Lo Progress", s["lo_progress"], "#2563eb")}
  {row("Pending", s["pending"], "#d97706")}
  {row("Solved", r["solved"], "#16a34a")}
  {row("Unsolved", r["unsolved"], "#6b7280")}
</table>

<p style="margin:0 0 6px 0;font-size:13px;color:#6b7280;">
  Resolution rate: <strong style="color:#111827;">{stats["resolution_rate"]}%</strong>
</p>"""


def _escape(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_batch_email(batch_id: int) -> dict:
    """
    The draft the preview overlay edits: addresses and subject from settings,
    body from the batch notes plus the summary figures, and the ring as a
    base64 PNG the overlay can show inline and hand back on send.
    """
    batch = Batch.query.get_or_404(batch_id)
    settings = get_settings()
    stats = batch_summary_numbers(batch_id)

    png = render_error_resolution_png(
        stats["status_breakdown"],
        stats["resolution"],
        title=f"{batch.name} — {stats['total_errors']:,} error transactions",
    )

    return {
        "batch": batch.to_dict(),
        "subject": settings["mail_subject"],
        "from_addr": settings["mail_from"],
        "from_name": settings["mail_from_name"],
        "to": settings["mail_to"],
        "cc": settings["mail_cc"],
        "body_html": _default_body_html(batch, stats),
        "chart_png_base64": base64.b64encode(png).decode("ascii") if png else "",
        "signature_html": settings["mail_signature_html"],
        "stats": stats,
        "smtp_configured": bool(settings["smtp_host"]),
    }


def send_batch_email(
    *, subject: str, from_addr: str, from_name: str, to: str, cc: str,
    body_html: str, chart_png_base64: str = "", signature_html: str | None = None,
) -> dict:
    """
    Send one composed message. Everything is passed in rather than re-derived,
    so what the user saw and edited in the overlay is exactly what goes out.
    """
    settings = get_settings()
    host = (settings["smtp_host"] or "").strip()
    if not host:
        raise MailError("No SMTP host configured. Set one on the Settings page first.")

    to_list = _split_addresses(to)
    cc_list = _split_addresses(cc)
    if not to_list:
        raise MailError("At least one recipient is required.")
    if not (from_addr or "").strip():
        raise MailError("A From address is required.")

    msg = EmailMessage()
    msg["Subject"] = subject or settings["mail_subject"]
    msg["From"] = formataddr((from_name or None, from_addr))
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    # Plain-text alternative for clients that will not render HTML. Crude tag
    # stripping is fine here: the body is our own markup, not arbitrary input.
    import re
    text_body = re.sub(r"<[^>]+>", "", body_html or "")
    text_body = re.sub(r"\n{3,}", "\n\n", text_body).strip()
    msg.set_content(text_body or "QR Transaction Analysis Report")

    cid = make_msgid()
    chart_html = ""
    png = b""
    if chart_png_base64:
        try:
            png = base64.b64decode(chart_png_base64)
        except Exception:
            png = b""
    if png:
        # cid: strips the angle brackets make_msgid supplies.
        chart_html = (
            f'<div style="margin:18px 0;">'
            f'<img src="cid:{cid[1:-1]}" alt="Errors and resolution" '
            f'style="max-width:100%;height:auto;" /></div>'
        )

    # The signature is appended here rather than baked into the editable body,
    # so editing the message in the overlay cannot accidentally delete it.
    # Passing signature_html explicitly (even "") overrides the saved one.
    sig = settings["mail_signature_html"] if signature_html is None else signature_html
    sig_html = (
        f'<div style="margin-top:22px;padding-top:12px;'
        f'border-top:1px solid #e5e7eb;">{sig}</div>'
        if (sig or "").strip() else ""
    )

    html = (
        '<div style="font-family:Segoe UI,Calibri,Arial,sans-serif;font-size:14px;'
        f'color:#111827;line-height:1.5;">{body_html or ""}{chart_html}{sig_html}</div>'
    )
    msg.add_alternative(html, subtype="html")

    if png:
        # Attach to the HTML part, not the message root, or Outlook shows it as
        # a separate attachment instead of inline.
        html_part = msg.get_payload()[-1]
        html_part.add_related(png, maintype="image", subtype="png", cid=cid)

    recipients = to_list + cc_list
    port = settings["smtp_port"] or 587
    timeout = settings["smtp_timeout"] or 30

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
        with server:
            server.ehlo()
            if port != 465 and settings["smtp_use_tls"]:
                server.starttls()
                server.ehlo()
            if settings["smtp_username"]:
                server.login(settings["smtp_username"], settings["smtp_password"])
            server.send_message(msg, from_addr=from_addr, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(f"SMTP rejected the credentials: {e}") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Could not send via {host}:{port} — {e}") from e

    return {"sent_to": to_list, "cc": cc_list, "subject": msg["Subject"]}
