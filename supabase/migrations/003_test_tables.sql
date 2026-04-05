-- [LEGACY] This file is superseded by sql/001_canonical_schema.sql.
-- Do NOT run this file. It is kept for historical reference only.
-- See docs/supabase_rebuild_notes.md for migration instructions.

-- ================================================================
-- DEMONPULSE V8 — MIGRATION 003: Test Environment Tables
-- Creates test_ prefixed mirrors of all mutable tables.
-- Run AFTER 001 and 002.
--
-- Used when DP_ENV=TEST and no dedicated SUPABASE_TEST_URL is set.
-- Production tables are never touched by test-mode operations.
-- ================================================================

-- ----------------------------------------------------------------
-- test_today_races
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_today_races (
    LIKE today_races INCLUDING ALL
);
ALTER TABLE test_today_races ALTER COLUMN race_uid DROP NOT NULL;

-- ----------------------------------------------------------------
-- test_today_runners
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_today_runners (
    LIKE today_runners INCLUDING ALL
);

-- ----------------------------------------------------------------
-- test_bet_log
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_bet_log (
    LIKE bet_log INCLUDING ALL
);

-- ----------------------------------------------------------------
-- test_signals
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_signals (
    LIKE signals INCLUDING ALL
);

-- ----------------------------------------------------------------
-- test_system_state
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_system_state (
    LIKE system_state INCLUDING ALL
);
-- Seed a default row so the app starts cleanly in test mode
INSERT INTO test_system_state (id, bankroll, current_pl, bank_mode, active_code,
    posture, sys_state, variance, session_type, time_anchor)
VALUES (1, 10000, 0, 'STANDARD', 'GREYHOUND', 'NORMAL', 'STABLE', 'NORMAL', 'Test Session', '')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------
-- test_chat_history
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_chat_history (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT,
    role TEXT,
    content TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- test_sessions
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_sessions (
    LIKE sessions INCLUDING ALL
);

-- ----------------------------------------------------------------
-- test_training_logs
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_training_logs (
    LIKE training_logs INCLUDING ALL
);

-- ----------------------------------------------------------------
-- test_etg_tags
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_etg_tags (
    LIKE etg_tags INCLUDING ALL
);

-- ----------------------------------------------------------------
-- test_activity_log
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_activity_log (
    LIKE activity_log INCLUDING ALL
);

-- ----------------------------------------------------------------
-- test_exotic_suggestions
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_exotic_suggestions (
    LIKE exotic_suggestions INCLUDING ALL
);

-- ----------------------------------------------------------------
-- INDEXES on test tables (match production indexes)
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_test_today_races_date ON test_today_races(date);
CREATE INDEX IF NOT EXISTS idx_test_today_runners_race_uid ON test_today_runners(race_uid);
CREATE INDEX IF NOT EXISTS idx_test_bet_log_date ON test_bet_log(date);
CREATE INDEX IF NOT EXISTS idx_test_signals_race_uid ON test_signals(race_uid);
CREATE INDEX IF NOT EXISTS idx_test_chat_history_session ON test_chat_history(session_id);

-- ----------------------------------------------------------------
-- NOTICE
-- ----------------------------------------------------------------
-- users and audit_log are intentionally NOT duplicated.
-- Test mode still uses the real users table (same admins)
-- and the real audit_log (so test actions are traceable).
-- ================================================================
-- ----------------------------------------------------------------
-- test_epr_log (W-03: was missing from original 003)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_epr_log (
    LIKE epr_log INCLUDING ALL
);

-- ----------------------------------------------------------------
-- test_aeee_suggestions (W-03: was missing from original 003)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_aeee_suggestions (
    LIKE aeee_suggestions INCLUDING ALL
);

-- ----------------------------------------------------------------
-- test_user_accounts / test_user_permissions (from migration 004)
-- Mirrored here so TEST mode learning/user data stays isolated
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_user_accounts (
    LIKE user_accounts INCLUDING ALL
);
CREATE TABLE IF NOT EXISTS test_user_permissions (
    LIKE user_permissions INCLUDING ALL
);

-- ----------------------------------------------------------------
-- FIXES: align mirror tables with actual schema table names (CF-03/CF-04)
-- The previous W-03 fix created test_epr_log and test_aeee_suggestions
-- which are the wrong names. The actual tables are epr_data and aeee_adjustments.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_epr_data (
    LIKE epr_data INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS test_aeee_adjustments (
    LIKE aeee_adjustments INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS test_pass_log (
    LIKE pass_log INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS test_source_log (
    LIKE source_log INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS test_user_sessions (
    LIKE user_sessions INCLUDING ALL
);

CREATE TABLE IF NOT EXISTS test_user_activity (
    LIKE user_activity INCLUDING ALL
);

-- Note: test_epr_log and test_aeee_suggestions (added previously) remain
-- but are now unused since the routing was corrected to epr_data/aeee_adjustments.

-- ================================================================
-- FIXES appended (CF-03/CF-04/W-06/W-07): align names with real tables
-- ================================================================

-- test_epr_data: correct name (schema table is epr_data, not epr_log)
CREATE TABLE IF NOT EXISTS test_epr_data (
    LIKE epr_data INCLUDING ALL
);

-- test_aeee_adjustments: correct name (schema table is aeee_adjustments, not aeee_suggestions)
CREATE TABLE IF NOT EXISTS test_aeee_adjustments (
    LIKE aeee_adjustments INCLUDING ALL
);

-- test_pass_log: learning_engine writes pass_log, needs TEST isolation
CREATE TABLE IF NOT EXISTS test_pass_log (
    LIKE pass_log INCLUDING ALL
);

-- test_source_log: data_engine writes source_log, needs TEST isolation
CREATE TABLE IF NOT EXISTS test_source_log (
    LIKE source_log INCLUDING ALL
);

-- test_user_sessions: prevent TEST logins contaminating live sessions table
CREATE TABLE IF NOT EXISTS test_user_sessions (
    LIKE user_sessions INCLUDING ALL
);

-- test_user_activity: prevent TEST activity contaminating live user_activity table
CREATE TABLE IF NOT EXISTS test_user_activity (
    LIKE user_activity INCLUDING ALL
);

-- Note: test_epr_log and test_aeee_suggestions (added in W-03) remain for backwards
-- compatibility but are no longer the routing targets after CF-03/CF-04 fixes.
