import asyncio
import json
import re
import time
import logging
import tempfile
from pathlib import Path

from google import genai
from google.genai import types

from app.config import settings
from app.models.extraction import GeminiExtractionResult
from app.core.quota_manager import quota_manager, DailyQuotaExceededError

logger = logging.getLogger(__name__)

client = genai.Client(api_key=settings.GEMINI_API_KEY)

CATEGORY_PROMPT = """
Categories — pick the ONE most accurate for this document:
  - Raw Material        → steel, castings, metal rods, sheets, billets, wire rod
  - Tooling             → carbide inserts, cutting tools, drills, end mills, dies, fixtures
  - Machine Spare Parts → bearings, belts, seals, filters, motors, gear boxes
  - Consumables         → cutting oils, coolants, adhesives, chemicals, welding gas
  - Packaging Material  → kraft paper, corrugation board, boxes, strapping, stretch film
  - Utilities           → electricity board, water, fuel, LPG, diesel
  - Logistics           → freight, transport, courier, loading charges, Bharti Rodways
  - Maintenance         → machine service, AMC, calibration, repair, overhauling
  - Other               → anything that does not fit the above categories
"""

EXTRACTION_PROMPT = f"""
You are an expert at extracting structured data from Indian manufacturing invoices, bills, and delivery challans.

{CATEGORY_PROMPT}

CRITICAL: This document may have MULTIPLE PAGES. You MUST read and process EVERY page from start to finish before responding.
- Scan ALL pages for line items — many invoices continue the items table across pages 2, 3, or more.
- Header fields (supplier, buyer, invoice number) are usually on page 1.
- Line items may span ALL pages — do NOT stop at page 1.
- Totals and GST summary are usually on the LAST page.

Extract ALL fields from this document and return ONLY a valid JSON object with no markdown, no explanation, no code fences.

Rules:
1. All monetary values must be numbers (float), NEVER strings. Use 0.0 if not found.
2. Dates must be in DD/MM/YYYY format.
3. Auto-detect tax type: use "IGST" if supplier and buyer are in different states, "CGST+SGST" if same state.
4. Extract ALL line items from ALL pages — never skip any rows from any page of the items table.
5. Pick category from the list above based on document content, NOT from filename.
6. confidence_score: your own assessment from 0.0 to 1.0 of how accurately you extracted the data.
7. Return null for fields you cannot find — never guess invoice numbers or amounts.
8. HSN/SAC codes are 4-8 digit numbers on Indian invoices.
9. GSTIN is a 15-character alphanumeric code.
10. If the same line item appears continued across pages, merge it into one entry — do not duplicate.

Return this exact JSON structure:
{{
  "category": "string",
  "invoice_number": "string or null",
  "invoice_date": "DD/MM/YYYY or null",
  "challan_number": "string or null",
  "document_type": "TAX INVOICE / DELIVERY CHALLAN / PROFORMA / RECEIPT or null",
  "supplier_name": "string or null",
  "supplier_gstin": "string or null",
  "supplier_state": "string or null",
  "supplier_address": "string or null",
  "supplier_email": "string or null",
  "supplier_phone": "string or null",
  "supplier_bank": "string or null",
  "supplier_account_number": "string or null",
  "supplier_ifsc": "string or null",
  "buyer_name": "string or null",
  "buyer_gstin": "string or null",
  "place_of_supply": "string or null",
  "destination": "string or null",
  "transport_name": "string or null",
  "lr_number": "string or null",
  "vehicle_number": "string or null",
  "eway_bill_number": "string or null",
  "irn_number": "string or null",
  "assessable_value": 0.0,
  "tax_type": "IGST or CGST+SGST or null",
  "igst_percent": 0.0,
  "igst_amount": 0.0,
  "cgst_percent": 0.0,
  "cgst_amount": 0.0,
  "sgst_percent": 0.0,
  "sgst_amount": 0.0,
  "pf_charges": 0.0,
  "round_off": 0.0,
  "grand_total": 0.0,
  "total_weight_kg": 0.0,
  "total_qty": 0,
  "confidence_score": 0.8,
  "line_items": [
    {{
      "sr_no": 1,
      "die_number": "string or null",
      "po_number": "string or null",
      "description": "string or null",
      "hsn_sac_code": "string or null",
      "grade": "string or null",
      "quantity": 0.0,
      "rate": 0.0,
      "amount": 0.0
    }}
  ]
}}
"""

