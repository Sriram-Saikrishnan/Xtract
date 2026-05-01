import re
from typing import Optional
from app.models.extraction import GeminiExtractionResult
from app.models.bill import ExtractedBill, LineItem, BillStatus

# Matches a structurally valid GSTIN anywhere in a string.
_GSTIN_RE = re.compile(r'\b(\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z])\b')


def _safe_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val) -> int:
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _normalize_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    date_str = date_str.strip()

    # Already DD/MM/YYYY
    if re.match(r"^\d{2}/\d{2}/\d{4}$", date_str):
        return date_str

    # YYYY-MM-DD → DD/MM/YYYY
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_str)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"

    # DD-MM-YYYY → DD/MM/YYYY
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", date_str)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"

    return date_str


def _clean_str(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _scan_for_gstin(raw: GeminiExtractionResult, exclude: Optional[str]) -> Optional[str]:
    """
    Scan every string field of the extraction result for a GSTIN pattern.
    Returns the first match that is not the excluded GSTIN (typically the buyer GSTIN),
    or None if nothing is found.
    """
    exclude_upper = (exclude or "").strip().upper()
    all_text = " ".join(
        str(v) for v in raw.model_dump().values() if isinstance(v, str) and v
    )
    for match in _GSTIN_RE.finditer(all_text):
        candidate = match.group(1).upper()
        if candidate != exclude_upper:
            return candidate
    return None


def normalize(raw: GeminiExtractionResult, filename: str) -> ExtractedBill:
    line_items = []
    for idx, item in enumerate(raw.line_items or []):
        line_items.append(LineItem(
            sr_no=_safe_int(item.sr_no) if item.sr_no else idx + 1,
            die_number=_clean_str(item.die_number),
            po_number=_clean_str(item.po_number),
            description=_clean_str(item.description),
            hsn_sac_code=_clean_str(item.hsn_sac_code),
            grade=_clean_str(item.grade),
            quantity=_safe_float(item.quantity),
            rate=_safe_float(item.rate),
            amount=_safe_float(item.amount),
        ))

    total_qty = _safe_int(raw.total_qty)
    if total_qty == 0 and line_items:
        total_qty = int(sum(li.quantity for li in line_items))

    return ExtractedBill(
        source_filename=filename,
        category=_clean_str(raw.category) or "Other",
        invoice_number=_clean_str(raw.invoice_number),
        invoice_date=_normalize_date(raw.invoice_date),
        challan_number=_clean_str(raw.challan_number),
        document_type=_clean_str(raw.document_type),
        supplier_name=_clean_str(raw.supplier_name),
        supplier_gstin=_clean_str(raw.supplier_gstin),
        supplier_state=_clean_str(raw.supplier_state),
        supplier_address=_clean_str(raw.supplier_address),
        supplier_email=_clean_str(raw.supplier_email),
        supplier_phone=_clean_str(raw.supplier_phone),
        supplier_bank=_clean_str(raw.supplier_bank),
        supplier_account_number=_clean_str(raw.supplier_account_number),
        supplier_ifsc=_clean_str(raw.supplier_ifsc),
        buyer_name=_clean_str(raw.buyer_name),
        buyer_gstin=_clean_str(raw.buyer_gstin),
        place_of_supply=_clean_str(raw.place_of_supply),
        destination=_clean_str(raw.destination),
        transport_name=_clean_str(raw.transport_name),
        lr_number=_clean_str(raw.lr_number),
        vehicle_number=_clean_str(raw.vehicle_number),
        eway_bill_number=_clean_str(raw.eway_bill_number),
        irn_number=_clean_str(raw.irn_number),
        assessable_value=_safe_float(raw.assessable_value),
        tax_type=_clean_str(raw.tax_type),
        igst_percent=_safe_float(raw.igst_percent),
        igst_amount=_safe_float(raw.igst_amount),
        cgst_percent=_safe_float(raw.cgst_percent),
        cgst_amount=_safe_float(raw.cgst_amount),
        sgst_percent=_safe_float(raw.sgst_percent),
        sgst_amount=_safe_float(raw.sgst_amount),
        pf_charges=_safe_float(raw.pf_charges),
        round_off=_safe_float(raw.round_off),
        grand_total=_safe_float(raw.grand_total),
        total_weight_kg=_safe_float(raw.total_weight_kg),
        total_qty=total_qty,
        confidence_score=_safe_float(raw.confidence_score) or 0.8,
        status=BillStatus.VERIFIED,
        flags=[],
        line_items=line_items,
    )

    # Fallback: if primary extraction missed supplier_gstin, scan all text fields
    # for a GSTIN pattern and auto-populate (excluding the buyer GSTIN).
    if not bill.supplier_gstin:
        found = _scan_for_gstin(raw, exclude=bill.buyer_gstin)
        if found:
            import logging
            logging.getLogger(__name__).info(
                f"[{filename}] supplier_gstin auto-extracted: {found}"
            )
            bill = bill.model_copy(update={
                "supplier_gstin": found,
                "flags": ["GSTIN_AUTO_EXTRACTED"],
            })

    return bill
