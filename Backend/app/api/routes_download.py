import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.database import AsyncSessionLocal, JobORM

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/download/{job_id}")
async def download_excel(job_id: str):
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(JobORM, uuid.UUID(job_id))
    except Exception as e:
        logger.error(f"DB lookup failed for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "done":
        raise HTTPException(status_code=400, detail=f"Job is not complete yet. Status: {job.status}")

    if not job.excel_path:
        raise HTTPException(status_code=404, detail="Excel file not generated yet")

    excel_path = Path(job.excel_path)
    if not excel_path.exists():
        raise HTTPException(status_code=404, detail="Excel file not found on disk. It may have been cleaned up.")

    return FileResponse(
        path=str(excel_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="Bill_Extracted.xlsx",
    )
