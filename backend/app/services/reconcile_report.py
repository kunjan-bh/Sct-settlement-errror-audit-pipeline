"""
The reconciliation workbook.

Sheet 1 is the reconciliation itself: what came in, what went out, and every
rupee of the difference accounted for by name. It is meant to be read top to
bottom and to balance -- if the explained lines do not add up to the
difference, the sheet says so rather than hiding it.

Sheet 2 is the exception report: the rows someone has to do something about.
Then a sheet per finding, so a specific question ("what was never raised for
payment?") has a specific place to look.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

_TITLE = Font(bold=True, size=14, color="111827")
_H2 = Font(bold=True, size=11, color="111827")
_LABEL = Font(bold=True, size=10, color="374151")
_HEAD_FILL = PatternFill("solid", fgColor="1F2937")
_HEAD_FONT = Font(bold=True, color="FFFFFF", size=10)
_BAD = PatternFill("solid", fgColor="FEE2E2")
_WARN = PatternFill("solid", fgColor="FEF3C7")
_OK = PatternFill("solid", fgColor="DCFCE7")
_THIN = Side(style="thin", color="D1D5DB")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_MONEY = "#,##0.00"

# (header, key, width, is_money) for a per-payment sheet.
_FINDING_LABEL = {
    "not_in_settlement_report": "Never raised for payment",
    "settlement_failed": "Settlement failed",
    "awaiting_settlement": "Awaiting settlement",
    "settled_later": "Settled later",
    "settled": "Settled",
    "batch_merchant": "Batch-settled merchant",
}

_EXCEPTION_COLUMNS = [
    ("Finding", "_finding", 24, False),
    ("MID", "mid", 19, False),
    ("Merchant", "merchant", 24, False),
    ("Aggregator", "partner", 18, False),
    ("Txn CRRN", "crrn", 15, False),
    ("Txn amount", "txn_amount", 14, True),
    ("Txn date", "txn_date_time", 19, False),
    ("Settlement status", "settle_status", 16, False),
    ("Hold balance", "hold_balance", 13, True),
    ("Total balance", "total_balance", 13, True),
    ("Why", "why", 60, False),
]

_TXN_COLUMNS = [
    ("MID", "mid", 19, False),
    ("Merchant", "merchant", 24, False),
    ("Aggregator", "partner", 18, False),
    ("Txn CRRN", "crrn", 15, False),
    ("Txn amount", "txn_amount", 14, True),
    ("Txn date", "txn_date_time", 19, False),
    ("Settlement CRRN", "settle_crrn", 15, False),
    ("Settled amount", "settle_amount", 14, True),
    ("Settlement status", "settle_status", 16, False),
    ("Settlement date", "settle_date", 12, False),
    ("Hold balance", "hold_balance", 13, True),
    ("Total balance", "total_balance", 13, True),
    ("Why", "why", 60, False),
]

_BUCKET_SHEETS = [
    ("not_in_settlement_report", "Never raised", _BAD),
    ("settlement_failed", "Settlement failed", _BAD),
    ("awaiting_settlement", "Awaiting settlement", _WARN),
    ("settled_later", "Settled later", _OK),
]


def _write_table(ws, columns, rows, start_row=1, fill=None):
    for col, (label, _k, width, _m) in enumerate(columns, start=1):
        c = ws.cell(row=start_row, column=col, value=label)
        c.fill = _HEAD_FILL
        c.font = _HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = _BORDER
        ws.column_dimensions[get_column_letter(col)].width = width

    for i, row in enumerate(rows):
        r = start_row + 1 + i
        for col, (_label, key, _w, money) in enumerate(columns, start=1):
            c = ws.cell(row=r, column=col, value=row.get(key))
            c.border = _BORDER
            if money:
                c.number_format = _MONEY
            if key == "why":
                c.alignment = Alignment(wrap_text=True, vertical="top")
            if fill:
                c.fill = fill
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    if rows:
        ws.auto_filter.ref = (
            f"A{start_row}:{get_column_letter(len(columns))}{start_row + len(rows)}"
        )


def generate_reconcile_xlsx(data: dict, generated_at: str) -> bytes:
    t = data["totals"]
    rng = data["range"]
    wb = Workbook()

    # --- Sheet 1: the reconciliation ---------------------------------------
    ws = wb.active
    ws.title = "Reconciliation"
    ws["A1"] = "QR settlement reconciliation"
    ws["A1"].font = _TITLE
    ws["A2"] = f"{rng['from']} to {rng['to']}   ·   generated {generated_at}"

    rows = [
        ("", ""),
        ("Incoming — QR payments taken", None),
        ("Payments", t["incoming_txns"]),
        ("Amount", t["incoming_amount"]),
        ("", ""),
        ("Outgoing — settled to merchants", None),
        ("Settlements", t["settled_txns"]),
        ("Amount", t["settled_amount"]),
        ("", ""),
        ("Difference", t["difference"]),
        ("", ""),
        ("Accounted for by", None),
        ("Settled later / reprocessed", t["settled_later_amount"]),
        ("Settlement failed — outstanding", t["settlement_failed_amount"]),
        ("Awaiting settlement (within normal time)", t["awaiting_amount"]),
        ("Never raised for payment", t["not_in_report_amount"]),
        ("Batch-settled merchants (reconciled on totals)", t["batch_merchant_amount"]),
    ]
    r = 4
    for label, value in rows:
        if not label:
            r += 1
            continue
        cell = ws.cell(row=r, column=1, value=label)
        cell.font = _H2 if value is None else _LABEL
        if value is not None:
            v = ws.cell(row=r, column=2, value=value)
            if isinstance(value, float):
                v.number_format = _MONEY
        r += 1

    explained = round(
        t["settled_later_amount"] + t["settlement_failed_amount"]
        + t["awaiting_amount"] + t["not_in_report_amount"]
        + t["batch_merchant_amount"], 2
    )
    gap = round(t["difference"] - explained, 2)
    r += 1
    ws.cell(row=r, column=1, value="Explained total").font = _LABEL
    ws.cell(row=r, column=2, value=explained).number_format = _MONEY
    r += 1
    # Stating the residual outright: a reconciliation that quietly does not add
    # up is worse than one that says by how much.
    ws.cell(row=r, column=1, value="Unexplained residual").font = _LABEL
    resid = ws.cell(row=r, column=2, value=gap)
    resid.number_format = _MONEY
    resid.fill = _OK if abs(gap) < 0.01 else _BAD
    r += 1
    ws.cell(row=r, column=1, value="Settled at a different amount than taken").font = _LABEL
    mm = ws.cell(row=r, column=2, value=t.get("amount_mismatches", 0))
    mm.fill = _OK if not t.get("amount_mismatches") else _BAD

    r += 2
    # Saying plainly what the zero above does and does not prove. Every payment
    # is in exactly one of the lines, so they always sum to the difference --
    # presenting that as a clean bill of health would be flattering the report.
    note = data.get("balance_note") or ""
    ws.cell(row=r, column=1, value=note).alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 2, end_column=2)

    ws.column_dimensions["A"].width = 46
    ws.column_dimensions["B"].width = 20

    # --- Sheet 2: exceptions -----------------------------------------------
    ex = wb.create_sheet("Exceptions")
    ex["A1"] = f"Exceptions — {t['exceptions']:,} payments that have not reached the merchant"
    ex["A1"].font = _TITLE
    ex["A2"] = (
        "Money still owed, worst first. Settlements that failed and then went out on a "
        "retry are not here — they are on the Settled later sheet, since they need "
        "nothing done about them."
    )
    rows = [
        {**x, "_finding": _FINDING_LABEL.get(x.get("bucket"), x.get("bucket"))}
        for x in data.get("exceptions", [])
    ]
    _write_table(ex, _EXCEPTION_COLUMNS, rows, start_row=4)

    # --- A sheet per finding ------------------------------------------------
    for key, title, fill in _BUCKET_SHEETS:
        rows = data.get("buckets", {}).get(key, [])
        sheet = wb.create_sheet(title)
        sheet["A1"] = f"{title} — {len(rows):,}"
        sheet["A1"].font = _TITLE
        _write_table(sheet, _TXN_COLUMNS, rows, start_row=3, fill=fill if rows else None)

    # --- Batch merchants ----------------------------------------------------
    batch_cols = [
        ("MID", "mid", 19, False),
        ("Aggregator", "partner", 18, False),
        ("Payments taken", "txns", 14, False),
        ("Amount taken", "took_amount", 15, True),
        ("Settlements", "settlements", 12, False),
        ("Amount paid", "paid_amount", 15, True),
        ("Failed settlements", "failed_settlements", 16, False),
        ("Variance", "variance", 14, True),
        ("Hold balance", "hold_balance", 14, True),
        ("Explained by balance", "explained_by_balance", 18, False),
    ]
    bs = wb.create_sheet("Batch merchants")
    bs["A1"] = "Merchants settled in batches — reconciled on totals, not per payment"
    bs["A1"].font = _TITLE
    bs["A2"] = (
        "These merchants have no settlement row per payment, so a variance here is "
        "a period-edge effect as often as a problem. A variance the merchant is "
        "still holding is money on its way."
    )
    _write_table(bs, batch_cols, data.get("batch", []), start_row=4)

    # --- Orphans ------------------------------------------------------------
    orphan_cols = [
        ("MID", "mid", 19, False),
        ("CRRN", "crrn", 15, False),
        ("Amount", "amount", 15, True),
        ("Date", "date", 12, False),
        ("Bank / wallet", "bank_or_wallet", 24, False),
        ("Remarks", "remarks", 50, False),
    ]
    os_ = wb.create_sheet("Unmatched settlements")
    os_["A1"] = f"Settlements with no matching payment — {t['orphan_settlements']:,}"
    os_["A1"].font = _TITLE
    os_["A2"] = (
        "Money paid out with no incoming payment found for it in this window. "
        "Usually a payment from before the range; worth checking the large ones."
    )
    _write_table(os_, orphan_cols, data.get("orphans", []), start_row=4)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
