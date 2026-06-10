"""
Tax type and rate compliance validation.

Checks:
  1. Wrong tax type (IGST on intra-state / CGST+SGST on inter-state)
  2. Split tax conflict (IGST and CGST+SGST both present)
  3. Tax rate slab validation (non-standard GST rates)
  4. CGST and SGST must be equal
  5. Zero rate but non-zero amount
  6. ITC claimability (wrong tax type, invoice age)

Usage:
    from app.core.tax_validator import validate_tax
    flags = validate_tax(bill)   # list[dict] — same structure as gstin_validator
"""
import logging
import re
from datetime import date, datetime
from typing import Optional

from app.core.gstin_validator import GSTIN_STATES
from app.models.bill import ExtractedBill

logger = logging.getLogger(__name__)

# ── Union territories where IGST applies for intra-UT supply ──────────────────
# (Chandigarh, Dadra & NH, Lakshadweep, Puducherry, Andaman & Nicobar)
UT_IGST_CODES = {"04", "26", "31", "34", "35"}

# ── Valid GST rate slabs ───────────────────────────────────────────────────────
VALID_IGST_SLABS: frozenset[float] = frozenset(
    [0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28]
)
VALID_CGST_SLABS: frozenset[float] = frozenset(
    [0, 0.05, 0.125, 0.5, 0.75, 1.5, 2.5, 3, 3.75, 6, 9, 14]
)
VALID_SGST_SLABS: frozenset[float] = VALID_CGST_SLABS

# ── Place of supply — state name / alias → 2-digit code ──────────────────────
_NAME_TO_CODE: dict[str, str] = {
    # 01 — Jammu & Kashmir
    "jammu & kashmir": "01", "jammu and kashmir": "01",
    "j&k": "01", "jammu kashmir": "01",
    # 02 — Himachal Pradesh
    "himachal pradesh": "02", "himachal": "02",
    # 03 — Punjab
    "punjab": "03",
    # 04 — Chandigarh
    "chandigarh": "04",
    # 05 — Uttarakhand
    "uttarakhand": "05", "uttaranchal": "05",
    # 06 — Haryana
    "haryana": "06",
    # 07 — Delhi
    "delhi": "07", "new delhi": "07",
    # 08 — Rajasthan
    "rajasthan": "08",
    # 09 — Uttar Pradesh
    "uttar pradesh": "09", "u.p.": "09",
    # 10 — Bihar
    "bihar": "10",
    # 11 — Sikkim
    "sikkim": "11",
    # 12 — Arunachal Pradesh
    "arunachal pradesh": "12", "arunachal": "12",
    # 13 — Nagaland
    "nagaland": "13",
    # 14 — Manipur
    "manipur": "14",
    # 15 — Mizoram
    "mizoram": "15",
    # 16 — Tripura
    "tripura": "16",
    # 17 — Meghalaya
    "meghalaya": "17",
    # 18 — Assam
    "assam": "18",
    # 19 — West Bengal
    "west bengal": "19",
    # 20 — Jharkhand
    "jharkhand": "20",
    # 21 — Odisha
    "odisha": "21", "orissa": "21",
    # 22 — Chhattisgarh
    "chhattisgarh": "22", "chattisgarh": "22",
    # 23 — Madhya Pradesh
    "madhya pradesh": "23", "m.p.": "23",
    # 24 — Gujarat
    "gujarat": "24",
    # 25 — Daman & Diu
    "daman & diu": "25", "daman and diu": "25", "daman": "25",
    # 26 — Dadra & Nagar Haveli
    "dadra & nagar haveli": "26", "dadra and nagar haveli": "26",
    "dadra & nagar haveli and daman & diu": "26",
    "dadra nagar haveli": "26",
    # 27 — Maharashtra
    "maharashtra": "27",
    # 28 — Andhra Pradesh (old / pre-bifurcation)
    "andhra pradesh (old)": "28",
    # 29 — Karnataka
    "karnataka": "29",
    # 30 — Goa
    "goa": "30",
    # 31 — Lakshadweep
    "lakshadweep": "31",
    # 32 — Kerala
    "kerala": "32",
    # 33 — Tamil Nadu
    "tamil nadu": "33",
    # 34 — Puducherry
    "puducherry": "34", "pondicherry": "34",
    # 35 — Andaman & Nicobar Islands
    "andaman & nicobar islands": "35", "andaman and nicobar islands": "35",
    "andaman & nicobar": "35", "andaman nicobar": "35", "andaman": "35",
    # 36 — Telangana
    "telangana": "36",
    # 37 — Andhra Pradesh (current)
    "andhra pradesh": "37", "andhra": "37",
    # 38 — Ladakh
    "ladakh": "38",
    # Special
    "other territory": "97", "centre jurisdiction": "99",
}

