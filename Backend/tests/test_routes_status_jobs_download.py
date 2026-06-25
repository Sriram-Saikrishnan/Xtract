"""
Step 3 — route-level checks:
- GET /status reads from DB, not job_store (job_store left completely empty
  in every test here — simulating both a fresh poll and a post-restart poll).
- completed_with_errors is recognized by routes_status, routes_jobs, and
  routes_download (download must succeed, not 403/404/400).
"""
import uuid

import pytest
from fastapi import HTTPException

from app.api.routes_download import download_excel_route
from app.api.routes_jobs import list_jobs
from app.api.routes_status import get_status
from app.core.job_store import job_store
from app.database import JobPageORM


async def _seed_job_with_pages(make_job, patch_db, user_id, **job_fields):
    job_id = await make_job(user_id=user_id, **job_fields)
    async with patch_db() as session:
        for i in range(job_fields.get("total_pages", 0)):
            session.add(JobPageORM(
                job_id=job_id, page_index=i, filename=f"f{i}.jpg", page_label=f"f{i}.jpg",
                status="done", error_message=None,
            ))
        await session.commit()
    return job_id


@pytest.fixture(autouse=True)
def ensure_job_store_empty():
    """job_store is a process-wide singleton dict — make sure no test leaks state into it,
    and that it stays empty so /status is provably reading from the DB, not memory."""
    job_store._store.clear()
    job_store._queues.clear()
    yield
    job_store._store.clear()
    job_store._queues.clear()


async def test_status_reads_from_db_when_job_store_is_empty(make_job, patch_db, fake_user):
    job_id = await _seed_job_with_pages(
        make_job, patch_db, fake_user.id,
        status="processing", total_pages=5, completed_pages=3, failed_pages=0, total_files=2,
    )
    assert job_store.get(str(job_id)) is None  # confirm nothing in memory

    response = await get_status(str(job_id), current_user=fake_user)

    assert response.status == "processing"
    assert response.total_pages == 5
    assert response.completed_pages == 3
    assert response.failed_pages == 0
    assert response.processed_files == 3


async def test_status_404_for_job_belonging_to_another_user(make_job, patch_db, fake_user):
    other_user_job = await make_job(user_id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        await get_status(str(other_user_job), current_user=fake_user)
    assert exc_info.value.status_code == 404


async def test_status_404_for_nonexistent_job(patch_db, fake_user):
    with pytest.raises(HTTPException) as exc_info:
        await get_status(str(uuid.uuid4()), current_user=fake_user)
    assert exc_info.value.status_code == 404


async def test_status_reports_completed_with_errors(make_job, patch_db, fake_user):
    job_id = await _seed_job_with_pages(
        make_job, patch_db, fake_user.id,
        status="completed_with_errors", total_pages=5, completed_pages=4, failed_pages=1,
        verified_count=4, flagged_count=0, error_count=1,
    )

    response = await get_status(str(job_id), current_user=fake_user)

    assert response.status == "completed_with_errors"
    assert response.failed_pages == 1
    assert response.completed_pages == 4


async def test_worker_restart_simulation_status_survives_with_no_in_memory_state(make_job, patch_db, fake_user):
    """job_store starts and stays empty for the whole test (autouse fixture) — this is
    the worker-restart scenario: progress is only knowable from the DB."""
    job_id = await _seed_job_with_pages(
        make_job, patch_db, fake_user.id,
        status="processing", total_pages=10, completed_pages=7, failed_pages=1,
    )

    response = await get_status(str(job_id), current_user=fake_user)

    assert response.total_pages == 10
    assert response.completed_pages == 7
    assert response.failed_pages == 1
    assert response.processed_files == 8


async def test_jobs_list_reports_completed_with_errors_and_page_counts(make_job, patch_db, fake_user):
    await _seed_job_with_pages(
        make_job, patch_db, fake_user.id,
        status="completed_with_errors", total_pages=5, completed_pages=4, failed_pages=1,
        verified_count=4, flagged_count=0, error_count=1,
    )

    jobs = await list_jobs(current_user=fake_user)

    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed_with_errors"
    assert jobs[0]["total_pages"] == 5
    assert jobs[0]["completed_pages"] == 4
    assert jobs[0]["failed_pages"] == 1
    assert jobs[0]["processed_files"] == 5  # completed_pages + failed_pages


async def test_download_succeeds_for_completed_with_errors(make_job, patch_db, fake_user, monkeypatch):
    import app.api.routes_download as rd

    async def fake_download_excel(key):
        return b"fake-excel-bytes"

    monkeypatch.setattr(rd, "download_excel", fake_download_excel)

    job_id = await make_job(
        user_id=fake_user.id, status="completed_with_errors", excel_path="user123/job123.xlsx",
    )

    response = await download_excel_route(str(job_id), current_user=fake_user)

    assert response.status_code == 200
    assert response.body == b"fake-excel-bytes"


async def test_download_succeeds_for_plain_done(make_job, patch_db, fake_user, monkeypatch):
    import app.api.routes_download as rd

    async def fake_download_excel(key):
        return b"fake-excel-bytes"

    monkeypatch.setattr(rd, "download_excel", fake_download_excel)

    job_id = await make_job(user_id=fake_user.id, status="done", excel_path="user123/job123.xlsx")

    response = await download_excel_route(str(job_id), current_user=fake_user)
    assert response.status_code == 200


async def test_download_rejected_while_still_processing(make_job, patch_db, fake_user):
    job_id = await make_job(user_id=fake_user.id, status="processing")

    with pytest.raises(HTTPException) as exc_info:
        await download_excel_route(str(job_id), current_user=fake_user)
    assert exc_info.value.status_code == 400
