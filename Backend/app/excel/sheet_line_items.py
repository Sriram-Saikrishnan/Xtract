from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from typing import List

from app.database import InvoiceORM, LineItemORM

GREEN = PatternFill("solid", fgColor="C6EFCE")
YELLOW = PatternFill("solid", fgColor="FFEB9C")
RED = PatternFill("solid", fgColor="FFC7CE")
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
THIN = Side(style="thin", color="CCCCCC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

HEADERS = [
    # Source
    "Source File", "Category",
    # Invoice Header
    "Invoice No", "Invoice Date", "Challan No", "Document Type",
    # Supplier
    "Supplier Name", "Supplier GSTIN", "Supplier State", "Address",
    "Email", "Phone", "Bank", "A/C No", "IFSC",
    # Buyer
    "Buyer Name", "Buyer GSTIN", "Place of Supply", "Destination",
    # Logistics
    "Transport", "LR No", "Vehicle No", "E-Way Bill No", "IRN No", "Total Weight (kg)",
    # Line Item
    "Sr No", "Die No", "PO No", "Description", "HSN/SAC", "Grade",
    "Qty", "Rate (₹)", "Line Amount (₹)",
    # Invoice Totals
    "Assessable Value", "IGST %", "IGST Amt", "CGST %", "CGST Amt",
    "SGST %", "SGST Amt", "P&F Charges", "Round Off", "Grand Total",
    # Quality
    "Confidence", "Status", "Flags",
]


def _row_fill(status: str, confidence: float) -> PatternFill | None:
    if status in ("DUPLICATE", "ERROR"):
        return RED
    if status == "NEEDS_REVIEW" or confidence < 0.75:
        return YELLOW
    if confidence >= 0.85:
        return GREEN
    return None


def build_sheet(wb: Workbook, invoices: List[InvoiceORM]):
    ws = wb.create_sheet("Line Items")

    ws.append(HEADERS)
    for col_idx, _ in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    row_num = 2
    for inv in invoices:
        items = inv.line_items if inv.line_items else [None]
        fill = _row_fill(inv.status, inv.confidence_score or 0.0)

        for item in items:
            row = [
                inv.source_filename, inv.category,
                inv.invoice_number, inv.invoice_date, inv.challan_number, inv.document_type,
                inv.supplier_name, inv.supplier_gstin, inv.supplier_state, inv.supplier_address,
                inv.supplier_email, inv.supplier_phone, inv.supplier_bank,
                inv.supplier_account_number, inv.supplier_ifsc,
                inv.buyer_name, inv.buyer_gstin, inv.place_of_supply, inv.destination,
                inv.transport_name, inv.lr_number, inv.vehicle_number,
                inv.eway_bill_number, inv.irn_number, inv.total_weight_kg,
                item.sr_no if item else None,
                item.die_number if item else None,
                item.po_number if item else None,
                item.description if item else None,
                item.hsn_sac_code if item else None,
                item.grade if item else None,
                item.quantity if item else None,
                item.rate if item else None,
                item.amount if item else None,
                inv.assessable_value, inv.igst_percent, inv.igst_amount,
                inv.cgst_percent, inv.cgst_amount,
                inv.sgst_percent, inv.sgst_amount,
                inv.pf_charges, inv.round_off, inv.grand_total,
                inv.confidence_score, inv.status, inv.flags,
            ]
            ws.append(row)
            if fill:
                for col_idx in range(1, len(HEADERS) + 1):
                    ws.cell(row=row_num, column=col_idx).fill = fill
            row_num += 1

    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}1"

    # Column widths
    col_widths = [25, 18, 18, 14, 14, 16, 28, 18, 14, 30, 20, 14, 20,
                  16, 12, 28, 18, 16, 16, 20, 12, 14, 20, 40, 10,
                  6, 12, 12, 30, 10, 10, 8, 10, 14,
                  14, 8, 12, 8, 12, 8, 12, 10, 10, 14,
                  10, 14, 40]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