STRICT_PROMPT = EXTRACTION_PROMPT + "\nIMPORTANT: Return ONLY the raw JSON object. No markdown. No ```json. No explanation."


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json(text: str) -> dict | None:
    # Try 1: strip markdown fences, parse directly
    try:
        return json.loads(_strip_fences(text))
    except json.JSONDecodeError:
        pass

    # Try 2: locate the outermost { ... } by character search (handles extra preamble/suffix)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _is_rate_limit(err_str: str) -> bool:
    return "429" in err_str or "resource_exhausted" in err_str.lower() or "quota" in err_str.lower()


def _count_pdf_pages(file_bytes: bytes) -> int:
    import fitz  # pymupdf
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        return len(doc)


def split_pdf_pages(file_bytes: bytes) -> list:
    """Return each page of a PDF as an independent single-page PDF (bytes).
    Callers process each element as a separate invoice."""
    import fitz  # pymupdf
    pages = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page_num in range(len(doc)):
            single = fitz.open()
            single.insert_pdf(doc, from_page=page_num, to_page=page_num)
            pages.append(single.tobytes())
            single.close()
    return pages


def _pdf_to_page_images(file_bytes: bytes) -> list:
    """Convert every PDF page to a JPEG bytes object (150 DPI) for Gemini image input."""
    import fitz  # pymupdf
    images = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        mat = fitz.Matrix(150 / 72, 150 / 72)  # 150 DPI
        for page_num, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat)
            jpeg_bytes = pix.tobytes("jpeg")
            images.append(jpeg_bytes)
            logger.debug(f"  Page {page_num + 1}: {pix.width}x{pix.height}px, {len(jpeg_bytes) // 1024}KB")
    return images


def _upload_file(file_bytes: bytes, filename: str, mime_type: str):
    suffix = Path(filename).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        uploaded = client.files.upload(
            file=tmp_path,
            config=types.UploadFileConfig(mime_type=mime_type, display_name=filename)
        )
        # Poll until ACTIVE — Google processes all PDF pages asynchronously after upload.
        # Using the file before it's ACTIVE causes the model to see only partially-processed
        # content (typically page 1 only).
        max_wait_sec = 60
        elapsed = 0
        while getattr(uploaded.state, "name", "ACTIVE") == "PROCESSING" and elapsed < max_wait_sec:
            time.sleep(2)
            elapsed += 2
            uploaded = client.files.get(name=uploaded.name)
            logger.debug(f"[{filename}] Files API state: {uploaded.state.name} ({elapsed}s waited)")

        state = getattr(uploaded.state, "name", "UNKNOWN")
        if state != "ACTIVE":
            raise RuntimeError(f"File {filename!r} stuck in state {state!r} after {elapsed}s")
        logger.info(f"[{filename}] Files API upload complete: {uploaded.name}, state=ACTIVE")
        return uploaded
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _delete_file(file_name: str):
    try:
        client.files.delete(name=file_name)
    except Exception:
        pass


