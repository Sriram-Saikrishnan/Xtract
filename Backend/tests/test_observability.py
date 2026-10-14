"""AI observability — cost math, context propagation, logging, and the overview API."""
import asyncio
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.api.routes_observability as routes_observability
import app.core.observability as observability
from app.core.observability import compute_cost, get_llm_context, record_llm_call, set_llm_context
from app.database import LLMCallORM


@pytest.fixture
def obs_db(patch_db, monkeypatch):
    monkeypatch.setattr(observability, "AsyncSessionLocal", patch_db)
    monkeypatch.setattr(routes_observability, "AsyncSessionLocal", patch_db)
    monkeypatch.setattr(observability, "_write_sem", None)
    return patch_db


def _response(prompt=1000, out=200, thoughts=300, cached=0):
    return SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=thoughts,
        cached_content_token_count=cached, total_token_count=prompt + out + thoughts,
    ))


def test_compute_cost_bills_thinking_as_output_and_cached_at_cached_rate():
    # gemini-2.5-flash: input 0.30, output 2.50, cached 0.075 per 1M
    cost = compute_cost("gemini-2.5-flash", input_tokens=1_000_000, output_tokens=100_000,
                        thinking_tokens=100_000, cached_tokens=400_000)
    assert cost == pytest.approx(0.6 * 0.30 + 0.4 * 0.075 + 0.2 * 2.50)


def test_compute_cost_matches_versioned_model_by_prefix_and_unknown_is_zero():
    assert compute_cost("gemini-2.5-flash-preview-05-20", 1_000_000, 0, 0, 0) == pytest.approx(0.30)
    assert compute_cost("gemini-2.5-flash-lite-001", 1_000_000, 0, 0, 0) == pytest.approx(0.10)
    assert compute_cost("some-unknown-model", 1_000_000, 1_000_000, 0, 0) == 0.0


async def test_context_does_not_leak_between_sibling_tasks():
    set_llm_context(operation="extract", job_id="j1")

    async def page(label):
        set_llm_context(page_label=label)
        await asyncio.sleep(0)
        return get_llm_context()

    a, b = await asyncio.gather(page("a"), page("b"))
    assert a["page_label"] == "a" and b["page_label"] == "b"
    assert a["job_id"] == "j1"
    assert "page_label" not in get_llm_context()


async def test_record_llm_call_writes_row_with_context(obs_db, fake_user):
    job_id = uuid.uuid4()
    set_llm_context(operation="extract", user_id=fake_user.id, job_id=str(job_id), page_label="inv.pdf")
    record_llm_call(model="gemini-2.5-flash", latency_ms=1234.5, status="success",
                    response=_response(), attempt=2, input_mode="native_pdf")
    await asyncio.gather(*observability._pending)

    async with obs_db() as session:
        row = (await session.execute(select(LLMCallORM))).scalar_one()
    assert row.user_id == fake_user.id and row.job_id == job_id
    assert (row.input_tokens, row.output_tokens, row.thinking_tokens, row.total_tokens) == (1000, 200, 300, 1500)
    assert row.latency_ms == 1234 and row.attempt == 2 and row.status == "success"
    assert row.cost_usd == pytest.approx((1000 * 0.30 + 500 * 2.50) / 1_000_000)


async def test_record_llm_call_classifies_errors(obs_db, fake_user):
    set_llm_context(operation="extract", user_id=fake_user.id)
    record_llm_call(model="gemini-2.5-flash", latency_ms=10, status="error",
                    error=RuntimeError("429 RESOURCE_EXHAUSTED"))
    await asyncio.gather(*observability._pending)

    async with obs_db() as session:
        row = (await session.execute(select(LLMCallORM))).scalar_one()
    assert row.error_type == "rate_limit" and row.total_tokens == 0 and row.cost_usd == 0.0


