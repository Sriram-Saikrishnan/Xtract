import uuid
import logging
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal, InvoiceORM
from app.excel import sheet_line_items, sheet_summary, sheet_gst, sheet_flagged

logger = logging.getLogger(__name__)


async def build_excel(job_id: str) -> Path:
    output_dir = Path(settings.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job_id}.xlsx"

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceORM)
            .where(InvoiceORM.job_id == uuid.UUID(job_id))
            .options(selectinload(InvoiceORM.line_items))
            .order_by(InvoiceORM.extracted_at)
        )
        invoices = result.scalars().all()

    wb = Workbook()
    wb.remove(wb.active)

    sheet_line_items.build_sheet(wb, invoices)
    sheet_summary.build_sheet(wb, invoices)
    sheet_gst.build_sheet(wb, invoices)
    sheet_flagged.build_sheet(wb, invoices)

    wb.save(str(output_path))
    logger.info(f"Excel saved: {output_path} ({len(invoices)} invoices)")
    return output_path
