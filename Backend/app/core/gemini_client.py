import asyncio
import json
import logging
import re
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional

import httpx
import pydantic
from google import genai
from google.genai import types
from pydantic import create_model

from app.config import settings
from app.core.observability import record_llm_call
from app.core.quota_manager import quota_manager, DailyQuotaExceededError

logger = logging.getLogger(__name__)

client = genai.Client(api_key=settings.GEMINI_API_KEY)

_TYPE_MAP = {
    "string": str,
    "number": float,
    "date": str,
    "currency": float,
    "boolean": bool,
    "enum": str,
}


def _pascal(raw: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^0-9a-zA-Z]+", str(raw)) if part) or "X"


def _build_table_row_model(field_key: str, table_columns: Optional[list]) -> type:
    """A table-type field's rows become a nested pydantic model derived from
    table_columns (a JSON array of {key|field_key, type} column defs)."""
    columns = table_columns or []
    row_fields: dict[str, Any] = {}
    for col in columns:
        col_key = col.get("key") or col.get("field_key")
        if not col_key:
            continue
        py_type = _TYPE_MAP.get(col.get("type", "string"), str)
        row_fields[col_key] = (Optional[py_type], None)
    if not row_fields:
        row_fields["value"] = (Optional[str], None)
    return create_model(f"{_pascal(field_key)}Row", **row_fields)


def build_extraction_model(blueprint_id: str, fields: list) -> type:
    """Build a pydantic model dynamically from a blueprint's blueprint_fields.
    Every field is Optional — Gemini errors are soft-failed elsewhere, so a
    strict schema would only turn a partial extraction into a hard failure."""
    model_fields: dict[str, Any] = {}
    for f in fields:
        if f.field_key == "confidence_score":
            # Reserved for the fixed meta-field below — a user field of the
            # same name would otherwise silently vanish from the schema.
            logger.warning(
                f"Blueprint field_key 'confidence_score' is reserved and will be ignored "
                f"(blueprint_id={blueprint_id})"
            )
            continue
        if f.field_type == "table":
            row_model = _build_table_row_model(f.field_key, f.table_columns)
            model_fields[f.field_key] = (Optional[List[row_model]], None)
        else:
            py_type = _TYPE_MAP.get(f.field_type, str)
            model_fields[f.field_key] = (Optional[py_type], None)
    # Always appended, never user-defined — confidence_score is a standard
    # column regardless of what fields a blueprint declares.
    model_fields["confidence_score"] = (float, 0.0)
    return create_model(f"Extraction_{_pascal(blueprint_id)}", **model_fields)


def build_extraction_prompt(project, blueprint, fields: list) -> str:
    """Build the extraction prompt from blueprint.extraction_prompt_override
    if set, otherwise from project.context plus each field's description
    and example_value."""
    if blueprint is not None and blueprint.extraction_prompt_override:
        base = blueprint.extraction_prompt_override.strip() + "\n"
    else:
        base = "You are an expert document data extraction system.\n"
        if project is not None and project.context:
            base += f"\n=== CONTEXT ===\n{project.context}\n"
        base += "\n=== FIELDS TO EXTRACT ===\n"
        for f in fields:
            line = f"- {f.field_key} ({f.field_type})"
            if f.description:
                line += f": {f.description}"
            if f.example_value:
                line += f" (example: {f.example_value})"
            if f.is_required:
                line += " [REQUIRED]"
            if f.field_type == "enum" and f.enum_values:
                line += f" [allowed values: {f.enum_values}]"
            if f.field_type == "table" and f.table_columns:
                cols = ", ".join(
                    c.get("key") or c.get("field_key") for c in f.table_columns if c.get("key") or c.get("field_key")
                )
                line += f" [columns: {cols}]"
            base += line + "\n"

    base += (
        "\n=== RULES ===\n"
        "1. Return null for any field you cannot find with confidence — never guess.\n"
        "2. `confidence_score` must reflect your overall extraction confidence, from 0.0 to 1.0.\n"
        "3. Unless a field's description says otherwise, dates are DD/MM/YYYY strings and monetary "
        "values are plain numbers (no currency symbols or thousands separators).\n"
    )
    return base


