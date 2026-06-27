import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.config import settings
from app.core.file_cleaner import start_scheduler, stop_scheduler
from app.core.rate_limiter import limiter
from app.database import init_db, AsyncSessionLocal
from app.api import routes_upload, routes_status, routes_download, routes_review, routes_quota, routes_jobs, routes_auth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    await init_db()
    logger.info("Database tables verified/created")
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="BillScan Pro API",
    description="Extract structured data from Indian manufacturing invoices using Gemini Flash Vision",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_auth.router)
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
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    status_code = 200 if db_ok else 503
    return JSONResponse(status_code=status_code, content={"ok": db_ok, "version": "1.0.0"})
