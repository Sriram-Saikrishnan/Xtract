"""
GSTIN Validation Module — format checks (no API call) + GSTINCheck API verification
with in-memory/disk caching and a 1-second rate-limit between live lookups.

API used: https://sheet.gstincheck.co.in/check-einvoice-status/{api_key}/{gstin}
Requires env var GSTINCHECK_API_KEY. If not set, API step is skipped and
GSTIN_UNVERIFIED (INFO) is returned after all format checks pass.

Usage:
    from app.core.gstin_validator import validate_gstin, validate_buyer_gstin, FLAG_REGISTRY

    result       = await validate_gstin(gstin, supplier_name)  # → GSTINResult
    buyer_flags  = await validate_buyer_gstin(buyer_gstin)     # → list[dict]

    result.flags               # list[dict] — flag dicts to extract codes from
    result.einvoice_mandatory  # Optional[bool] — store on ExtractedBill

Each flag dict: {"code": str, "severity": str, "message": str}
Only the "code" field is appended to bill.flags (plain string list).
FLAG_REGISTRY is imported by sheet_flagged.py for severity/action lookup.
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import NamedTuple, Optional

import httpx

logger = logging.getLogger(__name__)

# ── State codes ───────────────────────────────────────────────────────────────
GSTIN_STATES: dict[str, str] = {
    "01": "Jammu & Kashmir",           "02": "Himachal Pradesh",
    "03": "Punjab",                    "04": "Chandigarh",
    "05": "Uttarakhand",               "06": "Haryana",
    "07": "Delhi",                     "08": "Rajasthan",
    "09": "Uttar Pradesh",             "10": "Bihar",
    "11": "Sikkim",                    "12": "Arunachal Pradesh",
    "13": "Nagaland",                  "14": "Manipur",
    "15": "Mizoram",                   "16": "Tripura",
    "17": "Meghalaya",                 "18": "Assam",
    "19": "West Bengal",               "20": "Jharkhand",
    "21": "Odisha",                    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",            "24": "Gujarat",
    "25": "Daman & Diu",               "26": "Dadra & Nagar Haveli",
    "27": "Maharashtra",               "28": "Andhra Pradesh (old)",
    "29": "Karnataka",                 "30": "Goa",
    "31": "Lakshadweep",               "32": "Kerala",
    "33": "Tamil Nadu",                "34": "Puducherry",
    "35": "Andaman & Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh",            "38": "Ladakh",
    "97": "Other Territory",           "99": "Centre Jurisdiction",
}

# ── PAN taxpayer type (position 6 of GSTIN, 0-indexed = PAN char 4) ──────────
PAN_TYPES: dict[str, str] = {
    "P": "Individual",  "C": "Company",   "H": "HUF",
    "F": "Firm",        "A": "AOP",       "T": "Trust",
    "B": "BOI",         "L": "Local Authority",
    "J": "Artificial Juridical Person",   "G": "Government",
}

# ── Central flag registry — ALL flag codes across the pipeline ─────────────────
# sheet_flagged.py imports this to resolve severity, message, and action text.
FLAG_REGISTRY: dict[str, dict] = {
    # ── Existing math/quality flags ──────────────────────────────────────────
    "LINE_ITEMS_MISMATCH": {
        "severity": "CRITICAL",
        "message": "Line item totals do not match the assessable value.",
        "action": "Re-check all line item amounts and assessable value. Correct before filing.",
    },
    "GST_CALC_MISMATCH": {
        "severity": "CRITICAL",
        "message": "Calculated tax amount does not match the tax amount on the invoice.",
        "action": "Verify tax rate × assessable value matches tax amount on invoice. Do not file until resolved.",
    },
    "GRAND_TOTAL_MISMATCH": {
        "severity": "CRITICAL",
        "message": "Computed grand total does not match the invoice grand total.",
        "action": "Verify all components (assessable + taxes + round-off) equal the grand total. Correct before filing.",
    },
    "DUPLICATE": {
        "severity": "CRITICAL",
        "message": "This invoice appears to be a duplicate of another invoice in the batch.",
        "action": "Check if this is a re-uploaded bill. Delete the duplicate before filing.",
    },
    # ── Supplier GSTIN format flags ──────────────────────────────────────────
    "GSTIN_MISSING": {
        "severity": "WARNING",
        "message": "Supplier GSTIN is missing. ITC claim may not be valid.",
        "action": "Obtain correct GSTIN from supplier. ITC claim is at risk without a valid GSTIN.",
    },
    "GSTIN_INVALID_LENGTH": {
        "severity": "CRITICAL",
        "message": "GSTIN must be exactly 15 characters.",
        "action": "Re-check the invoice — likely an OCR error. Confirm exact GSTIN with supplier.",
    },
    "GSTIN_INVALID_STATE_CODE": {
        "severity": "CRITICAL",
        "message": "First 2 digits of GSTIN are not a valid Indian state code.",
        "action": "Verify state code with supplier. Cross-check on https://www.gst.gov.in.",
    },
    "GSTIN_INVALID_PAN": {
        "severity": "CRITICAL",
        "message": "PAN segment (characters 3–12) in GSTIN is malformed.",
        "action": "Cross-check GSTIN against supplier's PAN card. Likely a transcription error.",
    },
    "GSTIN_INVALID_ENTITY": {
        "severity": "WARNING",
        "message": "Position 13 of GSTIN (entity number) is invalid.",
        "action": "Confirm the full GSTIN with the supplier.",
    },
    "GSTIN_INVALID_FORMAT": {
        "severity": "CRITICAL",
        "message": "Position 14 of GSTIN must be 'Z'.",
        "action": "GSTIN appears incorrectly entered. Verify with supplier.",
    },
    "GSTIN_CHECKSUM_FAILED": {
        "severity": "CRITICAL",
        "message": "GSTIN checksum is invalid — likely a typo or OCR error.",
        "action": "Confirm exact GSTIN with supplier. Do not file with an invalid GSTIN.",
    },
    # ── Supplier GSTIN API flags ─────────────────────────────────────────────
    "GSTIN_NOT_FOUND": {
        "severity": "CRITICAL",
        "message": "GSTIN not found after 3 verification attempts. Verify on gst.gov.in before claiming ITC.",
        "action": "Contact supplier to provide a valid, active GSTIN. Do not file.",
    },
    "GSTIN_CANCELLED": {
        # Reserved for a future authoritative API; not raised by GSTINCheck
        # because authStatus is not reliable enough for a CRITICAL ITC decision.
        "severity": "CRITICAL",
        "message": "Supplier GSTIN is cancelled as per government records. ITC claim is invalid.",
        "action": "Supplier's registration is cancelled. ITC cannot be legally claimed. Do not file.",
    },
    "GSTIN_COMPOSITION_DEALER": {
        "severity": "CRITICAL",
        "message": "Supplier is a composition dealer. ITC cannot be claimed on this invoice.",
        "action": "Composition dealers cannot issue tax invoices eligible for ITC. Reject this invoice for ITC.",
    },
    "GSTIN_NAME_MISMATCH": {
        "severity": "WARNING",
        "message": "Supplier name on invoice does not match government records.",
        "action": "Verify supplier name against government portal before filing. Could be trade name vs legal name. Confirm with supplier.",
    },
    "GSTIN_STATE_MISMATCH": {
        # Reserved for future use with an API that returns address data.
        "severity": "WARNING",
        "message": "Supplier state from GSTIN differs from registered address state.",
        "action": "Verify place of supply. State mismatch may affect IGST vs CGST/SGST treatment.",
    },
    "GSTIN_UNVERIFIED": {
        "severity": "INFO",
        "message": "GSTIN format is valid but could not be verified with government API.",
        "action": "Manually verify GSTIN on https://www.gst.gov.in before filing.",
    },
    "GSTIN_AUTO_EXTRACTED": {
        "severity": "INFO",
        "message": "Supplier GSTIN was not found in primary extraction. Auto-detected from document text.",
        "action": "Verify that the auto-detected GSTIN belongs to this supplier before filing.",
    },
    # ── Buyer GSTIN format flags ─────────────────────────────────────────────
    "BUYER_GSTIN_MISSING": {
        "severity": "WARNING",
        "message": "Buyer GSTIN is missing. Outward supply reporting may be affected.",
        "action": "Add buyer GSTIN to ensure correct B2B outward supply reporting in GSTR-1.",
    },
    "BUYER_GSTIN_INVALID_LENGTH": {
        "severity": "WARNING",
        "message": "Buyer GSTIN must be exactly 15 characters.",
        "action": "Verify buyer's GSTIN — possible transcription error on the invoice.",
    },
    "BUYER_GSTIN_INVALID_STATE_CODE": {
        "severity": "WARNING",
        "message": "First 2 digits of buyer GSTIN are not a valid Indian state code.",
        "action": "Verify buyer's GSTIN state code.",
    },
    "BUYER_GSTIN_INVALID_PAN": {
        "severity": "WARNING",
        "message": "PAN segment in buyer GSTIN is malformed.",
        "action": "Cross-check buyer GSTIN against buyer's PAN.",
    },
    "BUYER_GSTIN_INVALID_ENTITY": {
        "severity": "WARNING",
        "message": "Position 13 of buyer GSTIN (entity number) is invalid.",
        "action": "Confirm the full buyer GSTIN.",
    },
    "BUYER_GSTIN_INVALID_FORMAT": {
        "severity": "WARNING",
        "message": "Position 14 of buyer GSTIN must be 'Z'.",
        "action": "Buyer GSTIN appears incorrectly entered. Verify.",
    },
    "BUYER_GSTIN_CHECKSUM_FAILED": {
        "severity": "WARNING",
        "message": "Buyer GSTIN checksum is invalid — possible typo.",
        "action": "Verify buyer's GSTIN. Incorrect GSTIN will cause GSTR-1 mismatches.",
    },
    "BUYER_GSTIN_NOT_FOUND": {
        "severity": "WARNING",
        "message": "Buyer GSTIN not found in government records.",
        "action": "Verify buyer's GSTIN on https://www.gst.gov.in.",
    },
    "BUYER_GSTIN_CANCELLED": {
        "severity": "WARNING",
        "message": "Buyer GSTIN is cancelled as per government records.",
        "action": "Confirm buyer's current registration status before issuing invoice.",
    },
    "BUYER_GSTIN_UNVERIFIED": {
        "severity": "INFO",
        "message": "Buyer GSTIN format is valid but could not be verified with government API.",
        "action": "Manually verify buyer GSTIN on https://www.gst.gov.in.",
    },
}

SEVERITY_ORDER: dict[str, int] = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}

_GSTINCHECK_API_BASE = "https://sheet.gstincheck.co.in/check-einvoice-status"
_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CACHE_FILE = Path("gstin_cache.json")
_RETRY_DELAYS = (0, 2, 4)  # seconds before each of the 3 attempts


# ── Return type for validate_gstin ───────────────────────────────────────────

class GSTINResult(NamedTuple):
    flags: list  # list[dict] — each dict has code/severity/message
    einvoice_mandatory: Optional[bool]  # None = could not determine


# ── Cache ──────────────────────────────────────────────────────────────────────

class GSTINCache:
    """In-memory cache with optional JSON disk persistence and API rate limiting."""

    def __init__(self):
        self._store: dict[str, dict] = {}
        self._last_call_time: float = 0.0
        self._load_from_disk()

    def _load_from_disk(self):
        if _CACHE_FILE.exists():
            try:
                self._store = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
                logger.info(f"GSTIN cache loaded: {len(self._store)} entries from disk")
            except Exception as exc:
                logger.warning(f"Could not load GSTIN cache from disk: {exc}")
                self._store = {}

    def _persist(self):
        try:
            _CACHE_FILE.write_text(
                json.dumps(self._store, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(f"GSTIN cache persist failed: {exc}")

    def get(self, gstin: str) -> Optional[dict]:
        return self._store.get(gstin)

    def set(self, gstin: str, data: dict):
        self._store[gstin] = data
        self._persist()

    async def throttle(self):
        """Enforce ≥1 second gap between live API calls."""
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        self._last_call_time = time.monotonic()


_cache = GSTINCache()


# ── Checksum ──────────────────────────────────────────────────────────────────

def _compute_checksum(gstin14: str) -> str:
    """Compute the official GST checksum character for the first 14 characters.

    Factor alternates 1 → 2 → 1 → 2 … (starts at 1, not 2).
    Verified against portal-confirmed GSTINs:
      33AABCP7305B1Z → 1   (MOREIND AUTOMATION)
      24AAJFR0223R1Z → 0   (RAINBOW TECHNOCAST)
    """
    factor, total, mod = 1, 0, len(_CHARSET)
    for ch in gstin14:
        digit = _CHARSET.index(ch)
        digit *= factor
        digit = (digit // mod) + (digit % mod)
        total += digit
        factor = 3 - factor  # alternates 1 → 2 → 1 → …
    return _CHARSET[(mod - (total % mod)) % mod]


# ── Format validation ─────────────────────────────────────────────────────────

def _validate_format(gstin: str, prefix: str) -> list[dict]:
    """
    Run checks 1–7 in order; stop and return on first failure.
    Returns [] if all checks pass (GSTIN is structurally valid).
    """
    if not gstin:
        return [{"code": f"{prefix}_MISSING", "severity": "WARNING",
                 "message": "Supplier GSTIN is missing. ITC claim may not be valid."}]

    if len(gstin) != 15:
        return [{"code": f"{prefix}_INVALID_LENGTH", "severity": "CRITICAL",
                 "message": f"GSTIN must be exactly 15 characters. Found {len(gstin)} characters."}]

    state_code = gstin[:2]
    if state_code not in GSTIN_STATES:
        return [{"code": f"{prefix}_INVALID_STATE_CODE", "severity": "CRITICAL",
                 "message": f"GSTIN state code '{state_code}' is not a valid Indian state code."}]

    pan = gstin[2:12]
    if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", pan):
        return [{"code": f"{prefix}_INVALID_PAN", "severity": "CRITICAL",
                 "message": f"Embedded PAN '{pan}' in GSTIN does not match valid PAN format."}]

    entity = gstin[12]
    if entity not in "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        return [{"code": f"{prefix}_INVALID_ENTITY", "severity": "WARNING",
                 "message": f"GSTIN entity number '{entity}' at position 13 is invalid."}]

    if gstin[13] != "Z":
        return [{"code": f"{prefix}_INVALID_FORMAT", "severity": "CRITICAL",
                 "message": f"GSTIN position 14 must be 'Z'. Found '{gstin[13]}'."}]

    expected = _compute_checksum(gstin[:14])
    if gstin[14] != expected:
        return [{"code": f"{prefix}_CHECKSUM_FAILED", "severity": "CRITICAL",
                 "message": "GSTIN checksum invalid. Likely a typo or OCR error."}]

    return []


# ── GSTINCheck API ────────────────────────────────────────────────────────────

async def _fetch_gstin_data(gstin: str) -> tuple[str, Optional[dict]]:
    """
    Fetch GSTIN via GSTINCheck API with up to 3 attempts and backoff on flag:false.

    Outcomes:
      "success"     — flag:true (GSTIN found in govt database; response cached)
      "not_found"   — all 3 attempts returned flag:false
      "mixed"       — some flag:false + some errors (inconsistent API responses)
      "unreachable" — all 3 attempts failed with exception or timeout
    """
    api_key = os.getenv("GSTINCHECK_API_KEY", "").strip()
    if not api_key:
        logger.debug("GSTINCHECK_API_KEY not set; skipping API verification")
        return "unreachable", None

    cached = _cache.get(gstin)
    if cached is not None:
        logger.debug(f"GSTIN cache hit: {gstin}")
        return "success", cached

    url = f"{_GSTINCHECK_API_BASE}/{api_key}/{gstin}"
    outcomes: list[str] = []

    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            logger.info(f"GSTIN {gstin} attempt {attempt}: waiting {delay}s before retry")
            await asyncio.sleep(delay)

        await _cache.throttle()

        try:
            async with httpx.AsyncClient(timeout=8) as client:
                res = await client.get(url)

            if res.status_code != 200:
                logger.info(f"GSTIN {gstin} attempt {attempt}: HTTP {res.status_code}")
                outcomes.append("error")
                continue

            data = res.json()
            if not data.get("flag", False):
                logger.info(f"GSTIN {gstin} attempt {attempt}: flag=false")
                outcomes.append("false")
                continue

            auth = data.get("authStatus", "")
            logger.info(f"GSTIN {gstin} attempt {attempt}: flag=true authStatus={auth!r}")
            _cache.set(gstin, data)
            return "success", data

        except httpx.TimeoutException:
            logger.info(f"GSTIN {gstin} attempt {attempt}: timeout")
            outcomes.append("error")
        except Exception as exc:
            logger.info(f"GSTIN {gstin} attempt {attempt}: error — {exc}")
            outcomes.append("error")

    if all(o == "false" for o in outcomes):
        return "not_found", None
    if all(o == "error" for o in outcomes):
        return "unreachable", None
    return "mixed", None


async def _validate_via_api(
    gstin: str,
    supplier_name: str,
    prefix: str,
    check_name: bool,
) -> tuple[list[dict], Optional[bool]]:
    """
    Run GSTINCheck API checks. Only called after all format checks pass.

    Returns (flags, einvoice_mandatory).
    """
    unverified_code = f"{prefix}_UNVERIFIED"
    not_found_code  = f"{prefix}_NOT_FOUND"

    try:
        outcome, data = await _fetch_gstin_data(gstin)
    except Exception as exc:
        logger.warning(f"Unexpected error in GSTIN fetch for {gstin}: {exc}")
        return [{"code": unverified_code, "severity": "INFO",
                 "message": "GSTIN format valid but verification API unreachable."}], None

    if outcome == "not_found":
        return [{
            "code": not_found_code,
            "severity": "CRITICAL",
            "message": "GSTIN not found after 3 verification attempts. Verify on gst.gov.in before claiming ITC.",
        }], None

    if outcome == "mixed":
        return [{
            "code": unverified_code,
            "severity": "INFO",
            "message": (
                "GSTIN could not be reliably verified — API returned inconsistent responses. "
                "Verify manually before filing."
            ),
        }], None

    if outcome == "unreachable":
        return [{"code": unverified_code, "severity": "INFO",
                 "message": "GSTIN format valid but verification API unreachable."}], None

    # outcome == "success" — GSTIN found in government database (flag=true).
    # API response structure: {"flag": true, "data": {"authStatus": "A", "tradeName": "...", ...}}
    # All status fields are nested under response["data"], not at the top level.
    assert data is not None
    nested = data.get("data") or {}
    flags: list[dict] = []
    einvoice_mandatory: Optional[bool] = None

    if nested.get("authStatus") == "A":
        # Confirmed active — run name check and read e-invoice mandate
        if check_name:
            trade_name = (nested.get("tradeName") or "").strip()
            if trade_name and supplier_name:
                try:
                    from rapidfuzz import fuzz  # type: ignore[import]
                    score = fuzz.token_sort_ratio(supplier_name.strip(), trade_name)
                    if score < 75:
                        flags.append({
                            "code": "GSTIN_NAME_MISMATCH",
                            "severity": "WARNING",
                            "message": (
                                f"Supplier name '{supplier_name}' does not match "
                                f"government records '{trade_name}'. "
                                "Could be trade name vs legal name difference. "
                                "Verify with supplier."
                            ),
                        })
                except ImportError:
                    logger.warning("rapidfuzz not installed; skipping name-mismatch check")

        einv_status = nested.get("einvStatus", "")
        if einv_status == "Y":
            einvoice_mandatory = True
        elif einv_status == "N":
            einvoice_mandatory = False

    return flags, einvoice_mandatory


# ── Public API ────────────────────────────────────────────────────────────────

async def validate_gstin(
    gstin: Optional[str],
    supplier_name: Optional[str] = "",
) -> GSTINResult:
    """
    Validate supplier GSTIN — format checks then GSTINCheck API.

    Returns GSTINResult(flags, einvoice_mandatory).
    flags is empty when all checks pass.
    einvoice_mandatory is True/False when API confirms active status, None otherwise.
    """
    if not gstin or not gstin.strip():
        return GSTINResult(
            flags=[{"code": "GSTIN_MISSING", "severity": "WARNING",
                    "message": "Supplier GSTIN is missing. ITC claim may not be valid."}],
            einvoice_mandatory=None,
        )

    g = gstin.strip().upper()
    format_flags = _validate_format(g, prefix="GSTIN")
    if format_flags:
        return GSTINResult(flags=format_flags, einvoice_mandatory=None)

    flags, einvoice_mandatory = await _validate_via_api(
        g, supplier_name or "", prefix="GSTIN", check_name=True
    )
    return GSTINResult(flags=flags, einvoice_mandatory=einvoice_mandatory)


async def validate_buyer_gstin(gstin: Optional[str]) -> list[dict]:
    """
    Validate buyer GSTIN — format checks then GSTINCheck API (active check only).

    No name check (we know who the buyer is).
    All severities capped at WARNING (buyer issues affect outward reporting, not ITC).
    Returns list[dict] — no einvoice_mandatory (irrelevant for buyer).
    """
    if not gstin or not gstin.strip():
        return [{"code": "BUYER_GSTIN_MISSING", "severity": "WARNING",
                 "message": "Buyer GSTIN is missing. Outward supply reporting may be affected."}]

    g = gstin.strip().upper()
    format_flags = _validate_format(g, prefix="BUYER_GSTIN")
    for f in format_flags:
        if f["severity"] == "CRITICAL":
            f["severity"] = "WARNING"
    if format_flags:
        return format_flags

    flags, _ = await _validate_via_api(g, "", prefix="BUYER_GSTIN", check_name=False)
    for f in flags:
        if f["severity"] == "CRITICAL":
            f["severity"] = "WARNING"
    return flags
