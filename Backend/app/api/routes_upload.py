import uuid
import logging
from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File, Depends

from app.config import settings
from app.core.batch_processor import run_batch_processor
from app.core.job_store import job_store
from app.core.auth import get_current_user
from app.database import AsyncSessionLocal, JobORM, UserORM
from app.models.job import JobCreateResponse

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_EXTENSIONS = set(settings.ALLOWED_EXTENSIONS)


def _validate_file(file: UploadFile):
    ext = Path(file.filename).suffix.lstrip(".").lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File '{file.filename}' has unsupported type '.{ext}'. Allowed: {ALLOWED_EXTENSIONS}"
        )


@router.post("/upload", response_model=JobCreateResponse)
async def upload_files(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    current_user: UserORM = Depends(get_current_user),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    for f in files:
        _validate_file(f)

    job_id = str(uuid.uuid4())

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: List[Path] = []
    filenames: List[str] = []

    for file in files:
        content = await file.read()
        if len(content) > settings.max_file_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"File '{file.filename}' exceeds {settings.MAX_FILE_SIZE_MB}MB limit"
            )
        dest = upload_dir / f"{job_id}_{file.filename}"
        dest.write_bytes(content)
        saved_paths.append(dest)
        filenames.append(file.filename)

    async with AsyncSessionLocal() as session:
        job = JobORM(
            id=uuid.UUID(job_id),
            status="queued",
            total_files=len(files),
            user_id=current_user.id,
        )
        session.add(job)
        await session.commit()

    job_store.create(job_id, total_files=len(files))

    background_tasks.add_task(run_batch_processor, job_id, saved_paths, filenames)
    logger.info(f"Job {job_id} queued with {len(files)} files for user {current_user.id}")

    return JobCreateResponse(job_id=job_id, total_files=len(files))
