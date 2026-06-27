# Xtract — Full Product Audit

**Date:** 2026-06-27  
**Auditor:** Full codebase read — every router, every page, every core module.

---

## Discovery Summary

Before any recommendation, here is what was mapped:

**Routers (7):** `routes_auth`, `routes_upload`, `routes_status`, `routes_download`, `routes_review`, `routes_quota`, `routes_jobs`

**React pages (8):** Dashboard, Upload, Processing, Extractions, Detail, Profile, Login, Signup

**DB tables (6):** `users`, `jobs`, `invoices`, `line_items`, `job_pages`, `gemini_quota`

**Core modules:** `batch_processor`, `gemini_client`, `auth`, `job_store`, `quota_manager`, `storage`, `extractor`, `verifier`, `duplicate`, `gstin_validator`, `tax_validator`, `file_cleaner`

**Data flow:**
- User uploads files → `/upload` reads all content into memory, writes to `/tmp/billscan/uploads/{job_id}_{filename}`, creates `JobORM` + `job_store` entry, fires background task
- `batch_processor._flatten_to_page_tasks` splits every PDF to single-page PDFs (pymupdf); `_init_job_pages` inserts one `job_pages` row per page upfront
- `asyncio.gather` with semaphore (MAX_CONCURRENT_EXTRACTIONS=6) calls `extract_bill` → Gemini Files API (with page-image fallback on failure) → JSON parse + retry → `GeminiExtractionResult`
- `normalize` → `ExtractedBill` → `verify` → `validate_tax` → `check_duplicate` (sequential) → `_save_bill` (DB-semaphore-bounded parallel)
- `build_excel` fetches all invoices for job → 4-sheet workbook → `upload_excel` to Supabase Storage
- Job status set to `done` / `completed_with_errors`; `job_store.push_event("processing_complete")` fires into SSE queue
- Frontend: SSE on `/stream/{job_id}` for live stage narration; DB poll on `/status/{job_id}` every 3s for authoritative terminal state and page-level progress bar

State lives in: PostgreSQL (authoritative), in-memory `job_store` (SSE + live counters), `localStorage` (token, user object, active job ID).

External dependencies: Gemini 1.5 Flash (Files API + image fallback), Supabase (PostgreSQL via asyncpg/PgBouncer, Storage), Render (deploy), Vercel (frontend).

---

## 1. Backend Architecture

### 1.1 `database.py` does too many things
**Current:** Engine setup, session factory, all ORM models, `init_db` migration logic, and `db_retry` decorator — 267 lines in one file.  
**Problem:** Every new table or schema change touches this file. It violates SRP and creates change coupling between unrelated concerns.  
**Recommendation:** Split into `db/engine.py` (engine + session factory + retry), `db/models.py` (ORM classes), `db/migrations.py` (`init_db`). No behavior change needed — just relocation.  
**Priority:** P2

### 1.2 Migration strategy is brittle
**Current:** `init_db()` runs raw `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` at startup on every process start.  
**Problem:** Multi-instance deploy means two instances race to run the same migrations. A failed partial migration leaves the schema in an unknown state. Already has four ad-hoc `ALTER TABLE` calls that will keep growing.  
**Recommendation:** Add Alembic. Even a single `alembic upgrade head` step in the Render deploy pipeline (before the app starts) is far safer than migration-on-boot.  
**Priority:** P1 before multi-instance or before the next schema change

### 1.3 Upload reads all files into memory before writing to disk
**Current:** `routes_upload.py` — `content = await file.read()` followed by `if len(content) > settings.max_file_size_bytes` check.  
**Problem:** Size validation happens after the full file is in RAM. A 50MB file is read completely before being rejected. For a batch of 10 × 10MB PDFs, that's a 100MB spike inside a single request on a 512MB Render instance.  
**Recommendation:** Stream directly to disk using `aiofiles`, checking size incrementally. Reject as soon as the threshold is crossed:
```python
async with aiofiles.open(dest, 'wb') as f:
    written = 0
    async for chunk in file:
        written += len(chunk)
        if written > settings.max_file_size_bytes:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"File '{file.filename}' exceeds {settings.MAX_FILE_SIZE_MB}MB limit")
        await f.write(chunk)
```
**Priority:** P1

