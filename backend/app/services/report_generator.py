import os
from io import BytesIO
from datetime import datetime
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, PieChart, LineChart, Reference
from openpyxl.chart.label import DataLabelList

from app.models.batch import Batch
from app.models.transaction import Transaction
from app.models.issue_status import IssueStatus
from app.services.dashboard_service import build_dashboard
from app.services.excel_ingest import _find_header_index
from app.services.status_utils import normalize_txn_status
from app.services.error_classification import (
    ENTITY_TYPE_LABELS,
    SIDE_LABELS,
    build_error_classification,
)


def generate_report_bytes(batch_id: int) -> bytes:
    """
    Generates the 5-sheet Excel report workbook for the given batch_id and
    returns its raw bytes.
    Sheet 1: "Overview"
    Sheet 2: "Issue Summary"
    Sheet 3: "Lo Status"
    Sheet 4: "Processed Records" (second-to-last -- the raw row dump, kept
             behind the summary sheets so those open first)
    Sheet 5: "Error Classify"
    """
    batch = Batch.query.get_or_404(batch_id)
    transactions = Transaction.query.filter_by(batch_id=batch_id).all()
    issues = IssueStatus.query.filter_by(batch_id=batch_id).all()
    dashboard = build_dashboard(batch_id)

    # Fast lookup for IssueStatus status & comment:
    # issue_key = (side, partner_name, category, txn_status)
    issue_status_map = {
        (i.side, i.partner_name, i.category, i.txn_status): i
        for i in issues
    }

    wb = openpyxl.Workbook()
    # Remove default sheet
    default_sheet = wb.active

    # --- Styles ---
    font_title = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    font_section = Font(name="Calibri", size=12, bold=True, color="1F4E79")
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=10, bold=True)
    font_regular = Font(name="Calibri", size=10)

    fill_header = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
    fill_sub_header = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    fill_zebra = PatternFill(start_color="F9FBFD", end_color="F9FBFD", fill_type="solid")

    fill_solved = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    font_solved = Font(name="Calibri", size=10, color="006100", bold=True)

    fill_pending = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    font_pending = Font(name="Calibri", size=10, color="9C5700", bold=True)

    thin_border_side = Side(border_style="thin", color="D9D9D9")
    border_cell = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # ==========================================
    # SHEET 1: Overview
    # ==========================================
    ws1 = wb.create_sheet(title="Overview")
    ws1.views.sheetView[0].showGridLines = True

    # Title
    ws1["A1"] = "SmartQR Settlement Processing Report — Overview"
    ws1["A1"].font = font_title

    # Metadata Block
    ws1["A3"] = "Batch Name:"
    ws1["B3"] = batch.name
    ws1["A4"] = "Created Date:"
    ws1["B4"] = batch.created_at.strftime("%Y-%m-%d %H:%M:%S") if batch.created_at else ""
    ws1["A5"] = "Status:"
    ws1["B5"] = batch.status.upper()
    ws1["A6"] = "Total Transactions:"
    ws1["B6"] = dashboard["totals"]["total_transactions"]
    ws1["A7"] = "Pending Transactions:"
    ws1["B7"] = dashboard["totals"]["pending"]
    ws1["A8"] = "Lo Progress Transactions:"
    ws1["B8"] = dashboard["totals"]["lo_progress"]
    ws1["A9"] = "Settlement Failed:"
    ws1["B9"] = dashboard["totals"]["settlement_failed"]
    ws1["A10"] = "Transaction Failed (SCT):"
    ws1["B10"] = dashboard["totals"]["transaction_failed"]
    ws1["A11"] = "Reprocessed & Settled (excluded):"
    ws1["B11"] = dashboard["totals"].get("retry_resolved", 0)
    ws1["A12"] = "Batch Notes:"
    ws1["B12"] = batch.notes or ""

    for row_idx in range(3, 13):
        ws1[f"A{row_idx}"].font = font_bold
        ws1[f"B{row_idx}"].font = font_regular

    # Table 1: Partner Summary (aggregators first, then banks/wallets).
    # Counts come from the dashboard's three per-status issue lists -- there
    # is no precomputed "issue_count", the unique-issue total is their length.
    ws1["A15"] = "Partner Summary"
    ws1["A15"].font = font_section

    headers_agg = ["Partner", "Type", "Failed", "Pending", "Lo Progress", "Unique Issues"]
    for col_idx, h in enumerate(headers_agg, start=1):
        cell = ws1.cell(row=16, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center

    curr_row = 17
    partner_rows = (
        [("Aggregator", p) for p in dashboard["aggregator_summary"]]
        + [("Bank / Wallet", p) for p in dashboard["bank_summary"]]
    )
    for type_label, agg in partner_rows:
        unique_issues = (
            len(agg["issues_failed"]) + len(agg["issues_pending"]) + len(agg["issues_lo_progress"])
        )
        ws1.cell(row=curr_row, column=1, value=agg["partner_name"]).alignment = align_left
        ws1.cell(row=curr_row, column=2, value=type_label).alignment = align_left
        ws1.cell(row=curr_row, column=3, value=agg["failed"]).alignment = align_right
        ws1.cell(row=curr_row, column=4, value=agg["pending"]).alignment = align_right
        ws1.cell(row=curr_row, column=5, value=agg["lo_progress"]).alignment = align_right
        ws1.cell(row=curr_row, column=6, value=unique_issues).alignment = align_right
        for col_idx in range(1, 7):
            ws1.cell(row=curr_row, column=col_idx).font = font_regular
            ws1.cell(row=curr_row, column=col_idx).border = border_cell
        curr_row += 1

    # Table 2: SCT Summary
    curr_row += 2
    ws1.cell(row=curr_row, column=1, value="SCT Summary").font = font_section
    curr_row += 1

    headers_sct = ["SCT Issue Category", "Txn Status", "Count"]
    for col_idx, h in enumerate(headers_sct, start=1):
        cell = ws1.cell(row=curr_row, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
    curr_row += 1

    sct = dashboard["sct_summary"]
    sct_issues = sct["issues_failed"] + sct["issues_pending"] + sct["issues_lo_progress"]
    for sct_issue in sct_issues:
        ws1.cell(row=curr_row, column=1, value=sct_issue["category"]).alignment = align_left
        ws1.cell(
            row=curr_row, column=2,
            value=sct_issue["txn_status"].replace("_", " ").title(),
        ).alignment = align_center
        ws1.cell(row=curr_row, column=3, value=sct_issue["count"]).alignment = align_right
        for col_idx in range(1, 4):
            ws1.cell(row=curr_row, column=col_idx).font = font_regular
            ws1.cell(row=curr_row, column=col_idx).border = border_cell
        curr_row += 1

    # ==========================================
    # SHEET 2: Issue Summary
    # ==========================================
    ws3 = wb.create_sheet(title="Issue Summary")
    ws3.views.sheetView[0].showGridLines = True

    ws3["A1"] = "Issue Summary — Breakdown by Partner & SCT"
    ws3["A1"].font = font_title

    headers_summary = ["Entity / Partner", "Issue Category", "Affected Txns", "Solved Status", "Ops Remarks"]
    for col_idx, h in enumerate(headers_summary, start=1):
        cell = ws3.cell(row=3, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center

    curr_row = 4
    
    from collections import defaultdict
    summary_counts = defaultdict(int)
    summary_comments = {}
    # Sheet 4 accumulator: a row belongs on Lo Status if the transaction
    # itself came in as "In progress", OR if ops manually moved the issue to
    # Lo Progress. Keying it off ops status alone (as this used to) left the
    # sheet empty for every batch nobody had hand-flagged.
    lo_counts = defaultdict(int)
    lo_comments = {}

    for txn in transactions:
        if txn.retry_resolved:
            continue  # settled by a later reprocess; not an outstanding issue

        side = txn.error_side
        partner = txn.partner_name if side != "sct" else "SCT"
        category = txn.error_category or "Unclassified"
        txn_original_status = normalize_txn_status(txn.status)

        issue_obj = issue_status_map.get((side, txn.partner_name if side != "sct" else None, category, txn_original_status))
        
        override_obj = None
        if issue_obj and issue_obj.mid_overrides and txn.mid in issue_obj.mid_overrides:
            raw = issue_obj.mid_overrides[txn.mid]
            if isinstance(raw, dict):
                override_obj = raw
            else:
                override_obj = {"status": raw, "remark": ""}

        eff_status = (override_obj["status"] if override_obj else None) or (issue_obj.status if issue_obj else "pending")
        if eff_status == "exclude":
            continue
            
        key = (side, partner, category, eff_status)
        summary_counts[key] += 1
        if key not in summary_comments:
             summary_comments[key] = issue_obj.comment if issue_obj else ""

        if side != "sct" and (txn_original_status == "lo_progress" or eff_status == "lo_progress"):
            lo_key = (partner, category, txn_original_status, eff_status)
            lo_counts[lo_key] += 1
            if lo_key not in lo_comments:
                lo_comments[lo_key] = issue_obj.comment if issue_obj else ""

    # 1. Aggregator & Bank issues
    agg_keys = sorted([k for k in summary_counts.keys() if k[0] != "sct"])
    for key in agg_keys:
        side, partner, category, eff_status = key
        count = summary_counts[key]
        
        ws3.cell(row=curr_row, column=1, value=partner).alignment = align_left
        ws3.cell(row=curr_row, column=2, value=category).alignment = align_left
        ws3.cell(row=curr_row, column=3, value=count).alignment = align_right
        
        status_val = eff_status.replace("_", " ").title()
        status_cell = ws3.cell(row=curr_row, column=4, value=status_val)
        status_cell.alignment = align_center
        if status_val == "Solved":
            status_cell.fill = fill_solved
            status_cell.font = font_solved
        else:
            status_cell.fill = fill_pending
            status_cell.font = font_pending

        ws3.cell(row=curr_row, column=5, value=summary_comments[key] or "").alignment = align_left

        for c in range(1, 6):
            if c != 4:  # status cell is formatted separately
                ws3.cell(row=curr_row, column=c).font = font_regular
            ws3.cell(row=curr_row, column=c).border = border_cell
        curr_row += 1

    # 2. SCT issues
    sct_keys = sorted([k for k in summary_counts.keys() if k[0] == "sct"])
    for key in sct_keys:
        side, partner, category, eff_status = key
        count = summary_counts[key]
        
        ws3.cell(row=curr_row, column=1, value="SCT").alignment = align_left
        ws3.cell(row=curr_row, column=2, value=category).alignment = align_left
        ws3.cell(row=curr_row, column=3, value=count).alignment = align_right
        
        status_val = eff_status.replace("_", " ").title()
        status_cell = ws3.cell(row=curr_row, column=4, value=status_val)
        status_cell.alignment = align_center
        if status_val == "Solved":
            status_cell.fill = fill_solved
            status_cell.font = font_solved
        else:
            status_cell.fill = fill_pending
            status_cell.font = font_pending

        ws3.cell(row=curr_row, column=5, value=summary_comments[key] or "").alignment = align_left

        for c in range(1, 6):
            if c != 4:  # status cell is formatted separately
                ws3.cell(row=curr_row, column=c).font = font_regular
            ws3.cell(row=curr_row, column=c).border = border_cell
        curr_row += 1

    # ==========================================
    # SHEET 3: Lo Status
    # ==========================================
    ws4 = wb.create_sheet(title="Lo Status")
    ws4.views.sheetView[0].showGridLines = True

    ws4["A1"] = "Lo Status Tracking"
    ws4["A1"].font = font_title

    headers_lo = [
        "Aggregator / Wallet", "Issue Category", "Txn Status",
        "Affected Txns", "Solved Status", "Ops Remarks",
    ]
    for col_idx, h in enumerate(headers_lo, start=1):
        cell = ws4.cell(row=3, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center

    curr_row_lo = 4
    for key in sorted(lo_counts.keys(), key=lambda k: (-lo_counts[k], k[0], k[1])):
        partner, category, txn_status, eff_status = key

        ws4.cell(row=curr_row_lo, column=1, value=partner).alignment = align_left
        ws4.cell(row=curr_row_lo, column=2, value=category).alignment = align_left
        ws4.cell(
            row=curr_row_lo, column=3, value=txn_status.replace("_", " ").title(),
        ).alignment = align_center
        ws4.cell(row=curr_row_lo, column=4, value=lo_counts[key]).alignment = align_right

        status_val = eff_status.replace("_", " ").title()
        status_cell = ws4.cell(row=curr_row_lo, column=5, value=status_val)
        status_cell.alignment = align_center
        if status_val == "Solved":
            status_cell.fill = fill_solved
            status_cell.font = font_solved
        else:
            status_cell.fill = fill_pending
            status_cell.font = font_pending

        ws4.cell(row=curr_row_lo, column=6, value=lo_comments[key] or "").alignment = align_left

        for c in range(1, 7):
            if c != 5:
                ws4.cell(row=curr_row_lo, column=c).font = font_regular
            ws4.cell(row=curr_row_lo, column=c).border = border_cell
        curr_row_lo += 1

    # ==========================================
    # SHEET 4: Processed Records (second-to-last, ahead of Error Classify)
    # ==========================================
    ws2 = wb.create_sheet(title="Processed Records")
    ws2.views.sheetView[0].showGridLines = True

    # Read original columns from file if available, or extract from transactions
    orig_headers = []
    if batch.input_file_path and os.path.exists(batch.input_file_path):
        try:
            h_idx = _find_header_index(batch.input_file_path)
            raw_df = pd.read_excel(batch.input_file_path, header=h_idx, nrows=1)
            orig_headers = [str(c).strip() for c in raw_df.columns if pd.notna(c)]
        except Exception:
            pass

    if not orig_headers:
        orig_headers = ["SN", "Transaction Date", "Acquirer Name", "MID", "Merchant Name", "Txn Amount", "Service Charge", "Settled By", "STAN", "CRRN", "CR Transaction ID", "Bank/Wallet Name", "Bank Account/Wallet ID", "Account Name", "Remarks 1", "Remarks 2", "Status Code", "Status"]

    # "Retry Settled" goes last so the existing column offsets (used for the
    # Solved Status formatting below) stay put.
    appended_headers = [
        "Aggregator", "Issue Category", "Solved Status", "Solved Remarks",
        "Batch", "Timestamp", "Retry Settled",
    ]
    full_headers = orig_headers + appended_headers

    for col_idx, h in enumerate(full_headers, start=1):
        cell = ws2.cell(row=1, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center

    row_idx = 2
    for txn in transactions:
        extra = txn.extra_data or {}
        # Populate original column values
        row_vals = []
        for h in orig_headers:
            if h == "MID":
                row_vals.append(txn.mid)
            elif h == "Merchant Name":
                row_vals.append(txn.merchant_name)
            elif h == "Remarks 1":
                row_vals.append(txn.remark)
            elif h == "Status Code":
                row_vals.append(txn.status_code)
            elif h == "Status":
                row_vals.append(txn.status)
            else:
                row_vals.append(extra.get(h, ""))

        # Appended columns lookup
        txn_original_status = normalize_txn_status(txn.status)
        issue_obj = issue_status_map.get((txn.error_side, txn.partner_name if txn.error_side != "sct" else None, txn.error_category, txn_original_status))

        override_obj = None
        if issue_obj and issue_obj.mid_overrides and txn.mid in issue_obj.mid_overrides:
            raw = issue_obj.mid_overrides[txn.mid]
            # Support both old string format and new {status, remark} format
            if isinstance(raw, dict):
                override_obj = raw
            else:
                override_obj = {"status": raw, "remark": ""}

        eff_status = (override_obj["status"] if override_obj else None) or (issue_obj.status if issue_obj else "pending")

        # Skip transactions whose issue (or override) is marked as 'exclude'
        if eff_status == "exclude":
            continue

        solved_status = eff_status.replace("_", " ").title()
        # Override remark takes priority over group comment
        if override_obj and override_obj.get("remark"):
            solved_remarks = f"[MID Override] {override_obj['remark']}"
        else:
            solved_remarks = issue_obj.comment if issue_obj else ""

        row_vals.extend([
            txn.partner_name or "N/A",
            txn.error_category or "Unclassified",
            solved_status,
            solved_remarks,
            batch.name,
            batch.created_at.strftime("%Y-%m-%d %H:%M:%S") if batch.created_at else "",
            # The original row is preserved either way -- this column is how a
            # reader tells "still failed" from "failed, then reprocessed OK".
            "Yes" if txn.retry_resolved else "",
        ])

        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws2.cell(row=row_idx, column=col_idx, value=val)
            cell.font = font_regular
            cell.border = border_cell
            if col_idx <= len(orig_headers):
                cell.alignment = align_left
            else:
                cell.alignment = align_left

            # Solved Status formatting in Processed Records
            if col_idx == len(orig_headers) + 3:
                cell.alignment = align_center
                if val == "Solved":
                    cell.fill = fill_solved
                    cell.font = font_solved
                else:
                    cell.fill = fill_pending
                    cell.font = font_pending
        row_idx += 1

    # ==========================================
    # SHEET 5: Error Classify (last sheet)
    # Chart view over the same data the dashboard's Error Classification tab
    # and the standalone extract use (build_error_classification) -- so the
    # numbers behind the pictures can't drift from the raw extract. The raw
    # per-category table stays in the standalone extract (write_error_classify_
    # sheet) where it's meant to be filtered/pivoted; here it's visuals only.
    # ==========================================
    ws5 = wb.create_sheet(title="Error Classify")
    write_error_classify_charts_sheet(ws5, build_error_classification(batch_id))

    # Auto-adjust column widths across all sheets
    for ws in [ws1, ws2, ws3, ws4]:
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                if val_str:
                    max_len = max(max_len, len(val_str))
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 50)

    # Remove default sheet if present
    if "Sheet" in wb.sheetnames:
        wb.remove(wb["Sheet"])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


ERROR_CLASSIFY_HEADERS = [
    "Entity", "Entity Type", "Error Side", "Issue Category",
    "Failed", "Pending", "Lo Progress", "Total Txns",
    "% of Entity", "% of Batch",
]


def write_error_classify_sheet(ws, data: dict) -> None:
    """
    Writes the error-frequency table onto `ws`: one row per
    (entity, issue category), worst entity first, no charts -- the numbers
    only. Shared by the standalone extract and the report's last sheet.
    """
    batch = data["batch"]
    totals = data["totals"]

    font_title = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=10, bold=True)
    font_regular = Font(name="Calibri", size=10)
    fill_header = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
    fill_entity_total = PatternFill(start_color="EDF2FA", end_color="EDF2FA", fill_type="solid")
    thin = Side(border_style="thin", color="D9D9D9")
    border_cell = Border(left=thin, right=thin, top=thin, bottom=thin)
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    ws["A1"] = "Error Classification — Failure Frequency by Aggregator / SCT"
    ws["A1"].font = font_title

    ws["A2"] = (
        "Every non-success transaction in this batch, grouped by who it belongs to "
        "and what went wrong. Counts are independent of ops status (excluded and "
        "solved issues still count — they still happened). Failures that a later "
        "reprocess actually settled are excluded and counted separately below."
    )
    ws["A2"].font = Font(name="Calibri", size=9, italic=True, color="808080")

    meta = [
        ("Batch:", batch["name"]),
        ("Total Error Txns:", totals["total_errors"]),
        ("Entities Affected:", totals["entities"]),
        ("Distinct Categories:", totals["categories"]),
        ("Failed / Pending / Lo Progress:", "{failed} / {pending} / {lo_progress}".format(
            **totals["by_status"]
        )),
        ("Excluded (reprocessed & settled):", totals.get("retry_resolved", 0)),
    ]
    row = 4
    for label, value in meta:
        ws.cell(row=row, column=1, value=label).font = font_bold
        ws.cell(row=row, column=2, value=value).font = font_regular
        row += 1

    row += 1
    for col_idx, header in enumerate(ERROR_CLASSIFY_HEADERS, start=1):
        cell = ws.cell(row=row, column=col_idx, value=header)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
    header_row = row
    row += 1

    for entity in data["entities"]:
        for cat in entity["categories"]:
            values = [
                entity["entity"],
                ENTITY_TYPE_LABELS.get(entity["entity_type"], entity["entity_type"]),
                SIDE_LABELS.get(cat["side"], cat["side"]),
                cat["category"],
                cat["failed"],
                cat["pending"],
                cat["lo_progress"],
                cat["count"],
                cat["share_of_entity"] / 100.0,
                cat["share_of_batch"] / 100.0,
            ]
            for col_idx, value in enumerate(values, start=1):
                cell = ws.cell(row=row, column=col_idx, value=value)
                cell.font = font_regular
                cell.border = border_cell
                if col_idx <= 4:
                    cell.alignment = align_left
                elif col_idx <= 8:
                    cell.alignment = align_right
                else:
                    cell.alignment = align_right
                    cell.number_format = "0.0%"
            row += 1

        # Subtotal line so a reader can scan entity-level frequency without
        # re-adding the category rows themselves.
        subtotal = [
            entity["entity"], "", "", "TOTAL",
            entity["failed"], entity["pending"], entity["lo_progress"],
            entity["total"], 1.0, entity["share_of_batch"] / 100.0,
        ]
        for col_idx, value in enumerate(subtotal, start=1):
            cell = ws.cell(row=row, column=col_idx, value=value)
            cell.font = font_bold
            cell.fill = fill_entity_total
            cell.border = border_cell
            cell.alignment = align_left if col_idx <= 4 else align_right
            if col_idx >= 9:
                cell.number_format = "0.0%"
        row += 1

    # Entity name is repeated on every row rather than merged down the
    # column: this sheet is meant to be filtered and pivoted, and merged
    # cells break both.
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.auto_filter.ref = f"A{header_row}:J{max(row - 1, header_row)}"

    widths = [30, 14, 12, 46, 9, 10, 12, 11, 11, 11]
    for col_idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def write_error_classify_charts_sheet(ws, data: dict) -> None:
    """
    Visual version of the Error Classify data for the batch report: a pie
    chart for the Failed/Pending/Lo Progress split, a stacked bar + cumulative
    -% line (Pareto) for the worst entities, and a bar chart for the most
    frequent issue categories. Small tables in columns A-E are kept only as
    chart source data -- for the full filterable per-category table, use the
    standalone Error Classify extract (write_error_classify_sheet), which
    this function deliberately does not replace.
    """
    batch = data["batch"]
    totals = data["totals"]
    entities = data["entities"]
    categories = data["categories"]

    font_title = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    font_section = Font(name="Calibri", size=12, bold=True, color="1F4E79")
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=10, bold=True)
    font_regular = Font(name="Calibri", size=10)
    fill_header = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
    thin = Side(border_style="thin", color="D9D9D9")
    border_cell = Border(left=thin, right=thin, top=thin, bottom=thin)
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")
    align_center = Alignment(horizontal="center", vertical="center")

    ws["A1"] = "Error Classification — Visual Summary"
    ws["A1"].font = font_title
    ws["A2"] = (
        "Every non-success transaction in this batch, grouped by who it belongs to "
        "and what went wrong. Counts are independent of ops status -- excluded and "
        "solved issues still count, they still happened. For the full filterable "
        "per-category table, use the standalone Error Classify download."
    )
    ws["A2"].font = Font(name="Calibri", size=9, italic=True, color="808080")

    meta = [
        ("Batch:", batch["name"]),
        ("Total Error Txns:", totals["total_errors"]),
        ("Entities Affected:", totals["entities"]),
        ("Distinct Categories:", totals["categories"]),
        ("Excluded (reprocessed & settled):", totals.get("retry_resolved", 0)),
    ]
    row = 4
    for label, value in meta:
        ws.cell(row=row, column=1, value=label).font = font_bold
        ws.cell(row=row, column=2, value=value).font = font_regular
        row += 1

    if totals["total_errors"] == 0:
        ws.cell(row=row + 1, column=1, value="No non-success transactions in this batch.").font = font_bold
        return

    # ---- Status distribution: source table + pie chart ----
    row += 2
    ws.cell(row=row, column=1, value="Status Distribution").font = font_section
    row += 1
    status_header_row = row
    for col_idx, h in enumerate(["Status", "Count"], start=1):
        cell = ws.cell(row=row, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
    row += 1
    status_first_row = row
    for bucket, label in (("failed", "Failed"), ("pending", "Pending"), ("lo_progress", "Lo Progress")):
        ws.cell(row=row, column=1, value=label).alignment = align_left
        ws.cell(row=row, column=2, value=totals["by_status"].get(bucket, 0)).alignment = align_right
        for c in (1, 2):
            ws.cell(row=row, column=c).font = font_regular
            ws.cell(row=row, column=c).border = border_cell
        row += 1
    status_last_row = row - 1

    pie = PieChart()
    pie.title = "Status Distribution"
    pie_data = Reference(ws, min_col=2, min_row=status_header_row, max_row=status_last_row)
    pie_labels = Reference(ws, min_col=1, min_row=status_first_row, max_row=status_last_row)
    pie.add_data(pie_data, titles_from_data=True)
    pie.set_categories(pie_labels)
    pie.dataLabels = DataLabelList()
    pie.dataLabels.showPercent = True
    pie.height = 8
    pie.width = 11
    ws.add_chart(pie, f"G{status_header_row - 1}")

    # ---- Top entities by error volume: source table + Pareto bar/line ----
    row += 2
    entity_section_row = row
    ws.cell(row=row, column=1, value="Top Entities by Error Volume").font = font_section
    row += 1
    entity_header_row = row
    for col_idx, h in enumerate(["Entity", "Failed", "Pending", "Lo Progress", "Cumulative %"], start=1):
        cell = ws.cell(row=row, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
    row += 1
    entity_first_row = row
    top_entities = entities[:10]
    running_total = 0
    for ent in top_entities:
        running_total += ent["total"]
        ws.cell(row=row, column=1, value=ent["entity"]).alignment = align_left
        ws.cell(row=row, column=2, value=ent["failed"]).alignment = align_right
        ws.cell(row=row, column=3, value=ent["pending"]).alignment = align_right
        ws.cell(row=row, column=4, value=ent["lo_progress"]).alignment = align_right
        cum_cell = ws.cell(row=row, column=5, value=running_total / totals["total_errors"])
        cum_cell.alignment = align_right
        cum_cell.number_format = "0%"
        for c in range(1, 6):
            ws.cell(row=row, column=c).font = font_regular
            ws.cell(row=row, column=c).border = border_cell
        row += 1
    entity_last_row = row - 1

    if top_entities:
        bar = BarChart()
        bar.type = "col"
        bar.grouping = "stacked"
        bar.overlap = 100
        bar.title = "Top Entities by Error Volume"
        bar.y_axis.title = "Transactions"
        bar.x_axis.title = "Entity"
        cats = Reference(ws, min_col=1, min_row=entity_first_row, max_row=entity_last_row)
        for col in (2, 3, 4):
            bar.add_data(
                Reference(ws, min_col=col, min_row=entity_header_row, max_row=entity_last_row),
                titles_from_data=True,
            )
        bar.set_categories(cats)

        line = LineChart()
        line.add_data(
            Reference(ws, min_col=5, min_row=entity_header_row, max_row=entity_last_row),
            titles_from_data=True,
        )
        line.set_categories(cats)
        line.y_axis.axId = 200
        line.y_axis.title = "Cumulative %"

        bar.y_axis.crosses = "max"
        bar += line
        bar.height = 9
        bar.width = 16
        ws.add_chart(bar, f"G{entity_section_row}")

    # ---- Top issue categories: source table + bar chart ----
    row += 2
    category_section_row = row
    ws.cell(row=row, column=1, value="Top Issue Categories").font = font_section
    row += 1
    category_header_row = row
    for col_idx, h in enumerate(["Category", "Side", "Count"], start=1):
        cell = ws.cell(row=row, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
    row += 1
    category_first_row = row
    top_categories = categories[:10]
    for cat in top_categories:
        ws.cell(row=row, column=1, value=cat["category"]).alignment = align_left
        ws.cell(row=row, column=2, value=SIDE_LABELS.get(cat["side"], cat["side"])).alignment = align_left
        ws.cell(row=row, column=3, value=cat["count"]).alignment = align_right
        for c in (1, 2, 3):
            ws.cell(row=row, column=c).font = font_regular
            ws.cell(row=row, column=c).border = border_cell
        row += 1
    category_last_row = row - 1

    if top_categories:
        cat_bar = BarChart()
        cat_bar.type = "bar"
        cat_bar.title = "Top Issue Categories"
        cat_bar.x_axis.title = "Category"
        cat_bar.y_axis.title = "Count"
        cat_bar.add_data(
            Reference(ws, min_col=3, min_row=category_header_row, max_row=category_last_row),
            titles_from_data=True,
        )
        cat_bar.set_categories(Reference(ws, min_col=1, min_row=category_first_row, max_row=category_last_row))
        cat_bar.height = 9
        cat_bar.width = 16
        ws.add_chart(cat_bar, f"G{category_section_row}")

    widths = [30, 14, 14, 14, 14]
    for col_idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def generate_error_classification_bytes(batch_id: int) -> bytes:
    """Standalone one-sheet Excel of the error classification table."""
    data = build_error_classification(batch_id)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Error Classify"
    write_error_classify_sheet(ws, data)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def generate_aggregator_report_bytes(batch_id: int, partner_name: str, status_filter: str = None) -> bytes:
    """
    Generates a targeted extract for a specific aggregator containing ONLY
    transactions whose resolved issue status is pending, failed, or lo_progress.
    Optionally filters by the original transaction status.
    """
    batch = Batch.query.get_or_404(batch_id)
    transactions = Transaction.query.filter_by(batch_id=batch_id, partner_name=partner_name).all()
    issues = IssueStatus.query.filter_by(batch_id=batch_id).all()
    
    issue_status_map = {
        (i.side, i.partner_name, i.category, i.txn_status): i
        for i in issues
    }

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Aggregator Extract"
    
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_regular = Font(name="Calibri", size=10)
    fill_header = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
    thin_border_side = Side(border_style="thin", color="D9D9D9")
    border_cell = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")

    orig_headers = []
    if batch.input_file_path and os.path.exists(batch.input_file_path):
        try:
            h_idx = _find_header_index(batch.input_file_path)
            raw_df = pd.read_excel(batch.input_file_path, header=h_idx, nrows=1)
            orig_headers = [str(c).strip() for c in raw_df.columns if pd.notna(c)]
        except Exception:
            pass

    if not orig_headers:
        orig_headers = ["SN", "Transaction Date", "Acquirer Name", "MID", "Merchant Name", "Txn Amount", "Service Charge", "Settled By", "STAN", "CRRN", "CR Transaction ID", "Bank/Wallet Name", "Bank Account/Wallet ID", "Account Name", "Remarks 1", "Remarks 2", "Status Code", "Status"]

    appended_headers = ["Issue Category", "Solved Status", "Ops Remarks"]
    full_headers = orig_headers + appended_headers

    for col_idx, h in enumerate(full_headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center

    row_idx = 2
    for txn in transactions:
        if txn.retry_resolved:
            continue  # already settled on a retry -- nothing to chase up

        txn_original_status = normalize_txn_status(txn.status)
        if status_filter and txn_original_status != status_filter:
            continue

        extra = txn.extra_data or {}
        issue_obj = issue_status_map.get((txn.error_side, txn.partner_name if txn.error_side != "sct" else None, txn.error_category, txn_original_status))
        
        override_obj = None
        if issue_obj and issue_obj.mid_overrides and txn.mid in issue_obj.mid_overrides:
            raw = issue_obj.mid_overrides[txn.mid]
            if isinstance(raw, dict):
                override_obj = raw
            else:
                override_obj = {"status": raw, "remark": ""}

        eff_status = (override_obj["status"] if override_obj else None) or (issue_obj.status if issue_obj else "pending")
        
        # Only include un-solved, non-excluded transactions
        if eff_status in ("solved", "exclude", "success"):
            continue

        solved_status = eff_status.replace("_", " ").title()
        if override_obj and override_obj.get("remark"):
            solved_remarks = f"[MID Override] {override_obj['remark']}"
        else:
            solved_remarks = issue_obj.comment if issue_obj else ""

        row_vals = []
        for h in orig_headers:
            if h == "MID": row_vals.append(txn.mid)
            elif h == "Merchant Name": row_vals.append(txn.merchant_name)
            elif h == "Remarks 1": row_vals.append(txn.remark)
            elif h == "Status Code": row_vals.append(txn.status_code)
            elif h == "Status": row_vals.append(txn.status)
            else: row_vals.append(extra.get(h, ""))
            
        row_vals.extend([
            txn.error_category or "Unclassified",
            solved_status,
            solved_remarks
        ])

        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = font_regular
            cell.border = border_cell
            cell.alignment = align_left

        row_idx += 1

    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if val_str:
                max_len = max(max_len, len(val_str))
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 50)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
