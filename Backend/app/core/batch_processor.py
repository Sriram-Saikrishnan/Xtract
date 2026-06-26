import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.duplicate import check_duplicate
from app.core.extractor import normalize
from app.core.gemini_client import extract_bill, get_mime_type, split_pdf_pages
from app.core.tax_validator import validate_tax
from app.core.job_store import job_store
from app.core.verifier import verify
from app.core.storage import upload_excel
from app.database import AsyncSessionLocal, InvoiceORM, JobORM, JobPageORM, LineItemORM
from app.excel.excel_builder import build_excel
from app.models.bill import BillStatus, ExtractedBill

logger = logging.getLogger(__name__)


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
    bill: Optional[ExtractedBill]
    error: Optional[str]


async def _flatten_to_page_tasks(file_list: List[tuple]) -> tuple[List[PageTask], List[tuple[str, str]]]:
    """
    Split every uploaded file into its page-level work units up front.
    Sequential and fast (CPU-bound PDF split, no Gemini calls) — a corrupt
    file only costs that one file, the rest of the batch still flattens.
    Returns (page_tasks, failed_files) — failed_files is (filename, error) pairs
    for files that could not be read/split at all, so the caller can still give
    each one a terminal job_pages row instead of dropping it silently.
    """
    page_tasks: List[PageTask] = []
    failed_files: List[tuple[str, str]] = []

    for fp, fn in file_list:
        try:
            file_bytes = fp.read_bytes()
            mime = get_mime_type(fn)

            if mime == "application/pdf":
                page_bytes_list = await asyncio.to_thread(split_pdf_pages, file_bytes)
                logger.info(f"{fn}: {len(page_bytes_list)} page(s) — each processed as a separate invoice")
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
    """
    Insert one job_pages row per page (status=queued), plus one row per file
    that failed to read/split (status=failed) — every uploaded file lands in
    exactly one row up front, so none can be silently dropped from the count.
    Sets jobs.total_pages to the full page+failure count and pre-seeds
    jobs.failed_pages so split failures are reflected immediately.
    """
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
    """Update a single job_pages row. updated_at is always set explicitly — never relies on DB default."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(JobPageORM)
            .where(JobPageORM.job_id == uuid.UUID(job_id), JobPageORM.page_index == page_index)
            .values(status=status, error_message=error_message, updated_at=datetime.utcnow())
        )
        await session.commit()


async def _increment_job_counter(job_id: str, column: str, by: int = 1) -> None:
    """
    Atomic SQL increment (SET col = col + :by) — never read-then-write.
    Safe under concurrent page completions racing on the same job row.
    """
    async with AsyncSessionLocal() as session:
        col = getattr(JobORM, column)
        await session.execute(
            update(JobORM)
            .where(JobORM.id == uuid.UUID(job_id))
            .values(**{column: col + by})
        )
        await session.commit()


async def _extract_one_page(
    task: PageTask, job_id: str, sem: asyncio.Semaphore, total_pages: int, index: int,
    db_index: Optional[int] = None,
) -> PageResult:
    """
    Bounded by the extraction semaphore. Never lets an exception escape —
    failures are captured into the PageResult so one bad page cannot cancel
    its siblings in asyncio.gather.
    db_index: row index in job_pages (defaults to index). Pass explicitly for
    retry runs where SSE progress index differs from the original page_index.
    """
    page_row_index = db_index if db_index is not None else index
    async with sem:
        await _set_page_status(job_id, page_row_index, "processing")
        try:
            raw = await extract_bill(task.page_bytes, task.label, task.mime)
            if raw is None:
                logger.warning(f"{task.label}: extraction returned None")
                await _set_page_status(job_id, page_row_index, "failed", error_message="extraction returned None")
                await _increment_job_counter(job_id, "failed_pages")
                return PageResult(task, bill=None, error="extraction returned None")
            bill = normalize(raw, task.filename)
            logger.info(f"{task.label}: {len(bill.line_items)} item(s) extracted")
            await _set_page_status(job_id, page_row_index, "done")
            await _increment_job_counter(job_id, "completed_pages")
            return PageResult(task, bill=bill, error=None)
        except Exception as e:
            logger.error(f"{task.label}: extraction failed — {e}")
            await _set_page_status(job_id, page_row_index, "failed", error_message=str(e))
            await _increment_job_counter(job_id, "failed_pages")
            return PageResult(task, bill=None, error=str(e))
        finally:
            job_store.increment(job_id, "processed_files")
            await job_store.push_event(job_id, "stage_progress", {
                "stage": "extraction",
                "detail": f"Extracted {task.label}",
                "current": index + 1,
                "total": total_pages,
            })


async def _save_bill(session: AsyncSession, job_id: str, bill: ExtractedBill):
    invoice_id = uuid.uuid4()
    flags_str = "; ".join(bill.flags)

    invoice = InvoiceORM(
        id=invoice_id,
        job_id=uuid.UUID(job_id),
        source_filename=bill.source_filename,
        category=bill.category,
        invoice_number=bill.invoice_number,
        invoice_date=bill.invoice_date,
        challan_number=bill.challan_number,
        document_type=bill.document_type,
        supplier_name=bill.supplier_name,
        supplier_gstin=bill.supplier_gstin,
        supplier_state=bill.supplier_state,
        supplier_address=bill.supplier_address,
        supplier_email=bill.supplier_email,
        supplier_phone=bill.supplier_phone,
        supplier_bank=bill.supplier_bank,
        supplier_account_number=bill.supplier_account_number,
        supplier_ifsc=bill.supplier_ifsc,
        buyer_name=bill.buyer_name,
        buyer_gstin=bill.buyer_gstin,
        place_of_supply=bill.place_of_supply,
        destination=bill.destination,
        transport_name=bill.transport_name,
        lr_number=bill.lr_number,
        vehicle_number=bill.vehicle_number,
        eway_bill_number=bill.eway_bill_number,
        irn_number=bill.irn_number,
        assessable_value=bill.assessable_value,
        tax_type=bill.tax_type,
        igst_percent=bill.igst_percent,
        igst_amount=bill.igst_amount,
        cgst_percent=bill.cgst_percent,
        cgst_amount=bill.cgst_amount,
        sgst_percent=bill.sgst_percent,
        sgst_amount=bill.sgst_amount,
        pf_charges=bill.pf_charges,
        round_off=bill.round_off,
        grand_total=bill.grand_total,
        total_weight_kg=bill.total_weight_kg,
        total_qty=bill.total_qty,
        confidence_score=bill.confidence_score,
        status=bill.status.value,
        flags=flags_str,
        extracted_at=datetime.utcnow(),
    )
    session.add(invoice)

    for item in bill.line_items:
        li = LineItemORM(
            id=uuid.uuid4(),
            invoice_id=invoice_id,
            sr_no=item.sr_no,
            die_number=item.die_number,
            po_number=item.po_number,
            description=item.description,
            hsn_sac_code=item.hsn_sac_code,
            grade=item.grade,
            quantity=item.quantity,
            rate=item.rate,
            amount=item.amount,
        )
        session.add(li)

    await session.commit()
    return invoice_id


async def run_batch_processor(job_id: str, file_paths: List[Path], filenames: List[str], user_id: str = ""):
    job_store.update(job_id, status="processing")
    file_list = list(zip(file_paths, filenames))
    job_start = time.monotonic()

    # ── Stage 1: Extraction (flatten to pages, then bounded concurrent extraction) ─
    t0 = time.monotonic()

    page_tasks, failed_files = await _flatten_to_page_tasks(file_list)
    total_pages = len(page_tasks) + len(failed_files)
    total_errors = len(failed_files)
    if failed_files:
        job_store.increment(job_id, "error_count", by=len(failed_files))

    await _init_job_pages(job_id, page_tasks, failed_files)
    await job_store.push_event(job_id, "stage_start", {"stage": "extraction", "total": total_pages})

    extraction_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_EXTRACTIONS)
    extraction_results = await asyncio.gather(
        *(
            _extract_one_page(task, job_id, extraction_sem, total_pages, i)
            for i, task in enumerate(page_tasks)
        ),
        return_exceptions=True,  # defensive: _extract_one_page already catches internally
    )

    all_bills: List[ExtractedBill] = []
    for page_index, result in enumerate(extraction_results):
        if isinstance(result, BaseException):
            # Should never happen — _extract_one_page catches everything itself.
            # Guard anyway so a freak escape is counted, not silently dropped,
            # and the job_pages row doesn't get stuck at "processing" forever.
            logger.error(f"Unexpected exception escaped extraction task: {result}")
            total_errors += 1
            job_store.increment(job_id, "error_count")
            await _set_page_status(job_id, page_index, "failed", error_message=str(result))
            await _increment_job_counter(job_id, "failed_pages")
            continue
        if result.bill is not None:
            all_bills.append(result.bill)
        else:
            total_errors += 1
            job_store.increment(job_id, "error_count")

    await job_store.push_event(job_id, "stage_complete", {
        "stage": "extraction",
        "duration_ms": int((time.monotonic() - t0) * 1000),
    })

    total_bills = len(all_bills)

    # ── Stage 3: Compliance Checks (sequential — duplicate detection is order-dependent)
    # + bounded concurrent DB saves (parallelized I/O only, never the compliance logic) ─
    t0 = time.monotonic()
    await job_store.push_event(job_id, "stage_start", {"stage": "compliance", "total": total_bills})

    db_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_DB_WRITES)
    save_tasks: List[asyncio.Task] = []
    processed_bills: List[ExtractedBill] = []
    total_verified = 0
    total_flagged = 0

    async def _save_one(bill_to_save: ExtractedBill) -> None:
        async with db_sem:
            try:
                async with AsyncSessionLocal() as session:
                    await _save_bill(session, job_id, bill_to_save)
            except Exception as e:
                logger.error(f"Save failed for {bill_to_save.source_filename}: {e}")
                job_store.increment(job_id, "error_count")

    for i, bill in enumerate(all_bills):
        label = bill.invoice_number or bill.source_filename
        await job_store.push_event(job_id, "stage_progress", {
            "stage": "compliance",
            "detail": f"Checking {label}...",
            "current": i + 1,
            "total": total_bills,
        })
        # Sequential on purpose: check_duplicate compares against processed_bills
        # accumulated so far — parallelizing this loop would let two near-duplicate
        # bills both pass the check before either is recorded.
        bill = verify(bill)
        tax_flags = [f["code"] for f in validate_tax(bill)]
        if tax_flags:
            bill = bill.model_copy(update={"flags": list(bill.flags) + tax_flags})
        bill = check_duplicate(bill, processed_bills)
        processed_bills.append(bill)

        if bill.status == BillStatus.VERIFIED:
            total_verified += 1
            job_store.increment(job_id, "verified_count")
        else:
            total_flagged += 1
            job_store.increment(job_id, "flagged_count")

        # Only the DB write is dispatched concurrently (bounded by db_sem) —
        # compliance logic above stays fully sequential.
        save_tasks.append(asyncio.create_task(_save_one(bill)))

    # Explicit ordering guard: all saves MUST complete before Excel generation
    # below reads invoices/line_items back out of the DB.
    await asyncio.gather(*save_tasks, return_exceptions=True)

    await job_store.push_event(job_id, "stage_complete", {
        "stage": "compliance",
        "duration_ms": int((time.monotonic() - t0) * 1000),
    })

    # ── Stage 4: Excel Report ─────────────────────────────────────────────────
    t0 = time.monotonic()
    await job_store.push_event(job_id, "stage_start", {"stage": "excel", "total": 1})

    try:
        await job_store.push_event(job_id, "stage_progress", {
            "stage": "excel",
            "detail": "Building Excel report...",
            "current": 1,
            "total": 1,
        })
        local_excel = await build_excel(job_id)

        await job_store.push_event(job_id, "stage_progress", {
            "stage": "excel",
            "detail": "Uploading to storage...",
            "current": 1,
            "total": 1,
        })
        storage_path = await upload_excel(user_id, job_id, local_excel)
        try:
            local_excel.unlink(missing_ok=True)
        except Exception:
            pass

        page_failures = total_pages - len(all_bills)
        terminal_status = "completed_with_errors" if page_failures > 0 else "done"

        async with AsyncSessionLocal() as session:
            result = await session.get(JobORM, uuid.UUID(job_id))
            if result:
                result.status = terminal_status
                result.completed_at = datetime.utcnow()
                result.excel_path = storage_path
                result.verified_count = total_verified
                result.flagged_count = total_flagged
                result.error_count = total_errors
                await session.commit()

        job_store.update(
            job_id,
            status=terminal_status,
            completed_at=datetime.utcnow(),
            excel_ready=True,
            verified_count=total_verified,
            flagged_count=total_flagged,
            error_count=total_errors,
            failed_pages=page_failures,
            total_pages=total_pages,
        )

        await job_store.push_event(job_id, "stage_complete", {
            "stage": "excel",
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
        await job_store.push_event(job_id, "processing_complete", {
            "verified": total_verified,
            "flagged": total_flagged,
            "errors": total_errors,
            "failed_pages": page_failures,
            "total_pages": total_pages,
            "duration_ms": int((time.monotonic() - job_start) * 1000),
        })
        logger.info(f"Job {job_id}: completed. Excel at storage:{storage_path}")

    except Exception as e:
        logger.error(f"Job {job_id}: Excel build/upload failed: {e}")
        job_store.update(job_id, status="error")
        await job_store.push_event(job_id, "processing_complete", {
            "verified": total_verified,
            "flagged": total_flagged,
            "errors": total_errors + 1,
            "duration_ms": int((time.monotonic() - job_start) * 1000),
            "error": str(e),
        })


async def run_retry_processor(
    job_id: str,
    failed_pages: List[dict],  # [{"page_index": int, "filename": str, "page_label": str}]
    user_id: str,
) -> None:
    """
    Re-extract only the previously failed pages for a job.
    Never touches pages with status=done. Idempotent: the endpoint
    already reset the target pages to 'queued' before firing this task.
    """
    retry_total = len(failed_pages)
    job_start = time.monotonic()

    job_store.update(job_id, status="processing")
    await job_store.push_event(job_id, "stage_start", {"stage": "extraction", "total": retry_total})

    # ── Stage 1: Rebuild page tasks from disk ─────────────────────────────
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

    # ── Stage 2: Extraction (semaphore-bounded, same as original flow) ────
    extraction_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_EXTRACTIONS)
    extraction_results = await asyncio.gather(
        *(
            _extract_one_page(task, job_id, extraction_sem, retry_total, retry_idx, db_index=db_idx)
            for retry_idx, (task, db_idx) in enumerate(page_tasks_with_db_index)
        ),
        return_exceptions=True,
    )

    all_bills: List[ExtractedBill] = []
    for result in extraction_results:
        if isinstance(result, BaseException):
            logger.error(f"Unexpected exception escaped retry extraction: {result}")
            await _increment_job_counter(job_id, "failed_pages")
            continue
        if result.bill is not None:
            all_bills.append(result.bill)

    await job_store.push_event(job_id, "stage_complete", {
        "stage": "extraction",
        "duration_ms": int((time.monotonic() - job_start) * 1000),
    })

    # ── Stage 3: Compliance + save (sequential duplicate check, parallel saves) ──
    total_bills = len(all_bills)
    t0 = time.monotonic()
    await job_store.push_event(job_id, "stage_start", {"stage": "compliance", "total": total_bills})

    db_sem = asyncio.Semaphore(settings.MAX_CONCURRENT_DB_WRITES)
    save_tasks: List[asyncio.Task] = []
    processed_bills: List[ExtractedBill] = []
    total_verified = 0
    total_flagged = 0

    async def _save_one_retry(bill_to_save: ExtractedBill) -> None:
        async with db_sem:
            try:
                async with AsyncSessionLocal() as session:
                    await _save_bill(session, job_id, bill_to_save)
            except Exception as e:
                logger.error(f"Retry save failed for {bill_to_save.source_filename}: {e}")

    for i, bill in enumerate(all_bills):
        label = bill.invoice_number or bill.source_filename
        await job_store.push_event(job_id, "stage_progress", {
            "stage": "compliance",
            "detail": f"Checking {label}...",
            "current": i + 1,
            "total": total_bills,
        })
        bill = verify(bill)
        tax_flags = [f["code"] for f in validate_tax(bill)]
        if tax_flags:
            bill = bill.model_copy(update={"flags": list(bill.flags) + tax_flags})
        bill = check_duplicate(bill, processed_bills)
        processed_bills.append(bill)

        if bill.status == BillStatus.VERIFIED:
            total_verified += 1
            job_store.increment(job_id, "verified_count")
        else:
            total_flagged += 1
            job_store.increment(job_id, "flagged_count")

        save_tasks.append(asyncio.create_task(_save_one_retry(bill)))

    await asyncio.gather(*save_tasks, return_exceptions=True)

    await job_store.push_event(job_id, "stage_complete", {
        "stage": "compliance",
        "duration_ms": int((time.monotonic() - t0) * 1000),
    })

    # ── Stage 4: Rebuild Excel (includes all invoices for the job) ────────
    t0 = time.monotonic()
    await job_store.push_event(job_id, "stage_start", {"stage": "excel", "total": 1})

    try:
        await job_store.push_event(job_id, "stage_progress", {
            "stage": "excel", "detail": "Building Excel report...", "current": 1, "total": 1,
        })
        local_excel = await build_excel(job_id)

        await job_store.push_event(job_id, "stage_progress", {
            "stage": "excel", "detail": "Uploading to storage...", "current": 1, "total": 1,
        })
        storage_path = await upload_excel(user_id, job_id, local_excel)
        local_excel.unlink(missing_ok=True)

        # Terminal status: check remaining failed pages across the whole job
        async with AsyncSessionLocal() as session:
            remaining_q = await session.execute(
                select(JobPageORM).where(
                    JobPageORM.job_id == uuid.UUID(job_id),
                    JobPageORM.status == "failed",
                )
            )
            remaining_count = len(remaining_q.scalars().all())

        terminal_status = "completed_with_errors" if remaining_count > 0 else "done"

        # Atomically promote the job counters:
        #   verified_count / flagged_count: add the newly extracted bills
        #   error_count: subtract the pages that now succeeded (floor at 0)
        succeeded_count = total_verified + total_flagged
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(JobORM)
                .where(JobORM.id == uuid.UUID(job_id))
                .values(
                    status=terminal_status,
                    completed_at=datetime.utcnow(),
                    excel_path=storage_path,
                    verified_count=JobORM.verified_count + total_verified,
                    flagged_count=JobORM.flagged_count + total_flagged,
                    error_count=func.greatest(0, JobORM.error_count - succeeded_count),
                )
            )
            await session.commit()

        job_store.update(job_id, status=terminal_status, excel_ready=True, completed_at=datetime.utcnow())

        await job_store.push_event(job_id, "stage_complete", {
            "stage": "excel",
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
        await job_store.push_event(job_id, "processing_complete", {
            "verified": total_verified,
            "flagged": total_flagged,
            "errors": remaining_count,
            "failed_pages": remaining_count,
            "total_pages": retry_total,
            "duration_ms": int((time.monotonic() - job_start) * 1000),
        })
        logger.info(f"Retry job {job_id}: {terminal_status}. {remaining_count} page(s) still failed.")

    except Exception as e:
        logger.error(f"Retry job {job_id}: Excel build/upload failed: {e}")
        job_store.update(job_id, status="error")
        await job_store.push_event(job_id, "processing_complete", {
            "verified": total_verified,
            "flagged": total_flagged,
            "errors": 1,
            "duration_ms": int((time.monotonic() - job_start) * 1000),
            "error": str(e),
        })
