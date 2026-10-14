import asyncio
import functools
import logging
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime,
    ForeignKey, Text, UniqueConstraint, inspect, text
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import OperationalError, DBAPIError

from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)


def _async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


# NullPool — no SQLAlchemy connection pool.
#
# The DATABASE_URL points to Supabase's PgBouncer at port 6543 (transaction mode).
# PgBouncer transaction mode is ALREADY a connection pool: it assigns a backend
# PostgreSQL connection only for the duration of one transaction, then releases it.
# Idle client sockets are silently dropped by NAT/firewall or PgBouncer itself.
#
# Running SQLAlchemy's persistent pool on top of PgBouncer creates a "pool-of-pools"
# where SQLAlchemy thinks connections are alive but they are dead at the network level.
# pool_pre_ping then waits up to command_timeout (was 60s) per dead connection before
# giving up — causing 2+ minute hangs on the first request after any idle period.
#
# NullPool gives each request a fresh connection to PgBouncer. PgBouncer reuses its
# own backend PostgreSQL connections, so there is no wasted DB work. The overhead is
# one TCP+SSL handshake per request (~100-200ms Render→Mumbai), which is far better
# than 2-minute hangs from stale persistent pool connections.
async_engine = create_async_engine(
    _async_url(settings.DATABASE_URL),
    echo=False,
    poolclass=NullPool,
    connect_args={
        "ssl": "require",
        "timeout": 10,
        "command_timeout": 10,
        # PgBouncer transaction mode does not support prepared statements.
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0,
    },
)

# Exceptions that indicate a transient connection failure worth retrying.
# With NullPool each request gets a fresh connection, so these are rare
# but can still occur on Supabase cold-start or transient network glitches.
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


