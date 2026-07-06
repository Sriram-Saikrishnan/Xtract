"""
Step 1 (Gate housekeeping) — fail-fast behavior for model-not-found errors.
Step 4a — 429/rate-limit waits must use their own counter, never MAX_RETRIES.
Step 5 — structured output via response_json_schema, with _parse_json as fallback.
  (response_schema — the direct pydantic-class path — is rejected by the
  Gemini Developer API whenever any field carries a non-null default, which
  every field on GeminiExtractionResult does. response_json_schema sidesteps
  this: we derive+strip the schema ourselves, and the SDK hands back a plain
  dict via response.parsed rather than an already-instantiated model.)

extract_bill's Gemini network call and quota check are mocked (external
services) — pure unit tests of the retry-loop branches in gemini_client.py.
"""
import pytest

import app.core.gemini_client as gemini_client
from app.models.extraction import GeminiExtractionResult, GeminiLineItem


class _FakeCandidate:
    finish_reason = "STOP"


class _FakeResponse:
    text = "{}"
    candidates = [_FakeCandidate()]
    parsed = None


@pytest.fixture
def no_quota_limit(monkeypatch):
    """Bypass the real quota_manager (DB-backed) — irrelevant to this unit test."""
    async def fake_check_and_reserve(model):
        return {"daily": 0, "minute": 0}

    monkeypatch.setattr(gemini_client.quota_manager, "check_and_reserve", fake_check_and_reserve)


async def test_model_not_found_raises_immediately_with_no_retries(monkeypatch, no_quota_limit):
    call_count = 0

    def fake_generate_content(**kwargs):
        nonlocal call_count
        call_count += 1
        raise Exception(
            "404 NOT_FOUND: models/gemini-2.5-flash is not found for API version v1beta"
        )

    monkeypatch.setattr(gemini_client.client.models, "generate_content", fake_generate_content)

    with pytest.raises(RuntimeError) as exc_info:
        await gemini_client.extract_bill(b"fake-bytes", "invoice.jpg", "image/jpeg")

    assert "Model unavailable" in str(exc_info.value)
    assert call_count == 1  # no retries consumed — fails on the first attempt


async def test_rate_limit_retries_do_not_consume_main_attempt_counter(
    monkeypatch, no_quota_limit
):
    """
    MAX_RETRIES is deliberately set to 1 — if a 429 ever incremented the main
    `attempt` counter (instead of its own consecutive_429 counter), the very
    first 429 would immediately hit MAX_RETRIES and the page would fail.
    Succeeding after three 429s with MAX_RETRIES=1 proves the two counters
    are fully independent.
    """
    monkeypatch.setattr(gemini_client.settings, "MAX_RETRIES", 1)

    call_count = 0
    sleep_calls = []

    def fake_generate_content(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise Exception("429 RESOURCE_EXHAUSTED: rate limit exceeded")
        return _FakeResponse()

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(gemini_client.client.models, "generate_content", fake_generate_content)
    monkeypatch.setattr(gemini_client.asyncio, "sleep", fake_sleep)

    result = await gemini_client.extract_bill(b"fake-bytes", "invoice.jpg", "image/jpeg")

    assert result is not None
    assert call_count == 4  # 3 rate-limited attempts + 1 success
    assert sleep_calls == [30, 60, 90]  # _429_BACKOFFS, one per rate-limit wait


async def test_structured_parsed_response_used_directly_without_fallback(
    monkeypatch, no_quota_limit
):
    """response.parsed (a plain dict, per response_json_schema's contract) must be
    mapped into GeminiExtractionResult — _parse_json (the text-fallback path) must
    NOT be invoked when the structured parse succeeded."""
    parsed_dict = {
        "invoice_number": "INV-100",
        "supplier_name": "Acme Pvt Ltd",
        "line_items": [
            {"description": "Steel rod", "quantity": 10.0, "amount": 1000.0},
            {"description": "Carbide insert", "quantity": 5.0, "amount": 500.0},
        ],
    }

    class _StructuredResponse:
        text = "{}"  # deliberately not valid JSON for the real fields — proves it's unused
        candidates = [_FakeCandidate()]
        parsed = parsed_dict

    def fake_generate_content(**kwargs):
        return _StructuredResponse()

    parse_json_calls = []
    original_parse_json = gemini_client._parse_json

    def spy_parse_json(text):
        parse_json_calls.append(text)
        return original_parse_json(text)

    monkeypatch.setattr(gemini_client.client.models, "generate_content", fake_generate_content)
    monkeypatch.setattr(gemini_client, "_parse_json", spy_parse_json)

    result = await gemini_client.extract_bill(b"fake-bytes", "invoice.jpg", "image/jpeg")

    assert isinstance(result, GeminiExtractionResult)
    assert result.invoice_number == "INV-100"
    assert result.supplier_name == "Acme Pvt Ltd"
    assert len(result.line_items) == 2
    assert all(isinstance(li, GeminiLineItem) for li in result.line_items)
    assert parse_json_calls == []  # fallback never invoked


def test_response_json_schema_has_no_default_keys_anywhere():
    """Pins the exact failure mode hit against the live API: the Gemini Developer
    API rejects any schema node with a non-null 'default'. If a future edit adds
    a field with a default to GeminiExtractionResult/GeminiLineItem without also
    covering it in _strip_schema_keys, this must fail — not production."""

    def find_default_paths(node, path=""):
        found = []
        if isinstance(node, dict):
            if "default" in node:
                found.append(path or "<root>")
            for key, value in node.items():
                found.extend(find_default_paths(value, f"{path}.{key}"))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                found.extend(find_default_paths(item, f"{path}[{index}]"))
        return found

    offending_paths = find_default_paths(gemini_client._RESPONSE_JSON_SCHEMA)
    assert offending_paths == []


async def test_parsed_none_falls_back_to_text_parse_with_warning(
    monkeypatch, no_quota_limit, caplog
):
    """response.parsed = None (structured parse missed) must fall back to the
    existing _parse_json(response.text) path, and log a WARNING when it fires."""
    import json

    fallback_dict = {
        "invoice_number": "INV-200",
        "supplier_name": "Fallback Supplier",
        "line_items": [{"description": "Bearing", "quantity": 2.0, "amount": 200.0}],
    }

    class _FallbackResponse:
        text = json.dumps(fallback_dict)
        candidates = [_FakeCandidate()]
        parsed = None

    def fake_generate_content(**kwargs):
        return _FallbackResponse()

    monkeypatch.setattr(gemini_client.client.models, "generate_content", fake_generate_content)

    with caplog.at_level("WARNING"):
        result = await gemini_client.extract_bill(b"fake-bytes", "invoice.jpg", "image/jpeg")

    assert result is not None
    assert result.invoice_number == "INV-200"
    assert len(result.line_items) == 1
    assert "structured parse missed, fell back to text parse" in caplog.text
