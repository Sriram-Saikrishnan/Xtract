import logging
import time
from pathlib import Path
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

from app.config import settings
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _delete_old_files(directory: str, max_age_hours: int):
    dir_path = Path(directory)
    if not dir_path.exists():
        return
    cutoff = time.time() - (max_age_hours * 3600)
    deleted = 0
    for f in dir_path.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except Exception as e:
                logger.warning(f"Could not delete {f}: {e}")
    if deleted:
        logger.info(f"Cleaned {deleted} files from {directory}")


async def cleanup_uploads():
    await _delete_old_files(settings.UPLOAD_DIR, settings.AUTO_DELETE_HOURS)


async def cleanup_outputs():
    await _delete_old_files(settings.OUTPUT_DIR, 24)


async def supabase_keepalive():
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Supabase keepalive ping sent")
    except Exception as e:
        logger.warning(f"Supabase keepalive failed: {e}")


async def cleanup_quota_windows():
    from app.core.quota_manager import quota_manager
    await quota_manager.cleanup_old_windows()


def start_scheduler():
    scheduler.add_job(cleanup_uploads, "interval", minutes=30, id="cleanup_uploads")
    scheduler.add_job(cleanup_outputs, "interval", hours=6, id="cleanup_outputs")
    scheduler.add_job(supabase_keepalive, "interval", days=4, id="supabase_keepalive")
    scheduler.add_job(cleanup_quota_windows, "interval", hours=2, id="cleanup_quota_windows")
    scheduler.start()
    logger.info("File cleaner scheduler started")


def stop_scheduler():
    scheduler.shutdown(wait=False)