def _strip_schema_keys(node):
    """Recursively drop 'default' and 'title' keys from a JSON Schema tree.

    The Gemini Developer API's response_json_schema rejects any node carrying
    a non-null 'default' (see google-genai _transformers.py process_schema) —
    pydantic's model_json_schema() emits one for every field with a default
    value. 'title' is stripped too since it's redundant noise the SDK's own
    response_schema path also strips automatically.
    """
    if isinstance(node, dict):
        return {
            key: _strip_schema_keys(value)
            for key, value in node.items()
            if key not in ("default", "title")
        }
    if isinstance(node, list):
        return [_strip_schema_keys(item) for item in node]
    return node


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json(text: str) -> dict | None:
    try:
        return json.loads(_strip_fences(text))
    except json.JSONDecodeError:
        pass
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


def _is_service_unavailable(err_str: str) -> bool:
    return "503" in err_str or "service_unavailable" in err_str.lower()


def _is_model_not_found(err_str: str) -> bool:
    err_lower = err_str.lower()
    return (
        "404" in err_str
        or "not found" in err_lower
        or "not_found" in err_lower
        or "is not supported" in err_lower
    )


_503_BACKOFFS = [5, 15, 30, 60]
_MAX_503_ATTEMPTS = 5

_429_BACKOFFS = [30, 60, 90, 120]
_MAX_429_ATTEMPTS = 4


def _http_timeout_ms() -> int:
    # google-genai's HttpOptions.timeout is in MILLISECONDS; our setting is in seconds.
    return settings.GEMINI_TIMEOUT_SECONDS * 1000


def _count_pdf_pages(file_bytes: bytes) -> int:
    import fitz  # pymupdf
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        return len(doc)


def split_pdf_pages(file_bytes: bytes) -> list:
    """Return each page of a PDF as an independent single-page PDF (bytes).
    Callers process each element as a separate document."""
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


async def _generate(contents, config, *, attempt: int = 1, input_mode: Optional[str] = None):
    """generate_content in a worker thread, timed and logged to llm_calls.
    Exceptions are logged then re-raised unchanged for the caller's retry logic."""
    model = settings.GEMINI_MODEL
    t0 = time.monotonic()
    try:
        response = await asyncio.to_thread(
            client.models.generate_content, model=model, contents=contents, config=config,
        )
    except Exception as e:
        record_llm_call(
            model=model, latency_ms=(time.monotonic() - t0) * 1000, status="error",
            error=e, attempt=attempt, input_mode=input_mode,
        )
        raise
    record_llm_call(
        model=model, latency_ms=(time.monotonic() - t0) * 1000, status="success",
        response=response, attempt=attempt, input_mode=input_mode,
    )
    return response


def _upload_file(file_bytes: bytes, filename: str, mime_type: str):
    suffix = Path(filename).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        uploaded = client.files.upload(
            file=tmp_path,
            config=types.UploadFileConfig(
                mime_type=mime_type,
                display_name=filename,
                http_options=types.HttpOptions(timeout=_http_timeout_ms()),
            ),
        )
        # Poll until ACTIVE — Google processes all PDF pages asynchronously after upload.
        # Using the file before it's ACTIVE causes the model to see only partially-processed
        # content (typically page 1 only).
        max_wait_sec = 60
        elapsed = 0
        while getattr(uploaded.state, "name", "ACTIVE") == "PROCESSING" and elapsed < max_wait_sec:
            time.sleep(2)
            elapsed += 2
            uploaded = client.files.get(
                name=uploaded.name,
                config=types.GetFileConfig(http_options=types.HttpOptions(timeout=_http_timeout_ms())),
            )
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


_FIELD_GENERATION_SYSTEM_PROMPT = """
You design field schemas for a document data-extraction system.

Given a description of what to extract and optional project context, return
a strict JSON array of field definitions. Each element must have exactly
these keys:
  - field_key: short snake_case identifier, unique within the array
  - display_label: human-readable column header
  - field_type: one of string, number, date, currency, boolean, enum, table
  - description: one sentence describing what to extract for this field
  - example_value: a short realistic example value (string), or null
  - table_columns: REQUIRED when field_type is "table", null otherwise — a
    JSON array of {"key": "<snake_case column key>", "type": "<one of
    string, number, date, currency, boolean>"} describing each column of
    the repeating row data (e.g. line items). Omitting this for a table
    field makes it unusable, so never return "table" without it.

Only use field_type "table" for genuinely repeating line-item data.
Return ONLY the raw JSON array. No markdown, no ```json fences, no explanation.
"""


