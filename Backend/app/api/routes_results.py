import uuid
import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.database import AsyncSessionLocal, ExtractionResultORM, JobORM, UserORM, db_retry
from app.core.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Results"])


class ResultCorrect(BaseModel):
    data: dict


def _result_dict(r: ExtractionResultORM) -> dict:
    return {
        "id": str(r.id),
        "job_id": str(r.job_id),
        "blueprint_id": str(r.blueprint_id) if r.blueprint_id else None,
        "source_filename": r.source_filename,
        "status": r.status,
        "confidence_score": r.confidence_score,
        "data": r.data,
        "extracted_at": r.extracted_at.isoformat() if r.extracted_at else None,
    }


async def _get_owned_result(session, result_id: str, user_id) -> ExtractionResultORM:
    result = await session.get(ExtractionResultORM, uuid.UUID(result_id))
    if not result:
        raise HTTPException(404, "Result not found")
    job = await session.get(JobORM, result.job_id)
    if not job or job.user_id != user_id:
        raise HTTPException(404, "Result not found")
    return result


@router.get("/jobs/{job_id}/results")
@db_retry()
async def list_job_results(job_id: str, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))
        if not job or job.user_id != current_user.id:
            raise HTTPException(404, "Job not found")
        result = await session.execute(
            select(ExtractionResultORM)
            .where(ExtractionResultORM.job_id == uuid.UUID(job_id))
            .order_by(ExtractionResultORM.extracted_at)
        )
        return [_result_dict(r) for r in result.scalars().all()]


@router.get("/extraction-results/{result_id}")
@db_retry()
async def get_result(result_id: str, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        result = await _get_owned_result(session, result_id, current_user.id)
        return _result_dict(result)


@router.post("/extraction-results/{result_id}/correct")
@db_retry()
async def correct_result(result_id: str, body: ResultCorrect, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        result = await _get_owned_result(session, result_id, current_user.id)
        result.data = body.data
        await session.commit()
        await session.refresh(result)
        return _result_dict(result)
