"""
Tests for app.core.tax_validator — all 15 specified cases.
Run with: pytest Backend/tests/test_tax_validator.py -v
"""
import pytest
from datetime import date, timedelta

from app.models.bill import ExtractedBill
from app.core.tax_validator import validate_tax, _parse_pos


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_bill(**kwargs) -> ExtractedBill:
    """Minimal intra-state Gujarat bill with CGST+SGST as base."""
    defaults = dict(
        source_filename="test.pdf",
        supplier_gstin="24AAJFR0223R1Z0",   # Gujarat (code 24)
        place_of_supply="Gujarat",
        assessable_value=10000.0,
        cgst_percent=9.0,
        cgst_amount=900.0,
        sgst_percent=9.0,
        sgst_amount=900.0,
        igst_percent=0.0,
        igst_amount=0.0,
        grand_total=11800.0,
    )
    defaults.update(kwargs)
    return ExtractedBill(**defaults)


def codes(flags: list[dict]) -> list[str]:
    return [f["code"] for f in flags]


# ── Tax type checks ────────────────────────────────────────────────────────────

def test_1_intrastate_cgst_sgst_no_flag():
    """Intra-state with correct CGST+SGST → no WRONG_TAX_TYPE flag."""
    bill = make_bill()
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "WRONG_TAX_TYPE_IGST_ON_INTRASTATE" not in flag_codes
    assert "WRONG_TAX_TYPE_CGST_ON_INTERSTATE" not in flag_codes


def test_2_interstate_igst_no_flag():
    """Inter-state with correct IGST → no WRONG_TAX_TYPE flag."""
    bill = make_bill(
        supplier_gstin="24AAJFR0223R1Z0",   # Gujarat
        place_of_supply="Tamil Nadu",
        igst_percent=18.0,
        igst_amount=1800.0,
        cgst_percent=0.0,
        cgst_amount=0.0,
        sgst_percent=0.0,
        sgst_amount=0.0,
        grand_total=11800.0,
    )
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "WRONG_TAX_TYPE_IGST_ON_INTRASTATE" not in flag_codes
    assert "WRONG_TAX_TYPE_CGST_ON_INTERSTATE" not in flag_codes


def test_3_intrastate_igst_flagged():
    """Intra-state with IGST → WRONG_TAX_TYPE_IGST_ON_INTRASTATE (CRITICAL)."""
    bill = make_bill(
        supplier_gstin="24AAJFR0223R1Z0",   # Gujarat
        place_of_supply="Gujarat",
        igst_percent=18.0,
        igst_amount=1800.0,
        cgst_percent=0.0,
        cgst_amount=0.0,
        sgst_percent=0.0,
        sgst_amount=0.0,
        grand_total=11800.0,
    )
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "WRONG_TAX_TYPE_IGST_ON_INTRASTATE" in flag_codes
    flag = next(f for f in result if f["code"] == "WRONG_TAX_TYPE_IGST_ON_INTRASTATE")
    assert flag["severity"] == "CRITICAL"
    assert "Gujarat" in flag["message"]
    # ITC risk flag must also be raised
    assert "ITC_AT_RISK_WRONG_TAX_TYPE" in flag_codes


def test_4_interstate_cgst_sgst_flagged():
    """Inter-state with CGST+SGST → WRONG_TAX_TYPE_CGST_ON_INTERSTATE (CRITICAL)."""
    bill = make_bill(
        supplier_gstin="24AAJFR0223R1Z0",   # Gujarat
        place_of_supply="Tamil Nadu",        # different state
        cgst_percent=9.0,
        cgst_amount=900.0,
        sgst_percent=9.0,
        sgst_amount=900.0,
        igst_percent=0.0,
        igst_amount=0.0,
    )
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "WRONG_TAX_TYPE_CGST_ON_INTERSTATE" in flag_codes
    flag = next(f for f in result if f["code"] == "WRONG_TAX_TYPE_CGST_ON_INTERSTATE")
    assert flag["severity"] == "CRITICAL"
    assert "Gujarat" in flag["message"]
    assert "Tamil Nadu" in flag["message"]
    assert "ITC_AT_RISK_WRONG_TAX_TYPE" in flag_codes


def test_5_split_tax_conflict():
    """All three taxes present → SPLIT_TAX_CONFLICT (CRITICAL)."""
    bill = make_bill(
        igst_percent=18.0, igst_amount=1800.0,
        cgst_percent=9.0,  cgst_amount=900.0,
        sgst_percent=9.0,  sgst_amount=900.0,
    )
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "SPLIT_TAX_CONFLICT" in flag_codes
    flag = next(f for f in result if f["code"] == "SPLIT_TAX_CONFLICT")
    assert flag["severity"] == "CRITICAL"
    # Split conflict should NOT also raise WRONG_TAX_TYPE
    assert "WRONG_TAX_TYPE_IGST_ON_INTRASTATE" not in flag_codes
    assert "WRONG_TAX_TYPE_CGST_ON_INTERSTATE" not in flag_codes


# ── Tax rate slab validation ───────────────────────────────────────────────────

