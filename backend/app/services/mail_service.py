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
from us. The full Excel report rides along as a normal attachment -- the
picture is the summary, the workbook is the evidence behind it.
"""
import base64
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.extensions import db
from app.models.batch import Batch
from app.models.issue_status import IssueStatus
from app.models.transaction import Transaction
from app.services.analytics_service import EXCLUDED_OPS_STATUS, SOLVED_OPS_STATUSES
from app.services.chart_image import render_entity_volume_png, render_error_resolution_png
from app.services.settings_service import get_settings, signature_logo_path
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
    # Errors per partner, for the volume chart. Counted in the same pass and
    # behind the same filters as everything else, so the bars add up to
    # total_errors rather than telling a slightly different story.
    per_entity: dict[str, int] = {}
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
        entity = "SCT" if side == "sct" else (row.partner_name or "No Aggregator")
        per_entity[entity] = per_entity.get(entity, 0) + 1
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
        "per_entity": sorted(per_entity.items(), key=lambda kv: -kv[1]),
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
        else "<span style='color:#888888'>(no batch notes were recorded)</span>"
    )
    s = stats["status_breakdown"]
    r = stats["resolution"]

    def row(label, value):
        # Deliberately monochrome. The figures were colour-coded to match the
        # chart, but in a table the colour said nothing the label did not
        # already say, and read as decoration in a report to management.
        return (
            f"<tr>"
            f"<td style='padding:4px 16px 4px 0;color:#444444;'>{_escape(label)}</td>"
            f"<td style='padding:4px 0;font-weight:600;text-align:right;'>{value:,}</td>"
            f"</tr>"
        )

    return f"""<p style="margin:0 0 14px 0;">Dear Sir,</p>
<p style="margin:0 0 14px 0;">Please find below the QR transaction analysis summary for
<strong>{_escape(batch.name)}</strong>.</p>

<div style="margin:0 0 18px 0;padding:10px 14px;border-left:2px solid #cccccc;">
  {notes_html}
</div>

<table style="border-collapse:collapse;font-size:13px;margin:0 0 18px 0;">
  {row("Total transactions", stats["total_transactions"])}
  {row("Error transactions", stats["total_errors"])}
  {row("Failed", s["failed"])}
  {row("Lo Progress", s["lo_progress"])}
  {row("Pending", s["pending"])}
  {row("Solved", r["solved"])}
  {row("Unsolved", r["unsolved"])}
  {row("Resolution rate", stats["resolution_rate"])}
</table>"""


def _escape(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def report_attachment(batch_id: int, batch_name: str) -> tuple[bytes, str]:
    """
    The same workbook the Download Full Report button produces, so the mailed
    copy and the downloaded one can never differ.

    Imported lazily: report_generator pulls in most of the service layer, and
    composing a preview should not drag all of that in until an attachment is
    actually wanted.
    """
    from app.services.report_generator import generate_report_bytes

    return generate_report_bytes(batch_id), f"SmartQR_Settlement_Report_{batch_name}.xlsx"


def _logo_bytes() -> tuple[bytes, str]:
    """(image bytes, subtype) for the signature logo, or (b"", "") when none is
    configured. A path that no longer exists degrades to no logo rather than
    failing the send."""
    path = signature_logo_path()
    if not path:
        return b"", ""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    subtype = {"jpg": "jpeg", "svg": "svg+xml"}.get(ext, ext or "png")
    try:
        with open(path, "rb") as fh:
            return fh.read(), subtype
    except OSError:
        return b"", ""


def _logo_base64() -> str:
    data, _subtype = _logo_bytes()
    return base64.b64encode(data).decode("ascii") if data else ""


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
    volume_png = render_entity_volume_png(
        stats["per_entity"], title="Error volume by aggregator / bank"
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
        "volume_png_base64": base64.b64encode(volume_png).decode("ascii") if volume_png else "",
        "signature_html": settings["mail_signature_html"],
        "signature_logo_base64": _logo_base64(),
        "attach_report": True,
        "report_filename": f"SmartQR_Settlement_Report_{batch.name}.xlsx",
        "stats": stats,
        "smtp_configured": bool(settings["smtp_host"]),
    }


def send_batch_email(
    *, subject: str, from_addr: str, from_name: str, to: str, cc: str,
    body_html: str, chart_png_base64: str = "", volume_png_base64: str = "",
    signature_html: str | None = None,
    batch_id: int | None = None, batch_name: str = "", attach_report: bool = False,
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

    def _decode(b64):
        if not b64:
            return b""
        try:
            return base64.b64decode(b64)
        except Exception:
            return b""

    # Each inline image needs its own Content-ID; cid: strips the angle
    # brackets make_msgid supplies.
    cid = make_msgid()
    volume_cid = make_msgid()
    png = _decode(chart_png_base64)
    volume_png = _decode(volume_png_base64)

    chart_html = ""
    if png:
        chart_html += (
            f'<div style="margin:18px 0;">'
            f'<img src="cid:{cid[1:-1]}" alt="Errors and resolution" '
            f'style="max-width:100%;height:auto;" /></div>'
        )
    if volume_png:
        chart_html += (
            f'<div style="margin:18px 0;">'
            f'<img src="cid:{volume_cid[1:-1]}" alt="Error volume by aggregator" '
            f'style="max-width:100%;height:auto;" /></div>'
        )

    # The signature is appended here rather than baked into the editable body,
    # so editing the message in the overlay cannot accidentally delete it.
    # Passing signature_html explicitly (even "") overrides the saved one.
    sig = settings["mail_signature_html"] if signature_html is None else signature_html

    # The logo is a second inline image, referenced from inside the signature
    # block. Kept out of the signature HTML itself (settings_service builds
    # that) because only the sender knows the Content-ID.
    logo_bytes, logo_subtype = _logo_bytes()
    logo_cid = make_msgid() if logo_bytes else ""
    logo_html = (
        f'<div style="margin-top:10px;">'
        f'<img src="cid:{logo_cid[1:-1]}" alt="" style="border:0;" /></div>'
        if logo_bytes else ""
    )

    sig_html = (
        f'<div style="margin-top:22px;padding-top:12px;'
        f'border-top:1px solid #e5e7eb;">{sig}{logo_html}</div>'
        if ((sig or "").strip() or logo_bytes) else ""
    )

    html = (
        '<div style="font-family:Segoe UI,Calibri,Arial,sans-serif;font-size:14px;'
        f'color:#000000;line-height:1.5;">{body_html or ""}{chart_html}{sig_html}</div>'
    )
    msg.add_alternative(html, subtype="html")

    if png or volume_png or logo_bytes:
        # Attach to the HTML part, not the message root, or Outlook shows these
        # as separate attachments instead of inline.
        html_part = msg.get_payload()[-1]
        if png:
            html_part.add_related(png, maintype="image", subtype="png", cid=cid)
        if volume_png:
            html_part.add_related(
                volume_png, maintype="image", subtype="png", cid=volume_cid
            )
        if logo_bytes:
            html_part.add_related(
                logo_bytes, maintype="image", subtype=logo_subtype, cid=logo_cid
            )

    attached = None
    if attach_report and batch_id is not None:
        # Attached to the message root, not to the HTML part: this one is meant
        # to arrive as a file, unlike the chart which is referenced inline.
        report_bytes, filename = report_attachment(batch_id, batch_name or str(batch_id))
        msg.add_attachment(
            report_bytes,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
        )
        attached = {"filename": filename, "bytes": len(report_bytes)}

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

    return {
        "sent_to": to_list, "cc": cc_list, "subject": msg["Subject"],
        "attachment": attached,
    }
