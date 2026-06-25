import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy import update
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


async def _flatten_to_page_tasks(file_list: List[tuple]) -> tuple[List[PageTask], int]:
    """
    Split every uploaded file into its page-level work units up front.
    Sequential and fast (CPU-bound PDF split, no Gemini calls) — a corrupt
    file only costs that one file, the rest of the batch still flattens.
    Returns (page_tasks, file_level_error_count).
    """
    page_tasks: List[PageTask] = []
    file_errors = 0

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
            file_errors += 1

    return page_tasks, file_errors


async def _init_job_pages(job_id: str, page_tasks: List[PageTask]) -> None:
    """Insert one job_pages row per page (status=queued) and set jobs.total_pages."""
    async with AsyncSessionLocal() as session:
        for index, task in enumerate(page_tasks):
            session.add(JobPageORM(
                job_id=uuid.UUID(job_id),
                page_index=index,
                filename=task.filename,
                page_label=task.label,
                status="queued",
            ))
        await session.execute(
            update(JobORM)
            .where(JobORM.id == uuid.UUID(job_id))
            .values(total_pages=len(page_tasks))
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
    task: PageTask, job_id: str, sem: asyncio.Semaphore, total_pages: int, index: int
) -> PageResult:
    """
    Bounded by the extraction semaphore. Never lets an exception escape —
    failures are captured into the PageResult so one bad page cannot cancel
    its siblings in asyncio.gather.
    """
    async with sem:
        await _set_page_status(job_id, index, "processing")
        try:
            raw = await extract_bill(task.page_bytes, task.label, task.mime)
            if raw is None:
                logger.warning(f"{task.label}: extraction returned None")
                await _set_page_status(job_id, index, "failed", error_message="extraction returned None")
                await _increment_job_counter(job_id, "failed_pages")
                return PageResult(task, bill=None, error="extraction returned None")
            bill = normalize(raw, task.filename)
            logger.info(f"{task.label}: {len(bill.line_items)} item(s) extracted")
            await _set_page_status(job_id, index, "done")
            await _increment_job_counter(job_id, "completed_pages")
            return PageResult(task, bill=bill, error=None)
        except Exception as e:
            logger.error(f"{task.label}: extraction failed — {e}")
            await _set_page_status(job_id, index, "failed", error_message=str(e))
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

    page_tasks, file_split_errors = await _flatten_to_page_tasks(file_list)
    total_pages = len(page_tasks)
    total_errors = file_split_errors
    if file_split_errors:
        job_store.increment(job_id, "error_count", by=file_split_errors)

    await _init_job_pages(job_id, page_tasks)
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
        )

        await job_store.push_event(job_id, "stage_complete", {
            "stage": "excel",
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })
        await job_store.push_event(job_id, "processing_complete", {
            "verified": total_verified,
            "flagged": total_flagged,
            "errors": total_errors,
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
