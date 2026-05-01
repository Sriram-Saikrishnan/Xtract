"""
Supabase Storage client for Excel report persistence.

Files are stored in the private bucket 'xtract-excel' under the path:
  {user_id}/{job_id}.xlsx

The service role key bypasses all bucket policies, so the bucket can stay
private — no public URLs are ever generated.
"""
import logging
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BUCKET = "xtract-excel"
EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _object_url(key: str) -> str:
    return f"{settings.SUPABASE_URL}/storage/v1/object/{BUCKET}/{key}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}"}


def storage_key(user_id: str, job_id: str) -> str:
    """Canonical storage path for a job's Excel file."""
    return f"{user_id}/{job_id}.xlsx"


def is_storage_key(excel_path: str) -> bool:
    """Distinguishes a Supabase storage key from a legacy local filesystem path."""
    return not excel_path.startswith("/")


async def upload_excel(user_id: str, job_id: str, local_path: Path) -> str:
    """Upload local Excel file to Supabase Storage. Returns the storage key."""
    key = storage_key(user_id, job_id)
    file_bytes = local_path.read_bytes()

    async with httpx.AsyncClient(timeout=120) as client:
        res = await client.post(
            _object_url(key),
            content=file_bytes,
            headers={
                **_headers(),
                "Content-Type": EXCEL_MIME,
                "x-upsert": "true",
            },
        )

    if res.status_code not in (200, 201):
        raise RuntimeError(f"Storage upload failed [{res.status_code}]: {res.text}")

    logger.info(f"Excel uploaded: {key} ({len(file_bytes):,} bytes)")
    return key


async def download_excel(key: str) -> bytes:
    """Fetch Excel bytes from Supabase Storage."""
    async with httpx.AsyncClient(timeout=120) as client:
        res = await client.get(_object_url(key), headers=_headers())

    if res.status_code != 200:
        raise RuntimeError(f"Storage download failed [{res.status_code}]: {res.text}")

    return res.content


async def delete_excel(key: str) -> None:
    """Remove an Excel file from Supabase Storage (best-effort, errors are logged not raised)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.delete(_object_url(key), headers=_headers())
        if res.status_code not in (200, 204):
            logger.warning(f"Storage delete returned {res.status_code} for key: {key}")
        else:
            logger.info(f"Excel deleted from storage: {key}")
    except Exception as e:
        logger.warning(f"Storage delete failed for {key}: {e}")
