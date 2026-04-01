-- ================================================================
-- DEMONPULSE V8 — MIGRATION 005: Simulation Log + Helpers
-- Run AFTER 001, 002, 003, 004
-- ================================================================

-- ----------------------------------------------------------------
-- SIMULATION LOG — persist every /api/simulator/run result
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS simulation_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid        TEXT,
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    engine          TEXT NOT NULL DEFAULT 'monte_carlo',  -- 'monte_carlo' or 'legacy'
    n_runs          INTEGER NOT NULL,
    race_code       TEXT DEFAULT 'GREYHOUND',
    track           TEXT,
    distance_m      INTEGER,
    condition       TEXT,
    -- Top-line result
    decision        TEXT,           -- BET / SMALL_BET / CAUTION / PASS
    confidence_score DECIMAL(5,3),
    chaos_rating    TEXT,
    pace_type       TEXT,
    top_runner      TEXT,
    top_win_pct     DECIMAL(5,2),
    -- Full results JSONB (all runners with win_pct, place_pct, etc.)
    results_json    JSONB,
    simulation_summary TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sim_log_race_uid  ON simulation_log(race_uid);
CREATE INDEX IF NOT EXISTS idx_sim_log_user_id   ON simulation_log(user_id);
CREATE INDEX IF NOT EXISTS idx_sim_log_created_at ON simulation_log(created_at DESC);

-- Test mirror
CREATE TABLE IF NOT EXISTS test_simulation_log (
    LIKE simulation_log INCLUDING ALL
);

-- ----------------------------------------------------------------
-- ATOMIC LOGIN COUNT — prevents read-modify-write race on login
-- Called from api_login() in app.py
-- ----------------------------------------------------------------
CREATE OR REPLACE FUNCTION increment_login_count(p_user_id UUID)
RETURNS void AS $$
BEGIN
    UPDATE users
    SET    login_count = COALESCE(login_count, 0) + 1
    WHERE  id = p_user_id;
END;
$$ LANGUAGE plpgsql;

-- ----------------------------------------------------------------
-- UNUSED TABLE INVENTORY — tables defined but not yet connected.
-- Do NOT drop these; they are reserved for future feature phases.
-- ----------------------------------------------------------------
-- scratch_log         - manual scratchings log, not yet populated
-- market_snapshots    - real-time odds capture, not yet implemented
-- runner_profiles     - career form accumulation, not yet implemented
-- form_runs           - historical form storage, not yet implemented
-- track_profiles      - per-track bias profiles, not yet implemented
-- gpil_patterns       - GPIL pattern layer, depends on form_runs
-- performance_daily   - now written in settle_bet (see app.py fix)
-- performance_by_track- now written in settle_bet (see app.py fix)
-- training_logs       - backtest epoch results, not yet connected
-- changelog           - system changelog, not yet connected
-- session_history     - session summary snapshots, not yet connected

-- ================================================================
-- DONE: Migration 005 complete
-- ================================================================
