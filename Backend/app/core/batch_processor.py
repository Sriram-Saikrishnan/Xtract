"""
Batch extraction pipeline — blueprint-driven, generic across projects.

Per page: build (once per job) a dynamic pydantic model + prompt from the
job's blueprint fields, run Gemini extraction, and write straight to
extraction_results. No verify/tax/duplicate steps, no flags.
"""
import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy import select, update

from app.config import settings
from app.core.gemini_client import (
    build_extraction_model,
    build_extraction_prompt,
    extract_with_blueprint,
    get_mime_type,
    split_pdf_pages,
)
from app.core.job_store import job_store
from app.core.observability import set_llm_context
from app.core.storage import upload_excel
from app.database import (
    AsyncSessionLocal,
    BlueprintFieldORM,
    BlueprintORM,
    ExtractionResultORM,
    JobORM,
    JobPageORM,
    ProjectORM,
)
from app.excel.excel_builder import build_excel

logger = logging.getLogger(__name__)

_MODEL_NOT_FOUND_PREFIX = "Model unavailable:"
# Job IDs for which the model-not-found ERROR has already been logged once —
# every subsequent page in the same job hits the same permanent config error.
_model_not_found_logged_jobs: set[str] = set()


@dataclass
class PageTask:
    """One unit of extraction work — one image, or one page of a split PDF."""
    filename: str
    label: str
    mime: str
    page_bytes: bytes
    page_num: int


@dataclass
class PageResult:
    task: PageTask
    data: Optional[dict]
    error: Optional[str]
    page_row_index: int = 0


async def _load_blueprint_context(blueprint_id: Optional[str]) -> tuple[Optional[type], Optional[str]]:
    """Fetch the blueprint's fields + parent project.context and build the
    dynamic pydantic model and extraction prompt once per job."""
    if not blueprint_id:
        return None, None
    async with AsyncSessionLocal() as session:
        blueprint = await session.get(BlueprintORM, uuid.UUID(blueprint_id))
        if blueprint is None:
            return None, None
        result = await session.execute(
            select(BlueprintFieldORM)
            .where(BlueprintFieldORM.blueprint_id == blueprint.id)
            .order_by(BlueprintFieldORM.sort_order)
        )
        fields = list(result.scalars().all())
        project = await session.get(ProjectORM, blueprint.project_id)

    model_cls = build_extraction_model(str(blueprint.id), fields)
    prompt_text = build_extraction_prompt(project, blueprint, fields)
    return model_cls, prompt_text


async def _flatten_to_page_tasks(file_list: List[tuple]) -> tuple[List[PageTask], List[tuple[str, str]]]:
    """
    Split every uploaded file into its page-level work units up front.
    Returns (page_tasks, failed_files) — failed_files is (filename, error)
    pairs for files that could not be read/split at all.
    """
    page_tasks: List[PageTask] = []
    failed_files: List[tuple[str, str]] = []

    for fp, fn in file_list:
        try:
            file_bytes = fp.read_bytes()
            mime = get_mime_type(fn)

            if mime == "application/pdf":
                page_bytes_list = await asyncio.to_thread(split_pdf_pages, file_bytes)
                logger.info(f"{fn}: {len(page_bytes_list)} page(s)")
                for page_num, page_bytes in enumerate(page_bytes_list, 1):
                    label = f"{Path(fn).stem}_p{page_num}.pdf"
                    page_tasks.append(PageTask(fn, label, mime, page_bytes, page_num))
            else:
                page_tasks.append(PageTask(fn, fn, mime, file_bytes, 1))

        except Exception as e:
            logger.error(f"Error splitting {fn}: {e}")
            failed_files.append((fn, str(e)))

    return page_tasks, failed_files


async def _init_job_pages(
    job_id: str, page_tasks: List[PageTask], failed_files: List[tuple[str, str]]
) -> None:
    async with AsyncSessionLocal() as session:
        for index, task in enumerate(page_tasks):
            session.add(JobPageORM(
                job_id=uuid.UUID(job_id),
                page_index=index,
                filename=task.filename,
                page_label=task.label,
                status="queued",
            ))
        offset = len(page_tasks)
        for i, (filename, error) in enumerate(failed_files):
            session.add(JobPageORM(
                job_id=uuid.UUID(job_id),
                page_index=offset + i,
                filename=filename,
                page_label=filename,
                status="failed",
                error_message=error,
                updated_at=datetime.utcnow(),
            ))
        await session.execute(
            update(JobORM)
            .where(JobORM.id == uuid.UUID(job_id))
            .values(
                total_pages=len(page_tasks) + len(failed_files),
                failed_pages=len(failed_files),
            )
        )
        await session.commit()


