import uuid
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select

from app.database import AsyncSessionLocal, JobORM, InvoiceORM, LineItemORM, UserORM, db_retry
from app.core.auth import get_current_user
from app.core.job_store import job_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Jobs"])


@router.get("/jobs")
@db_retry()
async def list_jobs(current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(JobORM)
            .where(JobORM.user_id == current_user.id)
            .order_by(JobORM.created_at.desc())
            .limit(50)
        )
        jobs = result.scalars().all()
        return [
            {
                "id": str(j.id),
                "status": j.status,
                "total_files": j.total_files,
                "processed_files": j.processed_files,
                "verified_count": j.verified_count or 0,
                "flagged_count": j.flagged_count or 0,
                "error_count": j.error_count or 0,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                "excel_ready": j.excel_path is not None,
            }
            for j in jobs
        ]


@router.get("/jobs/{job_id}/invoices")
@db_retry()
async def list_job_invoices(job_id: str, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        job = await session.get(JobORM, uuid.UUID(job_id))
        if not job or job.user_id != current_user.id:
            raise HTTPException(404, "Job not found")

        result = await session.execute(
            select(InvoiceORM)
            .where(InvoiceORM.job_id == uuid.UUID(job_id))
            .order_by(InvoiceORM.extracted_at)
        )
        invoices = result.scalars().all()
        return [
            {
                "id": str(inv.id),
                "job_id": str(inv.job_id),
                "source_filename": inv.source_filename,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date,
                "supplier_name": inv.supplier_name,
                "document_type": inv.document_type,
                "category": inv.category,
                "grand_total": inv.grand_total,
                "assessable_value": inv.assessable_value,
                "confidence_score": inv.confidence_score,
                "status": inv.status,
                "flags": inv.flags,
                "tax_type": inv.tax_type,
                "extracted_at": inv.extracted_at.isoformat() if inv.extracted_at else None,
            }
            for inv in invoices
        ]


@router.get("/invoices/{invoice_id}")
@db_retry()
async def get_invoice(invoice_id: str, current_user: UserORM = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        inv = await session.get(InvoiceORM, uuid.UUID(invoice_id))
        if not inv:
            raise HTTPException(404, "Invoice not found")

        job = await session.get(JobORM, inv.job_id)
        if not job or job.user_id != current_user.id:
            raise HTTPException(404, "Invoice not found")

        result = await session.execute(
            select(LineItemORM)
            .where(LineItemORM.invoice_id == uuid.UUID(invoice_id))
            .order_by(LineItemORM.sr_no)
        )
        items = result.scalars().all()

        return {
            "id": str(inv.id),
            "job_id": str(inv.job_id),
            "source_filename": inv.source_filename,
            "category": inv.category,
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date,
            "challan_number": inv.challan_number,
            "document_type": inv.document_type,
            "supplier_name": inv.supplier_name,
            "supplier_gstin": inv.supplier_gstin,
            "supplier_state": inv.supplier_state,
            "supplier_address": inv.supplier_address,
            "supplier_email": inv.supplier_email,
            "supplier_phone": inv.supplier_phone,
            "supplier_bank": inv.supplier_bank,
            "supplier_account_number": inv.supplier_account_number,
            "supplier_ifsc": inv.supplier_ifsc,
            "buyer_name": inv.buyer_name,
            "buyer_gstin": inv.buyer_gstin,
            "place_of_supply": inv.place_of_supply,
            "destination": inv.destination,
            "transport_name": inv.transport_name,
            "lr_number": inv.lr_number,
            "vehicle_number": inv.vehicle_number,
            "eway_bill_number": inv.eway_bill_number,
            "irn_number": inv.irn_number,
            "assessable_value": inv.assessable_value,
            "tax_type": inv.tax_type,
            "igst_percent": inv.igst_percent,
            "igst_amount": inv.igst_amount,
            "cgst_percent": inv.cgst_percent,
            "cgst_amount": inv.cgst_amount,
            "sgst_percent": inv.sgst_percent,
            "sgst_amount": inv.sgst_amount,
            "pf_charges": inv.pf_charges,
            "round_off": inv.round_off,
            "grand_total": inv.grand_total,
            "total_weight_kg": inv.total_weight_kg,
            "total_qty": inv.total_qty,
            "confidence_score": inv.confidence_score,
            "status": inv.status,
            "flags": inv.flags,
            "extracted_at": inv.extracted_at.isoformat() if inv.extracted_at else None,
            "line_items": [
                {
                    "sr_no": item.sr_no,
                    "die_number": item.die_number,
                    "po_number": item.po_number,
                    "description": item.description,
                    "hsn_sac_code": item.hsn_sac_code,
                    "grade": item.grade,
                    "quantity": item.quantity,
                    "rate": item.rate,
                    "amount": item.amount,
                }
                for item in items
            ],
        }


@router.delete("/job/{job_id}")
@db_retry()
async def delete_job(job_id: str, current_user: UserORM = Depends(get_current_user)):
    excel_path = None
    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(JobORM, uuid.UUID(job_id))
            if not job or job.user_id != current_user.id:
                raise HTTPException(404, "Job not found")
            excel_path = job.excel_path
            await session.delete(job)
            await session.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete job {job_id} DB error: {e}")

    job_store.delete(job_id)

    if excel_path:
        try:
            Path(excel_path).unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Could not remove excel file {excel_path}: {e}")

    return {"deleted": job_id}