async def generate_fields(prompt: str, project_context: Optional[str] = None) -> list[dict]:
    """Text-only Gemini call that proposes a field list for a blueprint,
    given a free-text prompt plus optional project context. Returns a list
    of field-definition dicts for client-side review — never persisted here."""
    full_prompt = _FIELD_GENERATION_SYSTEM_PROMPT
    if project_context:
        full_prompt += f"\n=== PROJECT CONTEXT ===\n{project_context}\n"
    full_prompt += f"\n=== REQUEST ===\n{prompt}\n"

    response = await _generate(
        [full_prompt],
        types.GenerateContentConfig(
            max_output_tokens=4096,
            temperature=0.2,
            response_mime_type="application/json",
            http_options=types.HttpOptions(timeout=_http_timeout_ms()),
        ),
        input_mode="text",
    )

    raw_text = response.text
    parsed = None
    try:
        parsed = json.loads(_strip_fences(raw_text))
    except json.JSONDecodeError:
        start = raw_text.find("[")
        end = raw_text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(raw_text[start : end + 1])
            except json.JSONDecodeError:
                pass

    if not isinstance(parsed, list):
        raise RuntimeError(f"generate_fields: model did not return a JSON array — got: {raw_text[:200]!r}")

    # Same reserved-word guard as build_extraction_model — confidence_score
    # is always the fixed meta-field, never a user/model-suggested one.
    filtered = []
    for f in parsed:
        if isinstance(f, dict) and f.get("field_key") == "confidence_score":
            logger.warning("generate_fields: dropped model-suggested 'confidence_score' field (reserved)")
            continue
        filtered.append(f)

    return filtered


