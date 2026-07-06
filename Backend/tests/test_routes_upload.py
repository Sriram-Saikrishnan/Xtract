"""
Step 4b — MAX_FILES_PER_UPLOAD cap, enforced before any file touches disk.
"""
import io

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile

import app.api.routes_upload as routes_upload
from app.config import settings

_JPEG_BYTES = b"\xff\xd8\xff" + b"0" * 32  # valid JPEG magic bytes for _validate_magic


def _fake_files(n):
    return [
        UploadFile(file=io.BytesIO(_JPEG_BYTES), filename=f"invoice_{i}.jpg")
        for i in range(n)
    ]


async def test_upload_rejects_over_the_cap_before_touching_disk(fake_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    files = _fake_files(settings.MAX_FILES_PER_UPLOAD + 1)  # 151

    with pytest.raises(HTTPException) as exc_info:
        await routes_upload.upload_files(
            background_tasks=BackgroundTasks(), files=files, current_user=fake_user
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        f"Too many files: {settings.MAX_FILES_PER_UPLOAD + 1}. "
        f"Maximum {settings.MAX_FILES_PER_UPLOAD} per upload."
    )
    # Nothing should have been written — the cap check runs before the disk-streaming loop.
    assert list(tmp_path.iterdir()) == []


async def test_upload_accepts_exactly_the_cap(fake_user, tmp_path, monkeypatch, patch_db):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes_upload, "AsyncSessionLocal", patch_db)
    files = _fake_files(settings.MAX_FILES_PER_UPLOAD)  # 150

    response = await routes_upload.upload_files(
        background_tasks=BackgroundTasks(), files=files, current_user=fake_user
    )

    assert response.total_files == settings.MAX_FILES_PER_UPLOAD
    assert len(list(tmp_path.iterdir())) == settings.MAX_FILES_PER_UPLOAD
