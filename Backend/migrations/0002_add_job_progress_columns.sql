-- ============================================================
-- Xtract — Migration 0002: per-page progress counters on jobs
-- Run this in the Supabase SQL Editor (Project > SQL Editor)
-- ============================================================
-- Base.metadata.create_all() only creates missing tables, never
-- alters existing ones — these columns must be added explicitly.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS total_pages INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS completed_pages INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failed_pages INTEGER DEFAULT 0;
