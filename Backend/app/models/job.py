from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class JobCreateResponse(BaseModel):
    job_id: str
    total_files: int
    status: str = "queued"


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    total_files: int
    processed_files: int
    failed_files: int
    total_pages: int = 0
    completed_pages: int = 0
    failed_pages: int = 0
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    excel_ready: bool = False