# Vehicle registration / ISO abbreviations → state code
_ABBR_TO_CODE: dict[str, str] = {
    "JK": "01",  "HP": "02",  "PB": "03",  "CH": "04",  "UK": "05",
    "HR": "06",  "DL": "07",  "RJ": "08",  "UP": "09",  "BR": "10",
    "SK": "11",  "AR": "12",  "NL": "13",  "MN": "14",  "MZ": "15",
    "TR": "16",  "ML": "17",  "AS": "18",  "WB": "19",  "JH": "20",
    "OD": "21",  "OR": "21",  "CG": "22",  "MP": "23",  "GJ": "24",
    "DD": "25",  "DN": "26",  "MH": "27",  "KA": "29",  "GA": "30",
    "LD": "31",  "KL": "32",  "TN": "33",  "PY": "34",  "AN": "35",
    "TS": "36",  "AP": "37",  "LA": "38",
}


# ── Place-of-supply parser ─────────────────────────────────────────────────────

def _parse_pos(raw: str) -> Optional[str]:
    """
    Map a place_of_supply string to a 2-digit GST state code.

    Handles: "33", "33-Tamil Nadu", "Tamil Nadu (33)", "TN", "Tamil Nadu".
    Returns None if the state cannot be resolved.
    """
    s = raw.strip()
    if not s:
        return None

    # Pure numeric code: "33" or "3"
    if re.fullmatch(r"\d{1,2}", s):
        code = s.zfill(2)
        if code in GSTIN_STATES:
            return code

    # Leading code: "33-Tamil Nadu", "33 Tamil Nadu", "33 - TN"
    m = re.match(r"^(\d{1,2})\s*[-\s]", s)
    if m:
        code = m.group(1).zfill(2)
        if code in GSTIN_STATES:
            return code

    # Trailing parenthesized or hyphenated code: "Tamil Nadu (33)", "Tamil Nadu-33"
    m = re.search(r"[(\-\s](\d{1,2})\)?$", s)
    if m:
        code = m.group(1).zfill(2)
        if code in GSTIN_STATES:
            return code

    # 2-letter abbreviation (exact match, case-insensitive)
    upper = s.upper()
    if upper in _ABBR_TO_CODE:
        return _ABBR_TO_CODE[upper]

    # Full name (case-insensitive)
    lower = s.lower()
    if lower in _NAME_TO_CODE:
        return _NAME_TO_CODE[lower]

    # Partial name — longest match wins (avoids "andhra" matching "andhra pradesh")
    best_len, best_code = 0, None
    for name, code in _NAME_TO_CODE.items():
        if name in lower and len(name) > best_len:
            best_len = len(name)
            best_code = code
    if best_code:
        return best_code

    return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _nearest_slab(rate: float, slabs: frozenset[float]) -> float:
    return min(slabs, key=lambda s: abs(s - rate))


def _slab_str(slabs: frozenset[float]) -> str:
    return ", ".join(str(int(s) if s == int(s) else s) for s in sorted(slabs))


def _parse_date(raw: Optional[str]) -> Optional[date]:
    """Parse invoice_date (stored as DD/MM/YYYY by extractor, with fallbacks)."""
    if not raw or not raw.strip():
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y",
                "%d-%b-%Y", "%d %b %Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    logger.warning("Could not parse invoice date: %r", raw)
    return None


