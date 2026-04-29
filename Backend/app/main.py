import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import settings
from app.core.file_cleaner import start_scheduler, stop_scheduler
from app.database import init_db, db_retry
from app.api import routes_upload, routes_status, routes_download, routes_review, routes_quota, routes_jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    await init_db()
    logger.info("Database tables verified/created")
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(
    title="BillScan Pro API",
    description="Extract structured data from Indian manufacturing invoices using Gemini Flash Vision",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_upload.router, tags=["Upload"])
app.include_router(routes_status.router, tags=["Status"])
app.include_router(routes_download.router, tags=["Download"])
app.include_router(routes_review.router, tags=["Review"])
app.include_router(routes_quota.router, tags=["Quota"])
app.include_router(routes_jobs.router)


@app.get("/", include_in_schema=False)
async def serve_frontend():
    frontend = Path(__file__).parent.parent.parent / "Frontend" / "Xtract.html"
    if frontend.exists():
        return FileResponse(frontend, media_type="text/html")
    return {"message": "Frontend not built yet"}


@app.get("/health", tags=["Health"])
async def health():
    return {"ok": True, "version": "1.0.0"}


@app.delete("/job/{job_id}", tags=["Jobs"])
@db_retry()
async def delete_job(job_id: str):
    import uuid
    from pathlib import Path
    from app.database import AsyncSessionLocal, JobORM
    from app.core.job_store import job_store

    excel_path = None
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(JobORM, uuid.UUID(job_id))
            if job:
                excel_path = job.excel_path
                await session.delete(job)
                await session.commit()
    except Exception as e:
        logger.error(f"Delete job {job_id} DB error: {e}")

    job_store.delete(job_id)

    if excel_path:
        try:
            Path(excel_path).unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Could not remove excel file {excel_path}: {e}")

    return {"deleted": job_id}
