import uuid
import logging

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response

from app.core.auth import get_current_user
from app.core.storage import download_excel, is_storage_key
from app.database import AsyncSessionLocal, JobORM, UserORM

logger = logging.getLogger(__name__)
router = APIRouter()

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/download/{job_id}")
async def download_excel_route(job_id: str, current_user: UserORM = Depends(get_current_user)):
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(JobORM, uuid.UUID(job_id))
    except Exception as e:
        logger.error(f"DB lookup failed for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "done":
        raise HTTPException(status_code=400, detail=f"Job is not complete yet. Status: {job.status}")

    if not job.excel_path:
        raise HTTPException(status_code=404, detail="Excel file not generated yet")

    if not is_storage_key(job.excel_path):
        raise HTTPException(status_code=404, detail="Excel file is no longer available. Please re-run the extraction.")

    try:
        file_bytes = await download_excel(job.excel_path)
    except Exception as e:
        logger.error(f"Storage download failed for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve Excel file from storage")

    return Response(
        content=file_bytes,
        media_type=EXCEL_MIME,
        headers={"Content-Disposition": f'attachment; filename="Xtract_{job_id[:8]}.xlsx"'},
    )
