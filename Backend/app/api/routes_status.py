import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends

from app.core.job_store import job_store
from app.core.auth import get_current_user
from app.database import AsyncSessionLocal, JobORM, UserORM
from app.models.job import JobStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_status(job_id: str, current_user: UserORM = Depends(get_current_user)):
    # Live in-memory status takes priority (job is still processing on this instance)
    live = job_store.get(job_id)
    if live:
        return JobStatusResponse(
            job_id=job_id,
            status=live["status"],
            total_files=live["total_files"],
            processed_files=live["processed_files"],
            verified_count=live["verified_count"],
            flagged_count=live["flagged_count"],
            error_count=live["error_count"],
            created_at=live["created_at"],
            completed_at=live.get("completed_at"),
            excel_ready=live.get("excel_ready", False),
        )

    # Fall back to DB for completed/historical jobs
    try:
        async with AsyncSessionLocal() as session:
            result = await session.get(JobORM, uuid.UUID(job_id))
            if not result or result.user_id != current_user.id:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
            return JobStatusResponse(
                job_id=str(result.id),
                status=result.status,
                total_files=result.total_files,
                processed_files=result.processed_files,
                verified_count=result.verified_count,
                flagged_count=result.flagged_count,
                error_count=result.error_count,
                created_at=result.created_at,
                completed_at=result.completed_at,
                excel_ready=result.excel_path is not None,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Status lookup failed for {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve job status")
