from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from typing import List

from app.database import InvoiceORM

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
THIN = Side(style="thin", color="CCCCCC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

HEADERS = [
    "Source File", "Category", "Invoice No", "Invoice Date",
    "Supplier Name", "Buyer Name",
    "Total Items", "Total Qty", "Total Weight (kg)",
    "Assessable Value", "Total Tax", "Grand Total",
    "Confidence", "Status",
]


def build_sheet(wb: Workbook, invoices: List[InvoiceORM]):
    ws = wb.create_sheet("Invoice Summary")

    ws.append(HEADERS)
    for col_idx in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER

    ws.row_dimensions[1].height = 25
    ws.freeze_panes = "A2"

    for inv in invoices:
        total_tax = (inv.igst_amount or 0) + (inv.cgst_amount or 0) + (inv.sgst_amount or 0)
        item_count = len(inv.line_items) if inv.line_items else 0

        ws.append([
            inv.source_filename, inv.category, inv.invoice_number, inv.invoice_date,
            inv.supplier_name, inv.buyer_name,
            item_count, inv.total_qty, inv.total_weight_kg,
            inv.assessable_value, total_tax, inv.grand_total,
            inv.confidence_score, inv.status,
        ])

    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}1"

    widths = [25, 18, 18, 14, 28, 28, 10, 10, 14, 16, 12, 14, 10, 14]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
