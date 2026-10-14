# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

<!-- claude-config -->
```yaml
main_branch: dev
work_items_dir: .notes/Agile
build_cmd: cd Frontend && npm run build
test_cmd: cd Backend && python -m pytest tests/
format_cmd: cd Frontend && npm run build --dry-run
lint_cmd: ""
solution_file: ""
```

## Commands

### Backend (FastAPI + Python 3.11)
```bash
cd Backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload          # dev server on :8000
python -m uvicorn app.main:app --host 0.0.0.0    # prod-like
python -m pytest tests/                           # run all tests
python -m pytest tests/test_foo.py::test_bar      # single test
```

### Frontend (React + Vite)
```bash
cd Frontend
npm install
npm run dev      # dev server on localhost:5173
npm run build    # build to dist/
npm run preview  # preview production build locally
npm test         # vitest (src/test/)
```

## Architecture

Xtract is a generic, blueprint-driven AI document extraction SaaS. Users define a **Project** (domain context) and one or more **Blueprints** (a typed field schema), upload PDFs/images, Gemini extracts each page against the blueprint's schema, results are stored in Supabase, and users download a multi-sheet Excel report. It is not invoice-specific — the schema is data, not code.

### Domain model
```
Project (context)  ──<  Blueprint (input_type, generation_mode, extraction_prompt_override, is_active, thinking)
                            ──<  BlueprintField (field_key, display_label, field_type, description,
                                                 example_value, is_required, enum_values, table_columns, sort_order)
Job (project_id, blueprint_id, thinking, thinking_budget) ──< JobPage (per-page status)  ──< ExtractionResult (JSONB data, confidence_score, status)
```
`field_type`: `string | number | date | currency | boolean | enum | table` (table → `table_columns` JSON array of `{key, type}`).
DB tables: `users`, `projects`, `blueprints`, `blueprint_fields`, `jobs`, `job_pages`, `extraction_results`, `gemini_quota`, `llm_calls`. SQLAlchemy creates them on startup.

### Request lifecycle
1. User builds a blueprint (manually, or via `POST /blueprints/{id}/generate-fields` — Gemini proposes fields from a prompt) and can dry-run it with `POST /blueprints/{id}/test` (no DB write, accepts unsaved fields)
2. `POST /upload` (multipart: files + `project_id` + `blueprint_id`) → job created, files saved to `UPLOAD_DIR`
3. Background task (`batch_processor.py`) builds the pydantic model + prompt once per job, splits PDFs into single pages, extracts pages concurrently (semaphore), writes one `extraction_results` row per page (success or failure), builds Excel, uploads to Supabase Storage
4. Frontend streams progress via SSE `GET /stream/{job_id}?token=...` (`GET /status/{job_id}` for polling)
5. `POST /jobs/{job_id}/retry` re-extracts only failed pages; `POST /extraction-results/{id}/correct` saves human corrections
6. `GET /download/{job_id}` streams the Excel from Supabase Storage

### Backend structure (`Backend/app/`)
| Layer | Path | Responsibility |
|---|---|---|
| Config | `config.py` | Pydantic `Settings` — all env vars, no hardcoding (`GEMINI_MODEL` default `gemini-2.5-flash`) |
| DB | `database.py` | Async SQLAlchemy engine (NullPool for PgBouncer), ORM models, `init_db()`, `db_retry` |
| Models | `models/` | Pydantic request models (`blueprint.py`, `job.py`) |
| Auth | `core/auth.py` | JWT (HS256, 7-day), bcrypt, `get_current_user` FastAPI dep |
| Routes | `api/` | `routes_auth`, `routes_projects`, `routes_blueprints`, `routes_upload`, `routes_status` (status + SSE), `routes_jobs` (list/stats/retry/delete), `routes_results`, `routes_download`, `routes_quota`, `routes_observability` (`/observability/overview`, `/observability/jobs[/{id}]`, `/observability/calls?job_id=`) |
| AI | `core/gemini_client.py` | `generate_fields()` (prompt → field schema), `build_extraction_model()` (blueprint → dynamic pydantic model), `build_extraction_prompt()`, `extract_with_blueprint()` (structured output via `response_json_schema`, Files API + page-image fallback, retry/backoff) |
| Processing | `core/batch_processor.py` | Page splitting, concurrent extraction, per-page status, retry-failed-pages, Excel + Storage finalize |
| Progress | `core/job_store.py` | In-memory job state + SSE event queue |
| Excel | `excel/excel_builder.py` | openpyxl: `Results` sheet (non-table fields) + one sheet per `table` field |
| Storage | `core/storage.py` | Supabase Storage (`xtract-excel` private bucket), path = `{user_id}/{job_id}.xlsx` |
| Quota | `core/quota_manager.py` | Atomic per-minute + daily Gemini limits in Postgres (`SELECT ... FOR UPDATE`) |
| Observability | `core/observability.py` | Every Gemini call (incl. retries) → `llm_calls` row: model, tokens (input/output/thinking/cached), latency, cost (`GEMINI_PRICING`), status. Call-site context via `set_llm_context()` ContextVar; writes are fire-and-forget, never fail extraction |
| Recovery | `core/startup_recovery.py` | Startup sweep marks crash-interrupted pages/jobs failed so `/retry` can rescue them |
| Cleanup | `core/file_cleaner.py` | APScheduler job deletes uploads after `AUTO_DELETE_HOURS` |

