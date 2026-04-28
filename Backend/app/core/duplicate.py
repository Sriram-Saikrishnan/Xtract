from typing import List
from app.models.bill import ExtractedBill, BillStatus


def _norm(s: str | None) -> str:
    if not s:
        return ""
    return s.strip().lower()


def check_duplicate(bill: ExtractedBill, processed: List[ExtractedBill]) -> ExtractedBill:
    for existing in processed:
        inv_match = _norm(bill.invoice_number) and _norm(bill.invoice_number) == _norm(existing.invoice_number)
        sup_match = _norm(bill.supplier_name) and _norm(bill.supplier_name) == _norm(existing.supplier_name)
        amt_match = abs(bill.grand_total - existing.grand_total) <= 1.0

        if inv_match and sup_match and amt_match:
            flag_msg = f"DUPLICATE of {existing.source_filename} (Invoice: {existing.invoice_number})"
            flags = list(bill.flags) + [flag_msg]
            return bill.model_copy(update={
                "flags": flags,
                "confidence_score": 0.0,
                "status": BillStatus.DUPLICATE,
            })

    return bill