### 1.4 `_save_one` silently drops DB save failures without updating `job_pages`
**Current:** `batch_processor.py` lines 322–329 — if `_save_bill` throws, `error_count` is incremented in `job_store` but the `job_pages` row for that page stays `done` (it was already set in `_extract_one_page`).  
**Problem:** The page shows as successfully extracted, but no invoice row exists. The Excel report and Extractions view will silently omit this invoice. The user has no way to know.  
**Recommendation:** On save failure, also call `_set_page_status(job_id, page_index, "failed", error_message=str(e))`. The page_index is not available in `_save_one` as currently structured — pass it in or restructure so save and page-status update are co-located.  
**Priority:** P1

### 1.5 Dead configuration: `BATCH_SIZE` and `BATCH_DELAY_SECONDS`
**Current:** `config.py` defines `BATCH_SIZE = 10` and `BATCH_DELAY_SECONDS = 65`. Neither is referenced anywhere in `batch_processor.py` or any other module.  
**Problem:** False confidence that batching is being respected. Developers will assume these knobs do something.  
**Recommendation:** Remove from `config.py` or wire them up. The current model processes all pages concurrently (bounded by semaphore), which is correct — the old batch model is gone.  
**Priority:** P2

### 1.6 `job_store` threading model is mismatched
**Current:** `job_store.py` uses `threading.Lock()` around `self._store`, but `asyncio.Queue()` objects stored in `self._queues` are not thread-safe.  
**Problem:** FastAPI runs on a single asyncio event loop. `threading.Lock` is unnecessary overhead and gives false confidence about thread safety of the queues. If a thread pool executor ever calls `push_event`, it will break silently.  
**Recommendation:** Replace `threading.Lock` with `asyncio.Lock`. All callers of `job_store` are async. Since `create()` is called from an async context (via `background_tasks.add_task`), the asyncio lock is the right primitive.  
**Priority:** P2

---

## 2. AI/Extraction Pipeline

### 2.1 `max_output_tokens=8192` will truncate invoices with many line items
**Current:** `gemini_client.py` line 343 — `max_output_tokens=8192`. A warning is already logged when `finish_reason != STOP`.  
**Problem:** A 50-line-item invoice with verbose descriptions and 35+ header fields can exceed 8192 output tokens. When truncated, the JSON is malformed, `_parse_json` returns `None`, and all retries fail on the same truncation.  
**Recommendation:** Raise to `16384`. Gemini 1.5 Flash supports this. The JSON schema is fixed — output tokens scale with line item count, not with input complexity. No prompt change needed.  
**Priority:** P1

### 2.2 `quantity_unit` and `weight_kg` are extracted by Gemini but silently dropped
**Current:** The `EXTRACTION_PROMPT` in `gemini_client.py` (lines 71–76) explicitly asks Gemini to extract `quantity_unit` and `weight_kg` per line item. `GeminiExtractionResult` in `models/extraction.py` has neither field. `GeminiLineItem` has no `quantity_unit` or `weight_kg`.  
**Problem:** Every successful extraction loses weight-per-line data before it reaches the DB. Only the document-level `total_weight_kg` survives. The prompt teaches Gemini to extract granular weight data, then the code discards it.  
**Recommendation:** Add `quantity_unit: Optional[str] = None` and `weight_kg: float = 0.0` to both `GeminiLineItem` and `LineItem` (in `models/bill.py`), add corresponding columns to `LineItemORM`, and update `extractor.normalize` to map them through.  
**Priority:** P1

