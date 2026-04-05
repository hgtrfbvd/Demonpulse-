-- ================================================================
-- DEMONPULSE V8 — SUPABASE SCHEMA  (supabase_schema.sql)
-- ================================================================
-- ⚠️  LEGACY / SECONDARY COPY — DO NOT USE AS CANONICAL AUTHORITY
-- ================================================================
-- CF-02 / CF-03: Two conflicting schema files were identified.
-- CANONICAL AUTHORITY is now: sql/001_canonical_schema.sql
--
-- This file is retained as a human-readable quick-reference copy.
-- It is NOT the file that drives migrations or schema bootstrapping.
-- Any divergences between this file and sql/001_canonical_schema.sql
-- must be treated as errors in THIS file; sql/001 is always authoritative.
--
-- To apply the schema, run sql/001_canonical_schema.sql followed by
-- sql/002_indexes_constraints.sql and sql/003_views_optional.sql.
-- ================================================================
--   • CREATE TABLE IF NOT EXISTS       — never destroys existing data
--   • ALTER TABLE ADD COLUMN IF NOT EXISTS — never drops columns
--   • CREATE INDEX IF NOT EXISTS       — no-op if already present
--   • Constraints wrapped in DO $$ … EXCEPTION WHEN duplicate_object
--
-- SUPPORTED RACING CODES: GREYHOUND | HARNESS | GALLOPS
--   All tables that carry racing codes are validated by CHECK constraint
--   and by VALID_RACE_CODES in supabase_config.py.
--
-- ARCHITECTURE:
--   No legacy migration files.  No supabase/migrations/*.sql.
--   This file is the single authority for the Supabase schema.
-- ================================================================

-- ----------------------------------------------------------------
-- EXTENSIONS
-- ----------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ================================================================
-- SECTION 1: CORE MEETING / RACE DATA
-- ================================================================

-- ----------------------------------------------------------------
-- meetings
-- Meeting-level identity. Stable (date, track, code) natural key.
-- Conflict key: (date, track, code)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meetings (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date        DATE        NOT NULL    DEFAULT CURRENT_DATE,
    track       TEXT        NOT NULL    DEFAULT '',
    code        TEXT        NOT NULL    DEFAULT 'GREYHOUND'
                            CHECK (code IN ('GREYHOUND', 'HARNESS', 'GALLOPS')),
    state       TEXT                    DEFAULT '',
    country     TEXT                    DEFAULT 'AUS',
    weather     TEXT                    DEFAULT '',
    rail        TEXT                    DEFAULT '',
    track_cond  TEXT                    DEFAULT '',
    race_count  INTEGER                 DEFAULT 0,
    source      TEXT                    DEFAULT 'oddspro',
    updated_at  TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (date, track, code)
);

CREATE INDEX IF NOT EXISTS idx_meetings_date      ON meetings(date);
CREATE INDEX IF NOT EXISTS idx_meetings_code      ON meetings(code);
CREATE INDEX IF NOT EXISTS idx_meetings_date_code ON meetings(date, code);

-- ----------------------------------------------------------------
-- today_races
-- Primary race table. OddsPro is the authoritative source.
-- Conflict key: (date, track, race_num, code)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS today_races (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid             TEXT        NOT NULL    DEFAULT '',
    oddspro_race_id      TEXT        NOT NULL    DEFAULT '',
    date                 DATE        NOT NULL    DEFAULT CURRENT_DATE,
    track                TEXT        NOT NULL    DEFAULT '',
    state                TEXT                    DEFAULT '',
    race_num             INTEGER     NOT NULL    DEFAULT 0,
    code                 TEXT        NOT NULL    DEFAULT 'GREYHOUND',
    distance             TEXT                    DEFAULT '',
    grade                TEXT                    DEFAULT '',
    jump_time            TEXT                    DEFAULT '',
    prize_money          TEXT                    DEFAULT '',
    race_name            TEXT                    DEFAULT '',
    condition            TEXT                    DEFAULT '',
    status               TEXT        NOT NULL    DEFAULT 'upcoming',
    block_code           TEXT        NOT NULL    DEFAULT '',
    source               TEXT        NOT NULL    DEFAULT 'oddspro',
    source_url           TEXT                    DEFAULT '',
    time_status          TEXT        NOT NULL    DEFAULT 'PARTIAL',
    completeness_score   INTEGER                 DEFAULT 0,
    completeness_quality TEXT                    DEFAULT 'LOW',
    race_hash            TEXT                    DEFAULT '',
    lifecycle_state      TEXT                    DEFAULT 'fetched',
    fetched_at           TIMESTAMPTZ             DEFAULT NOW(),
    updated_at           TIMESTAMPTZ             DEFAULT NOW(),
    completed_at         TIMESTAMPTZ,
    normalized_at        TIMESTAMPTZ,
    scored_at            TIMESTAMPTZ,
    packet_built_at      TIMESTAMPTZ,
    ai_reviewed_at       TIMESTAMPTZ,
    bet_logged_at        TIMESTAMPTZ,
    result_captured_at   TIMESTAMPTZ,
    learned_at           TIMESTAMPTZ,
    UNIQUE (date, track, race_num, code)
);

CREATE INDEX IF NOT EXISTS idx_today_races_date        ON today_races(date);
CREATE INDEX IF NOT EXISTS idx_today_races_code        ON today_races(code);
CREATE INDEX IF NOT EXISTS idx_today_races_date_code   ON today_races(date, code);
CREATE INDEX IF NOT EXISTS idx_today_races_date_status ON today_races(date, status);
-- NOTE: idx_today_races_race_uid, idx_today_races_oddspro_id, idx_today_races_lifecycle_date
--       are deferred to Section 8B — those columns are only guaranteed to exist after the
--       ALTER TABLE … ADD COLUMN IF NOT EXISTS guards run (upgrade-safe ordering).

-- ----------------------------------------------------------------
-- today_runners
-- Per-runner data for each race.
-- Conflict key: (race_uid, box_num)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS today_runners (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid          TEXT        NOT NULL    DEFAULT '',
    oddspro_race_id   TEXT                    DEFAULT '',
    box_num           INTEGER     NOT NULL    DEFAULT 0,
    number            INTEGER,
    barrier           INTEGER,
    name              TEXT                    DEFAULT '',
    jockey            TEXT                    DEFAULT '',
    driver            TEXT                    DEFAULT '',
    trainer           TEXT                    DEFAULT '',
    owner             TEXT                    DEFAULT '',
    weight            NUMERIC,
    price             NUMERIC,
    rating            NUMERIC,
    run_style         TEXT                    DEFAULT '',
    early_speed       TEXT                    DEFAULT '',
    best_time         TEXT                    DEFAULT '',
    career            TEXT                    DEFAULT '',
    scratched         BOOLEAN                 DEFAULT FALSE,
    scratch_reason    TEXT                    DEFAULT '',
    is_fav            BOOLEAN                 DEFAULT FALSE,
    source_confidence TEXT                    DEFAULT '',
    updated_at        TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (race_uid, box_num)
);

-- NOTE: idx_today_runners_is_fav, idx_today_runners_race_uid and idx_today_runners_race_uid_box
--       are deferred to Section 8B — those columns are only guaranteed to exist after the
--       ALTER TABLE … ADD COLUMN IF NOT EXISTS guards run (upgrade-safe ordering).

-- ----------------------------------------------------------------
-- results_log
-- Official race results (OddsPro confirmed only).
-- One row per race. FormFav / provisional data must not enter here.
-- Conflict key: (date, track, race_num, code)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS results_log (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date         DATE        NOT NULL    DEFAULT CURRENT_DATE,
    track        TEXT        NOT NULL    DEFAULT '',
    race_num     INTEGER     NOT NULL    DEFAULT 0,
    code         TEXT        NOT NULL    DEFAULT 'GREYHOUND',
    race_uid     TEXT                    DEFAULT '',
    winner       TEXT                    DEFAULT '',
    winner_box   INTEGER,
    win_price    NUMERIC,
    place_2      TEXT                    DEFAULT '',
    place_3      TEXT                    DEFAULT '',
    margin       NUMERIC,
    winning_time NUMERIC,
    source       TEXT                    DEFAULT 'oddspro',
    created_at   TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (date, track, race_num, code)
);

CREATE INDEX IF NOT EXISTS idx_results_log_date      ON results_log(date);
CREATE INDEX IF NOT EXISTS idx_results_log_code      ON results_log(code);
CREATE INDEX IF NOT EXISTS idx_results_log_date_code ON results_log(date, code);
-- NOTE: idx_results_log_race_uid is deferred to Section 8B — race_uid is only guaranteed
--       to exist after the ALTER TABLE guard runs (upgrade-safe ordering).

-- ================================================================
-- SECTION 2: USERS & AUTH
-- ================================================================
-- CANONICAL model (aligned with sql/001_canonical_schema.sql):
--   • users         — expanded table (display_name, email, login_count, last_ip, etc.)
--   • user_accounts — per-user bankroll/settings (unchanged)
--   • user_permissions — ONE row per user, JSONB arrays: granted / revoked / effective
--   • user_sessions — token_jti (JWT jti claim), revoked / revoked_at / revoked_by
--   • user_activity — per-user activity log (unchanged)
--
-- ALTER TABLE guards ensure existing databases are upgraded safely.
-- ================================================================

-- ----------------------------------------------------------------
-- users
-- Role-based user accounts. Always in production namespace.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    username      TEXT        UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL    DEFAULT 'operator'
                              CHECK (role IN ('admin', 'operator', 'viewer')),
    active        BOOLEAN                 DEFAULT TRUE,
    display_name  TEXT,
    email         TEXT,
    created_by    TEXT,
    login_count   INTEGER                 DEFAULT 0,
    last_login    TIMESTAMPTZ,
    last_ip       TEXT,
    created_at    TIMESTAMPTZ             DEFAULT NOW(),
    updated_at    TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill any missing columns on existing users tables.
-- CREATE TABLE IF NOT EXISTS above is skipped on existing databases.
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name  TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email         TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by    TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS login_count   INTEGER     DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_ip       TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at    TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_role     ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_active   ON users(active);

-- ----------------------------------------------------------------
-- user_accounts
-- Per-user bankroll and preference data.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_accounts (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    bankroll            NUMERIC(12,2)           DEFAULT 1000.00,
    total_pl            NUMERIC(12,2)           DEFAULT 0.00,
    session_pl          NUMERIC(12,2)           DEFAULT 0.00,
    peak_bank           NUMERIC(12,2)           DEFAULT 1000.00,
    total_bets          INTEGER                 DEFAULT 0,
    total_wins          INTEGER                 DEFAULT 0,
    settings            JSONB                   DEFAULT '{}',
    alerts              JSONB                   DEFAULT '{"hot_bet":true,"t10_alert":true,"t1_alert":true,"signal_sounds":false}',
    admin_notes         TEXT,
    last_session_reset  TIMESTAMPTZ,
    created_at          TIMESTAMPTZ             DEFAULT NOW(),
    updated_at          TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_accounts_user_id ON user_accounts(user_id);

-- ----------------------------------------------------------------
-- user_permissions
-- ONE row per user — JSONB array model (not row-per-page).
-- granted   = pages explicitly granted beyond role defaults
-- revoked   = pages explicitly revoked below role defaults
-- effective = final resolved page set (pre-computed for fast reads)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_permissions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    granted     JSONB                   DEFAULT '[]',
    revoked     JSONB                   DEFAULT '[]',
    effective   JSONB                   DEFAULT '[]',
    updated_at  TIMESTAMPTZ             DEFAULT NOW(),
    updated_by  TEXT
);

-- Backfill missing columns on existing user_permissions tables.
ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS granted    JSONB       DEFAULT '[]';
ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS revoked    JSONB       DEFAULT '[]';
ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS effective  JSONB       DEFAULT '[]';
ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS updated_by TEXT;

CREATE INDEX IF NOT EXISTS idx_user_permissions_user_id ON user_permissions(user_id);

-- ----------------------------------------------------------------
-- user_sessions
-- Tracks active JWT sessions by JTI (jti claim from the token).
-- token_jti is NOT the raw token — it is the unique jti identifier.
-- revoked / revoked_at / revoked_by support force-logout.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_jti   TEXT        NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    ip_address  TEXT,
    user_agent  TEXT,
    revoked     BOOLEAN                 DEFAULT FALSE,
    revoked_at  TIMESTAMPTZ,
    revoked_by  TEXT,
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill missing columns on existing user_sessions tables.
-- token_jti is added as a plain nullable column first; the unique index
-- below enforces uniqueness only for non-NULL values, which is safe on
-- existing databases that have rows with no jti yet recorded.
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS token_jti  TEXT;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS user_agent TEXT;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS revoked    BOOLEAN     DEFAULT FALSE;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS revoked_by TEXT;

-- Unique index on token_jti (partial: only non-NULL values) so that
-- existing rows with token_jti = NULL don't violate uniqueness.
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_sessions_token_jti
    ON user_sessions(token_jti) WHERE token_jti IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id   ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_revoked   ON user_sessions(revoked, expires_at);

-- ----------------------------------------------------------------
-- user_activity
-- Per-user activity log.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_activity (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action      TEXT        NOT NULL,
    resource    TEXT,
    detail      JSONB,
    ip_address  TEXT,
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_activity_user_id    ON user_activity(user_id);
CREATE INDEX IF NOT EXISTS idx_user_activity_created_at ON user_activity(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_activity_action     ON user_activity(action);

-- ================================================================
-- SECTION 3: BETTING & SIGNALS
-- ================================================================

-- ----------------------------------------------------------------
-- bet_log
-- Individual bet records.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bet_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        REFERENCES users(id) ON DELETE SET NULL,
    session_id      UUID,
    date            DATE                    DEFAULT CURRENT_DATE,
    race_uid        TEXT                    DEFAULT '',
    track           TEXT                    DEFAULT '',
    race_num        INTEGER,
    code            TEXT                    DEFAULT 'GREYHOUND',
    runner          TEXT                    DEFAULT '',
    box_num         INTEGER,
    bet_type        TEXT                    DEFAULT 'WIN',
    stake           NUMERIC(10,2),
    price           NUMERIC,
    result          TEXT                    DEFAULT 'PENDING',
    pl              NUMERIC(10,2),
    signal_score    NUMERIC,
    confidence      NUMERIC,
    notes           TEXT,
    created_at      TIMESTAMPTZ             DEFAULT NOW(),
    settled_at      TIMESTAMPTZ
);

-- Backfill race_uid on bet_log BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_bet_log_date         ON bet_log(date);
CREATE INDEX IF NOT EXISTS idx_bet_log_user         ON bet_log(user_id);
CREATE INDEX IF NOT EXISTS idx_bet_log_race_uid     ON bet_log(race_uid);
CREATE INDEX IF NOT EXISTS idx_bet_log_date_result  ON bet_log(date, result);
CREATE INDEX IF NOT EXISTS idx_bet_log_user_date    ON bet_log(user_id, date DESC);

-- ----------------------------------------------------------------
-- signals
-- AI signals generated per race.
-- Conflict key: race_uid (one active signal per race)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid        TEXT        NOT NULL UNIQUE,
    date            DATE                    DEFAULT CURRENT_DATE,
    track           TEXT                    DEFAULT '',
    race_num        INTEGER,
    code            TEXT                    DEFAULT 'GREYHOUND',
    signal_type     TEXT,
    decision        TEXT,
    confidence      NUMERIC,
    top_runner      TEXT,
    top_box         INTEGER,
    top_price       NUMERIC,
    score           NUMERIC,
    edge_score      NUMERIC,
    model_version   TEXT,
    raw_data        JSONB,
    created_at      TIMESTAMPTZ             DEFAULT NOW(),
    updated_at      TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on signals BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE signals ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_signals_date     ON signals(date);
CREATE INDEX IF NOT EXISTS idx_signals_race_uid ON signals(race_uid);
CREATE INDEX IF NOT EXISTS idx_signals_code     ON signals(code);

-- ----------------------------------------------------------------
-- sessions
-- Betting session control.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        REFERENCES users(id) ON DELETE SET NULL,
    date            DATE                    DEFAULT CURRENT_DATE,
    session_type    TEXT                    DEFAULT 'Live Betting',
    account_type    TEXT                    DEFAULT 'Standard',
    bankroll_start  NUMERIC(10,2),
    bankroll_end    NUMERIC(10,2),
    bank_mode       TEXT                    DEFAULT 'STANDARD',
    active_code     TEXT                    DEFAULT 'GREYHOUND',
    learning_mode   TEXT                    DEFAULT 'Passive',
    execution_mode  TEXT                    DEFAULT 'Quick',
    posture         TEXT                    DEFAULT 'NORMAL',
    total_bets      INTEGER                 DEFAULT 0,
    wins            INTEGER                 DEFAULT 0,
    losses          INTEGER                 DEFAULT 0,
    pl              NUMERIC(10,2)           DEFAULT 0,
    roi             NUMERIC(6,2)            DEFAULT 0,
    notes           TEXT,
    created_at      TIMESTAMPTZ             DEFAULT NOW(),
    ended_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sessions_date    ON sessions(date);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id);

-- ================================================================
-- SECTION 4: SYSTEM STATE & AUDIT
-- ================================================================

-- ----------------------------------------------------------------
-- system_state
-- Global app state singleton (id = 1).
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_state (
    id              INTEGER     PRIMARY KEY DEFAULT 1,
    bankroll        NUMERIC(10,2)           DEFAULT 1000,
    current_pl      NUMERIC(10,2)           DEFAULT 0,
    bank_mode       TEXT                    DEFAULT 'STANDARD',
    active_code     TEXT                    DEFAULT 'GREYHOUND',
    posture         TEXT                    DEFAULT 'NORMAL',
    sys_state       TEXT                    DEFAULT 'STABLE',
    variance        TEXT                    DEFAULT 'NORMAL',
    session_type    TEXT                    DEFAULT 'Live Betting',
    time_anchor     TEXT                    DEFAULT '',
    updated_at      TIMESTAMPTZ             DEFAULT NOW()
);

-- Ensure the singleton row exists
INSERT INTO system_state (id) VALUES (1) ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------
-- audit_log
-- Immutable audit trail. Always in production namespace.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID,
    username    TEXT,
    event_type  TEXT        NOT NULL    DEFAULT '',
    resource    TEXT                    DEFAULT '',
    severity    TEXT                    DEFAULT 'INFO',
    data        JSONB,
    ip_address  TEXT,
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_time       ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_user       ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_event      ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_user_event ON audit_log(user_id, event_type);

-- ----------------------------------------------------------------
-- source_log
-- External API call log (OddsPro, FormFav, etc.).
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT        NOT NULL,
    endpoint        TEXT,
    method          TEXT                    DEFAULT 'GET',
    status_code     INTEGER,
    response_ms     INTEGER,
    success         BOOLEAN                 DEFAULT TRUE,
    error_msg       TEXT,
    records_fetched INTEGER,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_source_log_source ON source_log(source);
CREATE INDEX IF NOT EXISTS idx_source_log_time   ON source_log(created_at DESC);

-- ----------------------------------------------------------------
-- activity_log
-- General application activity log.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_log (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event       TEXT        NOT NULL,
    resource    TEXT,
    detail      JSONB,
    severity    TEXT                    DEFAULT 'INFO',
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activity_log_event ON activity_log(event);
CREATE INDEX IF NOT EXISTS idx_activity_log_time  ON activity_log(created_at DESC);

-- ----------------------------------------------------------------
-- simulation_log
-- Records every simulation run result.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS simulation_log (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid         TEXT,
    user_id          UUID        REFERENCES users(id) ON DELETE SET NULL,
    engine           TEXT        NOT NULL    DEFAULT 'monte_carlo',
    n_runs           INTEGER     NOT NULL,
    race_code        TEXT                    DEFAULT 'GREYHOUND',
    track            TEXT,
    distance_m       INTEGER,
    condition        TEXT,
    decision         TEXT,
    confidence_score NUMERIC(5,3),
    chaos_rating     TEXT,
    pace_type        TEXT,
    top_runner       TEXT,
    top_win_pct      NUMERIC(5,2),
    results          JSONB,
    filter_log       JSONB,
    created_at       TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on simulation_log BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE simulation_log ADD COLUMN IF NOT EXISTS race_uid TEXT;

CREATE INDEX IF NOT EXISTS idx_simulation_log_race_uid ON simulation_log(race_uid);
CREATE INDEX IF NOT EXISTS idx_simulation_log_time     ON simulation_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_simulation_log_user     ON simulation_log(user_id);

-- ================================================================
-- SECTION 5: AI / PREDICTIONS
-- ================================================================

-- ----------------------------------------------------------------
-- feature_snapshots
-- Serialized AI feature arrays per race with full lineage.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feature_snapshots (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid             TEXT        NOT NULL    DEFAULT '',
    date                 DATE                    DEFAULT CURRENT_DATE,
    track                TEXT                    DEFAULT '',
    race_code            TEXT                    DEFAULT 'GREYHOUND',
    model_version        TEXT                    DEFAULT '',
    features             JSONB,
    runner_count         INTEGER                 DEFAULT 0,
    completeness_score   INTEGER                 DEFAULT 0,
    source               TEXT                    DEFAULT 'oddspro',
    created_at           TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on feature_snapshots BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE feature_snapshots ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_feature_snaps_race_uid ON feature_snapshots(race_uid);
CREATE INDEX IF NOT EXISTS idx_feature_snaps_date     ON feature_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_feature_snaps_model    ON feature_snapshots(model_version);

-- ----------------------------------------------------------------
-- prediction_snapshots
-- Prediction run metadata (one row per prediction run).
-- Conflict key: prediction_snapshot_id
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prediction_snapshots (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_snapshot_id  TEXT        NOT NULL UNIQUE DEFAULT '',
    race_uid                TEXT        NOT NULL    DEFAULT '',
    date                    DATE                    DEFAULT CURRENT_DATE,
    track                   TEXT                    DEFAULT '',
    race_code               TEXT                    DEFAULT 'GREYHOUND',
    model_version           TEXT                    DEFAULT '',
    runner_count            INTEGER                 DEFAULT 0,
    top_runner              TEXT                    DEFAULT '',
    top_box                 INTEGER,
    top_score               NUMERIC,
    confidence              NUMERIC,
    decision                TEXT,
    enrichment_used         BOOLEAN                 DEFAULT FALSE,
    raw_output              JSONB,
    created_at              TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on prediction_snapshots BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_pred_snaps_race_uid    ON prediction_snapshots(race_uid);
CREATE INDEX IF NOT EXISTS idx_pred_snaps_date        ON prediction_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_pred_snaps_model_date  ON prediction_snapshots(model_version, created_at DESC);

-- ----------------------------------------------------------------
-- prediction_runner_outputs
-- Per-runner scores from a prediction run.
-- Linked to prediction_snapshots via prediction_snapshot_id.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prediction_runner_outputs (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_snapshot_id  TEXT        NOT NULL    DEFAULT '',
    race_uid                TEXT        NOT NULL    DEFAULT '',
    runner_name             TEXT                    DEFAULT '',
    box_num                 INTEGER,
    score                   NUMERIC,
    predicted_rank          INTEGER,
    win_prob                NUMERIC,
    place_prob              NUMERIC,
    model_version           TEXT                    DEFAULT '',
    created_at              TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on prediction_runner_outputs BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE prediction_runner_outputs ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_pred_outputs_snap_id  ON prediction_runner_outputs(prediction_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_pred_outputs_race_uid ON prediction_runner_outputs(race_uid);

-- ----------------------------------------------------------------
-- learning_evaluations
-- Post-result AI evaluation records.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS learning_evaluations (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid                TEXT        NOT NULL    DEFAULT '',
    prediction_snapshot_id  TEXT,
    date                    DATE                    DEFAULT CURRENT_DATE,
    track                   TEXT                    DEFAULT '',
    race_code               TEXT                    DEFAULT 'GREYHOUND',
    model_version           TEXT                    DEFAULT '',
    winner_hit              BOOLEAN                 DEFAULT FALSE,
    top2_hit                BOOLEAN                 DEFAULT FALSE,
    top3_hit                BOOLEAN                 DEFAULT FALSE,
    predicted_winner        TEXT,
    actual_winner           TEXT,
    score_at_prediction     NUMERIC,
    win_price               NUMERIC,
    pl_outcome              NUMERIC,
    evaluated_at            TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on learning_evaluations BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_learning_evals_race_uid  ON learning_evaluations(race_uid);
CREATE INDEX IF NOT EXISTS idx_learning_evals_date      ON learning_evaluations(date);
CREATE INDEX IF NOT EXISTS idx_learning_evals_model     ON learning_evaluations(model_version);
CREATE INDEX IF NOT EXISTS idx_learning_evals_race_code ON learning_evaluations(race_code);

-- ----------------------------------------------------------------
-- sectional_snapshots
-- Per-runner sectional timing data.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sectional_snapshots (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid        TEXT        NOT NULL    DEFAULT '',
    runner_name     TEXT                    DEFAULT '',
    box_num         INTEGER,
    split_labels    JSONB,
    split_times     JSONB,
    total_time      NUMERIC,
    source_type     TEXT                    DEFAULT 'pre_race',
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on sectional_snapshots BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE sectional_snapshots ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_sectionals_race_uid ON sectional_snapshots(race_uid);

-- ----------------------------------------------------------------
-- race_shape_snapshots
-- Race-level shape analysis (pace, collision model, etc.).
-- Conflict key: race_uid (one shape analysis per race)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS race_shape_snapshots (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid        TEXT        NOT NULL UNIQUE,
    date            DATE                    DEFAULT CURRENT_DATE,
    track           TEXT                    DEFAULT '',
    race_code       TEXT                    DEFAULT 'GREYHOUND',
    pace_type       TEXT,
    shape_score     NUMERIC,
    collision_risk  NUMERIC,
    leader_box      INTEGER,
    shape_data      JSONB,
    model_version   TEXT,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on race_shape_snapshots BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE race_shape_snapshots ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_race_shape_race_uid ON race_shape_snapshots(race_uid);
CREATE INDEX IF NOT EXISTS idx_race_shape_date     ON race_shape_snapshots(date);

-- ================================================================
-- SECTION 6: BACKTESTING
-- ================================================================

-- ----------------------------------------------------------------
-- backtest_runs
-- Backtest run summaries.
-- Conflict key: run_id
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_runs (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id           TEXT        NOT NULL UNIQUE DEFAULT '',
    model_version    TEXT                    DEFAULT '',
    race_code        TEXT                    DEFAULT 'GREYHOUND',
    date_from        DATE,
    date_to          DATE,
    total_races      INTEGER                 DEFAULT 0,
    winner_hits      INTEGER                 DEFAULT 0,
    top2_hits        INTEGER                 DEFAULT 0,
    top3_hits        INTEGER                 DEFAULT 0,
    winner_accuracy  NUMERIC,
    total_pl         NUMERIC,
    roi              NUMERIC,
    status           TEXT                    DEFAULT 'completed',
    notes            TEXT,
    created_at       TIMESTAMPTZ             DEFAULT NOW(),
    updated_at       TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_model ON backtest_runs(model_version, created_at DESC);

-- ----------------------------------------------------------------
-- backtest_run_items
-- Per-race results within a backtest run.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_run_items (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          TEXT        NOT NULL    DEFAULT '',
    race_uid        TEXT                    DEFAULT '',
    date            DATE,
    track           TEXT                    DEFAULT '',
    race_code       TEXT                    DEFAULT 'GREYHOUND',
    predicted_winner TEXT,
    actual_winner   TEXT,
    winner_hit      BOOLEAN                 DEFAULT FALSE,
    score           NUMERIC,
    win_price       NUMERIC,
    pl              NUMERIC,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on backtest_run_items BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE backtest_run_items ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_backtest_items_run_id   ON backtest_run_items(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_items_race_uid ON backtest_run_items(race_uid);

-- ================================================================
-- SECTION 7: LEARNING ENGINE
-- ================================================================

-- ----------------------------------------------------------------
-- etg_tags
-- Error / edge tagging per race.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etg_tags (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid    TEXT        NOT NULL    DEFAULT '',
    tag         TEXT        NOT NULL,
    reason      TEXT,
    session_id  TEXT,
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on etg_tags BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE etg_tags ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_etg_tags_race_uid ON etg_tags(race_uid);

-- ----------------------------------------------------------------
-- epr_data
-- Edge performance registry.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS epr_data (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid    TEXT        NOT NULL    DEFAULT '',
    edge_type   TEXT,
    edge_score  NUMERIC,
    result      TEXT,
    pl          NUMERIC,
    session_id  TEXT,
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on epr_data BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE epr_data ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_epr_data_race_uid ON epr_data(race_uid);

-- ----------------------------------------------------------------
-- aeee_adjustments
-- Auto edge evaluation adjustments.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aeee_adjustments (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    edge_type       TEXT,
    adjustment      NUMERIC,
    reason          TEXT,
    session_id      UUID        REFERENCES sessions(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aeee_adj_session ON aeee_adjustments(session_id);

-- ----------------------------------------------------------------
-- pass_log
-- Race pass records (why a race was skipped).
-- Conflict key: race_uid (one pass record per race)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pass_log (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid    TEXT        NOT NULL UNIQUE,
    reason      TEXT,
    block_code  TEXT,
    score       NUMERIC,
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on pass_log BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE pass_log ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_pass_log_race_uid ON pass_log(race_uid);

-- ================================================================
-- SECTION 8: MARKET DATA & SCORING
-- ================================================================

-- ----------------------------------------------------------------
-- chat_history
-- System / user chat log (for AI system prompt context).
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_history (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    role        TEXT        NOT NULL    DEFAULT 'user',
    content     TEXT        NOT NULL,
    session_id  TEXT,
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_history_user    ON chat_history(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_history_session ON chat_history(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_history_time    ON chat_history(created_at DESC);

-- ----------------------------------------------------------------
-- training_logs
-- ML training run records.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS training_logs (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    model       TEXT,
    version     TEXT,
    race_code   TEXT                    DEFAULT 'GREYHOUND',
    accuracy    NUMERIC,
    loss        NUMERIC,
    epochs      INTEGER,
    notes       TEXT,
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- exotic_suggestions
-- Exotic bet suggestions generated by AI.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exotic_suggestions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid        TEXT                    DEFAULT '',
    bet_type        TEXT,
    combinations    JSONB,
    confidence      NUMERIC,
    estimated_cost  NUMERIC,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill race_uid on exotic_suggestions BEFORE creating the index that references it.
-- On existing databases the CREATE TABLE above is a no-op.
ALTER TABLE exotic_suggestions ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_exotic_sugg_race ON exotic_suggestions(race_uid);

-- ================================================================
-- SECTION 8B: ADD MISSING COLUMNS TO EXISTING TABLES
-- ================================================================
-- These ALTER TABLE statements are idempotent guards.  If the table
-- was created by an earlier schema version that lacked a column, this
-- block adds it safely.  When the column already exists the statement
-- is a no-op (IF NOT EXISTS).
-- ----------------------------------------------------------------

-- today_races — columns added in V8 rebuild
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS race_uid             TEXT        NOT NULL DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS oddspro_race_id      TEXT        NOT NULL DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS block_code           TEXT        NOT NULL DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS source               TEXT        NOT NULL DEFAULT 'oddspro';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS source_url           TEXT                 DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS time_status          TEXT        NOT NULL DEFAULT 'PARTIAL';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS condition            TEXT                 DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS race_name            TEXT                 DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS updated_at           TIMESTAMPTZ          DEFAULT NOW();
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS completed_at         TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS completeness_score   INTEGER              DEFAULT 0;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS completeness_quality TEXT                 DEFAULT 'LOW';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS race_hash            TEXT                 DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS lifecycle_state      TEXT                 DEFAULT 'fetched';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS normalized_at        TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS scored_at            TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS packet_built_at      TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS ai_reviewed_at       TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS bet_logged_at        TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS result_captured_at   TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS learned_at           TIMESTAMPTZ;

-- today_runners — columns added in V8 rebuild
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS race_uid          TEXT        NOT NULL DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS oddspro_race_id   TEXT                 DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS number            INTEGER;
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS barrier           INTEGER;
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS jockey            TEXT                 DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS driver            TEXT                 DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS price             NUMERIC;
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS rating            NUMERIC;
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS source_confidence TEXT                 DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS scratch_reason    TEXT                 DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS is_fav            BOOLEAN              DEFAULT FALSE;
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ          DEFAULT NOW();

-- results_log — race_uid added in V8
ALTER TABLE results_log ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

-- backtest_runs — updated_at added so update_run() writes succeed
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- system_state — tuning columns added in V8
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS confidence_threshold NUMERIC(4,2) DEFAULT 0.65;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS ev_threshold         NUMERIC(4,2) DEFAULT 0.08;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS staking_mode         TEXT         DEFAULT 'KELLY';
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS tempo_weight         NUMERIC(4,2) DEFAULT 1.0;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS traffic_penalty      NUMERIC(4,2) DEFAULT 0.8;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS closer_boost         NUMERIC(4,2) DEFAULT 1.1;

-- ----------------------------------------------------------------
-- Indexes for guard-added columns
-- All CREATE INDEX statements below reference columns that are only
-- guaranteed to exist AFTER the ALTER TABLE … ADD COLUMN IF NOT EXISTS
-- guards above.  Placing them here ensures they are safe on both a
-- brand-new database and any older database being upgraded.
-- ----------------------------------------------------------------

-- today_races — columns guarded above
CREATE INDEX IF NOT EXISTS idx_today_races_race_uid       ON today_races(race_uid);
CREATE INDEX IF NOT EXISTS idx_today_races_oddspro_id     ON today_races(oddspro_race_id);
CREATE INDEX IF NOT EXISTS idx_today_races_lifecycle_date ON today_races(lifecycle_state, date DESC);

-- today_runners — columns guarded above
CREATE INDEX IF NOT EXISTS idx_today_runners_race_uid     ON today_runners(race_uid);
CREATE INDEX IF NOT EXISTS idx_today_runners_race_uid_box ON today_runners(race_uid, box_num);
CREATE INDEX IF NOT EXISTS idx_today_runners_oddspro_id   ON today_runners(oddspro_race_id);
CREATE INDEX IF NOT EXISTS idx_today_runners_is_fav       ON today_runners(is_fav) WHERE is_fav = TRUE;

-- results_log — race_uid guarded above
CREATE INDEX IF NOT EXISTS idx_results_log_race_uid ON results_log(race_uid);

-- ================================================================
-- SECTION 9: ADDITIONAL INDEXES & CONFLICT CONSTRAINTS
-- ================================================================

-- Ensure upsert conflict constraints exist even on databases migrated
-- from an older schema that may have skipped these.

-- today_runners: (race_uid, box_num) unique constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = 'today_runners'::regclass
          AND c.contype = 'u'
          AND array_length(c.conkey, 1) = 2
          AND EXISTS (SELECT 1 FROM pg_attribute
                      WHERE attrelid = c.conrelid
                        AND attnum = ANY(c.conkey)
                        AND attname = 'race_uid')
          AND EXISTS (SELECT 1 FROM pg_attribute
                      WHERE attrelid = c.conrelid
                        AND attnum = ANY(c.conkey)
                        AND attname = 'box_num')
    ) THEN
        ALTER TABLE today_runners
            ADD CONSTRAINT today_runners_race_uid_box_num_key UNIQUE (race_uid, box_num);
    END IF;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ================================================================
-- SECTION 10: REPORTING VIEWS
-- ================================================================

-- v_active_races — today's upcoming/open races with runner counts
CREATE OR REPLACE VIEW v_active_races AS
SELECT
    r.id,
    r.race_uid,
    r.date,
    r.track,
    r.code,
    r.race_num,
    r.jump_time,
    r.status,
    r.grade,
    r.distance,
    COUNT(ru.id)                                            AS runner_count,
    COUNT(ru.id) FILTER (WHERE ru.scratched = FALSE)        AS active_runners
FROM today_races r
LEFT JOIN today_runners ru ON ru.race_uid = r.race_uid
WHERE r.date = CURRENT_DATE
  AND r.status IN ('upcoming', 'open')
GROUP BY r.id, r.race_uid, r.date, r.track, r.code,
         r.race_num, r.jump_time, r.status, r.grade, r.distance
ORDER BY r.jump_time;

-- v_todays_results — official results for today with race context
CREATE OR REPLACE VIEW v_todays_results AS
SELECT
    rl.id,
    rl.race_uid,
    rl.date,
    rl.track,
    rl.code,
    rl.race_num,
    rl.winner,
    rl.winner_box,
    rl.win_price,
    rl.place_2,
    rl.place_3,
    rl.margin,
    rl.winning_time,
    r.grade,
    r.distance
FROM results_log rl
LEFT JOIN today_races r ON r.race_uid = rl.race_uid
WHERE rl.date = CURRENT_DATE
ORDER BY rl.track, rl.race_num;

-- v_prediction_accuracy — per-model prediction accuracy summary
CREATE OR REPLACE VIEW v_prediction_accuracy AS
SELECT
    model_version,
    race_code,
    COUNT(*)                                                     AS total_races,
    SUM(CASE WHEN winner_hit  THEN 1 ELSE 0 END)                 AS winner_hits,
    SUM(CASE WHEN top2_hit    THEN 1 ELSE 0 END)                 AS top2_hits,
    SUM(CASE WHEN top3_hit    THEN 1 ELSE 0 END)                 AS top3_hits,
    ROUND(
        SUM(CASE WHEN winner_hit THEN 1 ELSE 0 END)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 2
    )                                                            AS winner_pct,
    MIN(evaluated_at)                                            AS first_eval,
    MAX(evaluated_at)                                            AS last_eval
FROM learning_evaluations
GROUP BY model_version, race_code
ORDER BY model_version, race_code;

-- v_daily_betting_summary — per-date betting P/L
CREATE OR REPLACE VIEW v_daily_betting_summary AS
SELECT
    date,
    COUNT(*)                                                     AS total_bets,
    SUM(CASE WHEN result = 'WIN'  THEN 1 ELSE 0 END)             AS wins,
    SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END)             AS losses,
    ROUND(SUM(COALESCE(pl, 0))::NUMERIC, 2)                      AS total_pl,
    ROUND(AVG(COALESCE(pl, 0))::NUMERIC, 2)                      AS avg_pl
FROM bet_log
GROUP BY date
ORDER BY date DESC;

-- v_backtest_summary — aggregated backtest run performance
CREATE OR REPLACE VIEW v_backtest_summary AS
SELECT
    run_id,
    model_version,
    race_code,
    date_from,
    date_to,
    total_races,
    winner_hits,
    ROUND(winner_accuracy * 100, 2)  AS winner_pct,
    status,
    created_at
FROM backtest_runs
ORDER BY created_at DESC;

-- ================================================================
-- END OF SCHEMA
-- ================================================================
-- Tables: meetings, today_races, today_runners, results_log,
--         users, user_accounts, user_permissions, user_sessions, user_activity,
--         bet_log, signals, sessions,
--         system_state, audit_log, source_log, activity_log, simulation_log,
--         feature_snapshots, prediction_snapshots, prediction_runner_outputs,
--         learning_evaluations, sectional_snapshots, race_shape_snapshots,
--         backtest_runs, backtest_run_items,
--         etg_tags, epr_data, aeee_adjustments, pass_log,
--         chat_history, training_logs, exotic_suggestions
--
-- Views: v_active_races, v_todays_results, v_prediction_accuracy,
--        v_daily_betting_summary, v_backtest_summary
-- ================================================================