async def extract_with_blueprint(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    model_cls: type,
    prompt_text: str,
) -> Optional[dict]:
    """
    Run one page/file through Gemini against a blueprint-derived pydantic
    model and prompt. Returns the extracted data as a dict (including
    confidence_score), or None if extraction could not be completed —
    the caller records that as a soft failure, never an HTTP error.
    """
    is_pdf = mime_type == "application/pdf"

    if is_pdf:
        try:
            page_count = await asyncio.to_thread(_count_pdf_pages, file_bytes)
            logger.info(f"[{filename}] PDF — {page_count} page(s), {len(file_bytes) / 1024:.1f} KB")
        except Exception as e:
            logger.warning(f"[{filename}] Could not count PDF pages: {e}")

    try:
        usage = await quota_manager.check_and_reserve(settings.GEMINI_MODEL)
        logger.info(f"Quota OK — daily: {usage['daily']}, minute: {usage['minute']}")
    except DailyQuotaExceededError as e:
        logger.error(f"Skipping {filename}: {e}")
        return None

    response_json_schema = _strip_schema_keys(model_cls.model_json_schema())
    strict_suffix = "\nIMPORTANT: Return ONLY the raw JSON object. No markdown. No ```json. No explanation."

    attempt = 0
    consecutive_503 = 0
    consecutive_429 = 0

    while True:
        uploaded_file = None
        total_attempt = attempt + consecutive_503 + consecutive_429
        try:
            prompt = f"Filename (context only, do not use to guess fields): {filename}\n\n" + prompt_text
            if total_attempt > 0:
                prompt += strict_suffix

            if is_pdf:
                try:
                    uploaded_file = await asyncio.to_thread(_upload_file, file_bytes, filename, mime_type)
                    contents = [uploaded_file, prompt]
                    input_mode = "native_pdf"
                except Exception as upload_err:
                    logger.warning(f"[{filename}] Files API failed ({upload_err}); using page-image fallback")
                    page_images = await asyncio.to_thread(_pdf_to_page_images, file_bytes)
                    logger.info(f"[{filename}] Fallback: sending {len(page_images)} page image(s)")
                    contents = [
                        *[types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in page_images],
                        prompt,
                    ]
                    input_mode = "image_fallback"
            else:
                contents = [types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt]
                input_mode = "image_direct"

            logger.info(
                f"[{filename}] Attempt {attempt + 1}/{settings.MAX_RETRIES} | mode={input_mode} | "
                f"parts={len(contents)} | prompt={len(prompt)} chars"
            )

            response = await _generate(
                contents,
                types.GenerateContentConfig(
                    max_output_tokens=16384,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_json_schema=response_json_schema,
                    http_options=types.HttpOptions(timeout=_http_timeout_ms()),
                ),
                attempt=total_attempt + 1,
                input_mode=input_mode,
            )

            raw_text = response.text
            finish_reason = response.candidates[0].finish_reason if response.candidates else "UNKNOWN"
            logger.info(
                f"[{filename}] Response {len(raw_text)} chars, finish_reason={finish_reason} — preview: "
                f"{raw_text[:200].replace(chr(10), ' ')!r}"
            )
            if str(finish_reason) not in ("FinishReason.STOP", "STOP", "1"):
                logger.warning(
                    f"[{filename}] finish_reason={finish_reason} — response may be truncated; "
                    f"consider raising max_output_tokens beyond 16384"
                )

            if isinstance(response.parsed, dict):
                try:
                    result = model_cls(**response.parsed)
                    logger.info(f"[{filename}] Extraction OK (structured) — confidence={result.confidence_score}")
                    return result.model_dump()
                except pydantic.ValidationError as ve:
                    logger.warning(f"[{filename}] structured parse produced a schema mismatch ({ve}); fell back to text parse")
            else:
                logger.warning(f"[{filename}] structured parse missed, fell back to text parse")

            parsed = _parse_json(raw_text)
            if parsed is not None:
                result = model_cls(**parsed)
                logger.info(f"[{filename}] Extraction OK (fallback) — confidence={result.confidence_score}")
                return result.model_dump()

            logger.warning(
                f"[{filename}] Attempt {attempt + 1}: JSON parse failed "
                f"({len(raw_text)} chars, finish_reason={finish_reason}). "
                f"Head: {raw_text[:120]!r} ... Tail: {raw_text[-120:]!r}"
            )
            attempt += 1
            if attempt >= settings.MAX_RETRIES:
                break

        except Exception as e:
            err_str = str(e)

            if _is_model_not_found(err_str):
                logger.warning(f"[{filename}] Model not found ({settings.GEMINI_MODEL}): {err_str}")
                raise RuntimeError(
                    f"Model unavailable: {settings.GEMINI_MODEL} — check GEMINI_MODEL env var"
                ) from e

            if isinstance(e, httpx.TimeoutException):
                attempt += 1
                logger.warning(
                    f"[{filename}] Request timed out after {settings.GEMINI_TIMEOUT_SECONDS}s "
                    f"(attempt {attempt}/{settings.MAX_RETRIES})"
                )
                if attempt >= settings.MAX_RETRIES:
                    break
                await asyncio.sleep(5)
                continue

            logger.error(f"[{filename}] Attempt {attempt + 1} error: {err_str}")

            if _is_service_unavailable(err_str):
                consecutive_503 += 1
                if consecutive_503 >= _MAX_503_ATTEMPTS:
                    logger.error(f"[{filename}] Failed after {_MAX_503_ATTEMPTS} attempts — Gemini demand spike, mark for retry")
                    break
                wait = _503_BACKOFFS[consecutive_503 - 1]
                logger.warning(f"[{filename}] 503 received, retrying in {wait}s (attempt {consecutive_503 + 1}/{_MAX_503_ATTEMPTS})")
                await asyncio.sleep(wait)
            elif _is_rate_limit(err_str):
                consecutive_429 += 1
                if consecutive_429 >= _MAX_429_ATTEMPTS:
                    logger.error(f"[{filename}] Failed after {_MAX_429_ATTEMPTS} rate-limit (429) waits — giving up")
                    raise RuntimeError(
                        f"Rate limit exceeded — Gemini API returned 429 after {_MAX_429_ATTEMPTS} backoff waits"
                    ) from e
                wait = _429_BACKOFFS[consecutive_429 - 1]
                logger.warning(f"[{filename}] Rate limit (429) received, retrying in {wait}s (attempt {consecutive_429 + 1}/{_MAX_429_ATTEMPTS})")
                await asyncio.sleep(wait)
            else:
                attempt += 1
                if attempt >= settings.MAX_RETRIES:
                    break
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
