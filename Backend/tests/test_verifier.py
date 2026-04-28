import pytest
from app.models.bill import ExtractedBill, LineItem, BillStatus
from app.core.verifier import verify


def make_bill(**kwargs) -> ExtractedBill:
    defaults = dict(
        source_filename="test.pdf",
        line_items=[
            LineItem(sr_no=1, description="Part A", quantity=10, rate=100.0, amount=1000.0),
            LineItem(sr_no=2, description="Part B", quantity=5, rate=200.0, amount=1000.0),
        ],
        assessable_value=2000.0,
        igst_percent=18.0,
        igst_amount=360.0,
        grand_total=2360.0,
        confidence_score=0.85,
    )
    defaults.update(kwargs)
    return ExtractedBill(**defaults)


def test_clean_bill_gets_confidence_boost():
    bill = make_bill()
    result = verify(bill)
    assert result.confidence_score > 0.85
    assert not result.flags


def test_grand_total_mismatch_flagged():
    bill = make_bill(grand_total=9999.0)
    result = verify(bill)
    assert "GRAND_TOTAL_MISMATCH" in result.flags
    assert result.confidence_score < 0.85


def test_line_items_mismatch_flagged():
    bill = make_bill(assessable_value=5000.0)
    result = verify(bill)
    assert "LINE_ITEMS_MISMATCH" in result.flags


def test_low_confidence_sets_needs_review():
    bill = make_bill(confidence_score=0.5, grand_total=9999.0)
    result = verify(bill)
    assert result.status == BillStatus.NEEDS_REVIEW


def test_score_clamped_at_zero():
    bill = make_bill(confidence_score=0.1, grand_total=9999.0, assessable_value=5000.0)
    result = verify(bill)
    assert result.confidence_score >= 0.0
