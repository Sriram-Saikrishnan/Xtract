"""
Step 1 (Gate housekeeping) — fail-fast behavior for model-not-found errors.
Step 4a — 429/rate-limit waits must use their own counter, never MAX_RETRIES.

extract_bill's Gemini network call and quota check are mocked (external
services) — pure unit tests of the retry-loop branches in gemini_client.py.
"""
import pytest

import app.core.gemini_client as gemini_client


class _FakeCandidate:
    finish_reason = "STOP"


class _FakeResponse:
    text = "{}"
    candidates = [_FakeCandidate()]


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
