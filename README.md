# Xtract

**AI-powered, blueprint-driven document extraction.** Describe what you want in plain English, let Gemini design the schema, run it across hundreds of pages, and download clean multi-sheet Excel — no templates, no code.

Works for any document type: invoices, marksheets, bank statements, purchase orders, forms.

---

## How it works

1. **Project** — create a workspace with domain context (e.g. *"Indian GST invoices from vendors"*).
2. **Blueprint** — define the fields to extract, manually or by prompt: *"extract student details and every subject's grade"* → Gemini proposes field keys, types, and table columns.
3. **Test** — dry-run the blueprint on one sample file (even unsaved edits) before committing.
4. **Upload** — batch-upload PDFs/images. PDFs are split page-by-page and extracted in parallel with live progress.
5. **Review & export** — inspect per-page confidence, correct values, retry failed pages, download Excel. Repeating data (line items, grades) gets its own sheet, one row per item.

## AI features

| Feature | Implementation |
|---|---|
| **Prompt → schema** | Text-only Gemini call returns a strict JSON array of typed fields (`string`, `number`, `date`, `currency`, `boolean`, `enum`, `table`) |
| **Dynamic structured output** | Each blueprint is compiled at runtime into a Pydantic model (`create_model`, nested `List[Row]` for tables) → JSON Schema → enforced via Gemini `response_json_schema` |
| **Context-aware prompts** | Project context + field descriptions, examples, required/enum/column hints + global rules; optional per-blueprint prompt override |
| **Anti-hallucination** | "Return null, never guess"; every field `Optional`; model-reported `confidence_score` per page |
| **Multimodal input** | Native PDF via Gemini Files API (polled until `ACTIVE`), automatic fallback to 150-DPI page images; direct image input |
| **Validation chain** | Structured parse → Pydantic validation → tolerant JSON parse → retry with stricter prompt |
| **Resilience** | Separate backoff ladders for 503 (5/15/30/60s) and 429 (30/60/90/120s), timeout retries, soft-fail per page |
| **Quota control** | Atomic per-minute + daily limits in Postgres with `SELECT … FOR UPDATE` row locking |
| **Concurrency** | Page-level `asyncio.gather` bounded by a semaphore; one bad page never cancels its siblings |
| **Human in the loop** | Inline corrections, retry failed pages only, crash-recovery sweep on startup |
| **Real-time UX** | Server-Sent Events stream per-page and per-stage progress |

## Architecture

```
React (Vite) ──HTTPS/JWT──▶ FastAPI ──▶ Gemini 2.5 Flash
     ▲  SSE progress           │
     └─────────────────────────┤──▶ Supabase Postgres (projects, blueprints, jobs, results, quota)
                               └──▶ Supabase Storage (private Excel reports)
```

**Domain model**

```
Project ──< Blueprint ──< BlueprintField
Job (project, blueprint) ──< JobPage ──< ExtractionResult (JSONB data + confidence)
```

### Project structure

```
Backend/app/
  api/            routes: auth, projects, blueprints, upload, status (SSE), jobs, results, download, quota
  core/
    gemini_client.py     field generation, dynamic schema, prompt builder, extraction + retries
    batch_processor.py   page splitting, concurrent extraction, retry, finalize
    quota_manager.py     atomic Gemini rate limiting
    startup_recovery.py  crash recovery
    storage.py           Supabase Storage
    job_store.py         live progress events
  excel/excel_builder.py multi-sheet Excel export
  database.py            async SQLAlchemy models
Frontend/src/
  pages/          Dashboard, Projects, ProjectDetail, BlueprintBuilder, Upload, Processing, Results, ResultDetail, Profile
  context/        AuthContext
  utils/          apiFetch, formatters
```

## Tech stack

**Backend:** Python 3.11 · FastAPI · Pydantic v2 · SQLAlchemy (async) + asyncpg · google-genai · PyMuPDF · openpyxl · APScheduler · JWT + bcrypt
**AI:** Google Gemini 2.5 Flash (structured JSON output, Files API, multimodal)
**Frontend:** React · Vite · Vitest
**Infra:** Supabase (Postgres + Storage) · Render (API) · Vercel (web)

## Getting started

### Backend
```bash
cd Backend
pip install -r requirements.txt
cp .env.example .env        # fill in GEMINI_API_KEY, DATABASE_URL, JWT_SECRET_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, ALLOWED_ORIGINS
python -m uvicorn app.main:app --reload    # http://localhost:8000/docs
python -m pytest tests/
```

### Frontend
```bash
cd Frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev    # http://localhost:5173
npm test
```

### Database
Apply `supabase_rls_migration.sql` in the Supabase SQL editor and create a private Storage bucket named `xtract-excel`. Application tables are created automatically on first backend startup.

## Deployment

- **Backend** → Render (`Backend/render.yaml`)
- **Frontend** → Vercel (`vercel.json`), set `VITE_API_URL`
