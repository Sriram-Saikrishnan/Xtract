"""
Step 3 (Gate B) — startup recovery for jobs/pages orphaned by a server crash.
Exercised directly against recover_interrupted_jobs() with a real SQLite test DB.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.startup_recovery import recover_interrupted_jobs
from app.database import JobORM, JobPageORM

STALE = timedelta(minutes=settings.STARTUP_RECOVERY_STALE_MINUTES + 5)
FRESH = timedelta(minutes=1)


async def _add_page(patch_db, job_id, page_index, status, updated_at, filename="f.jpg"):
    async with patch_db() as session:
        session.add(JobPageORM(
            job_id=job_id,
            page_index=page_index,
            filename=filename,
            page_label=filename,
            status=status,
            updated_at=updated_at,
        ))
        await session.commit()


async def test_stale_processing_page_flipped_job_becomes_completed_with_errors(make_job, patch_db):
    job_id = await make_job(status="queued", total_pages=2)
    now = datetime.utcnow()
    await _add_page(patch_db, job_id, 0, "processing", now - STALE)
    await _add_page(patch_db, job_id, 1, "done", now - STALE)

    await recover_interrupted_jobs()

    async with patch_db() as session:
        job = await session.get(JobORM, job_id)
        pages = (await session.execute(
            select(JobPageORM).where(JobPageORM.job_id == job_id).order_by(JobPageORM.page_index)
        )).scalars().all()

    assert pages[0].status == "failed"
    assert pages[0].error_message == "Interrupted by server restart"
    assert pages[1].status == "done"
    assert job.status == "completed_with_errors"
    assert job.completed_pages == 1
    assert job.failed_pages == 1


async def test_stale_queued_job_with_zero_pages_becomes_error(make_job, patch_db):
    now = datetime.utcnow()
    job_id = await make_job(status="queued", total_pages=0, created_at=now - STALE)

    await recover_interrupted_jobs()

    async with patch_db() as session:
        job = await session.get(JobORM, job_id)

    assert job.status == "error"
    assert job.completed_at is not None


async def test_stale_page_flipped_but_fresh_processing_page_blocks_reconciliation(make_job, patch_db):
    job_id = await make_job(status="queued", total_pages=2)
    now = datetime.utcnow()
    await _add_page(patch_db, job_id, 0, "processing", now - STALE)   # stale -> flips
    await _add_page(patch_db, job_id, 1, "processing", now - FRESH)  # fresh -> untouched

    await recover_interrupted_jobs()

    async with patch_db() as session:
        job = await session.get(JobORM, job_id)
        pages = (await session.execute(
            select(JobPageORM).where(JobPageORM.job_id == job_id).order_by(JobPageORM.page_index)
        )).scalars().all()

    assert pages[0].status == "failed"
    assert pages[0].error_message == "Interrupted by server restart"
    assert pages[1].status == "processing"  # untouched — still fresh
    assert job.status == "queued"  # NOT reconciled — job left exactly as-is


async def test_all_pages_done_but_excel_path_null_becomes_error(make_job, patch_db):
    job_id = await make_job(status="queued", total_pages=1, excel_path=None)
    now = datetime.utcnow()
    await _add_page(patch_db, job_id, 0, "done", now - STALE)

    await recover_interrupted_jobs()

    async with patch_db() as session:
        job = await session.get(JobORM, job_id)

    assert job.status == "error"
    assert job.completed_pages == 1
    assert job.completed_at is not None


async def test_empty_database_is_a_noop_no_exception(patch_db):
    await recover_interrupted_jobs()  # must not raise
