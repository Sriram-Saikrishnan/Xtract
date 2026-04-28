import uuid
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.database import AsyncSessionLocal, InvoiceORM

logger = logging.getLogger(__name__)
router = APIRouter()

REVIEW_STATUSES = {"NEEDS_REVIEW", "DUPLICATE", "ERROR"}


class FlaggedInvoice(BaseModel):
    invoice_id: str
    source_filename: Optional[str]
    invoice_number: Optional[str]
    invoice_date: Optional[str]
    supplier_name: Optional[str]
    grand_total: float
    confidence_score: float
    status: str
    flags: Optional[str]
    category: Optional[str]


class CorrectionRequest(BaseModel):
    invoice_id: str
    corrected_fields: Dict[str, Any]


@router.get("/review/{job_id}", response_model=List[FlaggedInvoice])
async def get_flagged_bills(job_id: str):
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(InvoiceORM).where(
                    InvoiceORM.job_id == uuid.UUID(job_id),
                    InvoiceORM.status.in_(REVIEW_STATUSES),
                )
            )
            invoices = result.scalars().all()
    except Exception as e:
        logger.error(f"Review fetch failed for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    return [
        FlaggedInvoice(
            invoice_id=str(inv.id),
            source_filename=inv.source_filename,
            invoice_number=inv.invoice_number,
            invoice_date=inv.invoice_date,
            supplier_name=inv.supplier_name,
            grand_total=inv.grand_total or 0.0,
            confidence_score=inv.confidence_score or 0.0,
            status=inv.status,
            flags=inv.flags,
            category=inv.category,
        )
        for inv in invoices
    ]


@router.post("/review/{job_id}/correct")
async def correct_invoice(job_id: str, correction: CorrectionRequest):
    ALLOWED_FIELDS = {
        "invoice_number", "invoice_date", "supplier_name", "supplier_gstin",
        "supplier_state", "buyer_name", "buyer_gstin", "grand_total",
        "assessable_value", "igst_amount", "cgst_amount", "sgst_amount",
        "category", "document_type",
    }

    invalid = set(correction.corrected_fields.keys()) - ALLOWED_FIELDS
    if invalid:
        raise HTTPException(status_code=400, detail=f"Cannot update fields: {invalid}")

    try:
        async with AsyncSessionLocal() as session:
            invoice = await session.get(InvoiceORM, uuid.UUID(correction.invoice_id))
            if not invoice or str(invoice.job_id) != job_id:
                raise HTTPException(status_code=404, detail="Invoice not found")

            for field, value in correction.corrected_fields.items():
                setattr(invoice, field, value)

            invoice.status = "VERIFIED"
            invoice.confidence_score = 1.0
            invoice.flags = ""
            await session.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Correction failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to save correction")

    return {"ok": True, "invoice_id": correction.invoice_id}
