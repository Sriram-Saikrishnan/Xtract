import asyncio
import functools
import logging
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, DateTime,
    ForeignKey, Text, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import OperationalError, DBAPIError

from app.config import settings

logger = logging.getLogger(__name__)


def _async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


# Persistent pool instead of NullPool.
#
# NullPool creates a new TCP+SSL connection per request. On Render→Supabase Mumbai,
# that's ~300-500ms per request plus uvloop's Happy Eyeballs algorithm firing
# CancelledError during SSL setup (visible as TimeoutError in the traceback).
#
# A small pool establishes connections ONCE at startup and reuses them:
#   pool_size=2       — keep 2 live connections
#   max_overflow=3    — allow up to 5 total under burst load
#   pool_pre_ping     — verify connection health before each use; reconnect if stale
#   pool_recycle=280  — recycle before Supabase's ~300s server-side idle timeout
async_engine = create_async_engine(
    _async_url(settings.DATABASE_URL),
    echo=False,
    pool_size=2,
    max_overflow=3,
    pool_timeout=30,
    pool_recycle=280,
    pool_pre_ping=True,
    connect_args={
        "ssl": "require",
        "timeout": 60,
        "command_timeout": 60,
        # PgBouncer transaction mode (port 6543) does not support prepared statements.
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0,
    },
)

# Exceptions that indicate a transient connection failure worth retrying.
# OperationalError / DBAPIError cover SQLAlchemy-wrapped pool-checkout failures
# (e.g. when pool_pre_ping triggers a reconnect that also fails transiently).
_TRANSIENT_EXC = (asyncio.TimeoutError, TimeoutError, OSError, OperationalError, DBAPIError)


def db_retry(retries: int = 3, backoff: float = 1.0):
    """Retry a route handler on transient DB connection errors."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc: BaseException = RuntimeError("unreachable")
            for attempt in range(retries):
                try:
                    return await fn(*args, **kwargs)
                except _TRANSIENT_EXC as exc:
                    last_exc = exc
                    if attempt < retries - 1:
                        wait = backoff * (2 ** attempt)
                        logger.warning(
                            "DB transient error on attempt %d/%d, retrying in %.1fs: %s",
                            attempt + 1, retries, wait, exc,
                        )
                        await asyncio.sleep(wait)
            raise last_exc
        return wrapper
    return decorator

AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

Base = declarative_base()


class JobORM(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status = Column(String(50), nullable=False, default="queued")
    total_files = Column(Integer, default=0)
    processed_files = Column(Integer, default=0)
    verified_count = Column(Integer, default=0)
    flagged_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    excel_path = Column(String(512), nullable=True)

    invoices = relationship("InvoiceORM", back_populates="job", cascade="all, delete-orphan")


class InvoiceORM(Base):
    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False)
    source_filename = Column(String(512))
    category = Column(String(100))
    invoice_number = Column(String(200))
    invoice_date = Column(String(20))
    challan_number = Column(String(200))
    document_type = Column(String(100))
    supplier_name = Column(String(500))
    supplier_gstin = Column(String(20))
    supplier_state = Column(String(100))
    supplier_address = Column(Text)
    supplier_email = Column(String(200))
    supplier_phone = Column(String(50))
    supplier_bank = Column(String(200))
    supplier_account_number = Column(String(100))
    supplier_ifsc = Column(String(20))
    buyer_name = Column(String(500))
    buyer_gstin = Column(String(20))
    place_of_supply = Column(String(100))
    destination = Column(String(200))
    transport_name = Column(String(200))
    lr_number = Column(String(100))
    vehicle_number = Column(String(50))
    eway_bill_number = Column(String(100))
    irn_number = Column(String(200))
    assessable_value = Column(Float, default=0.0)
    tax_type = Column(String(20))
    igst_percent = Column(Float, default=0.0)
    igst_amount = Column(Float, default=0.0)
    cgst_percent = Column(Float, default=0.0)
    cgst_amount = Column(Float, default=0.0)
    sgst_percent = Column(Float, default=0.0)
    sgst_amount = Column(Float, default=0.0)
    pf_charges = Column(Float, default=0.0)
    round_off = Column(Float, default=0.0)
    grand_total = Column(Float, default=0.0)
    total_weight_kg = Column(Float, default=0.0)
    total_qty = Column(Float, default=0.0)
    confidence_score = Column(Float, default=0.0)
    status = Column(String(50), default="VERIFIED")
    flags = Column(Text, default="")
    extracted_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("JobORM", back_populates="invoices")
    line_items = relationship("LineItemORM", back_populates="invoice", cascade="all, delete-orphan")


class LineItemORM(Base):
    __tablename__ = "line_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False)
    sr_no = Column(Integer)
    die_number = Column(String(100))
    po_number = Column(String(100))
    description = Column(Text)
    hsn_sac_code = Column(String(50))
    grade = Column(String(100))
    quantity = Column(Float, default=0.0)
    rate = Column(Float, default=0.0)
    amount = Column(Float, default=0.0)

    invoice = relationship("InvoiceORM", back_populates="line_items")


class GeminiQuotaORM(Base):
    __tablename__ = "gemini_quota"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model = Column(String(100), nullable=False)
    window_type = Column(String(20), nullable=False)   # 'daily' | 'minute'
    window_key = Column(String(50), nullable=False)    # '2026-04-27' | '2026-04-27 10:30'
    request_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("model", "window_type", "window_key", name="uq_gemini_quota_window"),
    )


async def init_db(retries: int = 5, backoff: float = 2.0):
    """Create tables on startup. Retries so a slow Render cold-start doesn't abort launch."""
    for attempt in range(retries):
        try:
            async with async_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = backoff * (2 ** attempt)
            logger.warning("init_db attempt %d/%d failed, retrying in %.0fs: %s", attempt + 1, retries, wait, exc)
            await asyncio.sleep(wait)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
