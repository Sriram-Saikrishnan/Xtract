import pytest
from app.core.extractor import normalize, _normalize_date
from app.models.extraction import GeminiExtractionResult, GeminiLineItem


def test_date_normalization_iso():
    assert _normalize_date("2024-03-15") == "15/03/2024"


def test_date_normalization_already_correct():
    assert _normalize_date("15/03/2024") == "15/03/2024"


def test_date_normalization_dashes():
    assert _normalize_date("15-03-2024") == "15/03/2024"


def test_date_normalization_none():
    assert _normalize_date(None) is None


def test_normalize_basic():
    raw = GeminiExtractionResult(
        category="Raw Material",
        invoice_number="INV-001",
        invoice_date="2024-01-15",
        supplier_name="ABC Corp",
        grand_total=1180.0,
        assessable_value=1000.0,
        igst_percent=18.0,
        igst_amount=180.0,
        confidence_score=0.9,
        line_items=[
            GeminiLineItem(sr_no=1, description="Steel Rod", quantity=10, rate=100.0, amount=1000.0)
        ]
    )
    bill = normalize(raw, "test_invoice.pdf")
    assert bill.source_filename == "test_invoice.pdf"
    assert bill.invoice_date == "15/01/2024"
    assert bill.category == "Raw Material"
    assert len(bill.line_items) == 1
    assert bill.line_items[0].amount == 1000.0


def test_normalize_null_fields():
    raw = GeminiExtractionResult()
    bill = normalize(raw, "empty.pdf")
    assert bill.category == "Other"
    assert bill.grand_total == 0.0
    assert bill.line_items == []