class UserORM(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    company_name = Column(String(255), nullable=True)
    company_address = Column(Text, nullable=True)
    company_phone = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    jobs = relationship("JobORM", back_populates="owner", cascade="all, delete-orphan")
    projects = relationship("ProjectORM", back_populates="owner", cascade="all, delete-orphan")


class ProjectORM(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    context = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("UserORM", back_populates="projects")
    blueprints = relationship("BlueprintORM", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("JobORM", back_populates="project")


class BlueprintORM(Base):
    __tablename__ = "blueprints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    input_type = Column(String(20), nullable=False, default="pdf")  # pdf | image | mixed
    generation_mode = Column(String(20), nullable=False, default="manual")  # manual | prompt
    extraction_prompt_override = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    # Gemini thinking on/off — see app/core/thinking.py for the per-model budget mapping.
    thinking = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("ProjectORM", back_populates="blueprints")
    fields = relationship(
        "BlueprintFieldORM", back_populates="blueprint",
        cascade="all, delete-orphan", order_by="BlueprintFieldORM.sort_order",
    )
    jobs = relationship("JobORM", back_populates="blueprint")


class BlueprintFieldORM(Base):
    __tablename__ = "blueprint_fields"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blueprint_id = Column(UUID(as_uuid=True), ForeignKey("blueprints.id", ondelete="CASCADE"), nullable=False, index=True)
    field_key = Column(String(100), nullable=False)
    display_label = Column(String(255), nullable=False)
    field_type = Column(String(20), nullable=False)  # string|number|date|currency|boolean|enum|table
    description = Column(Text, nullable=True)
    example_value = Column(Text, nullable=True)
    is_required = Column(Boolean, default=False, nullable=False)
    enum_values = Column(Text, nullable=True)
    table_columns = Column(JSONB, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)

    blueprint = relationship("BlueprintORM", back_populates="fields")


class JobORM(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    blueprint_id = Column(UUID(as_uuid=True), ForeignKey("blueprints.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(50), nullable=False, default="queued")
    total_files = Column(Integer, default=0)
    processed_files = Column(Integer, default=0)
    failed_files = Column(Integer, default=0)
    total_pages = Column(Integer, default=0)
    completed_pages = Column(Integer, default=0)
    failed_pages = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    excel_path = Column(String(512), nullable=True)
    # Snapshot of the blueprint's thinking setting at job start — later blueprint
    # edits never relabel or change past jobs. thinking_budget NULL = model has no thinking.
    thinking = Column(Boolean, nullable=True)
    thinking_budget = Column(Integer, nullable=True)

    owner = relationship("UserORM", back_populates="jobs")
    project = relationship("ProjectORM", back_populates="jobs")
    blueprint = relationship("BlueprintORM", back_populates="jobs")
    pages = relationship("JobPageORM", back_populates="job", cascade="all, delete-orphan")
    results = relationship("ExtractionResultORM", back_populates="job", cascade="all, delete-orphan")


class ExtractionResultORM(Base):
    __tablename__ = "extraction_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    blueprint_id = Column(UUID(as_uuid=True), ForeignKey("blueprints.id", ondelete="SET NULL"), nullable=True, index=True)
    source_filename = Column(String(512))
    status = Column(String(50), default="done")
    confidence_score = Column(Float, default=0.0)
    data = Column(JSONB, nullable=True)
    extracted_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("JobORM", back_populates="results")


class JobPageORM(Base):
    __tablename__ = "job_pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    page_index = Column(Integer, nullable=False)
    filename = Column(String(512), nullable=False)
    page_label = Column(String(512), nullable=False)
    status = Column(String(20), nullable=False, default="queued")  # queued | processing | done | failed
    error_message = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("job_id", "page_index", name="uq_job_pages_job_page"),
    )

    job = relationship("JobORM", back_populates="pages")


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


class LLMCallORM(Base):
    """One row per Gemini API call (every retry attempt included) — the AI
    observability log. FKs are SET NULL so usage history survives deletes."""
    __tablename__ = "llm_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    blueprint_id = Column(UUID(as_uuid=True), ForeignKey("blueprints.id", ondelete="SET NULL"), nullable=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    page_label = Column(String(512), nullable=True)
    operation = Column(String(30), nullable=False)       # extract | generate_fields | test
    model = Column(String(100), nullable=False)
    input_mode = Column(String(30), nullable=True)       # native_pdf | image_fallback | image_direct | text
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    thinking_tokens = Column(Integer, default=0, nullable=False)
    cached_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    latency_ms = Column(Integer, default=0, nullable=False)
    cost_usd = Column(Float, default=0.0, nullable=False)
    status = Column(String(20), nullable=False)          # success | error (API-level outcome)
    error_type = Column(String(50), nullable=True)       # rate_limit | unavailable | timeout | model_not_found | other
    error_message = Column(Text, nullable=True)
    attempt = Column(Integer, default=1, nullable=False)
    thinking_budget = Column(Integer, nullable=True)   # budget sent (or model default); NULL = N/A
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


async def _add_fk_if_missing(conn, constraint_name: str, alter_sql: str) -> None:
    """Postgres has no 'ADD CONSTRAINT IF NOT EXISTS' — guard via pg_constraint lookup."""
    exists = await conn.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
        {"name": constraint_name},
    )
    if exists.scalar() is None:
        await conn.execute(text(alter_sql))


async def _migrate_thinking(conn) -> None:
    """One-shot thinking columns for pre-existing deployments. Each backfill runs
    only in the same startup that adds its column, so it never touches new rows."""
    from app.core.thinking import default_thinking_budget

    def _columns(sync_conn, table):
        return {c["name"] for c in inspect(sync_conn).get_columns(table)}

    if "thinking" not in await conn.run_sync(_columns, "blueprints"):
        # Existing blueprints migrate to OFF, same as the default for new ones.
        await conn.execute(text("ALTER TABLE blueprints ADD COLUMN thinking BOOLEAN NOT NULL DEFAULT FALSE"))

    job_cols = await conn.run_sync(_columns, "jobs")
    if "thinking" not in job_cols:
        await conn.execute(text("ALTER TABLE jobs ADD COLUMN thinking BOOLEAN"))
        if "thinking_budget" not in job_cols:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN thinking_budget INTEGER"))
        # Jobs before this change sent no thinking_config → the model's dynamic default.
        await conn.execute(text("UPDATE jobs SET thinking = TRUE, thinking_budget = -1"))

    if "thinking_budget" not in await conn.run_sync(_columns, "llm_calls"):
        await conn.execute(text("ALTER TABLE llm_calls ADD COLUMN thinking_budget INTEGER"))
        models = (await conn.execute(text("SELECT DISTINCT model FROM llm_calls"))).scalars().all()
        for model in models:
            budget = default_thinking_budget(model)
            if budget is not None:
                await conn.execute(
                    text("UPDATE llm_calls SET thinking_budget = :b WHERE model = :m"), {"b": budget, "m": model}
                )


async def init_db(retries: int = 5, backoff: float = 2.0):
    """Create all tables, then run idempotent column migration for pre-existing deployments."""
    for attempt in range(retries):
        try:
            async with async_engine.begin() as conn:
                # Creates projects/blueprints/blueprint_fields/extraction_results and any
                # other missing tables. Skips tables that already exist (checkfirst=True).
                await conn.run_sync(Base.metadata.create_all)
                await _migrate_thinking(conn)

                # jobs table: drop GST-era counters, add the new project/blueprint linkage.
                await conn.execute(text(
                    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_id UUID"
                ))
                await conn.execute(text(
                    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS project_id UUID"
                ))
                await conn.execute(text(
                    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS blueprint_id UUID"
                ))
                await conn.execute(text(
                    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failed_files INTEGER DEFAULT 0"
                ))
                await conn.execute(text(
                    "ALTER TABLE jobs DROP COLUMN IF EXISTS verified_count"
                ))
                await conn.execute(text(
                    "ALTER TABLE jobs DROP COLUMN IF EXISTS flagged_count"
                ))
                await conn.execute(text(
                    "ALTER TABLE jobs DROP COLUMN IF EXISTS error_count"
                ))

                await _add_fk_if_missing(
                    conn, "jobs_project_id_fkey",
                    "ALTER TABLE jobs ADD CONSTRAINT jobs_project_id_fkey "
                    "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL",
                )
                await _add_fk_if_missing(
                    conn, "jobs_blueprint_id_fkey",
                    "ALTER TABLE jobs ADD CONSTRAINT jobs_blueprint_id_fkey "
                    "FOREIGN KEY (blueprint_id) REFERENCES blueprints(id) ON DELETE SET NULL",
                )

                # extraction_results.blueprint_id: originally created with no ON DELETE
                # action (RESTRICT), which blocks deleting a blueprint that has any
                # extraction history. Drop and recreate with SET NULL to match
                # jobs.blueprint_id's behavior — historical results are kept, just
                # orphaned from the deleted blueprint.
                await conn.execute(text(
                    "ALTER TABLE extraction_results DROP CONSTRAINT IF EXISTS extraction_results_blueprint_id_fkey"
                ))
                await conn.execute(text(
                    "ALTER TABLE extraction_results ADD CONSTRAINT extraction_results_blueprint_id_fkey "
                    "FOREIGN KEY (blueprint_id) REFERENCES blueprints(id) ON DELETE SET NULL"
                ))

                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS company_name VARCHAR(255)"
                ))
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS company_address TEXT"
                ))
                await conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS company_phone VARCHAR(50)"
                ))

                # GST-specific tables — no production data, dropped outright (child first).
                await conn.execute(text("DROP TABLE IF EXISTS line_items CASCADE"))
                await conn.execute(text("DROP TABLE IF EXISTS invoices CASCADE"))
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