async def extract_bill(image_bytes: bytes, filename: str, mime_type: str = "image/jpeg") -> GeminiExtractionResult:
    is_pdf = mime_type == "application/pdf"

    # Log page count and size upfront so we can confirm all pages are present
    if is_pdf:
        try:
            page_count = await asyncio.to_thread(_count_pdf_pages, image_bytes)
            logger.info(f"[{filename}] PDF — {page_count} page(s), {len(image_bytes) / 1024:.1f} KB")
        except Exception as e:
            logger.warning(f"[{filename}] Could not count PDF pages: {e}")

    # Check and reserve quota BEFORE calling Gemini — blocks if RPM hit
    try:
        usage = await quota_manager.check_and_reserve(settings.GEMINI_MODEL)
        logger.info(f"Quota OK — daily: {usage['daily']}, minute: {usage['minute']}")
    except DailyQuotaExceededError as e:
        logger.error(f"Skipping {filename}: {e}")
        return None

    for attempt in range(settings.MAX_RETRIES):
        uploaded_file = None
        try:
            prompt_text = f"Filename (context only, do not use to guess fields): {filename}\n\n"
            prompt_text += EXTRACTION_PROMPT if attempt == 0 else STRICT_PROMPT

            if is_pdf:
                try:
                    uploaded_file = await asyncio.to_thread(_upload_file, image_bytes, filename, mime_type)
                    contents = [uploaded_file, prompt_text]
                    input_mode = "native_pdf"
                except Exception as upload_err:
                    # Fallback: render every page as a JPEG and send as individual image parts.
                    # This guarantees all pages reach the model even if the Files API is unavailable.
                    logger.warning(f"[{filename}] Files API failed ({upload_err}); using page-image fallback")
                    page_images = await asyncio.to_thread(_pdf_to_page_images, image_bytes)
                    logger.info(f"[{filename}] Fallback: sending {len(page_images)} page image(s)")
                    contents = [
                        *[types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in page_images],
                        prompt_text,
                    ]
                    input_mode = "image_fallback"
            else:
                contents = [
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt_text,
                ]
                input_mode = "image_direct"

            logger.info(
                f"[{filename}] Attempt {attempt + 1}/{settings.MAX_RETRIES} | mode={input_mode} | "
                f"parts={len(contents)} | prompt={len(prompt_text)} chars"
            )

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=settings.GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    max_output_tokens=8192,
                    temperature=0.1,
                ),
            )

            raw_text = response.text
            finish_reason = (
                response.candidates[0].finish_reason if response.candidates else "UNKNOWN"
            )
            logger.info(
                f"[{filename}] Response {len(raw_text)} chars, finish_reason={finish_reason} — preview: "
                f"{raw_text[:200].replace(chr(10), ' ')!r}"
            )
            if str(finish_reason) not in ("FinishReason.STOP", "STOP", "1"):
                logger.warning(
                    f"[{filename}] finish_reason={finish_reason} — response may be truncated; "
                    f"consider raising max_output_tokens beyond 8192"
                )

            parsed = _parse_json(raw_text)
            if parsed is not None:
                result = GeminiExtractionResult(**parsed)
                logger.info(
                    f"[{filename}] Extraction OK — {len(result.line_items)} line item(s), "
                    f"confidence={result.confidence_score}"
                )
                return result

            logger.warning(
                f"[{filename}] Attempt {attempt + 1}: JSON parse failed "
                f"({len(raw_text)} chars, finish_reason={finish_reason}). "
                f"Head: {raw_text[:120]!r} ... Tail: {raw_text[-120:]!r}"
            )

        except Exception as e:
            err_str = str(e)
            logger.error(f"[{filename}] Attempt {attempt + 1} error: {err_str}")

            if _is_rate_limit(err_str):
                logger.warning(f"Rate limit — waiting {settings.RETRY_WAIT_SECONDS}s before retry...")
                await asyncio.sleep(settings.RETRY_WAIT_SECONDS)
            else:
                if attempt < settings.MAX_RETRIES - 1:
                    await asyncio.sleep(5)

        finally:
            if uploaded_file is not None:
                await asyncio.to_thread(_delete_file, uploaded_file.name)

    logger.error(f"[{filename}] All {settings.MAX_RETRIES} attempts failed")
    return None


def get_mime_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".pdf": "application/pdf",
        ".heic": "image/heic",
    }
    return mime_map.get(ext, "image/jpeg")
