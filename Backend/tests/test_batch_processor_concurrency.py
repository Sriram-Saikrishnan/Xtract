"""
Step 2 — semaphore-bounded concurrent page extraction in batch_processor.py.

extract_bill/build_excel/upload_excel are mocked (Gemini + Supabase Storage —
external calls). job_pages/jobs writes go through the real SQLite test DB via
the patch_db fixture, so these are genuine end-to-end runs of
run_batch_processor, not isolated unit calls.
"""
import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.batch_processor import run_batch_processor
from app.database import JobORM, JobPageORM
from app.models.extraction import GeminiExtractionResult


def _write_dummy_images(tmp_path, n):
    """mime detection in get_mime_type() is extension-based only — content can be arbitrary."""
    paths, names = [], []
    for i in range(n):
        name = f"invoice_{i}.jpg"
        p = tmp_path / name
        p.write_bytes(b"not-a-real-image")
        paths.append(p)
        names.append(name)
    return paths, names


async def _fetch_job(session_factory, job_id):
    async with session_factory() as session:
        return await session.get(JobORM, uuid.UUID(job_id))


async def _fetch_pages(session_factory, job_id):
    async with session_factory() as session:
        result = await session.execute(
            select(JobPageORM).where(JobPageORM.job_id == uuid.UUID(job_id)).order_by(JobPageORM.page_index)
        )
        return result.scalars().all()


@pytest.fixture
def patched_excel(monkeypatch):
    """Mock the Excel-generation tail of the pipeline — external Supabase Storage call."""
    import app.core.batch_processor as bp

    calls = {"build_excel_at": None, "save_finished_at": []}

    async def fake_build_excel(job_id):
        calls["build_excel_at"] = time.monotonic()
        return tmp_excel_path()

    async def fake_upload_excel(user_id, job_id, local_path):
        return f"{user_id}/{job_id}.xlsx"

    def tmp_excel_path():
        import tempfile
        from pathlib import Path
        f = Path(tempfile.gettempdir()) / f"{uuid.uuid4()}.xlsx"
        f.write_bytes(b"fake-xlsx")
        return f

    monkeypatch.setattr(bp, "build_excel", fake_build_excel)
    monkeypatch.setattr(bp, "upload_excel", fake_upload_excel)
    return calls


async def test_six_pages_run_concurrently_not_sequentially(tmp_path, make_job, patched_excel, monkeypatch):
    import app.core.batch_processor as bp

    n = settings.MAX_CONCURRENT_EXTRACTIONS  # 6
    delay = 0.3

    async def slow_extract_bill(page_bytes, label, mime):
        await asyncio.sleep(delay)
        return GeminiExtractionResult(invoice_number=label, grand_total=100.0)

    monkeypatch.setattr(bp, "extract_bill", slow_extract_bill)

    job_id = str(await make_job())
    paths, names = _write_dummy_images(tmp_path, n)

    start = time.monotonic()
    await run_batch_processor(job_id, paths, names, user_id="u1")
    elapsed = time.monotonic() - start

    sequential_time = n * delay
    assert elapsed < sequential_time * 0.6, (
        f"Expected concurrent execution well under {sequential_time:.2f}s, took {elapsed:.2f}s"
    )


async def test_one_failing_page_does_not_block_the_others(tmp_path, make_job, patched_excel, monkeypatch):
    import app.core.batch_processor as bp

    async def extract_bill_fail_on_page_2(page_bytes, label, mime):
        if "invoice_1" in label:  # the 2nd of 5 files (0-indexed)
            raise RuntimeError("simulated extraction failure on page 2")
        return GeminiExtractionResult(invoice_number=label, grand_total=50.0)

    monkeypatch.setattr(bp, "extract_bill", extract_bill_fail_on_page_2)

    job_id = str(await make_job())
    paths, names = _write_dummy_images(tmp_path, 5)

    await run_batch_processor(job_id, paths, names, user_id="u1")

    job = await _fetch_job(bp.AsyncSessionLocal, job_id)
    pages = await _fetch_pages(bp.AsyncSessionLocal, job_id)

    assert len(pages) == 5
    statuses = {p.filename: p.status for p in pages}
    assert statuses["invoice_1.jpg"] == "failed"
    for fn in ("invoice_0.jpg", "invoice_2.jpg", "invoice_3.jpg", "invoice_4.jpg"):
        assert statuses[fn] == "done"

    failed_page = next(p for p in pages if p.filename == "invoice_1.jpg")
    assert failed_page.error_message and "simulated extraction failure" in failed_page.error_message

    assert job.completed_pages == 4
    assert job.failed_pages == 1
    assert job.status == "completed_with_errors"