# ── Main validator ─────────────────────────────────────────────────────────────

def validate_tax(bill: ExtractedBill) -> list[dict]:
    """
    Run all tax-type and rate compliance checks on an extracted bill.

    Returns list[dict] — each dict: {"code": str, "severity": str, "message": str}.
    Empty list means no issues found.
    """
    flags: list[dict] = []

    igst_a = bill.igst_amount  or 0.0
    cgst_a = bill.cgst_amount  or 0.0
    sgst_a = bill.sgst_amount  or 0.0
    igst_r = bill.igst_percent or 0.0
    cgst_r = bill.cgst_percent or 0.0
    sgst_r = bill.sgst_percent or 0.0

    all_amounts_zero = igst_a == 0 and cgst_a == 0 and sgst_a == 0
    all_rates_zero   = igst_r == 0 and cgst_r == 0 and sgst_r == 0

    wrong_tax_type = False  # tracks whether Check 1 fired, for the ITC flag

    # Nil-rated / exempt invoice: skip all tax checks, still run date checks
    if all_amounts_zero and all_rates_zero:
        _run_date_checks(bill, flags, wrong_tax_type=False)
        return flags

    # ── Check 5: Zero rate but non-zero amount ────────────────────────────────
    if all_rates_zero and not all_amounts_zero:
        total_tax = igst_a + cgst_a + sgst_a
        flags.append({
            "code": "ZERO_RATE_NONZERO_AMOUNT",
            "severity": "WARNING",
            "message": (
                f"Tax rate is 0% but tax amount is non-zero (₹{total_tax:.2f}). "
                "Extraction error — verify with original invoice."
            ),
        })

    # ── Check 2: Split tax conflict ───────────────────────────────────────────
    split_conflict = igst_a > 0 and cgst_a > 0 and sgst_a > 0
    if split_conflict:
        flags.append({
            "code": "SPLIT_TAX_CONFLICT",
            "severity": "CRITICAL",
            "message": (
                "Invoice has both IGST and CGST+SGST applied simultaneously. "
                "This is not valid under GST. Likely an extraction error — "
                "verify with original invoice."
            ),
        })

    # ── Check 1: Wrong tax type ───────────────────────────────────────────────
    # Skip if split conflict (ambiguous which type was intended) or no GSTIN
    if not split_conflict:
        gstin = (bill.supplier_gstin or "").strip().upper()
        supplier_code = gstin[:2] if len(gstin) >= 2 else ""

        if supplier_code in GSTIN_STATES:
            pos_raw = (bill.place_of_supply or "").strip()
            if not pos_raw:
                flags.append({
                    "code": "PLACE_OF_SUPPLY_UNREADABLE",
                    "severity": "WARNING",
                    "message": (
                        "Place of supply is missing. "
                        "Tax type validation skipped for this invoice."
                    ),
                })
            else:
                supply_code = _parse_pos(pos_raw)
                if supply_code is None:
                    flags.append({
                        "code": "PLACE_OF_SUPPLY_UNREADABLE",
                        "severity": "WARNING",
                        "message": (
                            f"Place of supply '{pos_raw}' could not be mapped to a state. "
                            "Tax type validation skipped for this invoice."
                        ),
                    })
                elif supplier_code not in UT_IGST_CODES and supply_code not in UT_IGST_CODES:
                    supplier_state = GSTIN_STATES[supplier_code]
                    supply_state   = GSTIN_STATES.get(supply_code, supply_code)

                    if supplier_code == supply_code:
                        # Intra-state → CGST+SGST required, IGST is wrong
                        if igst_a > 0 and (cgst_a == 0 or sgst_a == 0):
                            flags.append({
                                "code": "WRONG_TAX_TYPE_IGST_ON_INTRASTATE",
                                "severity": "CRITICAL",
                                "message": (
                                    f"Intra-state transaction (supplier and place of supply both in "
                                    f"{supplier_state}) should have CGST+SGST, not IGST. "
                                    "ITC treatment will differ. Verify with supplier."
                                ),
                            })
                            wrong_tax_type = True
                    else:
                        # Inter-state → IGST required, CGST+SGST is wrong
                        if cgst_a > 0 and sgst_a > 0 and igst_a == 0:
                            flags.append({
                                "code": "WRONG_TAX_TYPE_CGST_ON_INTERSTATE",
                                "severity": "CRITICAL",
                                "message": (
                                    f"Inter-state transaction (supplier in {supplier_state}, "
                                    f"supply in {supply_state}) should have IGST, not CGST+SGST. "
                                    "This is a GST compliance error."
                                ),
                            })
                            wrong_tax_type = True

    # ── Check 3: Tax rate slab validation ─────────────────────────────────────
    if bill.assessable_value != 0:
        for rate, tax_name, valid_slabs in [
            (igst_r, "IGST", VALID_IGST_SLABS),
            (cgst_r, "CGST", VALID_CGST_SLABS),
            (sgst_r, "SGST", VALID_SGST_SLABS),
        ]:
            if rate > 0 and rate not in valid_slabs:
                nearest = _nearest_slab(rate, valid_slabs)
                flags.append({
                    "code": "TAX_RATE_UNUSUAL",
                    "severity": "WARNING",
                    "message": (
                        f"Tax rate {rate}% for {tax_name} is not a standard GST slab. "
                        f"Valid slabs: {_slab_str(valid_slabs)}. "
                        "Likely an OCR misread — verify with original invoice."
                    ),
                    "expected_value": f"{nearest}%",
                    "found_value": f"{rate}%",
                })

    # ── Check 4: CGST and SGST must be equal ──────────────────────────────────
    if cgst_r > 0 or sgst_r > 0 or cgst_a > 0 or sgst_a > 0:
        if cgst_r != sgst_r or abs(cgst_a - sgst_a) > 2:
            flags.append({
                "code": "CGST_SGST_MISMATCH",
                "severity": "WARNING",
                "message": (
                    f"CGST ({cgst_r}%, ₹{cgst_a:.2f}) and SGST ({sgst_r}%, ₹{sgst_a:.2f}) "
                    "must be equal under GST law. Likely an extraction error."
                ),
            })

    # ── Check 6: ITC claimability ──────────────────────────────────────────────
    _run_date_checks(bill, flags, wrong_tax_type=wrong_tax_type)

    return flags