### 2.3 No post-extraction sanity check for hallucinated zeros
**Current:** After Gemini returns, `normalize` maps fields and `verify` checks GSTIN format and confidence. No check that financial figures are internally consistent.  
**Problem:** Gemini occasionally returns `grand_total = 0.0` on an invoice with non-zero line items, or `assessable_value = 0.0` with `igst_amount > 0`. These pass validation silently and get flagged only if `validate_tax` catches the specific mismatch.  
**Recommendation:** Add a `_sanity_check(bill: ExtractedBill)` step in `extractor.normalize` or as a new step between normalize and verify. Checks: `sum(item.amount) > 0 and bill.assessable_value == 0` → add flag `ZERO_ASSESSABLE_WITH_ITEMS`; `bill.grand_total == 0 and any(item.amount > 0 ...)` → add flag `ZERO_GRAND_TOTAL`.  
**Priority:** P1

### 2.4 Files API polling uses blocking `time.sleep` inside thread
**Current:** `gemini_client._upload_file` (lines 258–263) polls for ACTIVE state with `time.sleep(2)` every 2 seconds for up to 60 seconds. This runs in `asyncio.to_thread`, so it's in a thread pool, not the event loop.  
**Problem:** Not a correctness bug, but with MAX_CONCURRENT_EXTRACTIONS=6, you can have 6 threads simultaneously sleeping in a 2s poll loop during Files API processing. Increasing the sleep to 5s would reduce thread churn by 60% with negligible latency impact.  
**Recommendation:** Change `time.sleep(2)` to `time.sleep(5)` and set `max_wait_sec = 120` (Files API can take longer on larger PDFs).  
**Priority:** P2

### 2.5 Gemini model version is outdated
**Current:** `config.py` — `GEMINI_MODEL: str = "gemini-1.5-flash"`.  
**Problem:** Gemini 2.0 Flash and Gemini 2.5 Flash are available with better accuracy on structured extraction tasks. This is a one-line `.env` change, but the default is pinned to an older model.  
**Recommendation:** Update the default to `gemini-2.0-flash` or `gemini-2.5-flash`. Test on a batch of known invoices to validate accuracy before switching production.  
**Priority:** P2

---

## 3. Database & Data Model

### 3.1 Missing indexes on `invoices.job_id` and `line_items.invoice_id`
**Current:** `database.py` — `InvoiceORM.job_id` and `LineItemORM.invoice_id` have no `index=True`. SQLAlchemy creates the FK constraint but not the index.  
**Problem:** Every `SELECT * FROM invoices WHERE job_id = ?` does a full table scan. `excel_builder.py` runs this query to build every report. `routes_jobs.list_job_invoices` runs it for every Extractions page load. At 10k+ invoices, this becomes the primary latency bottleneck.  
**Recommendation:** Add `index=True` to both columns in `database.py`. Alembic (see 1.2) would apply this via `CREATE INDEX CONCURRENTLY` with zero downtime.  
**Priority:** P1

### 3.2 `invoice_date` stored as `String(20)` instead of `Date`
**Current:** `database.py` line 140 — `invoice_date = Column(String(20))`. The value is already normalized to `DD/MM/YYYY` format in `extractor.normalize`.  
**Problem:** SQL date range queries are impossible. The tax validator already parses this string back to a `datetime` object in Python. ITC age check (180 days) works only in application code, never in DB queries.  
**Recommendation:** Migrate `invoice_date` to `Column(Date, nullable=True)`. Update `extractor.normalize` to emit a Python `date` object, and `_save_bill` to store it directly. `excel_builder` already formats dates — change to use `.strftime("%d/%m/%Y")`.  
**Priority:** P1

### 3.3 `flags` column is a semicolon-delimited text string
**Current:** `database.py` — `flags = Column(Text, default="")`. Written as `"; ".join(bill.flags)` in `_save_bill`.  
**Problem:** Querying "all invoices flagged with TAX_TYPE_WRONG" requires `WHERE flags LIKE '%TAX_TYPE_WRONG%'` — a full table scan with a `LIKE` filter. No index can help.  
**Recommendation:** Use PostgreSQL `ARRAY` type: `flags = Column(ARRAY(Text), default=list)`. The Review page already filters on `status.in_(REVIEW_STATUSES)` — adding a flag-based filter would follow the same pattern cleanly.  
**Priority:** P2