async def _set_page_status(
    job_id: str, page_index: int, status: str, error_message: Optional[str] = None
) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(JobPageORM)
            .where(JobPageORM.job_id == uuid.UUID(job_id), JobPageORM.page_index == page_index)
            .values(status=status, error_message=error_message, updated_at=datetime.utcnow())
        )
        await session.commit()


async def _increment_job_counter(job_id: str, column: str, by: int = 1) -> None:
    """Atomic SQL increment — safe under concurrent page completions."""
    async with AsyncSessionLocal() as session:
        col = getattr(JobORM, column)
        await session.execute(
            update(JobORM)
            .where(JobORM.id == uuid.UUID(job_id))
            .values(**{column: col + by})
        )
        await session.commit()


async def _save_result(
    job_id: str, blueprint_id: Optional[str], task: PageTask,
    data: Optional[dict], status: str, error: Optional[str] = None,
) -> None:
    """Always writes a row — success or failure — so extraction_results is a
    complete record of every page (matches job_pages 1:1) rather than only
    ever containing successes indistinguishable from a real empty result."""
    confidence = float(data.get("confidence_score") or 0.0) if data else 0.0
    row_data = data
    if status != "done" and error:
        row_data = {"_error": error}
    async with AsyncSessionLocal() as session:
        session.add(ExtractionResultORM(
            id=uuid.uuid4(),
            job_id=uuid.UUID(job_id),
            blueprint_id=uuid.UUID(blueprint_id) if blueprint_id else None,
            source_filename=task.filename,
            status=status,
            confidence_score=confidence,
            data=row_data,
            extracted_at=datetime.utcnow(),
        ))
        await session.commit()


async def _extract_one_page(
    task: PageTask, job_id: str, blueprint_id: Optional[str], model_cls: type, prompt_text: str,
    sem: asyncio.Semaphore, total_pages: int, index: int, db_index: Optional[int] = None,
    thinking_budget: Optional[int] = None,
) -> PageResult:
    """Bounded by the extraction semaphore. Never lets an exception escape —
    failures are captured into the PageResult so one bad page cannot cancel
    its siblings in asyncio.gather."""
    page_row_index = db_index if db_index is not None else index
    async with sem:
        # Runs inside its own gather task, so this only tags this page's calls.
        set_llm_context(page_label=task.label)
        await _set_page_status(job_id, page_row_index, "processing")
        try:
            data = await extract_with_blueprint(
                task.page_bytes, task.label, task.mime, model_cls, prompt_text, thinking_budget=thinking_budget,
            )
            if data is None:
                logger.warning(f"{task.label}: extraction returned None")
                await _save_result(job_id, blueprint_id, task, None, status="failed", error="extraction returned None")
                await _set_page_status(job_id, page_row_index, "failed", error_message="extraction returned None")
                await _increment_job_counter(job_id, "failed_pages")
                return PageResult(task, data=None, error="extraction returned None", page_row_index=page_row_index)

            await _save_result(job_id, blueprint_id, task, data, status="done")
            logger.info(f"{task.label}: extracted, confidence={data.get('confidence_score')}")
            await _set_page_status(job_id, page_row_index, "done")
            await _increment_job_counter(job_id, "completed_pages")
            return PageResult(task, data=data, error=None, page_row_index=page_row_index)
        except Exception as e:
            err_str = str(e)
            if err_str.startswith(_MODEL_NOT_FOUND_PREFIX):
                if job_id not in _model_not_found_logged_jobs:
                    _model_not_found_logged_jobs.add(job_id)
                    logger.error(f"Job {job_id}: {err_str}")
            else:
                logger.error(f"{task.label}: extraction failed — {e}")
            await _save_result(job_id, blueprint_id, task, None, status="failed", error=err_str)
            await _set_page_status(job_id, page_row_index, "failed", error_message=err_str)
            await _increment_job_counter(job_id, "failed_pages")
            return PageResult(task, data=None, error=err_str, page_row_index=page_row_index)
        finally:
            job_store.increment(job_id, "processed_files")
            await job_store.push_event(job_id, "stage_progress", {
                "stage": "extraction",
                "detail": f"Extracted {task.label}",
                "current": index + 1,
                "total": total_pages,
            })