async def test_overview_aggregates_only_callers_rows(obs_db, fake_user):
    job_id = uuid.uuid4()
    now = datetime.utcnow()
    async with obs_db() as session:
        for i, (status, latency) in enumerate([("success", 1000), ("success", 3000), ("error", 50)]):
            session.add(LLMCallORM(
                user_id=fake_user.id, job_id=job_id, page_label=f"p{i}", operation="extract",
                model="gemini-2.5-flash", input_tokens=100, output_tokens=10, thinking_tokens=5,
                total_tokens=115, latency_ms=latency, cost_usd=0.01, status=status,
                error_type="timeout" if status == "error" else None, created_at=now,
            ))
        # Another user's row, and one outside the window — both excluded.
        session.add(LLMCallORM(user_id=uuid.uuid4(), operation="extract", model="m", status="success",
                               cost_usd=5.0, created_at=now))
        session.add(LLMCallORM(user_id=fake_user.id, operation="extract", model="m", status="success",
                               cost_usd=5.0, created_at=now - timedelta(days=40)))
        await session.commit()

    data = await routes_observability.overview(days=30, current_user=fake_user)
    t = data["totals"]
    assert t["calls"] == 3 and t["errors"] == 1 and t["cost_usd"] == pytest.approx(0.03)
    assert t["pages_extracted"] == 2 and t["p50_latency_ms"] == 2000
    assert len(data["daily"]) == 30 and data["daily"][-1]["calls"] == 3
    assert data["errors_by_type"] == [{"error_type": "timeout", "count": 1}]

    calls = await routes_observability.list_calls(limit=50, offset=0, job_id=None, current_user=fake_user)
    assert calls["total"] == 4 and calls["items"][0]["model"]


async def test_jobs_endpoint_aggregates_per_job_and_paginates(obs_db, fake_user, make_job):
    now = datetime.utcnow()
    cheap = await make_job(user_id=fake_user.id, status="completed", total_pages=2, completed_pages=2)
    pricey = await make_job(user_id=fake_user.id, status="completed", total_pages=1, completed_pages=1)
    async with obs_db() as session:
        for job_id, cost, latency, status, attempt in [
            (cheap, 0.01, 1000, "success", 1), (cheap, 0.01, 3000, "success", 1),
            (pricey, 0.0, 10, "error", 1), (pricey, 0.5, 5000, "success", 2),
        ]:
            session.add(LLMCallORM(
                user_id=fake_user.id, job_id=job_id, operation="extract", model="gemini-2.5-flash",
                input_tokens=100, output_tokens=10, thinking_tokens=5, total_tokens=115,
                latency_ms=latency, cost_usd=cost, status=status, attempt=attempt, created_at=now,
            ))
        # Another user's call on the same job id must not be counted.
        session.add(LLMCallORM(user_id=uuid.uuid4(), job_id=cheap, operation="extract", model="m",
                               status="success", cost_usd=9.0, created_at=now))
        await session.commit()

    page1 = await routes_observability.list_jobs(days=30, sort="cost", order="desc", limit=1, offset=0,
                                                 current_user=fake_user)
    assert page1["total"] == 2 and len(page1["items"]) == 1
    top = page1["items"][0]
    assert top["job_id"] == str(pricey)
    assert (top["calls"], top["errors"], top["retries"]) == (2, 1, 1)
    assert top["avg_latency_ms"] == 5000 and top["cost_per_page_usd"] == pytest.approx(0.5)

    page2 = await routes_observability.list_jobs(days=30, sort="cost", order="desc", limit=1, offset=1,
                                                 current_user=fake_user)
    other = page2["items"][0]
    assert other["job_id"] == str(cheap)
    assert other["cost_usd"] == pytest.approx(0.02) and other["p50_latency_ms"] == 2000
    assert other["input_tokens"] == 200 and other["tokens_per_page"] == 115

    detail = await routes_observability.job_detail(str(cheap), current_user=fake_user)
    assert detail["calls"] == 2

    calls = await routes_observability.list_calls(limit=1, offset=0, job_id=str(pricey), current_user=fake_user)
    assert calls["total"] == 2 and len(calls["items"]) == 1


async def test_job_detail_404_for_unknown_or_foreign_job(obs_db, fake_user):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await routes_observability.job_detail(str(uuid.uuid4()), current_user=fake_user)
    with pytest.raises(HTTPException):
        await routes_observability.job_detail("not-a-uuid", current_user=fake_user)