### 3.4 `gemini_quota` minute rows are never cleaned up
**Current:** `quota_manager.cleanup_old_windows` exists but is not wired into any scheduler. `file_cleaner.py` runs a periodic scheduler via APScheduler.  
**Problem:** Every Gemini call creates a minute-window row. At 3600 RPM, after one day of operation, you have 3600 × 60 × 24 = 5.2M rows if quota is at capacity. Even at modest usage, the table grows without bound.  
**Recommendation:** Add `quota_manager.cleanup_old_windows()` call in `file_cleaner.py`'s scheduler, alongside the existing file deletion job. Run every hour.  
**Priority:** P1

### 3.5 `JobORM.user_id` is nullable but should not be
**Current:** `database.py` line 112 — `user_id = Column(..., nullable=True)`. The `init_db` migration adds the column without `NOT NULL` to avoid breaking existing rows.  
**Problem:** Any new job created after auth was added should always have a `user_id`. A nullable FK is a footgun — a bug in the upload route could create an orphaned job with no owner, invisible to the user and uncleanable.  
**Recommendation:** Backfill or delete orphaned rows (there shouldn't be any in production post-auth), then run `ALTER TABLE jobs ALTER COLUMN user_id SET NOT NULL`. Add the Alembic migration to enforce this going forward.  
**Priority:** P2

---

## 4. Frontend Architecture

### 4.1 `API_BASE` is hardcoded in source
**Current:** `utils/formatters.js` line 1 — `export const API_BASE = 'https://xtract-nftf.onrender.com'`.  
**Problem:** Local development requires editing source. Any environment switch (staging, preview deploys) requires a code change.  
**Recommendation:** `export const API_BASE = import.meta.env.VITE_API_URL || 'https://xtract-nftf.onrender.com'`. Add `.env.local` with `VITE_API_URL=http://localhost:8000` for dev. Vercel reads `.env` at build time — add `VITE_API_URL` to Vercel environment variables for production.  
**Priority:** P1

### 4.2 SSE JWT passed as URL query parameter
**Current:** `pages/Processing.jsx` line 267 — `` `${API_BASE}/stream/${jobId}?token=${encodeURIComponent(token)}` ``.  
**Problem:** JWTs in query params appear in server access logs (Render logs), nginx/reverse-proxy access logs, and browser history. If logs are ever exfiltrated, all active SSE sessions are compromised.  
**Recommendation:** Add a `POST /stream-token` endpoint that returns a short-lived (60s) single-use token bound to a `job_id`. The `EventSource` connects with this ticket token, not the full JWT. The `/stream/{job_id}` endpoint validates the ticket token, not the bearer token.  
**Priority:** P1

### 4.3 No SSE reconnection logic
**Current:** `pages/Processing.jsx` lines 307–309 — `es.onerror` does nothing. The comment says "the status poll below keeps the UI moving."  
**Problem:** On SSE disconnect, the stage cards freeze at their last state. The progress bar keeps updating (DB poll), creating a jarring mismatch: "Extracting page 3 of 10" stage label while the progress bar shows 80%. This happens regularly on Render's free tier due to 30s idle connection drops.  
**Recommendation:** In `es.onerror`, close the current EventSource, wait 2–5s, and reconnect (same `sseRevision` bump pattern already used for retry). The backend's stream endpoint already handles reconnecting to a completed job gracefully.  
**Priority:** P1

### 4.4 User profile data is stale in `localStorage`
**Current:** `AuthContext.jsx` — user object is read from `localStorage` on mount and updated only on explicit profile save.  
**Problem:** If a user updates their profile from another device or browser tab, the cached object goes stale. More importantly, if the backend schema adds a field, the frontend never sees it without logout+login.  
**Recommendation:** Add a `/auth/me` fetch in `AuthContext`'s `useEffect` on mount (guarded by token presence). Replace the cached user object with the fresh response. This is a single API call at app load and costs nothing.  
**Priority:** P2

### 4.5 No per-file progress during upload
**Current:** `pages/Upload.jsx` — `handleSubmit` posts all files as a single multipart `FormData` with no progress events. The button shows "Uploading…" for the entire transfer.  
**Problem:** A user uploading 20 PDFs has no feedback for potentially 30+ seconds of network transfer. If the upload fails partway, they see a generic toast error with no indication of which files uploaded.  
**Recommendation:** Use `XMLHttpRequest` instead of `fetch` for the upload. Wire `xhr.upload.onprogress` to a bytes-transferred state, and display a percentage or "X.X MB / Y.Y MB" counter in the button label during upload.  
**Priority:** P2

### 4.6 Inline edit for invoice corrections is not wired up
**Current:** `routes_review.py` has a working `PATCH /review/correct` endpoint that accepts `corrected_fields`. The `Detail.jsx` page shows all fields but has no edit button or correction form.  
**Problem:** Users who spot an extraction error (wrong supplier name, misread invoice number) have no way to correct it from the UI. The backend capability exists — it's just not surfaced.  
**Recommendation:** Add an "Edit" toggle to `Detail.jsx` that converts read-only field values to inputs. On save, call `PATCH /review/correct` with changed fields. This is a significant UX gap for a review-oriented workflow.  
**Priority:** P1 (product capability)

---

## 5. API Design

### 5.1 Inconsistent URL structure
**Current:** `/status/{job_id}`, `/stream/{job_id}`, `/download/{job_id}`, `/review/{job_id}` are at top-level, while `/jobs/{job_id}/invoices`, `/jobs/{job_id}/pages`, `/jobs/{job_id}/retry` are nested under `/jobs`.  
**Problem:** A client SDK or API consumer cannot infer the URL pattern. Every endpoint must be discovered individually.  
**Recommendation:** Move everything job-scoped under `/jobs/{job_id}`: `/jobs/{job_id}/status`, `/jobs/{job_id}/stream`, `/jobs/{job_id}/download`, `/jobs/{job_id}/review`, `/jobs/{job_id}/retry`. Add frontend route constants so the migration is one-file.  
**Priority:** P2

### 5.2 Download proxies Excel through the backend unnecessarily
**Current:** `routes_download.py` — calls `storage.download_excel(key)` which fetches the file from Supabase into backend memory, then streams it to the client. The backend is a middleman for a potentially 10MB+ file.  
**Problem:** Every download consumes backend RAM (full file in memory), network bandwidth twice (Supabase→Render + Render→client), and holds an open connection for the transfer duration. On Render's free tier, this can time out.  
**Recommendation:** Generate a Supabase Storage signed URL (valid 60–300 seconds) and return a `302 RedirectResponse`. The client downloads directly from Supabase CDN. The Supabase Python client supports `storage.create_signed_url(bucket, key, expires_in=300)`. This eliminates the proxy entirely.  
**Priority:** P1

### 5.3 `PATCH /review/correct` accepts arbitrary field overwrite
**Current:** `routes_review.py` — `CorrectionRequest.corrected_fields: Dict[str, Any]`. Any field name, any value, no type checking beyond Pydantic's `Any`.  
**Problem:** A client (or XSS payload) can overwrite `status`, `confidence_score`, `job_id`, `id`, or any other `InvoiceORM` column. This is an authorization bypass and a data integrity risk.  
**Recommendation:** Define an explicit `InvoiceCorrectionFields` model with only the fields that should be user-editable:
```python
class InvoiceCorrectionFields(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_gstin: Optional[str] = None
    grand_total: Optional[float] = None
    category: Optional[str] = None
```
Apply only these fields to the ORM object.  
**Priority:** P1

### 5.4 No pagination on `/jobs` or invoice listing
**Current:** `routes_jobs.list_jobs` has `.limit(50)` hardcoded. `list_job_invoices` has no limit at all.  
**Problem:** A user with one large job (200 invoices) gets all 200 rows in a single response. The frontend renders them all without virtualization. At scale, this will cause multi-second load times.  
**Recommendation:** Add `?page=1&per_page=50` query params to both endpoints. The frontend can implement infinite scroll or page controls.  
**Priority:** P2

---

## 6. Reliability & Observability

### 6.1 File TTL conflicts with retry window
**Current:** `config.py` — `AUTO_DELETE_HOURS = 2`. `file_cleaner.py` deletes uploaded files after 2 hours. `run_retry_processor` reads source files from disk.  
**Problem:** If a job completes with errors and the user waits more than 2 hours before clicking "Retry Failed Pages," the retry silently fails per page with "Source file not found on disk." The retry button is still active (enabled by `completed_with_errors` status) but will always fail.  
**Recommendation:** Either (a) extend TTL to 24 hours to match a realistic "I'll retry tomorrow" window, or (b) in the retry endpoint, pre-check that all `failed_pages` have their source files present and return a clear error if not: `"Some source files have expired. Please re-upload to retry."`.  
**Priority:** P1

### 6.2 Health check does not probe the database
**Current:** `main.py` — `GET /health` returns `{"ok": True, "version": "1.0.0"}` unconditionally.  
**Problem:** Render's health check will mark the instance healthy even if the database connection is broken. Requests will start failing with 500s after the health check returns 200.  
**Recommendation:**
```python
@app.get("/health")
async def health():
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"ok": db_ok, "version": "1.0.0"}
```
If `db_ok` is False, return HTTP 503 so Render marks the instance unhealthy and restarts it.  
**Priority:** P1

### 6.3 Excel build failure leaves job in `error` state with data intact but unreachable
**Current:** `batch_processor.py` lines 434–443 — if `build_excel` or `upload_excel` throws, `job_store.update(job_id, status="error")` is set. All invoices are already in the DB.  
**Problem:** The user sees a red "Extraction failed" status, but their invoice data is complete and correct in the DB. There is no recovery path. Re-uploading would create duplicate invoices.  
**Recommendation:** Add a `POST /jobs/{job_id}/rebuild-excel` endpoint that reruns only the Excel build + Storage upload step for an existing job. Surface a "Rebuild Excel" button in the Processing error state.  
**Priority:** P2

### 6.4 Logs are unstructured text
**Current:** `main.py` — `logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")`.  
**Problem:** Render's log aggregation is line-by-line text. Querying "all errors for job X" requires grep. Correlating a job_id across modules requires full-text search.  
**Recommendation:** Switch to `python-json-logger`. Add `job_id` to a `logging.LoggerAdapter` in `batch_processor` so every log line from a job's processing automatically includes `{"job_id": "..."}`. This makes filtering trivial in any log aggregation tool.  
**Priority:** P2

---

## 7. Security

### 7.1 File uploads are validated by extension only (no magic byte check)
**Current:** `routes_upload._validate_file` checks `Path(file.filename).suffix` only.  
**Problem:** A `.pdf` file that is actually a JavaScript file, an executable, or a zip bomb is accepted. The backend passes these to pymupdf/Gemini which may reject them with cryptic errors, but not before writing them to disk.  
**Recommendation:** After reading the first 8 bytes, check magic bytes:
```python
PDF_MAGIC = b'%PDF'
JPEG_MAGIC = b'\xff\xd8\xff'
PNG_MAGIC = b'\x89PNG'
```
Reject files where the declared extension does not match the actual content type. The `python-magic` library (`pip install python-magic-bin`) automates this.  
**Priority:** P1

### 7.2 No rate limiting on auth endpoints
**Current:** `/auth/signup` and `/auth/login` have no request rate limiting, no lockout after failed attempts, no CAPTCHA.  
**Problem:** An attacker can enumerate registered emails (signup returns 409 for existing emails) and brute-force passwords indefinitely. With bcrypt and a 512MB instance, ~50 login attempts/second is feasible.  
**Recommendation:** Add `slowapi` (Starlette-compatible rate limiter): `@limiter.limit("10/minute")` on `/auth/login` and `@limiter.limit("5/minute")` on `/auth/signup`. This stops brute-force and enumeration with one dependency and three decorator lines.  
**Priority:** P1

### 7.3 Email validation is naive
**Current:** `routes_auth.signup` — `if "@" not in body.email or "." not in body.email.split("@")[-1]`.  
**Problem:** Accepts `a@b.c` (technically valid but suspicious), `user@.com` (invalid), and would reject some valid internationalized email addresses. More importantly, no check that the domain portion is non-empty.  
**Recommendation:** Use `email-validator` (`pip install email-validator`) which is already in the Pydantic ecosystem: `EmailStr` field type on `SignupRequest.email` validates and normalizes automatically.  
**Priority:** P2

### 7.4 CORS `ALLOWED_ORIGINS` defaults include localhost in production
**Current:** `config.py` — `ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173"]`. If `ALLOWED_ORIGINS` is not set in the production `.env`, these defaults apply.  
**Problem:** Not a critical risk (localhost origins only match requests from the same machine), but it's sloppy for a production service. Any misconfiguration of the env var would open the API to cross-origin requests from any localhost app.  
**Recommendation:** Default to `[]` (empty list). Fail fast at startup if `ALLOWED_ORIGINS` is empty and `ENV != "development"`. Add an `ENV: str = "development"` setting to `config.py`.  
**Priority:** P2

---

## 8. Scalability Ceiling

**Where it breaks at 10x load:**

**Bottleneck 1 — Worker memory (first to hit):** With `MAX_CONCURRENT_EXTRACTIONS=6` and the current approach of reading all files into memory at upload time, 10 concurrent users each uploading 5 × 10MB PDFs = 500MB peak just for file reads, before page-splitting allocations. Fix: stream files to disk (see 1.3).

**Bottleneck 2 — In-memory `job_store` is not shareable:** SSE queues and live counters live in the process's memory. A second Render instance would not hold the right queue — any SSE connection routed to the wrong instance gets a 404. Fix: replace `job_store` with Redis Pub/Sub. The queue abstraction in `job_store.py` maps cleanly to Redis channels. The DB poll fallback in the frontend keeps UX acceptable during any SSE failure.

**Bottleneck 3 — Background tasks die with the process:** `background_tasks.add_task(run_batch_processor, ...)` binds job execution to the HTTP process lifecycle. A Render instance restart (deploy, OOM, idle sleep) kills all in-flight jobs with no recovery. Fix: move to a proper task queue. A Supabase-backed queue with a dedicated worker process (or Celery with Redis) would decouple ingestion from execution.

**Bottleneck 4 — PgBouncer at high concurrency:** `NullPool` means every DB operation opens and closes a TCP+SSL connection to PgBouncer (100–200ms round-trip Render→Mumbai). At 6 concurrent extractions × 4 DB ops per page × multiple concurrent jobs, you can saturate PgBouncer's 15 backend connections. Fix: The NullPool decision is correct for the current setup (explained thoroughly in the code comments). The real fix is to increase Supabase's PgBouncer pool size or move to the direct PostgreSQL port (5432) with SQLAlchemy's built-in pool — but only if the NAT/firewall idle-drop problem is resolved (e.g., keepalive settings).

**What doesn't need to change:** The Gemini quota design (DB-backed `SELECT FOR UPDATE` is correct for multi-user quota sharing), the page-level extraction model (clean), and the SSE + DB-poll dual-track pattern (resilient).

---

## Priority Summary

| # | Finding | Priority | File(s) |
|---|---------|----------|---------|
| 1.3 | Upload reads all files into memory | P1 | `routes_upload.py` |
| 1.4 | `_save_one` drops DB failures silently | P1 | `batch_processor.py` |
| 1.2 | Migration-on-boot is fragile | P1 | `database.py`, `main.py` |
| 2.1 | `max_output_tokens=8192` truncates large invoices | P1 | `gemini_client.py` |
| 2.2 | `quantity_unit`/`weight_kg` extracted but discarded | P1 | `models/extraction.py`, `models/bill.py`, `database.py` |
| 2.3 | No post-extraction sanity check | P1 | `core/extractor.py` |
| 3.1 | Missing indexes on `invoices.job_id`, `line_items.invoice_id` | P1 | `database.py` |
| 3.2 | `invoice_date` stored as string | P1 | `database.py`, `extractor.py` |
| 3.4 | `gemini_quota` rows never cleaned up | P1 | `core/quota_manager.py`, `core/file_cleaner.py` |
| 4.1 | `API_BASE` hardcoded in source | P1 | `utils/formatters.js` |
| 4.2 | JWT in SSE URL query param | P1 | `pages/Processing.jsx` |
| 4.3 | No SSE reconnection | P1 | `pages/Processing.jsx` |
| 4.6 | Invoice correction not wired in UI | P1 | `pages/Detail.jsx` |
| 5.2 | Download proxies Excel through backend | P1 | `routes_download.py` |
| 5.3 | Arbitrary field overwrite via `/review/correct` | P1 | `routes_review.py` |
| 6.1 | File TTL conflicts with retry window | P1 | `core/file_cleaner.py`, `routes_jobs.py` |
| 6.2 | Health check doesn't probe DB | P1 | `main.py` |
| 7.1 | No magic byte validation on upload | P1 | `routes_upload.py` |
| 7.2 | No rate limiting on auth endpoints | P1 | `routes_auth.py` |
| 1.1 | `database.py` violates SRP | P2 | `database.py` |
| 1.5 | Dead config: `BATCH_SIZE`, `BATCH_DELAY_SECONDS` | P2 | `config.py` |
| 1.6 | `job_store` uses wrong lock primitive | P2 | `core/job_store.py` |
| 2.4 | Files API polling too frequent | P2 | `gemini_client.py` |
| 2.5 | Gemini model version outdated | P2 | `config.py` |
| 3.3 | `flags` stored as semicolon string | P2 | `database.py` |
| 3.5 | `JobORM.user_id` nullable | P2 | `database.py` |
| 4.4 | Stale user object in localStorage | P2 | `context/AuthContext.jsx` |
| 4.5 | No per-file upload progress | P2 | `pages/Upload.jsx` |
| 5.1 | Inconsistent URL structure | P2 | `api/routes_*.py` |
| 5.4 | No pagination | P2 | `routes_jobs.py` |
| 6.3 | Excel failure leaves data unreachable | P2 | `batch_processor.py` |
| 6.4 | Unstructured logs | P2 | `main.py` |
| 7.3 | Naive email validation | P2 | `routes_auth.py` |
| 7.4 | CORS defaults include localhost | P2 | `config.py` |

---

## What is Well-Designed

These are genuinely good decisions that should be left alone:

- **`NullPool` + PgBouncer rationale** — the inline comment in `database.py` explaining pool-of-pools and why NullPool was chosen is correct and clear. Don't second-guess it.
- **`SELECT FOR UPDATE` in `quota_manager`** — atomic quota reservation without application-level locking is the right choice. The pattern is clean.
- **Pre-seeding all `job_pages` rows upfront** — `_init_job_pages` writes every page before extraction starts. This means no page is ever silently dropped and progress tracking is always accurate from the start.
- **`asyncio.gather` with semaphore** — clean bounded concurrency. Each page is independent and the exception guard (`return_exceptions=True` + internal try/catch) ensures one bad page never cancels siblings.
- **Sequential duplicate check + parallel DB saves** — the comment in `batch_processor.py` explains exactly why this ordering matters. It's correct.
- **Dual-track progress (SSE + DB poll)** — the design decision to use SSE for live narration and DB for authoritative state is exactly right. The frontend's `finish()` gate function (which runs only once, guarded by `finishedRef`) prevents race conditions between the two sources. Well thought out.
- **Files API fallback to page images** — if the Files API fails, the extraction still works via image rendering. This is a good degradation path.
- **`db_retry` decorator** — transient connection error handling at the route layer is the right place. The backoff is appropriate.