async def _finalize_job(job_id: str, user_id: str, total_pages: int, total_done: int) -> None:
    t0 = time.monotonic()
    await job_store.push_event(job_id, "stage_start", {"stage": "excel", "total": 1})
    page_failures = total_pages - total_done
    terminal_status = "completed_with_errors" if page_failures > 0 else "done"
    excel_path = None

    try:
        await job_store.push_event(job_id, "stage_progress", {
            "stage": "excel", "detail": "Building Excel report...", "current": 1, "total": 1,
        })
        local_excel = await build_excel(job_id)
        await job_store.push_event(job_id, "stage_progress", {
            "stage": "excel", "detail": "Uploading to storage...", "current": 1, "total": 1,
        })
        excel_path = await upload_excel(user_id, job_id, local_excel)
        try:
            local_excel.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Job {job_id}: Excel build/upload failed: {e}")
        terminal_status = "completed_with_errors"

    async with AsyncSessionLocal() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))
        if job:
            job.status = terminal_status
            job.completed_at = datetime.utcnow()
            job.excel_path = excel_path
            await session.commit()

    job_store.update(job_id, status=terminal_status, completed_at=datetime.utcnow(), excel_ready=excel_path is not None)

    await job_store.push_event(job_id, "stage_complete", {
        "stage": "excel", "duration_ms": int((time.monotonic() - t0) * 1000),
    })
    await job_store.push_event(job_id, "processing_complete", {
        "completed_pages": total_done,
        "failed_pages": page_failures,
        "total_pages": total_pages,
    })
    logger.info(f"Job {job_id}: completed, status={terminal_status}")


async def run_batch_processor(job_id: str, file_paths: List[Path], filenames: List[str], user_id: str = ""):
    job_store.update(job_id, status="processing")
    file_list = list(zip(file_paths, filenames))
    job_start = time.monotonic()

    async with AsyncSessionLocal() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))
        blueprint_id = str(job.blueprint_id) if job and job.blueprint_id else None
        project_id = str(job.project_id) if job and job.project_id else None
        owner_id = str(job.user_id) if job and job.user_id else user_id
        # Job snapshot from upload time — never re-read from the blueprint.
        thinking_budget = job.thinking_budget if job else None

    set_llm_context(operation="extract", user_id=owner_id, project_id=project_id, blueprint_id=blueprint_id, job_id=job_id)
    model_cls, prompt_text = await _load_blueprint_context(blueprint_id)
    if model_cls is None:
        logger.error(f"Job {job_id}: no blueprint set — cannot extract")
        async with AsyncSessionLocal() as session:
            job = await session.get(JobORM, uuid.UUID(job_id))
            if job:
                job.status = "error"
                job.completed_at = datetime.utcnow()
                await session.commit()
        job_store.update(job_id, status="error", completed_at=datetime.utcnow())
        await job_store.push_event(job_id, "processing_complete", {"error": "no blueprint set on job"})
        return

    t0 = time.monotonic()
    page_tasks, failed_files = await _flatten_to_page_tasks(file_list)
    total_pages = len(page_tasks) + len(failed_files)
    if failed_files:
        job_store.increment(job_id, "failed_files", by=len(failed_files))
        for filename, error in failed_files:
            await _save_result(
                job_id, blueprint_id, PageTask(filename, filename, "", b"", 1),
                None, status="failed", error=error,
            )

    await _init_job_pages(job_id, page_tasks, failed_files)
    await job_store.push_event(job_id, "stage_start", {"stage": "extraction", "total": total_pages})

    extraction_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_EXTRACTIONS)
    extraction_results = await asyncio.gather(
        *(
            _extract_one_page(
                task, job_id, blueprint_id, model_cls, prompt_text, extraction_sem, total_pages, i,
                thinking_budget=thinking_budget,
            )
            for i, task in enumerate(page_tasks)
        ),
        return_exceptions=True,
    )

    total_done = 0
    for page_index, result in enumerate(extraction_results):
        if isinstance(result, BaseException):
            logger.error(f"Unexpected exception escaped extraction task: {result}")
            await _set_page_status(job_id, page_index, "failed", error_message=str(result))
            await _increment_job_counter(job_id, "failed_pages")
            continue
        if result.data is not None:
            total_done += 1

    await job_store.push_event(job_id, "stage_complete", {
        "stage": "extraction", "duration_ms": int((time.monotonic() - t0) * 1000),
    })

    await _finalize_job(job_id, user_id, total_pages, total_done)


