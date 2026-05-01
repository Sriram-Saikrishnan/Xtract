import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.duplicate import check_duplicate
from app.core.extractor import normalize
from app.core.gemini_client import extract_bill, get_mime_type, split_pdf_pages
from app.core.gstin_validator import validate_gstin, validate_buyer_gstin
from app.core.job_store import job_store
from app.core.verifier import verify
from app.core.storage import upload_excel
from app.database import AsyncSessionLocal, InvoiceORM, JobORM, LineItemORM
from app.excel.excel_builder import build_excel
from app.models.bill import BillStatus, ExtractedBill

logger = logging.getLogger(__name__)


async def _process_file(filepath: Path, filename: str) -> List[ExtractedBill]:
    """Process one uploaded file; returns one bill per page for PDFs, one bill for images."""
    try:
        file_bytes = filepath.read_bytes()
        mime = get_mime_type(filename)

        if mime == "application/pdf":
            page_bytes_list = await asyncio.to_thread(split_pdf_pages, file_bytes)
            logger.info(f"{filename}: {len(page_bytes_list)} page(s) — each processed as a separate invoice")
            bills = []
            for page_num, page_bytes in enumerate(page_bytes_list, 1):
                page_label = f"{Path(filename).stem}_p{page_num}.pdf"
                raw = await extract_bill(page_bytes, page_label, mime)
                if raw is not None:
                    bill = normalize(raw, filename)
                    logger.info(f"{filename} page {page_num}: {len(bill.line_items)} item(s) extracted")
                    bills.append(bill)
                else:
                    logger.warning(f"{filename} page {page_num}: extraction returned None")
            return bills
        else:
            raw = await extract_bill(file_bytes, filename, mime)
            if raw is None:
                return []
            return [normalize(raw, filename)]

    except Exception as e:
        logger.error(f"Error processing {filename}: {e}")
        return []


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

    processed_bills: List[ExtractedBill] = []
    file_list = list(zip(file_paths, filenames))

    total_verified = 0
    total_flagged = 0
    total_errors = 0

    for fp, fn in file_list:
        results = await _process_file(fp, fn)  # list — one bill per PDF page (or per image)

        async with AsyncSessionLocal() as session:
            if not results:
                total_errors += 1
                job_store.increment(job_id, "error_count")
            else:
                for bill in results:
                    bill = verify(bill)

                    gstin_result = await validate_gstin(bill.supplier_gstin, bill.supplier_name)
                    buyer_gstin_flags = await validate_buyer_gstin(bill.buyer_gstin)
                    new_flag_codes = [f["code"] for f in gstin_result.flags + buyer_gstin_flags]
                    bill_updates: dict = {}
                    if new_flag_codes:
                        bill_updates["flags"] = list(bill.flags) + new_flag_codes
                    if gstin_result.einvoice_mandatory is not None:
                        bill_updates["einvoice_mandatory"] = gstin_result.einvoice_mandatory
                    if bill_updates:
                        bill = bill.model_copy(update=bill_updates)

                    bill = check_duplicate(bill, processed_bills)
                    await _save_bill(session, job_id, bill)
                    processed_bills.append(bill)

                    if bill.status == BillStatus.VERIFIED:
                        total_verified += 1
                        job_store.increment(job_id, "verified_count")
                    else:
                        total_flagged += 1
                        job_store.increment(job_id, "flagged_count")

        job_store.increment(job_id, "processed_files")

    # Build Excel, upload to Supabase Storage, delete local temp file
    try:
        local_excel = await build_excel(job_id)

        storage_path = await upload_excel(user_id, job_id, local_excel)
        try:
            local_excel.unlink(missing_ok=True)
        except Exception:
            pass  # temp file cleanup is best-effort

        async with AsyncSessionLocal() as session:
            result = await session.get(JobORM, uuid.UUID(job_id))
            if result:
                result.status = "done"
                result.completed_at = datetime.utcnow()
                result.excel_path = storage_path  # Supabase key, not a local path
                result.verified_count = total_verified
                result.flagged_count = total_flagged
                result.error_count = total_errors
                await session.commit()

        job_store.update(
            job_id,
            status="done",
            completed_at=datetime.utcnow(),
            excel_ready=True,
            verified_count=total_verified,
            flagged_count=total_flagged,
            error_count=total_errors,
        )
        logger.info(f"Job {job_id}: completed. Excel at storage:{storage_path}")
    except Exception as e:
        logger.error(f"Job {job_id}: Excel build/upload failed: {e}")
        job_store.update(job_id, status="error")
