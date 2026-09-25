import uuid
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from sqlalchemy import select, update, func

from app.config import settings
from app.database import AsyncSessionLocal, BlueprintORM, JobORM, JobPageORM, ProjectORM, UserORM, db_retry
from app.core.auth import get_current_user
from app.core.batch_processor import run_retry_processor
from app.core.job_store import job_store
from app.core.storage import delete_excel, is_storage_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Jobs"])


@router.get("/jobs")
@db_retry()
async def list_jobs(current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(JobORM, ProjectORM.name, BlueprintORM.name)
            .outerjoin(ProjectORM, JobORM.project_id == ProjectORM.id)
            .outerjoin(BlueprintORM, JobORM.blueprint_id == BlueprintORM.id)
            .where(JobORM.user_id == current_user.id)
            .order_by(JobORM.created_at.desc())
            .limit(50)
        )
        return [
            {
                "id": str(j.id),
                "project_id": str(j.project_id) if j.project_id else None,
                "project_name": project_name,
                "blueprint_name": blueprint_name,
                "status": j.status,
                "total_files": j.total_files,
                "processed_files": (j.completed_pages or 0) + (j.failed_pages or 0),
                "failed_files": j.failed_files or 0,
                "total_pages": j.total_pages or 0,
                "completed_pages": j.completed_pages or 0,
                "failed_pages": j.failed_pages or 0,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                "excel_ready": j.excel_path is not None,
            }
            for j, project_name, blueprint_name in result.all()
        ]


@router.get("/stats")
@db_retry()
async def get_user_stats(current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        row = await session.execute(
            select(
                func.count(JobORM.id).label("total_jobs"),
                func.coalesce(func.sum(JobORM.total_files), 0).label("total_files"),
            ).where(JobORM.user_id == current_user.id)
        )
        r = row.one()
        return {
            "total_jobs": r.total_jobs,
            "total_files": r.total_files,
        }


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserORM = Depends(get_current_user),
):
    """
    Re-extract only the failed pages for a job. Pages with status=done are
    never touched. Idempotent: calling twice only retries whatever is still failed.
    Returns 409 if the job is already processing.
    """
    async with AsyncSessionLocal() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))
        if not job or job.user_id != current_user.id:
            raise HTTPException(404, "Job not found")

        if job.status == "processing":
            raise HTTPException(409, "Job is already processing")

        result = await session.execute(
            select(JobPageORM).where(
                JobPageORM.job_id == uuid.UUID(job_id),
                JobPageORM.status == "failed",
            )
        )
        failed_pages = result.scalars().all()

        if not failed_pages:
            return {"job_id": job_id, "retrying": 0, "status": job.status}

        # Pre-check: all source files must exist on disk before we start
        upload_dir = Path(settings.UPLOAD_DIR)
        missing = [
            p.filename for p in failed_pages
            if not (upload_dir / f"{job_id}_{p.filename}").exists()
        ]
        if missing:
            unique_missing = list(dict.fromkeys(missing))
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Source files for {len(unique_missing)} file(s) have expired and are no longer on disk. "
                    f"Please re-upload to retry. Missing: {unique_missing[:5]}"
                ),
            )

        retry_count = len(failed_pages)
        failed_page_data = [
            {"page_index": p.page_index, "filename": p.filename, "page_label": p.page_label}
            for p in failed_pages
        ]

        # Reset target pages to queued
        await session.execute(
            update(JobPageORM)
            .where(
                JobPageORM.job_id == uuid.UUID(job_id),
                JobPageORM.status == "failed",
            )
            .values(status="queued", error_message=None, updated_at=datetime.utcnow())
        )

        # Atomic decrement of failed_pages; set job back to processing
        await session.execute(
            update(JobORM)
            .where(JobORM.id == uuid.UUID(job_id))
            .values(
                failed_pages=JobORM.failed_pages - retry_count,
                status="processing",
                completed_at=None,
            )
        )
        await session.commit()

    # Reset job_store for SSE before the background task starts (avoids
    # a race where the client hits /stream before the task calls job_store.update)
    job_store.delete(job_id)
    job_store.create(job_id, total_files=retry_count)
    job_store.update(job_id, status="processing")

    background_tasks.add_task(run_retry_processor, job_id, failed_page_data, str(current_user.id))

    return {"job_id": job_id, "retrying": retry_count, "status": "processing"}


@router.delete("/job/{job_id}")
@db_retry()
async def delete_job(job_id: str, current_user: UserORM = Depends(get_current_user)):
    excel_path = None
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(JobORM, uuid.UUID(job_id))
            if not job or job.user_id != current_user.id:
                raise HTTPException(404, "Job not found")
            excel_path = job.excel_path
            await session.delete(job)
            await session.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete job {job_id} DB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete job")

    job_store.delete(job_id)

    if excel_path and is_storage_key(excel_path):
        await delete_excel(excel_path)

    return {"deleted": job_id}
