"""Blueprint thinking toggle — budget resolution, migration, and job snapshot."""
import io
import uuid

import pytest
from fastapi import BackgroundTasks, UploadFile
from sqlalchemy import text

import app.api.routes_upload as routes_upload
import app.core.batch_processor as batch_processor
from app.config import settings
from app.core.job_store import job_store
from app.core.thinking import resolve_thinking_budget, thinking_label
from app.database import BlueprintORM, JobORM, LLMCallORM, ProjectORM, _migrate_thinking

_JPEG_BYTES = b"\xff\xd8\xff" + b"0" * 32


# ── Budget resolution ───────────────────────────────────────────────────────

@pytest.mark.parametrize("model, enabled, expected", [
    ("gemini-2.5-flash", True, -1),
    ("gemini-2.5-flash", False, 0),
    ("gemini-2.5-flash-preview-05-20", False, 0),   # versioned name → prefix match
    ("gemini-2.5-flash-lite", False, 0),            # longest prefix wins over 2.5-flash
    ("gemini-2.5-pro", True, -1),
    ("gemini-2.5-pro", False, 128),                 # can't disable → model minimum
    ("gemini-2.0-flash", True, None),               # no thinking support → no thinking_config
    ("gemini-2.0-flash", False, None),
    ("some-unknown-model", True, None),
])
def test_resolve_thinking_budget(model, enabled, expected):
    assert resolve_thinking_budget(model, enabled) == expected


def test_thinking_labels():
    assert thinking_label("gemini-2.5-flash", -1) == "On (dynamic)"
    assert thinking_label("gemini-2.5-flash", 0) == "Off"
    assert thinking_label("gemini-2.5-pro", 128) == "Min (128)"
    assert thinking_label("gemini-2.0-flash", None) == "N/A"


# ── Migration ───────────────────────────────────────────────────────────────

async def test_migration_defaults_and_one_shot_backfill(test_engine, test_session_factory):
    bp_id, job_id = uuid.uuid4(), uuid.uuid4()
    async with test_session_factory() as session:
        session.add(BlueprintORM(id=bp_id, project_id=uuid.uuid4(), name="legacy", thinking=True))
        session.add(JobORM(id=job_id, status="completed"))
        session.add(LLMCallORM(operation="extract", model="gemini-2.5-flash", status="success"))
        session.add(LLMCallORM(operation="extract", model="gemini-2.0-flash", status="success"))
        await session.commit()

    # Simulate a pre-feature schema: drop the new columns (values are lost with them).
    async with test_engine.begin() as conn:
        for table, col in [("blueprints", "thinking"), ("jobs", "thinking"),
                           ("jobs", "thinking_budget"), ("llm_calls", "thinking_budget")]:
            await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))

    async with test_engine.begin() as conn:
        await _migrate_thinking(conn)

    async with test_engine.begin() as conn:
        assert (await conn.execute(text("SELECT thinking FROM blueprints"))).scalar() in (0, False)
        assert tuple((await conn.execute(text("SELECT thinking, thinking_budget FROM jobs"))).one()) == (1, -1)
        budgets = dict((await conn.execute(text("SELECT model, thinking_budget FROM llm_calls"))).all())
        assert budgets == {"gemini-2.5-flash": -1, "gemini-2.0-flash": None}

    # A second startup must not re-backfill rows created after the migration.
    async with test_session_factory() as session:
        session.add(JobORM(id=uuid.uuid4(), status="queued", thinking=False, thinking_budget=0))
        await session.commit()
    async with test_engine.begin() as conn:
        await _migrate_thinking(conn)
        rows = (await conn.execute(text("SELECT thinking_budget FROM jobs ORDER BY thinking_budget"))).scalars().all()
    assert rows == [-1, 0]


async def test_new_blueprint_defaults_to_thinking_off(test_session_factory):
    async with test_session_factory() as session:
        bp = BlueprintORM(id=uuid.uuid4(), project_id=uuid.uuid4(), name="new")
        session.add(bp)
        await session.commit()
        await session.refresh(bp)
    assert bp.thinking is False


# ── Job snapshot ────────────────────────────────────────────────────────────

@pytest.fixture
async def thinking_blueprint(patch_db, fake_user):
    project_id, bp_id = uuid.uuid4(), uuid.uuid4()
    async with patch_db() as session:
        session.add(ProjectORM(id=project_id, user_id=fake_user.id, name="P"))
        session.add(BlueprintORM(id=bp_id, project_id=project_id, name="B", thinking=True))
        await session.commit()
    return project_id, bp_id


async def test_upload_snapshots_thinking_and_blueprint_edits_dont_change_it(
    thinking_blueprint, patch_db, fake_user, tmp_path, monkeypatch,
):
    project_id, bp_id = thinking_blueprint
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(routes_upload, "AsyncSessionLocal", patch_db)

    response = await routes_upload.upload_files(
        background_tasks=BackgroundTasks(),
        files=[UploadFile(file=io.BytesIO(_JPEG_BYTES), filename="a.jpg")],
        project_id=str(project_id), blueprint_id=str(bp_id), current_user=fake_user,
    )
    async with patch_db() as session:
        (await session.get(BlueprintORM, bp_id)).thinking = False  # edit after the job started
        await session.commit()
        job = await session.get(JobORM, uuid.UUID(response.job_id))
    assert (job.thinking, job.thinking_budget) == (True, -1)
    job_store._store.pop(response.job_id, None)


async def test_processor_uses_job_snapshot_not_current_blueprint(
    thinking_blueprint, patch_db, make_job, fake_user, tmp_path, monkeypatch,
):
    project_id, bp_id = thinking_blueprint
    async with patch_db() as session:
        (await session.get(BlueprintORM, bp_id)).thinking = False  # blueprint now says OFF
        await session.commit()
    job_id = str(await make_job(user_id=fake_user.id, project_id=project_id, blueprint_id=bp_id,
                                thinking=True, thinking_budget=-1))

    sent = []

    async def fake_extract(*args, thinking_budget=None, **kwargs):
        sent.append(thinking_budget)
        return {"confidence_score": 0.9}

    async def fake_build_excel(_job_id):
        return tmp_path / "x.xlsx"

    async def fake_upload_excel(*_args):
        return "path.xlsx"

    monkeypatch.setattr(batch_processor, "extract_with_blueprint", fake_extract)
    monkeypatch.setattr(batch_processor, "build_excel", fake_build_excel)
    monkeypatch.setattr(batch_processor, "upload_excel", fake_upload_excel)
    page = tmp_path / "a.jpg"
    page.write_bytes(_JPEG_BYTES)

    job_store.create(job_id, total_files=1)
    try:
        await batch_processor.run_batch_processor(job_id, [page], ["a.jpg"], str(fake_user.id))
    finally:
        job_store._store.pop(job_id, None)
        job_store._queues.pop(job_id, None)
    assert sent == [-1]
