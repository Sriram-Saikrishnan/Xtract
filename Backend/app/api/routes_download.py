import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user
from app.core.storage import download_excel, is_storage_key
from app.database import AsyncSessionLocal, JobORM, InvoiceORM, UserORM
from app.excel.excel_builder import build_workbook, workbook_to_bytes

logger = logging.getLogger(__name__)
router = APIRouter()

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/download/master")
async def download_master_excel(
    start_date: str = Query(..., description="ISO date string, e.g. 2026-01-01"),
    end_date: str = Query(..., description="ISO date string, e.g. 2026-06-30"),
    current_user: UserORM = Depends(get_current_user),
):
    try:
        start_dt = datetime.fromisoformat(start_date).replace(tzinfo=None)
        # Include the full end day
        end_dt = datetime.fromisoformat(end_date).replace(tzinfo=None) + timedelta(days=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if (end_dt - start_dt).days > 365:
        raise HTTPException(status_code=400, detail="Date range cannot exceed 365 days.")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceORM)
            .join(JobORM, InvoiceORM.job_id == JobORM.id)
            .where(
                JobORM.user_id == current_user.id,
                InvoiceORM.extracted_at >= start_dt,
                InvoiceORM.extracted_at < end_dt,
            )
            .options(selectinload(InvoiceORM.line_items))
            .order_by(InvoiceORM.extracted_at)
        )
        invoices = result.scalars().all()

    if not invoices:
        raise HTTPException(status_code=404, detail="No invoices found in the selected date range.")

    wb = build_workbook(invoices)
    file_bytes = workbook_to_bytes(wb)

    label = f"{start_date}_to_{end_date}"
    logger.info(f"Master Excel: user={current_user.id}, range={label}, invoices={len(invoices)}")

    return Response(
        content=file_bytes,
        media_type=EXCEL_MIME,
        headers={"Content-Disposition": f'attachment; filename="Xtract_Master_{label}.xlsx"'},
    )


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