async def run_retry_processor(
    job_id: str,
    failed_pages: List[dict],  # [{"page_index": int, "filename": str, "page_label": str}]
    user_id: str,
) -> None:
    """
    Re-extract only the previously failed pages for a job.
    Never touches pages with status=done. Idempotent: the endpoint already
    reset the target pages to 'queued' before firing this task.
    """
    retry_total = len(failed_pages)
    job_store.update(job_id, status="processing")
    await job_store.push_event(job_id, "stage_start", {"stage": "extraction", "total": retry_total})

    async with AsyncSessionLocal() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))
        blueprint_id = str(job.blueprint_id) if job and job.blueprint_id else None
        total_pages_on_job = job.total_pages if job else retry_total
        project_id = str(job.project_id) if job and job.project_id else None
        owner_id = str(job.user_id) if job and job.user_id else user_id
        # Job snapshot from upload time — never re-read from the blueprint.
        thinking_budget = job.thinking_budget if job else None

    set_llm_context(operation="extract", user_id=owner_id, project_id=project_id, blueprint_id=blueprint_id, job_id=job_id)
    model_cls, prompt_text = await _load_blueprint_context(blueprint_id)
    if model_cls is None:
        logger.error(f"Job {job_id}: no blueprint set — cannot retry")
        await job_store.push_event(job_id, "processing_complete", {"error": "no blueprint set on job"})
        return

    page_tasks_with_db_index: List[tuple] = []
    for page in failed_pages:
        db_page_index: int = page["page_index"]
        filename: str = page["filename"]
        page_label: str = page["page_label"]

        file_path = Path(settings.UPLOAD_DIR) / f"{job_id}_{filename}"
        if not file_path.exists():
            msg = f"Source file not found on disk: {file_path}"
            logger.error(f"[{page_label}] {msg}")
            await _set_page_status(job_id, db_page_index, "failed", error_message=msg)
            await _increment_job_counter(job_id, "failed_pages")
            continue

        mime = get_mime_type(filename)

        if mime == "application/pdf":
            m = re.search(r"_p(\d+)\.pdf$", page_label)
            page_num = int(m.group(1)) if m else 1
            try:
                file_bytes = file_path.read_bytes()
                page_bytes_list = await asyncio.to_thread(split_pdf_pages, file_bytes)
                if page_num - 1 >= len(page_bytes_list):
                    msg = f"Page {page_num} out of range (PDF has {len(page_bytes_list)} pages)"
                    logger.error(f"[{page_label}] {msg}")
                    await _set_page_status(job_id, db_page_index, "failed", error_message=msg)
                    await _increment_job_counter(job_id, "failed_pages")
                    continue
                page_bytes = page_bytes_list[page_num - 1]
            except Exception as e:
                logger.error(f"[{page_label}] Failed to read/split PDF: {e}")
                await _set_page_status(job_id, db_page_index, "failed", error_message=str(e))
                await _increment_job_counter(job_id, "failed_pages")
                continue
        else:
            try:
                page_bytes = file_path.read_bytes()
            except Exception as e:
                logger.error(f"[{page_label}] Failed to read file: {e}")
                await _set_page_status(job_id, db_page_index, "failed", error_message=str(e))
                await _increment_job_counter(job_id, "failed_pages")
                continue

        page_tasks_with_db_index.append((PageTask(filename, page_label, mime, page_bytes, 1), db_page_index))

    extraction_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_EXTRACTIONS)
    extraction_results = await asyncio.gather(
        *(
            _extract_one_page(
                task, job_id, blueprint_id, model_cls, prompt_text, extraction_sem, retry_total, retry_idx,
                db_index=db_idx, thinking_budget=thinking_budget,
            )
            for retry_idx, (task, db_idx) in enumerate(page_tasks_with_db_index)
        ),
        return_exceptions=True,
    )

    for result in extraction_results:
        if isinstance(result, BaseException):
            logger.error(f"Unexpected exception escaped retry extraction: {result}")
            await _increment_job_counter(job_id, "failed_pages")

    await job_store.push_event(job_id, "stage_complete", {"stage": "extraction"})

    # Read back accumulated counts — this retry only re-ran a subset of the
    # job's pages, so completion totals must reflect the whole job, not just
    # this batch.
    async with AsyncSessionLocal() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))
        completed_pages = job.completed_pages if job else 0
        total_pages_final = job.total_pages if job else total_pages_on_job

    await _finalize_job(job_id, user_id, total_pages_final, completed_pages)