def test_6_unusual_igst_rate_flagged():
    """IGST rate of 17% → TAX_RATE_UNUSUAL with nearest slab 18%."""
    bill = make_bill(
        place_of_supply="Tamil Nadu",
        igst_percent=17.0,
        igst_amount=1700.0,
        cgst_percent=0.0, cgst_amount=0.0,
        sgst_percent=0.0, sgst_amount=0.0,
    )
    result = validate_tax(bill)
    unusual = [f for f in result if f["code"] == "TAX_RATE_UNUSUAL"]
    assert unusual, "TAX_RATE_UNUSUAL should be raised for 17% IGST"
    flag = unusual[0]
    assert flag["severity"] == "WARNING"
    assert "17" in flag["message"]
    assert flag["expected_value"] == "18%"
    assert flag["found_value"] == "17.0%"


# ── CGST/SGST equality check ───────────────────────────────────────────────────

def test_7_cgst_sgst_mismatch():
    """CGST 9% / SGST 6% → CGST_SGST_MISMATCH (WARNING)."""
    bill = make_bill(
        cgst_percent=9.0, cgst_amount=900.0,
        sgst_percent=6.0, sgst_amount=600.0,
    )
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "CGST_SGST_MISMATCH" in flag_codes
    flag = next(f for f in result if f["code"] == "CGST_SGST_MISMATCH")
    assert flag["severity"] == "WARNING"
    assert "9.0" in flag["message"]
    assert "6.0" in flag["message"]


# ── Zero rate / non-zero amount ────────────────────────────────────────────────

def test_8_zero_rate_nonzero_amount():
    """All rates 0% but IGST amount > 0 → ZERO_RATE_NONZERO_AMOUNT."""
    bill = make_bill(
        cgst_percent=0.0, cgst_amount=0.0,
        sgst_percent=0.0, sgst_amount=0.0,
        igst_percent=0.0, igst_amount=500.0,
    )
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "ZERO_RATE_NONZERO_AMOUNT" in flag_codes
    flag = next(f for f in result if f["code"] == "ZERO_RATE_NONZERO_AMOUNT")
    assert flag["severity"] == "WARNING"
    assert "500.00" in flag["message"]


# ── ITC date checks ────────────────────────────────────────────────────────────

def test_9_invoice_200_days_old_itc_expired():
    """Invoice date 200 days ago → ITC_WINDOW_EXPIRED (CRITICAL)."""
    old_date = (date.today() - timedelta(days=200)).strftime("%d/%m/%Y")
    bill = make_bill(invoice_date=old_date)
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "ITC_WINDOW_EXPIRED" in flag_codes
    flag = next(f for f in result if f["code"] == "ITC_WINDOW_EXPIRED")
    assert flag["severity"] == "CRITICAL"
    assert "180" in flag["message"]


def test_10_future_invoice_date():
    """Invoice date tomorrow → INVOICE_DATE_FUTURE (CRITICAL)."""
    future_date = (date.today() + timedelta(days=1)).strftime("%d/%m/%Y")
    bill = make_bill(invoice_date=future_date)
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "INVOICE_DATE_FUTURE" in flag_codes
    flag = next(f for f in result if f["code"] == "INVOICE_DATE_FUTURE")
    assert flag["severity"] == "CRITICAL"
    # Expired window flag should NOT fire if date is future
    assert "ITC_WINDOW_EXPIRED" not in flag_codes


# ── Place-of-supply parser ─────────────────────────────────────────────────────

def test_11_pos_numeric_code():
    """place_of_supply = '33' → parsed as Tamil Nadu (33)."""
    assert _parse_pos("33") == "33"


def test_12_pos_abbreviation():
    """place_of_supply = 'TN' → parsed as Tamil Nadu (33)."""
    assert _parse_pos("TN") == "33"


def test_13_pos_combined_format():
    """place_of_supply = '33-Tamil Nadu' → parsed as Tamil Nadu (33)."""
    assert _parse_pos("33-Tamil Nadu") == "33"
    assert _parse_pos("Tamil Nadu (33)") == "33"
    assert _parse_pos("33 - Tamil Nadu") == "33"


# ── Edge cases ─────────────────────────────────────────────────────────────────

def test_14_nil_rated_no_flags():
    """All rates and amounts zero → exempt/nil rated, no tax flags."""
    bill = make_bill(
        cgst_percent=0.0, cgst_amount=0.0,
        sgst_percent=0.0, sgst_amount=0.0,
        igst_percent=0.0, igst_amount=0.0,
        grand_total=10000.0,
    )
    result = validate_tax(bill)
    tax_flag_codes = [
        f["code"] for f in result
        if f["code"] not in {"INVOICE_DATE_OLD", "INVOICE_DATE_FUTURE", "ITC_WINDOW_EXPIRED"}
    ]
    assert tax_flag_codes == [], f"Nil-rated invoice should have no tax flags, got: {tax_flag_codes}"


def test_15_ut_puducherry_intrastate_igst_no_flag():
    """
    Puducherry (34) is a UT where IGST applies even intra-UT.
    Intra-UT with IGST should NOT raise WRONG_TAX_TYPE_IGST_ON_INTRASTATE.
    """
    bill = make_bill(
        supplier_gstin="34AABCP7305B1Z1",   # Puducherry (34)
        place_of_supply="34",               # Puducherry
        igst_percent=18.0,
        igst_amount=1800.0,
        cgst_percent=0.0, cgst_amount=0.0,
        sgst_percent=0.0, sgst_amount=0.0,
        grand_total=11800.0,
    )
    result = validate_tax(bill)
    flag_codes = codes(result)
    assert "WRONG_TAX_TYPE_IGST_ON_INTRASTATE" not in flag_codes
