-- [LEGACY] This file is superseded by sql/001_canonical_schema.sql.
-- Do NOT run this file. It is kept for historical reference only.
-- See docs/supabase_rebuild_notes.md for migration instructions.

-- ================================================================
-- DEMONPULSE V8 — MIGRATION 006: session_id backfill
-- Run AFTER 001, 002, 003, 004, 005
--
-- Fixes "column session_id does not exist" errors on tables that
-- were created by earlier migrations before session_id was part
-- of their schema definitions.
--
-- Safe to re-run — all statements use ADD COLUMN IF NOT EXISTS.
-- ================================================================

-- ----------------------------------------------------------------
-- epr_data — session_id may be absent on databases created from
-- a pre-006 version of migration 001 that did not yet include it.
-- Uses TEXT (no FK) to allow flexible session identifiers including
-- non-UUID tokens written by the learning engine.
-- ----------------------------------------------------------------
ALTER TABLE epr_data ADD COLUMN IF NOT EXISTS session_id TEXT;

-- ----------------------------------------------------------------
-- aeee_adjustments — migration 001 created this table without
-- session_id; this backfill must run BEFORE any index on the
-- column is created (index is created immediately below).
-- Uses UUID FK to sessions(id) for referential integrity since
-- aeee_adjustments records structured adjustments tied to sessions.
-- ----------------------------------------------------------------
ALTER TABLE aeee_adjustments ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES sessions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_aeee_adjustments_session_id ON aeee_adjustments(session_id);
CREATE INDEX IF NOT EXISTS idx_aeee_adjustments_edge_type  ON aeee_adjustments(edge_type);

-- ----------------------------------------------------------------
-- etg_tags — same root cause as aeee_adjustments; migration 001
-- created this table without session_id.
-- ----------------------------------------------------------------
ALTER TABLE etg_tags ADD COLUMN IF NOT EXISTS session_id      UUID REFERENCES sessions(id) ON DELETE SET NULL;
ALTER TABLE etg_tags ADD COLUMN IF NOT EXISTS manual_override BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_etg_tags_bet_id     ON etg_tags(bet_id);
CREATE INDEX IF NOT EXISTS idx_etg_tags_session_id ON etg_tags(session_id);
CREATE INDEX IF NOT EXISTS idx_etg_tags_race_uid   ON etg_tags(race_uid);

-- ----------------------------------------------------------------
-- test_ mirror tables — created by migration 003 using
-- LIKE <source> INCLUDING ALL, so they also predate session_id.
-- ----------------------------------------------------------------

-- test_epr_data
ALTER TABLE test_epr_data ADD COLUMN IF NOT EXISTS session_id TEXT;

-- test_aeee_adjustments
ALTER TABLE test_aeee_adjustments ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES sessions(id) ON DELETE SET NULL;

-- test_etg_tags
ALTER TABLE test_etg_tags ADD COLUMN IF NOT EXISTS session_id      UUID REFERENCES sessions(id) ON DELETE SET NULL;
ALTER TABLE test_etg_tags ADD COLUMN IF NOT EXISTS manual_override BOOLEAN DEFAULT FALSE;

-- ================================================================
-- DONE: Migration 006 complete
-- ================================================================
