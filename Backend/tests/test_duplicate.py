import pytest
from app.models.bill import ExtractedBill, BillStatus
from app.core.duplicate import check_duplicate


def make_bill(invoice_no: str, supplier: str, total: float, filename: str = "test.pdf") -> ExtractedBill:
    return ExtractedBill(
        source_filename=filename,
        invoice_number=invoice_no,
        supplier_name=supplier,
        grand_total=total,
    )


def test_no_duplicate_on_empty_list():
    bill = make_bill("INV001", "ABC Corp", 1000.0)
    result = check_duplicate(bill, [])
    assert result.status == BillStatus.VERIFIED


def test_exact_duplicate_detected():
    existing = make_bill("INV001", "ABC Corp", 1000.0, "first.pdf")
    bill = make_bill("INV001", "ABC Corp", 1000.0, "second.pdf")
    result = check_duplicate(bill, [existing])
    assert result.status == BillStatus.DUPLICATE
    assert result.confidence_score == 0.0
    assert "DUPLICATE" in result.flags[0]


def test_amount_within_tolerance_is_duplicate():
    existing = make_bill("INV001", "ABC Corp", 1000.0, "first.pdf")
    bill = make_bill("INV001", "ABC Corp", 1000.50, "second.pdf")
    result = check_duplicate(bill, [existing])
    assert result.status == BillStatus.DUPLICATE


def test_different_invoice_no_is_not_duplicate():
    existing = make_bill("INV001", "ABC Corp", 1000.0, "first.pdf")
    bill = make_bill("INV002", "ABC Corp", 1000.0, "second.pdf")
    result = check_duplicate(bill, [existing])
    assert result.status == BillStatus.VERIFIED


def test_case_insensitive_match():
    existing = make_bill("inv-001", "abc corp", 500.0, "first.pdf")
    bill = make_bill("INV-001", "ABC CORP", 500.0, "second.pdf")
    result = check_duplicate(bill, [existing])
    assert result.status == BillStatus.DUPLICATE