async def test_raw_exception_in_gather_results_is_handled_not_crashed(tmp_path, make_job, patched_excel, monkeypatch):
    """
    _extract_one_page always catches its own exceptions, so a raw BaseException
    landing in the gather results list should never happen in practice. This
    test simulates that freak case directly by monkeypatching _extract_one_page
    itself for one task, proving the isinstance(result, BaseException) guard
    in run_batch_processor logs it, marks that page failed, and lets the run
    finish instead of crashing the whole batch.
    """
    import app.core.batch_processor as bp

    real_extract_one_page = bp._extract_one_page

    async def extract_one_page_raises_for_index_1(task, job_id, sem, total_pages, index):
        if index == 1:
            raise RuntimeError("freak escape — should never happen in production")
        return await real_extract_one_page(task, job_id, sem, total_pages, index)

    async def ok_extract_bill(page_bytes, label, mime):
        return GeminiExtractionResult(invoice_number=label, grand_total=10.0)

    monkeypatch.setattr(bp, "extract_bill", ok_extract_bill)
    monkeypatch.setattr(bp, "_extract_one_page", extract_one_page_raises_for_index_1)

    job_id = str(await make_job())
    paths, names = _write_dummy_images(tmp_path, 3)

    # Must not raise — the isinstance(result, BaseException) guard catches it.
    await run_batch_processor(job_id, paths, names, user_id="u1")

    job = await _fetch_job(bp.AsyncSessionLocal, job_id)
    pages = await _fetch_pages(bp.AsyncSessionLocal, job_id)

    assert len(pages) == 3
    assert pages[1].status == "failed"
    assert "freak escape" in pages[1].error_message
    assert job.completed_pages == 2
    assert job.failed_pages == 1
    assert job.status == "completed_with_errors"


async def test_all_saves_awaited_before_excel_generation(tmp_path, make_job, monkeypatch):
    import app.core.batch_processor as bp

    events = []

    async def ok_extract_bill(page_bytes, label, mime):
        return GeminiExtractionResult(invoice_number=label, grand_total=10.0)

    async def slow_save_bill(session, job_id, bill):
        events.append(f"save_start:{bill.invoice_number}")
        await asyncio.sleep(0.05)
        events.append(f"save_end:{bill.invoice_number}")

    async def recording_build_excel(job_id):
        events.append("build_excel_called")
        import tempfile
        from pathlib import Path
        f = Path(tempfile.gettempdir()) / f"{uuid.uuid4()}.xlsx"
        f.write_bytes(b"fake-xlsx")
        return f

    async def fake_upload_excel(user_id, job_id, local_path):
        return f"{user_id}/{job_id}.xlsx"

    monkeypatch.setattr(bp, "extract_bill", ok_extract_bill)
    monkeypatch.setattr(bp, "_save_bill", slow_save_bill)
    monkeypatch.setattr(bp, "build_excel", recording_build_excel)
    monkeypatch.setattr(bp, "upload_excel", fake_upload_excel)

    job_id = str(await make_job())
    paths, names = _write_dummy_images(tmp_path, 4)

    await run_batch_processor(job_id, paths, names, user_id="u1")

    build_excel_index = events.index("build_excel_called")
    save_end_indices = [i for i, e in enumerate(events) if e.startswith("save_end:")]

    assert len(save_end_indices) == 4
    assert all(i < build_excel_index for i in save_end_indices), (
        f"build_excel_called fired before all saves finished: {events}"
    )
