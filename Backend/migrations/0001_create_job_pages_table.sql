-- ============================================================
-- Xtract — Migration 0001: job_pages table
-- Run this in the Supabase SQL Editor (Project > SQL Editor)
-- ============================================================
-- Tracks per-page extraction status so progress survives page
-- refresh and worker restarts (previously only in job_store.py's
-- in-memory dict).

CREATE TABLE IF NOT EXISTS job_pages (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id         UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    page_index     INTEGER NOT NULL,        -- 0-based position in the job's flattened page list
    filename       VARCHAR(512) NOT NULL,   -- original uploaded filename
    page_label     VARCHAR(512) NOT NULL,   -- e.g. "invoice_p2.pdf" — matches PageTask.label
    status         VARCHAR(20) NOT NULL DEFAULT 'queued',  -- queued | processing | done | failed
    error_message  TEXT,
    updated_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_job_pages_job_page UNIQUE (job_id, page_index)
);

CREATE INDEX IF NOT EXISTS idx_job_pages_job_id ON job_pages(job_id);
