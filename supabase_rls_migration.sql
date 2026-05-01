-- ============================================================
-- Xtract — Supabase RLS Migration
-- Run this in the Supabase SQL Editor (Project > SQL Editor)
-- ============================================================

-- Enable Row Level Security on all tables.
-- The backend connects via the service role (DATABASE_URL) which
-- bypasses RLS by default — all existing backend operations continue
-- to work unchanged. RLS prevents direct anon/user-key access.

ALTER TABLE users        ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs         ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices     ENABLE ROW LEVEL SECURITY;
ALTER TABLE line_items   ENABLE ROW LEVEL SECURITY;
ALTER TABLE gemini_quota ENABLE ROW LEVEL SECURITY;

-- No permissive policies are needed because the service role key
-- (used by the FastAPI backend via DATABASE_URL) bypasses RLS entirely.
-- Deny-by-default on all tables blocks any anon/authenticated Supabase
-- client key from reading or writing data directly.
