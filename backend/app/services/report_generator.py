import os
from io import BytesIO
from datetime import datetime
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.models.batch import Batch
from app.models.transaction import Transaction
from app.models.issue_status import IssueStatus
from app.services.dashboard_service import build_dashboard
from app.services.excel_ingest import _find_header_index


def generate_report_bytes(batch_id: int) -> bytes:
    """
    Generates a 3-sheet Excel report workbook for the given batch_id and returns
    its raw bytes.
    Sheet 1: "Overview"
    Sheet 2: "Processed Records"
    Sheet 3: "Issue Summary"
    """
    batch = Batch.query.get_or_404(batch_id)
    transactions = Transaction.query.filter_by(batch_id=batch_id).all()
    issues = IssueStatus.query.filter_by(batch_id=batch_id).all()
    dashboard = build_dashboard(batch_id)

    # Fast lookup for IssueStatus status & comment:
    # issue_key = (side, partner_name, category)
    issue_status_map = {
        (i.side, i.partner_name, i.category): i
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
    ws1["A8"] = "Settlement Failed:"
    ws1["B8"] = dashboard["totals"]["settlement_failed"]
    ws1["A9"] = "Transaction Failed (SCT):"
    ws1["B9"] = dashboard["totals"]["transaction_failed"]

    for row_idx in range(3, 10):
        ws1[f"A{row_idx}"].font = font_bold
        ws1[f"B{row_idx}"].font = font_regular

    # Table 1: Aggregator Summary
    ws1["A12"] = "Aggregator Summary"
    ws1["A12"].font = font_section

    headers_agg = ["Aggregator", "Failed Count", "Pending Count", "Unique Issues"]
    for col_idx, h in enumerate(headers_agg, start=1):
        cell = ws1.cell(row=13, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center

    curr_row = 14
    for agg in dashboard["aggregator_summary"]:
        ws1.cell(row=curr_row, column=1, value=agg["partner_name"]).alignment = align_left
        ws1.cell(row=curr_row, column=2, value=agg["failed"]).alignment = align_right
        ws1.cell(row=curr_row, column=3, value=agg["pending"]).alignment = align_right
        ws1.cell(row=curr_row, column=4, value=agg["issue_count"]).alignment = align_right
        for col_idx in range(1, 5):
            ws1.cell(row=curr_row, column=col_idx).font = font_regular
            ws1.cell(row=curr_row, column=col_idx).border = border_cell
        curr_row += 1

    # Table 2: SCT Summary
    curr_row += 2
    ws1.cell(row=curr_row, column=1, value="SCT Summary").font = font_section
    curr_row += 1

    headers_sct = ["SCT Issue Category", "Failed Count"]
    for col_idx, h in enumerate(headers_sct, start=1):
        cell = ws1.cell(row=curr_row, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
    curr_row += 1

    for sct_issue in dashboard["sct_summary"]["issues"]:
        ws1.cell(row=curr_row, column=1, value=sct_issue["category"]).alignment = align_left
        ws1.cell(row=curr_row, column=2, value=sct_issue["count"]).alignment = align_right
        ws1.cell(row=curr_row, column=1).font = font_regular
        ws1.cell(row=curr_row, column=2).font = font_regular
        ws1.cell(row=curr_row, column=1).border = border_cell
        ws1.cell(row=curr_row, column=2).border = border_cell
        curr_row += 1

    # ==========================================
    # SHEET 2: Processed Records
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

    appended_headers = ["Aggregator", "Issue Category", "Solved Status", "Solved Remarks", "Batch", "Timestamp"]
    full_headers = orig_headers + appended_headers

    for col_idx, h in enumerate(full_headers, start=1):
        cell = ws2.cell(row=1, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center

    for r_idx, txn in enumerate(transactions, start=2):
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
        issue_obj = issue_status_map.get((txn.error_side, txn.partner_name if txn.error_side != "sct" else None, txn.error_category))
        solved_status = issue_obj.status.replace("_", " ").title() if issue_obj else "Pending"
        solved_remarks = issue_obj.comment if issue_obj else ""

        row_vals.extend([
            txn.partner_name or "N/A",
            txn.error_category or "Unclassified",
            solved_status,
            solved_remarks,
            batch.name,
            batch.created_at.strftime("%Y-%m-%d %H:%M:%S") if batch.created_at else "",
        ])

        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws2.cell(row=r_idx, column=col_idx, value=val)
            cell.font = font_regular
            cell.border = border_cell
            if col_idx <= len(orig_headers):
                cell.alignment = align_left
            else:
                cell.alignment = align_left

    # ==========================================
    # SHEET 3: Issue Summary
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
    # 1. Aggregator & Bank issues
    for partner in dashboard["aggregator_summary"] + dashboard["bank_summary"]:
        for issue in partner["issues"]:
            ws3.cell(row=curr_row, column=1, value=partner["partner_name"]).alignment = align_left
            ws3.cell(row=curr_row, column=2, value=issue["category"]).alignment = align_left
            ws3.cell(row=curr_row, column=3, value=issue["count"]).alignment = align_right
            ws3.cell(row=curr_row, column=4, value=issue["status"].replace("_", " ").title()).alignment = align_center
            ws3.cell(row=curr_row, column=5, value=issue["comment"] or "").alignment = align_left

            for c in range(1, 6):
                ws3.cell(row=curr_row, column=c).font = font_regular
                ws3.cell(row=curr_row, column=c).border = border_cell
            curr_row += 1

    # 2. SCT issues
    for sct_issue in dashboard["sct_summary"]["issues"]:
        ws3.cell(row=curr_row, column=1, value="SCT").alignment = align_left
        ws3.cell(row=curr_row, column=2, value=sct_issue["category"]).alignment = align_left
        ws3.cell(row=curr_row, column=3, value=sct_issue["count"]).alignment = align_right
        ws3.cell(row=curr_row, column=4, value=sct_issue["status"].replace("_", " ").title()).alignment = align_center
        ws3.cell(row=curr_row, column=5, value=sct_issue["comment"] or "").alignment = align_left

        for c in range(1, 6):
            ws3.cell(row=curr_row, column=c).font = font_regular
            ws3.cell(row=curr_row, column=c).border = border_cell
        curr_row += 1

    # Auto-adjust column widths across all sheets
    for ws in [ws1, ws2, ws3]:
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
