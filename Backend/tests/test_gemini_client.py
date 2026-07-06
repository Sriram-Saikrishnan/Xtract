"""
Step 1 (Gate housekeeping) — fail-fast behavior for model-not-found errors.

extract_bill's Gemini network call and quota check are mocked (external
services) — this is a pure unit test of the retry-loop branch added in
gemini_client.py: a model-not-found error must raise immediately, with
zero retries consumed.
"""
import pytest

import app.core.gemini_client as gemini_client


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
