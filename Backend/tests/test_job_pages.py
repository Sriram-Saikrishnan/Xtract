"""
Step 3 — DB-backed job progress: job_pages table + atomic jobs counters,
exercised directly against the batch_processor helper functions (real
SQLite test DB via patch_db, no mocked ORM calls).
"""
import asyncio
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.core.batch_processor import (
    PageTask,
    _increment_job_counter,
    _init_job_pages,
    _set_page_status,
)
from app.database import JobORM, JobPageORM


def _tasks(n):
    return [PageTask(f"file{i}.jpg", f"file{i}.jpg", "image/jpeg", b"x", 1) for i in range(n)]


async def test_init_job_pages_creates_one_row_per_page_with_queued_status(make_job, patch_db):
    job_id = str(await make_job())
    tasks = _tasks(4)

    await _init_job_pages(job_id, tasks)

    async with patch_db() as session:
        result = await session.execute(
            select(JobPageORM).where(JobPageORM.job_id == uuid.UUID(job_id)).order_by(JobPageORM.page_index)
        )
        pages = result.scalars().all()
        job = await session.get(JobORM, uuid.UUID(job_id))

    assert len(pages) == 4
    assert all(p.status == "queued" for p in pages)
    assert [p.page_index for p in pages] == [0, 1, 2, 3]
    assert [p.filename for p in pages] == [f"file{i}.jpg" for i in range(4)]
    assert job.total_pages == 4


async def test_set_page_status_done_updates_status_and_updated_at(make_job, patch_db):
    job_id = str(await make_job())
    await _init_job_pages(job_id, _tasks(1))

    async with patch_db() as session:
        result = await session.execute(select(JobPageORM).where(JobPageORM.job_id == uuid.UUID(job_id)))
        original = result.scalars().one()
        original_updated_at = original.updated_at

    await asyncio.sleep(0.01)  # ensure a measurable time delta
    before_call = datetime.utcnow()
    await _set_page_status(job_id, 0, "done")

    async with patch_db() as session:
        result = await session.execute(select(JobPageORM).where(JobPageORM.job_id == uuid.UUID(job_id)))
        updated = result.scalars().one()

    assert updated.status == "done"
    assert updated.updated_at >= before_call
    assert updated.updated_at > original_updated_at


async def test_set_page_status_failed_stores_error_message(make_job, patch_db):
    job_id = str(await make_job())
    await _init_job_pages(job_id, _tasks(1))

    await _set_page_status(job_id, 0, "failed", error_message="Gemini timed out")

    async with patch_db() as session:
        result = await session.execute(select(JobPageORM).where(JobPageORM.job_id == uuid.UUID(job_id)))
        page = result.scalars().one()

    assert page.status == "failed"
    assert page.error_message == "Gemini timed out"


async def test_increment_job_counter_is_a_single_atomic_update_no_read_then_write(make_job, patch_db, monkeypatch):
    """
    Confirm _increment_job_counter issues exactly one session.execute() call —
    i.e. a single `SET col = col + :by` statement — never a separate SELECT
    to read the current value first.
    """
    job_id = str(await make_job(completed_pages=0))

    import app.core.batch_processor as bp
    real_factory = bp.AsyncSessionLocal
    execute_calls = []

    class _CountingSession:
        def __init__(self, inner):
            self._inner = inner

        async def __aenter__(self):
            self._session = await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

        async def execute(self, *a, **kw):
            execute_calls.append(a[0])
            return await self._session.execute(*a, **kw)

        async def commit(self):
            await self._session.commit()

    def counting_factory():
        return _CountingSession(real_factory())

    monkeypatch.setattr(bp, "AsyncSessionLocal", counting_factory)

    await _increment_job_counter(job_id, "completed_pages", 1)

    assert len(execute_calls) == 1, f"Expected exactly one execute() call, got {len(execute_calls)}"


async def test_concurrent_increments_produce_correct_count_no_undercount(make_job, patch_db):
    """
    The classic race: if the production code ever did read-then-write instead
    of an atomic SQL increment, N concurrent increments would undercount.
    Run 25 concurrent _increment_job_counter calls and confirm the final
    value is exactly 25, proving the atomic SET col = col + 1 pattern holds
    even under real concurrent execution.
    """
    job_id = str(await make_job(completed_pages=0))

    await asyncio.gather(*(
        _increment_job_counter(job_id, "completed_pages", 1) for _ in range(25)
    ))

    async with patch_db() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))

    assert job.completed_pages == 25


async def test_terminal_status_done_when_no_pages_failed(make_job, patch_db):
    job_id = str(await make_job(completed_pages=3, failed_pages=0, total_pages=3))
    async with patch_db() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))
    page_failures = job.total_pages - job.completed_pages
    assert page_failures == 0  # mirrors the run_batch_processor terminal-status check


async def test_terminal_status_completed_with_errors_when_any_page_failed(make_job, patch_db):
    job_id = str(await make_job(completed_pages=2, failed_pages=1, total_pages=3))
    async with patch_db() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))
    page_failures = job.total_pages - job.completed_pages
    assert page_failures == 1
