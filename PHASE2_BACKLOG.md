# Phase 2 Backlog

## Durability
- Persist uploads to Supabase Storage before extraction begins — fixes "retry impossible after `/tmp` cleanup" and "instance lost mid-job" failure modes (currently uploads live only in `/tmp/billscan/uploads`, per `UPLOAD_DIR` in `app/config.py`).
- Add a content-hash (SHA-256) dedup column on the upload/invoice path with a short-circuit — avoid re-extracting identical files re-uploaded across jobs.
- `/retry` should rebuild the Excel file when all pages are `done` but `excel_path` is still `null` (currently `startup_recovery.py` marks this case `error` rather than resolving it — see `jobs_reconciled` branch at `app/core/startup_recovery.py:139`).

## Extraction quality
- Wire real buyer context from the `users` table (`company_name` / `company_address` — note `company_phone` also exists, `app/database.py:97-102`) into the extraction prompt in `app/core/gemini_client.py`. Currently static boilerplate; the buyer's actual identity would improve `buyer_name`/`buyer_gstin` disambiguation.
- Extend `_trunc()` (`app/core/batch_processor.py:202`) to every unguarded VARCHAR-limited field. Currently only `invoice_date`, `supplier_gstin`, `supplier_ifsc`, `buyer_gstin`, `tax_type` are guarded. Unguarded fields found in this audit, with their DB column limits (`app/database.py`):
  - `category` (100), `document_type` (100), `supplier_name` (500), `supplier_state` (100), `place_of_supply` (100), `destination` (200), `transport_name` (200), `lr_number` (100), `vehicle_number` (50), `eway_bill_number` (100), `irn_number` (200)
  - Line items: `die_number` (100), `po_number` (100), `hsn_sac_code` (50), `grade` (100), `quantity_unit` (50)
  - A value from Gemini exceeding any of these will raise a DB-level truncation/length error at insert time rather than degrading gracefully.
- Evaluate multi-page invoice merge (low priority — inputs are ~99% single-image per page).

## Operational
- Add a DB index on `jobs(user_id, created_at)` — `list_jobs` and the new `/jobs/stats` endpoint both filter/sort on this pair without one.
- `NullPool` connection churn under polling load — `GET /job/{job_id}` polling opens/closes a new Postgres connection every call against PgBouncer; consider a lightweight pooled path or polling-interval backoff on the frontend.
- Unify `_upload_file`'s hardcoded `max_wait_sec=60` (Files API ACTIVE-poll timeout, `app/core/gemini_client.py`) with `GEMINI_TIMEOUT_SECONDS` — right now the two timeouts are independent and can drift.
- Local-dev env gaps: `JWT_SECRET_KEY` / `SUPABASE_*` vars aren't documented as required-for-local in onboarding; also confirm IPv6 reachability to the Supabase Postgres host for local dev machines that lack IPv6 egress (asyncpg connect will hang/fail silently otherwise).
- `gemini-2.5-flash` retirement is dated **2026-10-16**. Before that date: bump `GEMINI_MODEL` to its successor and re-run the `sample_bills` smoke test to confirm output schema/quality parity.

## Notes from this closeout step
No additional "Found but not fixed" items surfaced beyond what's listed above — this conversation started fresh at Step 6 (context was cleared after Step 5), so there is no earlier discussion to sweep from. Everything found during the Step 6 sanity pass (git diff review, log-line audit, settings audit) is already folded into the sections above or into `DEPLOY_CHECKLIST.md`.
