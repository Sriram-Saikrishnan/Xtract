# IMPORTANT: env vars must be set before any `app.*` module is imported,
# since app.config.Settings() is instantiated at import time and requires
# these fields. Force-override (not setdefault) so tests never touch real
# secrets/services even if a developer .env happens to be loaded.
import os

os.environ["GEMINI_API_KEY"] = "test-gemini-key"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret"
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_SERVICE_KEY"] = "test-service-key"

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, JobORM, UserORM


@pytest_asyncio.fixture
async def test_engine():
    """
    Shared in-memory SQLite, one connection for the whole test (StaticPool) so
    that every `AsyncSessionLocal()` call in production code — which opens a
    fresh connection each time against the real NullPool/PgBouncer setup —
    still sees the same in-memory database here instead of a blank one.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session_factory(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def patch_db(test_session_factory, monkeypatch):
    """
    Each module did `from app.database import AsyncSessionLocal`, which copies
    the reference at import time — patching app.database.AsyncSessionLocal
    alone would not affect those. Patch each consuming module's own binding.
    """
    import app.api.routes_download as routes_download
    import app.api.routes_jobs as routes_jobs
    import app.api.routes_status as routes_status
    import app.core.batch_processor as batch_processor
    import app.database as database

    for mod in (database, batch_processor, routes_status, routes_jobs, routes_download):
        monkeypatch.setattr(mod, "AsyncSessionLocal", test_session_factory)

    return test_session_factory


@pytest_asyncio.fixture
async def make_job(patch_db):
    """Factory: create a JobORM row (and optionally JobPageORM rows) directly in the test DB."""
    async def _make(status="processing", user_id=None, **fields):
        job_id = uuid.uuid4()
        async with patch_db() as session:
            job = JobORM(
                id=job_id,
                user_id=user_id,
                status=status,
                total_files=fields.pop("total_files", 1),
                created_at=datetime.utcnow(),
                **fields,
            )
            session.add(job)
            await session.commit()
        return job_id
    return _make


@pytest.fixture
def fake_user():
    user = UserORM(id=uuid.uuid4(), email="test@example.com", hashed_password="x")
    return user