### Frontend structure (`Frontend/src/`)
- **Routing**: `App.jsx` uses a single `useState(page)` + switch — no React Router. Navigate by calling `setPage('projects')`. Pages: `dashboard`, `projects`, `project-form`, `project-detail`, `blueprint-builder`, `upload`, `processing`, `results`, `result-detail`, `profile`, `observability` (opened from Profile), plus Login/Signup.
- **Auth**: `context/AuthContext.jsx` — token + user stored in `localStorage`, auto-logout on any 401 via a custom event.
- **API client**: `utils/formatters.js` — `apiFetch()` wraps `fetch`, injects `Authorization: Bearer`, dispatches `auth:logout` on 401. `API_BASE` = `VITE_API_URL` env var, falling back to `https://xtract-nftf.onrender.com`.
- **Styles**: Single global file `src/index.css` with CSS custom properties (`--text-1`, `--red`, etc.). No CSS modules or Tailwind.
- **Tests**: Vitest in `src/test/`.

### Environment & deployment
- Backend: Render (`Backend/render.yaml`) — Python 3.11, reads `.env`
- Frontend: Vercel (`vercel.json`) — builds `dist/`, no SSR
- DB + Storage: Supabase (PostgreSQL via asyncpg, private Storage bucket `xtract-excel`)
- Initial DB schema: apply `supabase_rls_migration.sql` manually in Supabase console; SQLAlchemy `init_db()` creates application tables on first startup

Required backend env vars (see `Backend/.env.example`): `GEMINI_API_KEY`, `DATABASE_URL`, `JWT_SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ALLOWED_ORIGINS`. Tunables: `GEMINI_MODEL`, `MAX_RETRIES`, `GEMINI_TIMEOUT_SECONDS`, `MAX_CONCURRENT_EXTRACTIONS`, `GEMINI_RPM_CAP`, `GEMINI_DAILY_CAP_ENABLED`, `GEMINI_RPD_CAP`.

### Key conventions
- All authenticated endpoints require `Depends(get_current_user)` — never skip this. Ownership is checked via project → `user_id` (e.g. `_get_owned_blueprint`).
- Gemini errors are soft-failed (`extract_with_blueprint` returns `None`) — do not raise HTTP errors from the Gemini client. The only exception is model-not-found / exhausted 429, which raise `RuntimeError` and are recorded as page failures.
- Every extraction field is `Optional`; `confidence_score` is a reserved, always-appended meta-field — never let a user/AI field use that key.
- Blueprint `thinking` (default off) maps to `thinking_budget` per model via `GEMINI_THINKING_LIMITS` in `config.py` + `core/thinking.py` (on = -1 dynamic, off = 0 or the model's minimum, unsupported model = no `thinking_config`, "N/A"). It is snapshotted onto the job at upload — processing and retries read the job, never the blueprint.
- Extraction prompt goes in `system_instruction` (static per job); per-page `contents` hold only the page + filename, keeping a shared prefix for implicit caching.
- Gemini's `response_json_schema` rejects `default` keys — always pass schemas through `_strip_schema_keys()`.
- A blueprint with extraction history is archived (`is_active=false`), never hard-deleted.
- Indian number formatting (`fmt()`, `fmtDate()`) is in `utils/formatters.js` — use it everywhere monetary values are displayed.
- Adding a new profile field requires: backend schema update (`users` table + `PATCH /profile`), Pydantic model update, and Frontend `AuthContext` + Profile page update.
