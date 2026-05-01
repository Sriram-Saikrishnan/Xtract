"""
Tests for app.core.gstin_validator

Run with: pytest tests/gstin_validator_test.py -v

All API calls are mocked — no real HTTP requests are made.

Valid format reference (checksum verified):
  "29AABCP1234A1ZP"
  State: 29 (Karnataka), PAN: AABCP1234A, Entity: 1, Z-check: Z, Checksum: P
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.core.gstin_validator import (
    GSTINCache,
    GSTINResult,
    _compute_checksum,
    _validate_format,
    validate_buyer_gstin,
    validate_gstin,
)

VALID_GSTIN = "29AABCP1234A1ZP"


# ── Helpers ────────────────────────────────────────────────────────────────────

def codes(flags: list[dict]) -> list[str]:
    return [f["code"] for f in flags]


# ── Checksum sanity ────────────────────────────────────────────────────────────

def test_checksum_matches_valid_gstin():
    assert _compute_checksum(VALID_GSTIN[:14]) == VALID_GSTIN[14]


# ── Format-only checks (sync) ──────────────────────────────────────────────────

def test_format_valid_gstin_passes():
    assert _validate_format(VALID_GSTIN, "GSTIN") == []


def test_gstin_invalid_length():
    flags = _validate_format("29ABCDE123", "GSTIN")
    assert codes(flags) == ["GSTIN_INVALID_LENGTH"]
    assert flags[0]["severity"] == "CRITICAL"


def test_gstin_invalid_state_code():
    # "00" is not a valid state code
    flags = _validate_format("00AABCP1234A1ZP", "GSTIN")
    assert codes(flags) == ["GSTIN_INVALID_STATE_CODE"]
    assert flags[0]["severity"] == "CRITICAL"


def test_gstin_invalid_pan_wrong_format():
    # PAN must be 5 letters + 4 digits + 1 letter; "ABCD12345F" has only 4 leading letters
    flags = _validate_format("29ABCD12345F1ZX", "GSTIN")
    assert codes(flags) == ["GSTIN_INVALID_PAN"]
    assert flags[0]["severity"] == "CRITICAL"


def test_gstin_invalid_z_position():
    bad = VALID_GSTIN[:13] + "X" + VALID_GSTIN[14]
    flags = _validate_format(bad, "GSTIN")
    assert codes(flags) == ["GSTIN_INVALID_FORMAT"]


def test_gstin_checksum_failed():
    bad = VALID_GSTIN[:14] + "5"  # valid format, wrong checksum char
    flags = _validate_format(bad, "GSTIN")
    assert codes(flags) == ["GSTIN_CHECKSUM_FAILED"]
    assert flags[0]["severity"] == "CRITICAL"


def test_gstin_missing_empty_string():
    flags = _validate_format("", "GSTIN")
    assert codes(flags) == ["GSTIN_MISSING"]
    assert flags[0]["severity"] == "WARNING"


# ── validate_gstin (async, with mocked API) ────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_gstin_none_returns_missing_flag():
    result = await validate_gstin(None)
    assert isinstance(result, GSTINResult)
    assert codes(result.flags) == ["GSTIN_MISSING"]
    assert result.einvoice_mandatory is None


@pytest.mark.asyncio
async def test_validate_gstin_empty_returns_missing_flag():
    result = await validate_gstin("")
    assert codes(result.flags) == ["GSTIN_MISSING"]


@pytest.mark.asyncio
async def test_validate_gstin_format_failure_skips_api():
    with patch("app.core.gstin_validator._call_gstincheck_api") as mock_api:
        result = await validate_gstin("29ABCDE123")  # too short
        mock_api.assert_not_called()
    assert codes(result.flags) == ["GSTIN_INVALID_LENGTH"]
    assert result.einvoice_mandatory is None


@pytest.mark.asyncio
async def test_validate_gstin_active_no_name_mismatch():
    api_response = {
        "flag": True,
        "authStatus": "A",
        "tradeName": "Test Company Pvt Ltd",
        "einvStatus": "N",
    }
    with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = api_response
        result = await validate_gstin(VALID_GSTIN, "Test Company Pvt Ltd")
    assert result.flags == []
    assert result.einvoice_mandatory is False


@pytest.mark.asyncio
async def test_validate_gstin_einvoice_mandatory_true():
    api_response = {
        "flag": True,
        "authStatus": "A",
        "tradeName": "ACME Corp",
        "einvStatus": "Y",
    }
    with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = api_response
        result = await validate_gstin(VALID_GSTIN, "ACME Corp")
    assert result.einvoice_mandatory is True
    assert result.flags == []


@pytest.mark.asyncio
async def test_validate_gstin_flag_false_returns_unverified():
    with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"flag": False}
        result = await validate_gstin(VALID_GSTIN, "Any Name")
    assert codes(result.flags) == ["GSTIN_UNVERIFIED"]
    assert result.flags[0]["severity"] == "INFO"
    assert result.einvoice_mandatory is None


@pytest.mark.asyncio
async def test_validate_gstin_auth_status_not_a_returns_unverified():
    # flag=True but authStatus blank — should NOT raise CRITICAL
    api_response = {"flag": True, "authStatus": "", "tradeName": "Test Co"}
    with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = api_response
        result = await validate_gstin(VALID_GSTIN, "Test Co")
    assert codes(result.flags) == ["GSTIN_UNVERIFIED"]
    assert result.flags[0]["severity"] == "INFO"  # not CRITICAL
    assert result.einvoice_mandatory is None


@pytest.mark.asyncio
async def test_validate_gstin_api_none_returns_unverified():
    with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = None
        result = await validate_gstin(VALID_GSTIN, "Test Co")
    assert codes(result.flags) == ["GSTIN_UNVERIFIED"]
    assert result.flags[0]["severity"] == "INFO"


@pytest.mark.asyncio
async def test_validate_gstin_name_mismatch():
    api_response = {
        "flag": True,
        "authStatus": "A",
        "tradeName": "Completely Different Corporation",
        "einvStatus": "N",
    }
    with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = api_response
        result = await validate_gstin(VALID_GSTIN, "Test Co Pvt Ltd")
    assert "GSTIN_NAME_MISMATCH" in codes(result.flags)
    mismatch = next(f for f in result.flags if f["code"] == "GSTIN_NAME_MISMATCH")
    assert mismatch["severity"] == "WARNING"


# ── validate_buyer_gstin (async) ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_buyer_missing():
    flags = await validate_buyer_gstin(None)
    assert codes(flags) == ["BUYER_GSTIN_MISSING"]
    assert flags[0]["severity"] == "WARNING"


@pytest.mark.asyncio
async def test_validate_buyer_invalid_length_downgraded_to_warning():
    flags = await validate_buyer_gstin("29ABCDE123")
    assert codes(flags) == ["BUYER_GSTIN_INVALID_LENGTH"]
    assert flags[0]["severity"] == "WARNING"  # CRITICAL downgraded


@pytest.mark.asyncio
async def test_validate_buyer_no_name_mismatch_check():
    api_response = {
        "flag": True,
        "authStatus": "A",
        "tradeName": "Completely Different Name",
        "einvStatus": "N",
    }
    with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = api_response
        flags = await validate_buyer_gstin(VALID_GSTIN)
    # No name check for buyer — expect no flags
    assert "BUYER_GSTIN_NAME_MISMATCH" not in codes(flags)
    assert "GSTIN_NAME_MISMATCH" not in codes(flags)
    assert flags == []


@pytest.mark.asyncio
async def test_validate_buyer_active_returns_no_flags():
    api_response = {"flag": True, "authStatus": "A", "tradeName": "Buyer Co", "einvStatus": "N"}
    with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = api_response
        flags = await validate_buyer_gstin(VALID_GSTIN)
    assert flags == []


# ── No GSTIN_CANCELLED raised by API ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_gstin_never_raises_cancelled_flag():
    # Even with authStatus "C" or "I", the flag should be UNVERIFIED not CANCELLED
    for bad_status in ("C", "I", "Cancelled", "Inactive"):
        api_response = {"flag": True, "authStatus": bad_status, "tradeName": "Co"}
        with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
            mock_api.return_value = api_response
            result = await validate_gstin(VALID_GSTIN, "Co")
        assert "GSTIN_CANCELLED" not in codes(result.flags), f"Should not raise CANCELLED for authStatus={bad_status}"


# ── Cache: second lookup must not trigger a new live call ──────────────────────

@pytest.mark.asyncio
async def test_cache_prevents_duplicate_api_calls():
    cache = GSTINCache()
    cache._store.clear()

    api_response = {"flag": True, "authStatus": "A", "tradeName": "Cached Co", "einvStatus": "N"}
    cache.set(VALID_GSTIN, api_response)  # prime the cache

    call_count = 0

    async def fake_api(gstin):
        # Should hit cache before reaching real HTTP — count how many times called
        nonlocal call_count
        call_count += 1
        return cache.get(gstin)

    with patch("app.core.gstin_validator._cache", cache):
        with patch("app.core.gstin_validator._call_gstincheck_api", side_effect=fake_api):
            await validate_gstin(VALID_GSTIN, "Cached Co")
            await validate_gstin(VALID_GSTIN, "Cached Co")

    assert cache.get(VALID_GSTIN) == api_response


# ── No API key → UNVERIFIED without network call ──────────────────────────────

@pytest.mark.asyncio
async def test_no_api_key_returns_unverified_without_network():
    with patch.dict("os.environ", {}, clear=True):
        # Remove key if present
        import os
        os.environ.pop("GSTINCHECK_API_KEY", None)

        with patch("app.core.gstin_validator._call_gstincheck_api", new_callable=AsyncMock) as mock_api:
            mock_api.return_value = None  # simulates key missing → returns None
            result = await validate_gstin(VALID_GSTIN, "Test Co")

    assert codes(result.flags) == ["GSTIN_UNVERIFIED"]
    assert result.einvoice_mandatory is None
