# BillScan Pro — Backend Technical Implementation Plan
> FastAPI · Gemini Flash Vision · openpyxl · Supabase (PostgreSQL)
> Designed for: Mechanical Parts Shops, CNC Fabricators, Casting Suppliers

---

## Table of Contents
1. [System Architecture](#1-system-architecture)
2. [Project Structure](#2-project-structure)
3. [Database — Supabase](#3-database--supabase)
4. [API Endpoints](#4-api-endpoints)
5. [Core Modules — Responsibilities](#5-core-modules--responsibilities)
6. [Gemini Extraction Prompt](#6-gemini-extraction-prompt)
7. [Batch Processing Logic](#7-batch-processing-logic)
8. [Excel Builder Specification](#8-excel-builder-specification)
9. [Duplicate Detection Logic](#9-duplicate-detection-logic)
10. [Math Verification Logic](#10-math-verification-logic)
11. [Environment & Configuration](#11-environment--configuration)
12. [Dependencies](#12-dependencies)
13. [Running Locally](#13-running-locally)
14. [Deployment](#14-deployment)
15. [Implementation Order](#15-implementation-order)

---

## 1. System Architecture

```mermaid
flowchart TD
    A([User Browser / Mobile]) -->|HTTPS multipart POST\nfiles only — no labels or categories| B[FastAPI Backend]

    subgraph API["FastAPI — main.py"]
        B --> C["/upload\nReceive files only"]
        B --> D["/status/{job_id}\nPoll progress"]
        B --> E["/download/{job_id}\nStream Excel"]
        B --> F["/review/{job_id}\nGet flagged bills"]
        B --> G["/review/{job_id}/correct\nSave corrections"]
    end

    C --> H[Job Manager\njob_store.py]
    H --> I[Background Task\nbatch_processor.py]

    subgraph BATCH["Batch Processor"]
        I --> J[Split into\nbatches of 10]
        J --> K[Process Batch]
        K --> L[Gemini Flash\nVision API\nCategory auto-detected\nfrom hardcoded prompt]
        L --> M[JSON Parser\nextractor.py]
        M --> N[Math Verifier\nverifier.py]
        N --> O[Duplicate Checker\nduplicate.py]
        O --> P{Confidence\n>= 0.75?}
        P -->|Yes| Q[✅ Verified Queue]
        P -->|No| R[⚠️ Review Queue]
        K -->|65s delay| K
    end

    Q --> S[Excel Builder\nexcel_builder.py]
    R --> S
    S --> T[(Supabase\nPostgreSQL)]
    S --> U[📊 bills_extracted.xlsx\n4 sheets]

    T --> E
    U --> E

    style BATCH fill:#EFF6FF,stroke:#2563EB
    style API fill:#F0FDF4,stroke:#16A34A
```

---

```mermaid
flowchart LR
    subgraph FILES["File Flow"]
        A[Upload\nJPG/PNG/PDF\nno labels needed] --> B[Temp Storage\n/tmp/uploads/]
        B --> C[Gemini API\nbase64 encoded]
        C --> D[Extracted JSON\nwith auto-detected category]
        D --> E[Excel .xlsx]
        E --> F[/tmp/outputs/]
        F --> G[Download\nby user]
        G --> H[Auto-delete\nafter 2hrs]
    end

    style FILES fill:#FFF7ED,stroke:#EA580C
```

---

```mermaid
flowchart TD
    subgraph EXTRACT["Gemini Extraction Flow — Per File"]
        A[Image Bytes] --> B[Build Prompt\nCategories hardcoded inside prompt\nFilename passed for context only]
        B --> C[Gemini Flash\ngoogle-generativeai SDK]
        C --> D{Response\nvalid JSON?}
        D -->|Yes| E[Parse Fields]
        D -->|No| F[Retry Once\nwith stricter prompt]
        F --> G{Valid?}
        G -->|Yes| E
        G -->|No| H[Mark as ERROR\ninclude in Flagged sheet]
        E --> I[Math Check\nsubtotal + tax - discount == total]
        I --> J{Match?}
        J -->|Yes| K[confidence boost +0.05]
        J -->|No| L[Flag MATH_MISMATCH\nconfidence -0.20]
        K --> M[Duplicate Check\ninvoice_no + supplier + amount]
        L --> M
        M --> N{Duplicate?}
        N -->|Yes| O[Flag DUPLICATE\nconfidence = 0.0]
        N -->|No| P[Final Result → Supabase]
    end

    style EXTRACT fill:#F5F3FF,stroke:#7C3AED
```

---

```mermaid
flowchart TD
    subgraph EXCEL["Excel Builder — 4 Sheet Structure"]
        A[All Results from Supabase] --> B[Sheet 1: Line Items\nOne row per product\nPRIMARY SHEET]
        A --> C[Sheet 2: Invoice Summary\nOne row per invoice]
        A --> D[Sheet 3: GST Summary\nIGST / CGST / SGST per vendor]
        A --> E[Sheet 4: Flagged Bills\nDuplicates · Low confidence · Errors]
        B --> F[📥 bills_extracted.xlsx]
        C --> F
        D --> F
        E --> F
    end

    style EXCEL fill:#ECFDF5,stroke:#059669
```

---

## 2. Project Structure

```
billscan-backend/
│
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, routes, CORS, startup
│   ├── config.py                # Settings, env vars, constants
│   ├── database.py              # SQLAlchemy models + Supabase session
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes_upload.py     # POST /upload — files only, no labels
│   │   ├── routes_status.py     # GET /status/{job_id}
│   │   ├── routes_download.py   # GET /download/{job_id}
│   │   └── routes_review.py     # GET/POST /review/{job_id}
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── job_store.py         # In-memory job state manager
│   │   ├── batch_processor.py   # Orchestrates batches, rate limiting
│   │   ├── gemini_client.py     # Gemini Flash API wrapper + prompt
│   │   ├── extractor.py         # JSON parsing, field normalization
│   │   ├── verifier.py          # Math verification logic
│   │   ├── duplicate.py         # Duplicate detection
│   │   └── file_cleaner.py      # Auto-delete uploaded files
│   │
│   ├── excel/
│   │   ├── __init__.py
│   │   ├── excel_builder.py     # Main Excel orchestrator
│   │   ├── sheet_line_items.py  # Sheet 1 builder
│   │   ├── sheet_summary.py     # Sheet 2 builder
│   │   ├── sheet_gst.py         # Sheet 3 builder
│   │   └── sheet_flagged.py     # Sheet 4 builder
│   │
│   └── models/
│       ├── __init__.py
│       ├── job.py               # Job Pydantic models
│       ├── bill.py              # Bill + LineItem Pydantic models
│       └── extraction.py        # Gemini response models
│
├── tests/
│   ├── test_gemini_client.py
│   ├── test_verifier.py
│   ├── test_duplicate.py
│   ├── test_excel_builder.py
│   └── sample_bills/            # Test PDFs/images
│
├── .env                         # API keys (never commit)
├── .env.example                 # Template
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## 3. Database — Supabase

### Why Supabase Over SQLite

Supabase is used as the database from day one — not SQLite. The key reason is that Render.com's free tier has **ephemeral disk storage**, meaning any SQLite file gets wiped on every server restart or redeploy. Supabase is external and fully persistent.

| Factor | SQLite | Supabase Free |
|---|---|---|
| Survives Render restart | ❌ Wiped on redeploy | ✅ Always persistent |
| Setup time | Zero | ~10 minutes |
| Data browsable via UI | ❌ | ✅ Built-in table editor |
| Backup | ❌ Manual | ✅ Automatic 7 days |
| Scale to production | Hard migration | ✅ Same Postgres, just upgrade plan |
| Cost | Free | Free (500 MB, unlimited rows) |

### Supabase Free Tier Limits

| Resource | Free Limit | Sufficient for Launch? |
|---|---|---|
| Database size | 500 MB | ✅ Handles ~2M+ invoice rows |
| Rows | Unlimited | ✅ |
| API requests | Unlimited | ✅ |
| Bandwidth | 5 GB/month | ✅ |
| Projects | 2 | ✅ |
| Automatic backups | 7 days | ✅ |
| Pauses after inactivity | 7 days idle | ⚠️ Need a keepalive ping every 4 days |

### Supabase Setup — 10 Minutes

```
1. Go to supabase.com → Create new project
2. Set a strong DB password → save it securely
3. Go to Settings → Database → copy "Connection string (URI)"
4. Paste into .env as DATABASE_URL
5. Run FastAPI app — SQLAlchemy auto-creates all tables on startup
6. Browse extracted data live: Supabase Dashboard → Table Editor → invoices
```

### Database Schema

```mermaid
erDiagram
    JOB {
        uuid id PK
        string status
        int total_files
        int processed_files
        int verified_count
        int flagged_count
        int error_count
        timestamp created_at
        timestamp completed_at
        string excel_path
    }

    INVOICE {
        uuid id PK
        uuid job_id FK
        string source_filename
        string category
        string invoice_number
        date invoice_date
        string challan_number
        string document_type
        string supplier_name
        string supplier_gstin
        string supplier_state
        string supplier_address
        string supplier_email
        string supplier_phone
        string supplier_bank
        string supplier_account_number
        string supplier_ifsc
        string buyer_name
        string buyer_gstin
        string place_of_supply
        string destination
        string transport_name
        string lr_number
        string vehicle_number
        string eway_bill_number
        string irn_number
        float assessable_value
        string tax_type
        float igst_percent
        float igst_amount
        float cgst_percent
        float cgst_amount
        float sgst_percent
        float sgst_amount
        float pf_charges
        float round_off
        float grand_total
        float total_weight_kg
        int total_qty
        float confidence_score
        string status
        string flags
        timestamp extracted_at
    }

    LINE_ITEM {
        uuid id PK
        uuid invoice_id FK
        int sr_no
        string die_number
        string po_number
        string description
        string hsn_sac_code
        string grade
        float quantity
        float rate
        float amount
    }

    JOB ||--o{ INVOICE : "contains"
    INVOICE ||--o{ LINE_ITEM : "has"
```

> **Note:** `bill_label` is removed from the schema entirely. Users upload files only — no manual labels. `source_filename` serves as the identifier. `category` is auto-detected by Gemini using the hardcoded category list in the prompt.

---

## 4. API Endpoints

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend
    participant API as FastAPI
    participant BP as BatchProcessor
    participant GM as Gemini API
    participant DB as Supabase

    User->>FE: Upload 12 bills — files only, no labels
    FE->>API: POST /upload (multipart — files[] only)
    API->>DB: Create Job record in Supabase
    API->>BP: Start background task
    API-->>FE: { job_id, total_files: 12 }

    loop Every 3 seconds
        FE->>API: GET /status/{job_id}
        API-->>FE: { processed: 4, total: 12, status: "processing" }
    end

    loop Batch 1 - files 1 to 10
        BP->>GM: Extract file — prompt includes hardcoded category list
        GM-->>BP: JSON with auto-detected category + all fields
        BP->>DB: Save invoice + line items to Supabase
    end

    Note over BP: Wait 65 seconds

    loop Batch 2 - files 11 to 12
        BP->>GM: Extract file
        GM-->>BP: JSON fields
        BP->>DB: Save to Supabase
    end

    BP->>DB: Update job status to done
    BP->>BP: Build Excel from Supabase records
    FE->>API: GET /status/{job_id}
    API-->>FE: { status: "done", flagged: 1 }
    FE->>API: GET /review/{job_id}
    API-->>FE: flagged bills list
    User->>FE: Correct fields, approve
    FE->>API: POST /review/{job_id}/correct
    FE->>API: GET /download/{job_id}
    API-->>FE: Stream bills_extracted.xlsx
```

### Endpoint Reference

| Method | Path | Description | Request | Response |
|--------|------|-------------|---------|----------|
| `POST` | `/upload` | Upload files, start job | multipart: `files[]` only — no labels | `{ job_id, total_files }` |
| `GET` | `/status/{job_id}` | Poll job progress | — | `{ status, processed, total, flagged, errors }` |
| `GET` | `/review/{job_id}` | Get bills needing review | — | `[ { invoice, fields, flags } ]` |
| `POST` | `/review/{job_id}/correct` | Save user corrections | `{ invoice_id, corrected_fields }` | `{ ok }` |
| `GET` | `/download/{job_id}` | Stream Excel file | — | `.xlsx` binary stream |
| `GET` | `/health` | Health check | — | `{ ok, version }` |
| `DELETE` | `/job/{job_id}` | Delete job + files | — | `{ deleted }` |

---

## 5. Core Modules — Responsibilities

Each module has a single focused responsibility. The table below defines what each file must do.

| Module | File | Responsibility |
|---|---|---|
| **App entry** | `main.py` | Register all routers, add CORS middleware, run startup hooks (init Supabase DB connection, start file cleaner scheduler) |
| **Config** | `config.py` | Load all env vars via Pydantic Settings. Single source of truth for BATCH_SIZE, delays, thresholds, Supabase URL, Gemini key |
| **Database** | `database.py` | SQLAlchemy engine pointed at Supabase PostgreSQL. Define ORM models for Job, Invoice, LineItem. `init_db()` creates tables if not exist on startup |
| **Job store** | `core/job_store.py` | In-memory dict keyed by `job_id`. Tracks real-time progress (processed count, current batch number, next batch countdown). Supabase stores final persistent data; job store handles live polling only |
| **Batch processor** | `core/batch_processor.py` | Split file list into batches of 10. For each batch: run all 10 extractions concurrently with `asyncio.gather`. Sleep 65s between batches. After all batches complete: trigger Excel build, update Supabase job record to `done` |
| **Gemini client** | `core/gemini_client.py` | Accept image bytes + filename. Build prompt with hardcoded category list. Call Gemini Flash Vision API. Strip markdown fences from response. Parse JSON. Retry up to 3 times on failure or HTTP 429. Return `ExtractedBill` model |
| **Extractor** | `core/extractor.py` | Normalize raw Gemini JSON into typed `ExtractedBill` Pydantic model. Handle null fields, type coercion, date format standardization to DD/MM/YYYY |
| **Verifier** | `core/verifier.py` | Independently recalculate: line items sum → assessable value, assessable value × tax % → tax amount, assessable + tax + round_off → grand total. Flag mismatches, adjust confidence score accordingly |
| **Duplicate checker** | `core/duplicate.py` | Compare each new bill against all already-processed bills in the same job. Match on: invoice_number + supplier_name + grand_total. Flag as DUPLICATE, set confidence to 0.0 |
| **File cleaner** | `core/file_cleaner.py` | APScheduler job running every 30 minutes. Delete uploaded files from `/tmp/uploads/` older than 2 hours. Delete Excel outputs older than 24 hours. Also runs Supabase keepalive `SELECT 1` every 4 days to prevent free tier project pause |
| **Excel builder** | `excel/excel_builder.py` | Orchestrator: fetch all invoices + line items for a job from Supabase. Call all 4 sheet builders. Save to `/tmp/outputs/{job_id}.xlsx`. Return file path |
| **Sheet 1** | `excel/sheet_line_items.py` | One row per line item. ~40 columns covering source, invoice header, supplier, buyer, logistics, line item details, invoice totals, quality flags. Freeze header row. Auto-filter on all columns. Color code rows by status. PRIMARY sheet |
| **Sheet 2** | `excel/sheet_summary.py` | One row per invoice. Totals, GST summary, weight, qty. Quick overview for the business owner |
| **Sheet 3** | `excel/sheet_gst.py` | One row per invoice. Full GSTIN details, IGST/CGST/SGST breakdown, ITC claimable column. Ready to hand directly to CA for filing |
| **Sheet 4** | `excel/sheet_flagged.py` | One row per flagged or errored invoice. Flag reason, confidence score, original filename. Owner knows exactly which file to re-photograph and re-upload |
| **Bill model** | `models/bill.py` | Pydantic models: `LineItem`, `ExtractedBill`. All fields typed. No `bill_label` field. `category` is a string auto-filled by Gemini. Status enum: VERIFIED / NEEDS_REVIEW / DUPLICATE / ERROR |
| **Job model** | `models/job.py` | Pydantic models for Job create/response. Upload response schema |
| **Upload route** | `api/routes_upload.py` | Accept `files[]` only — no labels, no categories from user. Validate file types and sizes. Save to `/tmp/uploads/`. Create Supabase Job record. Fire background batch processor task |
| **Status route** | `api/routes_status.py` | Read from in-memory job store for live in-progress status. Fall back to Supabase query for completed or historical jobs |
| **Download route** | `api/routes_download.py` | Check job is `done` in Supabase. Stream `.xlsx` file from output path as `StreamingResponse` |
| **Review route** | `api/routes_review.py` | GET: return all NEEDS_REVIEW and DUPLICATE invoices for a job from Supabase. POST: accept corrected fields from user, update Supabase invoice record, mark as VERIFIED |

---

## 6. Gemini Extraction Prompt

The prompt does all the work that the user used to do manually. Categories are hardcoded inside the prompt — the user uploads files with zero metadata. Gemini reads the document and picks the right category automatically.

### Hardcoded Category List in Prompt

```
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
```

### Prompt Design Principles

The following choices are baked into the prompt for accurate extraction from Indian manufacturing invoices:

1. Explicitly name Indian GST field labels — IGST, CGST, SGST, HSN/SAC code, IRN number, E-Way Bill number
2. Name the specific column headers common in casting and CNC invoices — Die No., P.O. No., Grade
3. Force all monetary values to be returned as numbers (float), never strings
4. Ask Gemini to self-assess and return a `confidence_score` between 0.0 and 1.0
5. Enforce DD/MM/YYYY date format — the Indian standard
6. Auto-detect tax type: IGST if supplier and buyer are in different states, CGST+SGST if same state
7. Explicit instruction: extract ALL line items — never skip any rows from the items table
8. Pick category from the hardcoded list above based on document content — not from filename
9. Filename is passed as context only — never used to guess invoice fields

---

## 7. Batch Processing Logic

```mermaid
flowchart TD
    A[N Files Uploaded] --> B[Split into batches of 10]
    B --> C[Batch 1 — 10 files concurrently]
    C --> D[Save results to Supabase]
    D --> E{More batches?}
    E -->|Yes| F[Wait 65 seconds\nrate limit buffer]
    F --> G[Next Batch — 10 files concurrently]
    G --> D
    E -->|No| H[Build Excel from Supabase records]
    H --> I[Update Job status = done in Supabase]

    style F fill:#FFF7ED,stroke:#EA580C
```

### Rate Limit Math

| Files Uploaded | Batches | Wait Time | Total Time |
|---|---|---|---|
| 10 files | 1 batch | 0s | ~45 seconds |
| 50 files | 5 batches | 4 × 65s | ~6 minutes |
| 100 files | 10 batches | 9 × 65s | ~11 minutes |
| 150 files | 15 batches | 14 × 65s | ~17 minutes |

- Gemini free tier limit: **15 RPM**
- Batch size: **10** — safe headroom below 15
- Delay: **65 seconds** — safe buffer above 60s rate window reset
- On HTTP 429 from Gemini: wait 90 seconds and retry, up to 3 times per file

---

## 8. Excel Builder Specification

| Sheet | Row Unit | Example Row Count | Primary User |
|---|---|---|---|
| 📋 Line Items | 1 row per line item | 12 rows for 2 invoices (4+8 items) | Owner, accountant |
| 📊 Invoice Summary | 1 row per invoice | 2 rows for 2 invoices | Owner overview |
| 🧾 GST Summary | 1 row per invoice | 2 rows | CA, tax filing |
| ⚠️ Flagged Bills | 1 row per flagged invoice | Varies | Owner review |

### Sheet 1 — Line Items Column Groups (~40 columns)

| Group | Columns |
|---|---|
| Source | Source File, Category (auto-detected) |
| Invoice Header | Invoice No, Invoice Date, Challan No, Document Type |
| Supplier | Supplier Name, Supplier GSTIN, Supplier State, Address, Email, Phone, Bank, A/C No, IFSC |
| Buyer | Buyer Name, Buyer GSTIN, Place of Supply, Destination |
| Logistics | Transport, LR No, Vehicle No, E-Way Bill No, IRN No, Total Weight (kg) |
| Line Item | Sr No, Die No, PO No, Description, HSN/SAC, Grade, Qty, Rate (₹), Line Amount (₹) |
| Invoice Totals | Assessable Value, IGST %, IGST Amt, CGST %, CGST Amt, SGST %, SGST Amt, P&F Charges, Round Off, Grand Total |
| Quality | Confidence Score, Status, Flags |

### Row Color Coding

- 🟢 Green — Verified, confidence ≥ 0.85, no flags
- 🟡 Yellow — Needs review, has flags or low confidence
- 🔴 Red — Duplicate detected or extraction error
- ⬜ White — Verified, standard confidence

---

## 9. Duplicate Detection Logic

**Match criteria — ALL three must match to flag as duplicate:**

1. `invoice_number` — case-insensitive, whitespace stripped
2. `supplier_name` — case-insensitive, whitespace stripped
3. `grand_total` — within ₹1.00 tolerance

**Scope:**
- Phase 1: within the current upload session only (in-memory list comparison during batch processing)
- Phase 2: compare against all historical invoices stored in Supabase for the same buyer GSTIN

**On duplicate detected:**
- Add flag: `DUPLICATE of {original_filename} (Invoice: {invoice_no})`
- Set `confidence_score` to 0.0
- Set `status` to DUPLICATE
- Still write to Excel Sheet 4 (Flagged) so the owner can see it and decide

---

## 10. Math Verification Logic

For each extracted bill, independently recompute all totals and compare against what Gemini extracted:

```
line_total          = sum of all line_item.amount values
expected_assessable = line_total + pf_charges

expected_igst       = assessable_value × (igst_percent / 100)
expected_cgst       = assessable_value × (cgst_percent / 100)
expected_sgst       = assessable_value × (sgst_percent / 100)
total_tax           = igst_amount + cgst_amount + sgst_amount

expected_grand      = assessable_value + total_tax + round_off

Tolerance: ₹2.00 allowed on all comparisons

Flags raised:
  LINE_ITEMS_MISMATCH   → |expected_assessable - assessable_value| > ₹2
  GST_CALC_MISMATCH     → |expected_igst - igst_amount| > ₹2
  GRAND_TOTAL_MISMATCH  → |expected_grand - grand_total| > ₹2

Confidence score adjustments:
  LINE_ITEMS_MISMATCH   → confidence - 0.15
  GST_CALC_MISMATCH     → confidence - 0.10
  GRAND_TOTAL_MISMATCH  → confidence - 0.20
  No mismatches found   → confidence + 0.05 (reward boost)
```

---

## 11. Environment & Configuration

### `.env.example`

```env
# ── Gemini API ──────────────────────────────────────────────────
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-1.5-flash

# ── Supabase / PostgreSQL ───────────────────────────────────────
# Get from: Supabase Dashboard → Settings → Database → Connection string (URI)
DATABASE_URL=postgresql://postgres:[YOUR-PASSWORD]@db.[YOUR-PROJECT-REF].supabase.co:5432/postgres

# ── Batch Processing ────────────────────────────────────────────
BATCH_SIZE=10
BATCH_DELAY_SECONDS=65
MAX_RETRIES=3
RETRY_WAIT_SECONDS=90
CONFIDENCE_THRESHOLD=0.75

# ── File Handling ───────────────────────────────────────────────
UPLOAD_DIR=/tmp/billscan/uploads
OUTPUT_DIR=/tmp/billscan/outputs
MAX_FILE_SIZE_MB=10
AUTO_DELETE_HOURS=2
ALLOWED_EXTENSIONS=["jpg","jpeg","png","pdf","heic"]

# ── Server ──────────────────────────────────────────────────────
ALLOWED_ORIGINS=["http://localhost:3000","https://yourdomain.com"]
```

---

## 12. Dependencies

### `requirements.txt`

```
# ── Web framework ───────────────────────────────────────────────
fastapi==0.111.0
uvicorn[standard]==0.29.0
python-multipart==0.0.9        # Multipart file uploads

# ── Data validation ─────────────────────────────────────────────
pydantic==2.7.0
pydantic-settings==2.2.1

# ── AI extraction ───────────────────────────────────────────────
google-generativeai==0.5.4     # Gemini Flash Vision API

# ── Excel generation ────────────────────────────────────────────
openpyxl==3.1.2

# ── Database — Supabase / PostgreSQL ────────────────────────────
sqlalchemy==2.0.29             # ORM — works identically with PostgreSQL
asyncpg==0.29.0                # Async PostgreSQL driver (primary)
psycopg2-binary==2.9.9         # Sync PostgreSQL driver (migrations, fallback)

# ── Utilities ───────────────────────────────────────────────────
python-dotenv==1.0.1
httpx==0.27.0                  # Async HTTP client
pillow==10.3.0                 # Image quality pre-check before sending to Gemini
apscheduler==3.10.4            # File cleanup + Supabase keepalive scheduler

# ── Dev / Testing ───────────────────────────────────────────────
pytest==8.2.0
pytest-asyncio==0.23.6
```

---

## 13. Running Locally

```bash
# 1. Clone and setup
git clone https://github.com/yourname/billscan-backend
cd billscan-backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env:
#   - Add GEMINI_API_KEY from Google AI Studio
#   - Add DATABASE_URL from Supabase project settings

# 4. Run development server
uvicorn app.main:app --reload --port 8000
# SQLAlchemy will auto-create all tables in Supabase on first run

# 5. Test with real bills — files only, no labels needed
curl -X POST http://localhost:8000/upload \
  -F "files=@rainbow_technocast_gt362.pdf" \
  -F "files=@rainbow_technocast_gt365.pdf"

# 6. Check interactive API docs
open http://localhost:8000/docs

# 7. View extracted data live in Supabase
# Supabase Dashboard → Table Editor → invoices → view all rows
# Supabase Dashboard → Table Editor → line_items → view all rows
```

---

## 14. Deployment

### `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /tmp/billscan/uploads /tmp/billscan/outputs

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `render.yaml` — Render.com Deployment

```yaml
services:
  - type: web
    name: billscan-api
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: GEMINI_API_KEY
        sync: false                  # Set manually in Render dashboard
      - key: DATABASE_URL
        sync: false                  # Paste Supabase connection string
      - key: ALLOWED_ORIGINS
        value: '["https://your-frontend.vercel.app"]'
```

### Supabase Free Tier — Keep-Alive Strategy

Supabase free tier pauses a project after **7 days of inactivity**. The `file_cleaner.py` scheduler includes a lightweight keepalive that runs a `SELECT 1` query against Supabase every 4 days. This costs zero compute and prevents the project from pausing during slow periods.

### Infrastructure Cost Summary

| Service | Plan | Monthly Cost |
|---|---|---|
| FastAPI backend | Render.com free | ₹0 |
| Database | Supabase free (500 MB, unlimited rows) | ₹0 |
| Temp file storage | Render ephemeral /tmp | ₹0 |
| Gemini Flash Vision API | Google free tier (1,500 bills/day) | ₹0 |
| Email delivery | Resend.com free (100/day) | ₹0 |
| Domain + SSL | Cloudflare | ~₹70/month (₹850/year) |
| **Total** | | **~₹70/month** |

---

## 15. Implementation Order

```mermaid
gantt
    title BillScan Backend — Build Order
    dateFormat  YYYY-MM-DD
    section Week 1 — Foundation
    Project setup + config.py           :a1, 2025-05-01, 1d
    Supabase project + database.py      :a2, after a1, 1d
    Pydantic models — bill.py job.py    :a3, after a2, 1d
    Gemini client + hardcoded prompt    :a4, after a3, 2d

    section Week 2 — Core Pipeline
    Math verifier                       :b1, 2025-05-08, 1d
    Duplicate checker                   :b2, after b1, 1d
    Batch processor                     :b3, after b2, 2d
    Job store + status tracking         :b4, after b3, 1d

    section Week 3 — Excel
    Excel builder orchestrator          :c1, 2025-05-15, 1d
    Sheet 1 — Line Items                :c2, after c1, 2d
    Sheet 2 — Invoice Summary           :c3, after c2, 1d
    Sheet 3 — GST Summary               :c4, after c3, 1d

    section Week 4 — API and Polish
    Upload endpoint — files only        :d1, 2025-05-22, 1d
    Status and download endpoints       :d2, after d1, 1d
    Review queue endpoints              :d3, after d2, 1d
    File cleanup + Supabase keepalive   :d4, after d3, 1d
    End-to-end test with real bills     :d5, after d4, 2d
```

### Priority Order for First Working Version

| # | File | Why First |
|---|------|-----------|
| 1 | `config.py` | Every other module reads settings from here |
| 2 | `database.py` | Supabase connection + ORM table models needed early |
| 3 | `models/bill.py` | Data contract for the whole system — no `bill_label` field |
| 4 | `core/gemini_client.py` | Core value — if extraction doesn't work, the product doesn't exist |
| 5 | `core/verifier.py` | Catch bad extractions before they reach Supabase |
| 6 | `core/duplicate.py` | Simple logic, very high value to users |
| 7 | `core/batch_processor.py` | Orchestrates all the above into a working pipeline |
| 8 | `excel/sheet_line_items.py` | The primary user deliverable |
| 9 | `excel/excel_builder.py` | Ties all 4 sheets together into one file |
| 10 | `api/routes_upload.py` | First real endpoint — accepts files only |
| 11 | `api/routes_status.py` | Frontend needs this to poll progress |
| 12 | `api/routes_download.py` | Delivers the Excel to the user |
| 13 | `main.py` | Wire all routers together, start the server |

---

> **First milestone:** Upload one PDF → Gemini extracts all fields and auto-detects category → data saved to Supabase → valid Excel downloaded with all line items in the correct rows.
> Everything else is built on top of that working core.
