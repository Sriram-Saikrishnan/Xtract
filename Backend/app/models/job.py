from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid


class JobCreateResponse(BaseModel):
    job_id: str
    total_files: int
    status: str = "queued"


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    total_files: int
    processed_files: int
    verified_count: int
    flagged_count: int
    error_count: int
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    excel_ready: bool = False
