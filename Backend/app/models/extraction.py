from pydantic import BaseModel
from typing import Optional, List


class GeminiLineItem(BaseModel):
    sr_no: Optional[int] = None
    die_number: Optional[str] = None
    po_number: Optional[str] = None
    description: Optional[str] = None
    hsn_sac_code: Optional[str] = None
    grade: Optional[str] = None
    quantity: Optional[float] = 0.0
    rate: Optional[float] = 0.0
    amount: Optional[float] = 0.0


class GeminiExtractionResult(BaseModel):
    category: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    challan_number: Optional[str] = None
    document_type: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_gstin: Optional[str] = None
    supplier_state: Optional[str] = None
    supplier_address: Optional[str] = None
    supplier_email: Optional[str] = None
    supplier_phone: Optional[str] = None
    supplier_bank: Optional[str] = None
    supplier_account_number: Optional[str] = None
    supplier_ifsc: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_gstin: Optional[str] = None
    place_of_supply: Optional[str] = None
    destination: Optional[str] = None
    transport_name: Optional[str] = None
    lr_number: Optional[str] = None
    vehicle_number: Optional[str] = None
    eway_bill_number: Optional[str] = None
    irn_number: Optional[str] = None
    assessable_value: Optional[float] = 0.0
    tax_type: Optional[str] = None
    igst_percent: Optional[float] = 0.0
    igst_amount: Optional[float] = 0.0
    cgst_percent: Optional[float] = 0.0
    cgst_amount: Optional[float] = 0.0
    sgst_percent: Optional[float] = 0.0
    sgst_amount: Optional[float] = 0.0
    pf_charges: Optional[float] = 0.0
    round_off: Optional[float] = 0.0
    grand_total: Optional[float] = 0.0
    total_weight_kg: Optional[float] = 0.0
    total_qty: Optional[float] = 0.0
    confidence_score: Optional[float] = 0.8
    line_items: List[GeminiLineItem] = []
