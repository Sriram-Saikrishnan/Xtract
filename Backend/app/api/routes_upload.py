import uuid
import logging
from pathlib import Path
from typing import List

import aiofiles
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
_CHUNK_SIZE = 64 * 1024  # 64 KB

_MAGIC_MAP: dict[str, bytes] = {
    "pdf":  b"%PDF",
    "jpg":  b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "png":  b"\x89PNG",
}


def _validate_extension(file: UploadFile):
    ext = Path(file.filename).suffix.lstrip(".").lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File '{file.filename}' has unsupported type '.{ext}'. Allowed: {ALLOWED_EXTENSIONS}"
        )


def _validate_magic(filename: str, ext: str, header: bytes):
    expected = _MAGIC_MAP.get(ext)
    if expected and not header.startswith(expected):
        raise HTTPException(
            status_code=400,
            detail=f"File '{filename}' content does not match its declared type '.{ext}'"
        )


async def _stream_to_disk(file: UploadFile, dest: Path, ext: str) -> int:
    """Stream upload to disk with size and magic byte validation."""
    written = 0
    limit = settings.max_file_size_bytes
    first_chunk = True
    async with aiofiles.open(dest, "wb") as f:
        while True:
            chunk = await file.read(_CHUNK_SIZE)
            if not chunk:
                break
            if first_chunk:
                _validate_magic(file.filename, ext, chunk)
                first_chunk = False
            written += len(chunk)
            if written > limit:
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=400,
                    detail=f"File '{file.filename}' exceeds {settings.MAX_FILE_SIZE_MB}MB limit"
                )
            await f.write(chunk)
    return written


@router.post("/upload", response_model=JobCreateResponse)
async def upload_files(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    current_user: UserORM = Depends(get_current_user),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    for f in files:
        _validate_extension(f)

    job_id = str(uuid.uuid4())

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: List[Path] = []
    filenames: List[str] = []

    for file in files:
        ext = Path(file.filename).suffix.lstrip(".").lower()
        dest = upload_dir / f"{job_id}_{file.filename}"
        await _stream_to_disk(file, dest, ext)
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

    background_tasks.add_task(run_batch_processor, job_id, saved_paths, filenames, str(current_user.id))
    logger.info(f"Job {job_id} queued with {len(files)} files for user {current_user.id}")

    return JobCreateResponse(job_id=job_id, total_files=len(files))