def _run_date_checks(bill: ExtractedBill, flags: list[dict], *, wrong_tax_type: bool) -> None:
    if wrong_tax_type:
        flags.append({
            "code": "ITC_AT_RISK_WRONG_TAX_TYPE",
            "severity": "CRITICAL",
            "message": (
                "ITC claim is at risk because wrong tax type was applied. "
                "Supplier must issue a corrected invoice before ITC can be safely claimed."
            ),
        })

    inv_date = _parse_date(bill.invoice_date)
    if inv_date is None:
        return

    today = date.today()

    if inv_date > today:
        flags.append({
            "code": "INVOICE_DATE_FUTURE",
            "severity": "CRITICAL",
            "message": (
                f"Invoice date {bill.invoice_date} is in the future. "
                "Likely an OCR error — verify with original document."
            ),
        })
        return  # future date — skip age checks

    delta = (today - inv_date).days

    if delta > 180:
        flags.append({
            "code": "ITC_WINDOW_EXPIRED",
            "severity": "CRITICAL",
            "message": (
                f"Invoice date {bill.invoice_date} is more than 180 days old. "
                "ITC claim window under Section 16(4) of CGST Act has likely expired. "
                "Consult your CA before claiming."
            ),
        })
    elif delta > 90:
        flags.append({
            "code": "INVOICE_DATE_OLD",
            "severity": "INFO",
            "message": (
                f"Invoice is {delta} days old. "
                "Ensure ITC is claimed before the 180-day window expires."
            ),
        })
