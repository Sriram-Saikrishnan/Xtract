from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from typing import List

from app.database import InvoiceORM

HEADER_FILL = PatternFill("solid", fgColor="C00000")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
ORANGE_FILL = PatternFill("solid", fgColor="FCE4D6")
THIN = Side(style="thin", color="CCCCCC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

HEADERS = [
    "Source File", "Invoice No", "Invoice Date", "Supplier Name",
    "Grand Total", "Confidence Score", "Status", "Flags", "Category",
    "Action Required",
]

FLAGGED_STATUSES = {"NEEDS_REVIEW", "DUPLICATE", "ERROR"}


def build_sheet(wb: Workbook, invoices: List[InvoiceORM]):
    ws = wb.create_sheet("Flagged Bills")

    ws.append(HEADERS)
    for col_idx in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER

    ws.row_dimensions[1].height = 25
    ws.freeze_panes = "A2"

    flagged = [inv for inv in invoices if inv.status in FLAGGED_STATUSES or not inv.flags == ""]

    for inv in flagged:
        if inv.status == "DUPLICATE":
            action = "Check if this is a re-uploaded bill. Delete if duplicate."
        elif inv.status == "ERROR":
            action = "Re-photograph the bill clearly and re-upload."
        else:
            action = "Review extracted fields and correct if needed."

        row_data = [
            inv.source_filename, inv.invoice_number, inv.invoice_date, inv.supplier_name,
            inv.grand_total, inv.confidence_score, inv.status, inv.flags, inv.category,
            action,
        ]
        ws.append(row_data)

        for col_idx in range(1, len(HEADERS) + 1):
            ws.cell(row=ws.max_row, column=col_idx).fill = ORANGE_FILL

    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}1"

    widths = [25, 18, 14, 28, 14, 14, 14, 50, 18, 40]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
