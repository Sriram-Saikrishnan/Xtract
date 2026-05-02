import asyncio
import threading
from typing import Dict, Any, Optional
from datetime import datetime


class JobStore:
    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}
        self._queues: Dict[str, asyncio.Queue] = {}
        self._lock = threading.Lock()

    def create(self, job_id: str, total_files: int):
        with self._lock:
            self._store[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "total_files": total_files,
                "processed_files": 0,
                "verified_count": 0,
                "flagged_count": 0,
                "error_count": 0,
                "created_at": datetime.utcnow(),
                "completed_at": None,
                "excel_ready": False,
            }
            self._queues[job_id] = asyncio.Queue()

    async def push_event(self, job_id: str, event_type: str, data: dict):
        q = self.get_event_queue(job_id)
        if q is not None:
            await q.put({"type": event_type, "data": data})

    def get_event_queue(self, job_id: str) -> Optional[asyncio.Queue]:
        with self._lock:
            return self._queues.get(job_id)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._store.get(job_id)

    def update(self, job_id: str, **kwargs):
        with self._lock:
            if job_id in self._store:
                self._store[job_id].update(kwargs)

    def increment(self, job_id: str, field: str, by: int = 1):
        with self._lock:
            if job_id in self._store:
                self._store[job_id][field] = self._store[job_id].get(field, 0) + by

    def delete(self, job_id: str):
        with self._lock:
            self._store.pop(job_id, None)
            self._queues.pop(job_id, None)

    def exists(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._store


job_store = JobStore()
