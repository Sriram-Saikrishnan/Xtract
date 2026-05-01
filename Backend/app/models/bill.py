from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


class BillStatus(str, Enum):
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    DUPLICATE = "DUPLICATE"
    ERROR = "ERROR"


class LineItem(BaseModel):
    sr_no: Optional[int] = None
    die_number: Optional[str] = None
    po_number: Optional[str] = None
    description: Optional[str] = None
    hsn_sac_code: Optional[str] = None
    grade: Optional[str] = None
    quantity: float = 0.0
    rate: float = 0.0
    amount: float = 0.0


class ExtractedBill(BaseModel):
    source_filename: str = ""
    category: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    challan_number: Optional[str] = None
    document_type: Optional[str] = None

    # Supplier
    supplier_name: Optional[str] = None
    supplier_gstin: Optional[str] = None
    supplier_state: Optional[str] = None
    supplier_address: Optional[str] = None
    supplier_email: Optional[str] = None
    supplier_phone: Optional[str] = None
    supplier_bank: Optional[str] = None
    supplier_account_number: Optional[str] = None
    supplier_ifsc: Optional[str] = None

    # Buyer
    buyer_name: Optional[str] = None
    buyer_gstin: Optional[str] = None
    place_of_supply: Optional[str] = None
    destination: Optional[str] = None

    # Logistics
    transport_name: Optional[str] = None
    lr_number: Optional[str] = None
    vehicle_number: Optional[str] = None
    eway_bill_number: Optional[str] = None
    irn_number: Optional[str] = None

    # Financials
    assessable_value: float = 0.0
    tax_type: Optional[str] = None
    igst_percent: float = 0.0
    igst_amount: float = 0.0
    cgst_percent: float = 0.0
    cgst_amount: float = 0.0
    sgst_percent: float = 0.0
    sgst_amount: float = 0.0
    pf_charges: float = 0.0
    round_off: float = 0.0
    grand_total: float = 0.0
    total_weight_kg: float = 0.0
    total_qty: int = 0

    # Quality
    confidence_score: float = 0.8
    status: BillStatus = BillStatus.VERIFIED
    flags: List[str] = Field(default_factory=list)
    einvoice_mandatory: Optional[bool] = None  # set by GSTIN validator from GSTINCheck API

    # Line items
    line_items: List[LineItem] = Field(default_factory=list)
