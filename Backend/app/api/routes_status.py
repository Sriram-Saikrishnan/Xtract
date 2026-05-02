import asyncio
import json
import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt

from app.config import settings
from app.core.job_store import job_store
from app.core.auth import get_current_user
from app.database import AsyncSessionLocal, JobORM, UserORM
from app.models.job import JobStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter()


async def _user_from_token(token: str) -> UserORM:
    exc = HTTPException(status_code=401, detail="Invalid token")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise exc
    except JWTError:
        raise exc
    async with AsyncSessionLocal() as session:
        user = await session.get(UserORM, uuid.UUID(str(user_id)))
        if not user:
            raise exc
        return user


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


@router.get("/stream/{job_id}")
async def stream_job(job_id: str, token: str = Query(...)):
    """SSE endpoint — streams stage_start/stage_progress/stage_complete/processing_complete events."""
    user = await _user_from_token(token)

    sse_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

    # Job already completed — check DB and return an immediate completion event
    live = job_store.get(job_id)
    if live is None:
        try:
            async with AsyncSessionLocal() as session:
                job = await session.get(JobORM, uuid.UUID(job_id))
                if not job or job.user_id != user.id:
                    raise HTTPException(status_code=404, detail="Job not found")
                data = json.dumps({
                    "verified": job.verified_count or 0,
                    "flagged": job.flagged_count or 0,
                    "errors": job.error_count or 0,
                    "duration_ms": 0,
                })

                async def _done():
                    yield f"event: processing_complete\ndata: {data}\n\n"

                return StreamingResponse(_done(), media_type="text/event-stream", headers=sse_headers)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Stream lookup failed for {job_id}: {e}")
            raise HTTPException(status_code=404, detail="Job not found")

    # Job is in-memory but already finished — send completion immediately
    if live.get("status") in ("done", "error"):
        data = json.dumps({
            "verified": live.get("verified_count", 0),
            "flagged": live.get("flagged_count", 0),
            "errors": live.get("error_count", 0),
            "duration_ms": 0,
        })

        async def _done_live():
            yield f"event: processing_complete\ndata: {data}\n\n"

        return StreamingResponse(_done_live(), media_type="text/event-stream", headers=sse_headers)

    q = job_store.get_event_queue(job_id)
    if q is None:
        raise HTTPException(status_code=404, detail="Job stream not available")

    async def _stream():
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=25)
                yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                if event["type"] == "processing_complete":
                    break
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream", headers=sse_headers)
