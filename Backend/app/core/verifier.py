from app.models.bill import ExtractedBill, BillStatus

TOLERANCE = 2.0


def verify(bill: ExtractedBill) -> ExtractedBill:
    flags = list(bill.flags)
    score = bill.confidence_score
    has_mismatch = False

    line_total = sum(item.amount for item in bill.line_items)
    expected_assessable = line_total + bill.pf_charges

    if bill.line_items and abs(expected_assessable - bill.assessable_value) > TOLERANCE:
        flags.append("LINE_ITEMS_MISMATCH")
        score -= 0.15
        has_mismatch = True

    if bill.igst_percent > 0:
        expected_igst = bill.assessable_value * (bill.igst_percent / 100)
        if abs(expected_igst - bill.igst_amount) > TOLERANCE:
            flags.append("GST_CALC_MISMATCH")
            score -= 0.10
            has_mismatch = True

    if bill.cgst_percent > 0:
        expected_cgst = bill.assessable_value * (bill.cgst_percent / 100)
        if abs(expected_cgst - bill.cgst_amount) > TOLERANCE:
            if "GST_CALC_MISMATCH" not in flags:
                flags.append("GST_CALC_MISMATCH")
                score -= 0.10
            has_mismatch = True

    if bill.sgst_percent > 0:
        expected_sgst = bill.assessable_value * (bill.sgst_percent / 100)
        if abs(expected_sgst - bill.sgst_amount) > TOLERANCE:
            if "GST_CALC_MISMATCH" not in flags:
                flags.append("GST_CALC_MISMATCH")
                score -= 0.10
            has_mismatch = True

    total_tax = bill.igst_amount + bill.cgst_amount + bill.sgst_amount
    expected_grand = bill.assessable_value + total_tax + bill.round_off
    if bill.grand_total > 0 and abs(expected_grand - bill.grand_total) > TOLERANCE:
        flags.append("GRAND_TOTAL_MISMATCH")
        score -= 0.20
        has_mismatch = True

    if not has_mismatch:
        score += 0.05

    score = max(0.0, min(1.0, score))

    updated = bill.model_copy(update={"flags": flags, "confidence_score": score})

    if score < 0.75 and updated.status == BillStatus.VERIFIED:
        updated = updated.model_copy(update={"status": BillStatus.NEEDS_REVIEW})

    return updated
