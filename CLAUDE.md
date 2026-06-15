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
```

## Architecture

Xtract is an AI-powered invoice extraction SaaS. Users upload PDFs, Gemini processes each page, structured invoice data is stored in Supabase, and users download multi-sheet Excel reports.

### Request lifecycle
1. User uploads PDFs via `POST /upload` → job record created, files saved to `/tmp/billscan/uploads/`
2. Background task (`batch_processor.py`) splits PDFs page-by-page, calls Gemini per page, validates results, writes invoices + line_items to DB, uploads Excel to Supabase Storage
3. Frontend polls `GET /job/{job_id}` for real-time progress
4. `GET /download/{job_id}` returns a signed URL or streams the Excel from Supabase Storage

### Backend structure (`Backend/app/`)
| Layer | Path | Responsibility |
|---|---|---|
| Config | `config.py` | Pydantic `Settings` — all env vars, no hardcoding |
| DB | `database.py` | Async SQLAlchemy engine (NullPool for PgBouncer), `init_db()` |
| Auth | `core/auth.py` | JWT (HS256, 7-day), bcrypt, `get_current_user` FastAPI dep |
| Routes | `api/` | One file per domain: auth, upload, jobs, download, review, quota |
| Extraction | `core/gemini_client.py` | Gemini 1.5 Flash, Files API upload + page-image fallback, retry/backoff |
| Processing | `core/batch_processor.py` | Page splitting, Gemini call per page, validation, Excel gen, Storage upload |
| Excel | `excel/excel_builder.py` | openpyxl multi-sheet: Summary / Line Items / GST Summary / Flagged |
| Storage | `core/storage.py` | Supabase Storage (`xtract-excel` private bucket), path = `{user_id}/{job_id}.xlsx` |
| Validation | `core/gstin_validator.py`, `tax_validator.py`, `duplicate.py` | Domain validators |
| Quota | `core/quota_manager.py` | Daily + per-minute Gemini rate-limit tracking |

DB tables: `users`, `jobs`, `invoices`, `line_items`, `gemini_quota`. SQLAlchemy creates them on startup.

### Frontend structure (`Frontend/src/`)
- **Routing**: `App.jsx` uses a single `useState(page)` + switch — no React Router. Navigate by calling `setPage('dashboard')`.
- **Auth**: `context/AuthContext.jsx` — token + user stored in `localStorage`, auto-logout on any 401 via a custom event.
- **API client**: `utils/formatters.js` — `apiFetch()` wraps `fetch`, injects `Authorization: Bearer`, dispatches `auth:logout` on 401. **API base URL is hardcoded here** (`https://xtract-nftf.onrender.com`).
- **Styles**: Single global file `src/index.css` with CSS custom properties (`--text-1`, `--red`, etc.). No CSS modules or Tailwind.
- **Pages**: Dashboard, Upload, Processing, Extractions, Detail, Profile, Login, Signup.

### Environment & deployment
- Backend: Render (`render.yaml`) — Python 3.11, reads `.env`
- Frontend: Vercel (`vercel.json`) — builds `dist/`, no SSR
- DB + Storage: Supabase (PostgreSQL via asyncpg, private Storage bucket)
- Initial DB schema: apply `supabase_rls_migration.sql` manually in Supabase console; SQLAlchemy `init_db()` creates application tables on first startup

Required backend env vars (see `.env.example`): `GEMINI_API_KEY`, `DATABASE_URL`, `JWT_SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ALLOWED_ORIGINS`.

### Key conventions
- All authenticated endpoints require `Depends(get_current_user)` — never skip this.
- Gemini errors are soft-failed (extraction returns partial data with low confidence) — do not raise HTTP errors from the Gemini client.
- Indian number formatting (`fmt()`, `fmtDate()`) is in `utils/formatters.js` — use it everywhere monetary values are displayed.
- Adding a new profile field requires: backend schema update (`users` table + `PATCH /auth/profile`), Pydantic model update, and Frontend `AuthContext` + Profile page update.
