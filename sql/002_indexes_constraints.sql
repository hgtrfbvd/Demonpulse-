-- ================================================================
-- DEMONPULSE V8 — ADDITIONAL INDEXES & CONSTRAINTS
-- sql/002_indexes_constraints.sql
-- ================================================================
-- Supplementary indexes for query performance.
-- All indexes use IF NOT EXISTS — safe to re-run.
--
-- Run AFTER 001_canonical_schema.sql.
-- ================================================================

-- ----------------------------------------------------------------
-- today_races — additional composite indexes
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_today_races_date_code
    ON today_races(date, code);

CREATE INDEX IF NOT EXISTS idx_today_races_date_status
    ON today_races(date, status);

CREATE INDEX IF NOT EXISTS idx_today_races_lifecycle_date
    ON today_races(lifecycle_state, date DESC);

-- ----------------------------------------------------------------
-- today_runners — additional indexes
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_today_runners_race_uid_box
    ON today_runners(race_uid, box_num);

CREATE INDEX IF NOT EXISTS idx_today_runners_is_fav
    ON today_runners(is_fav) WHERE is_fav = true;

-- ----------------------------------------------------------------
-- results_log — additional indexes
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_results_log_code
    ON results_log(code);

CREATE INDEX IF NOT EXISTS idx_results_log_date_code
    ON results_log(date, code);

-- ----------------------------------------------------------------
-- bet_log — additional indexes
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_bet_log_date_result
    ON bet_log(date, result);

CREATE INDEX IF NOT EXISTS idx_bet_log_user_date
    ON bet_log(user_id, date DESC);

-- ----------------------------------------------------------------
-- prediction_snapshots — additional indexes
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_pred_snaps_model_date
    ON prediction_snapshots(model_version, created_at DESC);

-- ----------------------------------------------------------------
-- learning_evaluations — additional indexes
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_learning_evals_model_version
    ON learning_evaluations(model_version);

CREATE INDEX IF NOT EXISTS idx_learning_evals_race_code
    ON learning_evaluations(race_code);

-- ----------------------------------------------------------------
-- backtest_runs — additional indexes
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_backtest_runs_model
    ON backtest_runs(model_version, created_at DESC);

-- ----------------------------------------------------------------
-- audit_log — additional indexes
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_audit_log_user_event
    ON audit_log(user_id, event_type);

-- ================================================================
-- UPSERT CONFLICT CONSTRAINTS VERIFICATION
-- ================================================================
-- These DO blocks ensure upsert conflict keys are in place even on
-- databases that were created from an old migration set.
-- ================================================================

-- today_runners: (race_uid, box_num) — needed by runners_repo upsert
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = 'today_runners'::regclass
          AND c.contype = 'u'
          AND array_length(c.conkey, 1) = 2
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'race_uid')
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'box_num')
    ) THEN
        ALTER TABLE today_runners
            ADD CONSTRAINT today_runners_race_uid_box_num_key UNIQUE (race_uid, box_num);
    END IF;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ================================================================
-- DONE
-- ================================================================
