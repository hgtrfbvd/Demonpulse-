-- ================================================================
-- DEMONPULSE V8 — CANONICAL SCHEMA  (sql/001_canonical_schema.sql)
-- ================================================================
-- Single source of truth for the Supabase schema.
-- Replaces all previous migration files in supabase/migrations/.
--
-- SOURCE OF TRUTH: supabase_config.py, repositories/, services/
-- Python layer: supabase_client.py → repositories/*_repo.py
--
-- SAFE TO RUN ON EXISTING DATABASES:
--   • CREATE TABLE IF NOT EXISTS — never destroys existing tables
--   • ALTER TABLE ... ADD COLUMN IF NOT EXISTS — never drops existing columns
--   • CREATE INDEX IF NOT EXISTS — idempotent
--   • Unique constraints added only if they do not already exist
--
-- MULTI-CODE SUPPORT: GREYHOUND, HARNESS, GALLOPS
--   No GREYHOUND hard-coding except where the application default is
--   intentionally 'GREYHOUND' (code TEXT DEFAULT 'GREYHOUND').
--
-- RUN INSTRUCTIONS:
--   • Paste the entire file into the Supabase SQL Editor and execute.
--   • Can be run against an existing database — existing data is preserved.
--   • No manual cleanup required.
--   • After running, restart the application so the schema cache refreshes.
-- ================================================================

-- ----------------------------------------------------------------
-- EXTENSIONS
-- ----------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ================================================================
-- SECTION 1: CORE RUNTIME TABLES
-- ================================================================

-- ----------------------------------------------------------------
-- meetings
-- Meeting-level identity table. Stable (date, track, code) key.
-- Prevents race-code contamination: each meeting is code-scoped.
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

CREATE INDEX IF NOT EXISTS idx_meetings_date       ON meetings(date);
CREATE INDEX IF NOT EXISTS idx_meetings_code       ON meetings(code);
CREATE INDEX IF NOT EXISTS idx_meetings_date_code  ON meetings(date, code);

-- ----------------------------------------------------------------
-- today_races
-- Primary race data table. OddsPro is the authoritative source.
-- Conflict key: (date, track, race_num, code)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS today_races (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid            TEXT        NOT NULL    DEFAULT '',
    oddspro_race_id     TEXT        NOT NULL    DEFAULT '',
    date                DATE        NOT NULL    DEFAULT CURRENT_DATE,
    track               TEXT        NOT NULL    DEFAULT '',
    state               TEXT                    DEFAULT '',
    race_num            INTEGER     NOT NULL    DEFAULT 0,
    code                TEXT        NOT NULL    DEFAULT 'GREYHOUND',
    distance            TEXT                    DEFAULT '',
    grade               TEXT                    DEFAULT '',
    jump_time           TEXT                    DEFAULT '',
    prize_money         TEXT                    DEFAULT '',
    race_name           TEXT                    DEFAULT '',
    condition           TEXT                    DEFAULT '',
    status              TEXT        NOT NULL    DEFAULT 'upcoming',
    block_code          TEXT        NOT NULL    DEFAULT '',
    source              TEXT        NOT NULL    DEFAULT 'oddspro',
    source_url          TEXT                    DEFAULT '',
    time_status         TEXT        NOT NULL    DEFAULT 'PARTIAL',
    completeness_score  INTEGER                 DEFAULT 0,
    completeness_quality TEXT                   DEFAULT 'LOW',
    race_hash           TEXT                    DEFAULT '',
    lifecycle_state     TEXT                    DEFAULT 'fetched',
    fetched_at          TIMESTAMPTZ             DEFAULT NOW(),
    updated_at          TIMESTAMPTZ             DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    normalized_at       TIMESTAMPTZ,
    scored_at           TIMESTAMPTZ,
    packet_built_at     TIMESTAMPTZ,
    ai_reviewed_at      TIMESTAMPTZ,
    bet_logged_at       TIMESTAMPTZ,
    result_captured_at  TIMESTAMPTZ,
    learned_at          TIMESTAMPTZ,
    UNIQUE (date, track, race_num, code)
);

-- Backfill any missing columns on today_races BEFORE creating indexes that
-- reference them. On existing databases the CREATE TABLE above is skipped,
-- so these columns must be guaranteed present before any index references them.
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS race_uid           TEXT        NOT NULL DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS oddspro_race_id    TEXT        NOT NULL DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS block_code         TEXT        NOT NULL DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS source             TEXT        NOT NULL DEFAULT 'oddspro';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS source_url         TEXT                 DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS time_status        TEXT        NOT NULL DEFAULT 'PARTIAL';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS condition          TEXT                 DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS race_name          TEXT                 DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMPTZ          DEFAULT NOW();
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS completed_at       TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS completeness_score INTEGER              DEFAULT 0;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS completeness_quality TEXT               DEFAULT 'LOW';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS race_hash          TEXT                 DEFAULT '';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS lifecycle_state    TEXT                 DEFAULT 'fetched';
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS normalized_at      TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS scored_at          TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS packet_built_at    TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS ai_reviewed_at     TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS bet_logged_at      TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS result_captured_at TIMESTAMPTZ;
ALTER TABLE today_races ADD COLUMN IF NOT EXISTS learned_at         TIMESTAMPTZ;

-- Partial unique index on race_uid: legacy rows may carry race_uid = ''.
-- A full UNIQUE constraint would reject multiple empty-string rows, so we
-- use a partial index that only enforces uniqueness for non-empty values.
CREATE UNIQUE INDEX IF NOT EXISTS idx_today_races_race_uid ON today_races(race_uid) WHERE race_uid != '';
CREATE INDEX IF NOT EXISTS idx_today_races_date         ON today_races(date);
CREATE INDEX IF NOT EXISTS idx_today_races_status       ON today_races(status);
CREATE INDEX IF NOT EXISTS idx_today_races_lifecycle    ON today_races(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_today_races_track_race   ON today_races(track, race_num);
CREATE INDEX IF NOT EXISTS idx_today_races_oddspro_id   ON today_races(oddspro_race_id);

-- Ensure UNIQUE constraint on (date, track, race_num, code) for upserts.
-- Detection is column-based (not name-based) so it is reliable regardless
-- of how the constraint was originally named.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        WHERE c.conrelid = 'today_races'::regclass
          AND c.contype  = 'u'
          AND array_length(c.conkey, 1) = 4
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'date')
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'track')
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'race_num')
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'code')
    ) THEN
        ALTER TABLE today_races
            ADD CONSTRAINT today_races_date_track_race_num_code_key
            UNIQUE (date, track, race_num, code);
    END IF;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- ----------------------------------------------------------------
-- today_runners
-- Per-runner data for each race. FK to today_races.
-- Conflict key: (race_id, box_num)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS today_runners (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_id             UUID        REFERENCES today_races(id) ON DELETE CASCADE,
    race_uid            TEXT        NOT NULL    DEFAULT '',
    oddspro_race_id     TEXT                    DEFAULT '',
    date                DATE        NOT NULL    DEFAULT CURRENT_DATE,
    track               TEXT                    DEFAULT '',
    race_num            INTEGER,
    box_num             INTEGER     NOT NULL    DEFAULT 0,
    name                TEXT        NOT NULL    DEFAULT '',
    number              INTEGER,
    barrier             INTEGER,
    trainer             TEXT                    DEFAULT '',
    jockey              TEXT                    DEFAULT '',
    driver              TEXT                    DEFAULT '',
    owner               TEXT                    DEFAULT '',
    weight              NUMERIC(5,2),
    run_style           TEXT                    DEFAULT '',
    early_speed         TEXT                    DEFAULT '',
    best_time           TEXT                    DEFAULT '',
    career              TEXT                    DEFAULT '',
    price               NUMERIC(10,4),
    rating              NUMERIC(10,4),
    scratched           BOOLEAN                 DEFAULT FALSE,
    scratch_reason      TEXT                    DEFAULT '',
    source_confidence   TEXT                    DEFAULT 'official',
    raw_hash            TEXT                    DEFAULT '',
    created_at          TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (race_id, box_num)
);

-- Backfill missing columns on today_runners BEFORE creating indexes that
-- reference them. On existing databases the CREATE TABLE above is skipped.
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS race_uid          TEXT        NOT NULL DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS oddspro_race_id   TEXT                 DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS number            INTEGER;
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS barrier           INTEGER;
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS jockey            TEXT                 DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS driver            TEXT                 DEFAULT '';
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS price             NUMERIC(10,4);
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS rating            NUMERIC(10,4);
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS source_confidence TEXT                 DEFAULT 'official';
-- scratch_reason replaces scratch_timing as the canonical column name.
-- Migration 001 used scratch_timing; the application now writes scratch_reason.
-- Add both so old and new schemas are fully compatible.
ALTER TABLE today_runners ADD COLUMN IF NOT EXISTS scratch_reason    TEXT                 DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_today_runners_race_id    ON today_runners(race_id);
CREATE INDEX IF NOT EXISTS idx_today_runners_race_uid   ON today_runners(race_uid);
CREATE INDEX IF NOT EXISTS idx_today_runners_name       ON today_runners(name);
CREATE INDEX IF NOT EXISTS idx_today_runners_track_race ON today_runners(track, race_num);
CREATE INDEX IF NOT EXISTS idx_today_runners_scratched  ON today_runners(scratched);

-- ----------------------------------------------------------------
-- results_log
-- Official race results (OddsPro-confirmed only).
-- Conflict key: (date, track, race_num, code)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS results_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date            DATE        NOT NULL    DEFAULT CURRENT_DATE,
    track           TEXT        NOT NULL    DEFAULT '',
    race_num        INTEGER     NOT NULL    DEFAULT 0,
    code            TEXT        NOT NULL    DEFAULT 'GREYHOUND',
    race_uid        TEXT                    DEFAULT '',
    winner          TEXT                    DEFAULT '',
    winner_box      INTEGER,
    win_price       NUMERIC(8,2),
    place_2         TEXT                    DEFAULT '',
    place_3         TEXT                    DEFAULT '',
    margin          NUMERIC(6,2),
    winning_time    NUMERIC(7,3),
    source          TEXT                    DEFAULT 'oddspro',
    recorded_at     TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (date, track, race_num, code)
);

-- Backfill missing columns on results_log BEFORE creating indexes that
-- reference them. On existing databases the CREATE TABLE above is skipped.
ALTER TABLE results_log ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_results_log_date_track_race ON results_log(date, track, race_num);
CREATE INDEX IF NOT EXISTS idx_results_log_race_uid        ON results_log(race_uid);

-- Ensure (date, track, race_num, code) unique constraint exists for upserts.
-- Detection is column-based (not name-based) so it is reliable regardless
-- of how the constraint was originally named.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        WHERE c.conrelid = 'results_log'::regclass
          AND c.contype  = 'u'
          AND array_length(c.conkey, 1) = 4
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'date')
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'track')
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'race_num')
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'code')
    ) THEN
        ALTER TABLE results_log
            ADD CONSTRAINT results_log_date_track_race_num_code_key
            UNIQUE (date, track, race_num, code);
    END IF;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- ----------------------------------------------------------------
-- race_status
-- Per-race status tracking (distinct from today_races.status).
-- Conflict key: (date, track, race_num, code)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS race_status (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date                DATE        NOT NULL    DEFAULT CURRENT_DATE,
    track               TEXT        NOT NULL    DEFAULT '',
    race_num            INTEGER     NOT NULL    DEFAULT 0,
    code                TEXT        NOT NULL    DEFAULT 'GREYHOUND',
    race_uid            TEXT                    DEFAULT '',
    status              TEXT        NOT NULL    DEFAULT 'upcoming',
    has_runners         BOOLEAN                 DEFAULT FALSE,
    has_scratchings     BOOLEAN                 DEFAULT FALSE,
    has_result          BOOLEAN                 DEFAULT FALSE,
    jump_time           TEXT                    DEFAULT '',
    time_status         TEXT                    DEFAULT 'PARTIAL',
    updated_at          TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (date, track, race_num, code)
);

-- Backfill missing columns on race_status BEFORE creating indexes that
-- reference them. On existing databases the CREATE TABLE above is skipped.
ALTER TABLE race_status ADD COLUMN IF NOT EXISTS race_uid TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_race_status_date       ON race_status(date);
CREATE INDEX IF NOT EXISTS idx_race_status_track_race ON race_status(track, race_num);
CREATE INDEX IF NOT EXISTS idx_race_status_race_uid   ON race_status(race_uid);


-- ================================================================
-- SECTION 2: SESSION AND SYSTEM STATE
-- ================================================================

-- ----------------------------------------------------------------
-- system_state
-- Singleton row (id=1). Global engine and betting config.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_state (
    id                      INTEGER     PRIMARY KEY DEFAULT 1,
    bankroll                NUMERIC(10,2)           DEFAULT 1000,
    current_pl              NUMERIC(10,2)           DEFAULT 0,
    bank_mode               TEXT                    DEFAULT 'STANDARD',
    active_code             TEXT                    DEFAULT 'GREYHOUND',
    posture                 TEXT                    DEFAULT 'NORMAL',
    sys_state               TEXT                    DEFAULT 'STABLE',
    variance                TEXT                    DEFAULT 'NORMAL',
    session_type            TEXT                    DEFAULT 'Live Betting',
    time_anchor             TEXT                    DEFAULT '',
    confidence_threshold    NUMERIC(4,2)            DEFAULT 0.65,
    ev_threshold            NUMERIC(4,2)            DEFAULT 0.08,
    staking_mode            TEXT                    DEFAULT 'KELLY',
    tempo_weight            NUMERIC(4,2)            DEFAULT 1.0,
    traffic_penalty         NUMERIC(4,2)            DEFAULT 0.8,
    closer_boost            NUMERIC(4,2)            DEFAULT 1.1,
    fade_penalty            NUMERIC(4,2)            DEFAULT 0.9,
    simulation_depth        INTEGER                 DEFAULT 1000,
    updated_at              TIMESTAMPTZ             DEFAULT NOW()
);

INSERT INTO system_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Backfill v8 system_state columns
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS confidence_threshold NUMERIC(4,2) DEFAULT 0.65;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS ev_threshold         NUMERIC(4,2) DEFAULT 0.08;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS staking_mode         TEXT         DEFAULT 'KELLY';
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS tempo_weight         NUMERIC(4,2) DEFAULT 1.0;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS traffic_penalty      NUMERIC(4,2) DEFAULT 0.8;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS closer_boost         NUMERIC(4,2) DEFAULT 1.1;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS fade_penalty         NUMERIC(4,2) DEFAULT 0.9;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS simulation_depth     INTEGER      DEFAULT 1000;

-- ----------------------------------------------------------------
-- sessions
-- Daily betting sessions.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date            DATE                    DEFAULT CURRENT_DATE,
    session_type    TEXT,
    account_type    TEXT,
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

CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);

-- ----------------------------------------------------------------
-- session_history
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS session_history (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID        REFERENCES sessions(id) ON DELETE SET NULL,
    date        DATE,
    pl          NUMERIC(10,2),
    bets        INTEGER,
    wins        INTEGER,
    strike_rate NUMERIC(5,2),
    roi         NUMERIC(6,2),
    bank_mode   TEXT,
    code        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);


-- ================================================================
-- SECTION 3: AUTHENTICATION AND USER MANAGEMENT
-- ================================================================

-- ----------------------------------------------------------------
-- users
-- Application user accounts (not Supabase auth).
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    username        TEXT        UNIQUE NOT NULL,
    password_hash   TEXT        NOT NULL,
    role            TEXT        NOT NULL    DEFAULT 'operator'
                                CHECK (role IN ('admin','operator','viewer')),
    active          BOOLEAN                 DEFAULT TRUE,
    last_login      TIMESTAMPTZ,
    login_count     INTEGER                 DEFAULT 0,
    last_ip         TEXT,
    display_name    TEXT,
    email           TEXT,
    created_by      TEXT,
    created_at      TIMESTAMPTZ             DEFAULT NOW(),
    updated_at      TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_role     ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_active   ON users(active);

-- Backfill extended user columns (added in migration 004)
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email        TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by   TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS login_count  INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_ip      TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at   TIMESTAMPTZ DEFAULT NOW();

-- ----------------------------------------------------------------
-- user_accounts
-- Per-user bankroll and settings. One row per user (FK users.id).
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
-- Per-user permission overrides on top of role defaults.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_permissions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    granted     TEXT[]                  DEFAULT '{}',
    revoked     TEXT[]                  DEFAULT '{}',
    effective   TEXT[]                  DEFAULT '{}',
    updated_at  TIMESTAMPTZ             DEFAULT NOW(),
    updated_by  TEXT
);

CREATE INDEX IF NOT EXISTS idx_user_permissions_user_id ON user_permissions(user_id);

-- ----------------------------------------------------------------
-- user_sessions
-- Active JWT token tracking (enables force-logout).
-- token_jti maps 1:1 to a JWT "jti" claim.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_jti   TEXT        NOT NULL UNIQUE,
    ip_address  TEXT,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ             DEFAULT NOW(),
    expires_at  TIMESTAMPTZ,
    revoked     BOOLEAN                 DEFAULT FALSE,
    revoked_at  TIMESTAMPTZ,
    revoked_by  TEXT
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id   ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_token_jti ON user_sessions(token_jti);
CREATE INDEX IF NOT EXISTS idx_user_sessions_revoked   ON user_sessions(revoked, expires_at);

-- ----------------------------------------------------------------
-- user_activity
-- Per-user searchable action history.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_activity (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action      TEXT        NOT NULL,
    detail      JSONB,
    ip_address  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_activity_user_id    ON user_activity(user_id);
CREATE INDEX IF NOT EXISTS idx_user_activity_created_at ON user_activity(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_activity_action     ON user_activity(action);

-- ----------------------------------------------------------------
-- audit_log
-- System-wide immutable audit trail.
-- Fields: event_type, resource, data, severity match users.py log_event().
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    username    TEXT,
    event_type  TEXT        NOT NULL,
    resource    TEXT,
    data        JSONB,
    ip          TEXT,
    severity    TEXT                    DEFAULT 'INFO'
                            CHECK (severity IN ('INFO','WARN','ERROR','CRITICAL')),
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill audit_log columns used by users.py log_event() BEFORE creating
-- indexes that reference them. On existing databases the CREATE TABLE above
-- is skipped, so event_type and severity must exist before index creation.
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS event_type TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS resource   TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS data       JSONB;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS severity   TEXT DEFAULT 'INFO';
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS ip         TEXT;

CREATE INDEX IF NOT EXISTS idx_audit_log_user_id    ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_severity   ON audit_log(severity);


-- ================================================================
-- SECTION 4: BETTING LAYER
-- ================================================================

-- ----------------------------------------------------------------
-- bet_log
-- All bet records. user_id FK enables per-user bankroll tracking.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bet_log (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              UUID        REFERENCES sessions(id) ON DELETE SET NULL,
    user_id                 UUID        REFERENCES users(id)    ON DELETE SET NULL,
    race_uid                TEXT                    DEFAULT '',
    date                    DATE                    DEFAULT CURRENT_DATE,
    track                   TEXT,
    race_num                INTEGER,
    code                    TEXT                    DEFAULT 'GREYHOUND',
    runner                  TEXT,
    box_num                 INTEGER,
    bet_type                TEXT,
    odds                    NUMERIC(8,2),
    stake                   NUMERIC(10,2),
    ev                      NUMERIC(6,3),
    ev_status               TEXT,
    confidence              TEXT,
    edge_type               TEXT,
    edge_status             TEXT,
    decision                TEXT,
    race_shape              TEXT,
    result                  TEXT                    DEFAULT 'PENDING',
    pl                      NUMERIC(10,2)           DEFAULT 0,
    error_tag               TEXT,
    manual_tag_override     BOOLEAN                 DEFAULT FALSE,
    placed_by               TEXT,
    signal                  TEXT,
    exotic_type             TEXT,
    created_at              TIMESTAMPTZ             DEFAULT NOW(),
    settled_at              TIMESTAMPTZ
);

-- Backfill bet_log columns BEFORE creating indexes that reference them.
-- On existing databases the CREATE TABLE above is skipped, so user_id and
-- race_uid must be present before the indexes below are created.
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS user_id             UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS race_uid            TEXT DEFAULT '';
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS manual_tag_override BOOLEAN DEFAULT FALSE;
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS placed_by           TEXT;
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS signal              TEXT;
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS exotic_type         TEXT;

CREATE INDEX IF NOT EXISTS idx_bet_log_session_id ON bet_log(session_id);
CREATE INDEX IF NOT EXISTS idx_bet_log_user_id    ON bet_log(user_id);
CREATE INDEX IF NOT EXISTS idx_bet_log_race_uid   ON bet_log(race_uid);
CREATE INDEX IF NOT EXISTS idx_bet_log_date       ON bet_log(date);
CREATE INDEX IF NOT EXISTS idx_bet_log_result     ON bet_log(result);
CREATE INDEX IF NOT EXISTS idx_bet_log_track_race ON bet_log(track, race_num);

-- ----------------------------------------------------------------
-- signals
-- Generated race signals (SNIPER, VALUE, GEM, etc.)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid        TEXT        NOT NULL UNIQUE,
    signal          TEXT        NOT NULL
                    CHECK (signal IN ('SNIPER','VALUE','GEM','WATCH','RISK','NO_BET')),
    confidence      NUMERIC(5,3),
    ev              NUMERIC(6,3),
    alert_level     TEXT                    DEFAULT 'NONE',
    hot_bet         BOOLEAN                 DEFAULT FALSE,
    risk_flags      JSONB                   DEFAULT '[]',
    top_runner      TEXT,
    top_box         INTEGER,
    top_odds        NUMERIC(6,2),
    generated_at    TIMESTAMPTZ             DEFAULT NOW(),
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_race_uid    ON signals(race_uid);
CREATE INDEX IF NOT EXISTS idx_signals_signal      ON signals(signal);
CREATE INDEX IF NOT EXISTS idx_signals_alert_level ON signals(alert_level);

-- ----------------------------------------------------------------
-- exotic_suggestions
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exotic_suggestions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid    TEXT        NOT NULL,
    signal      TEXT,
    exotic_type TEXT,
    selections  JSONB,
    cost        NUMERIC(10,2),
    est_return  NUMERIC(10,2),
    risk_level  TEXT,
    accepted    BOOLEAN                 DEFAULT FALSE,
    created_at  TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exotic_suggestions_race_uid ON exotic_suggestions(race_uid);


-- ================================================================
-- SECTION 5: PHASE 3 — INTELLIGENCE / AI TABLES
-- ================================================================

-- ----------------------------------------------------------------
-- feature_snapshots
-- Serialized feature arrays with full race lineage.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feature_snapshots (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid            TEXT        NOT NULL    DEFAULT '',
    oddspro_race_id     TEXT                    DEFAULT '',
    snapshot_date       DATE,
    runner_count        INTEGER                 DEFAULT 0,
    features            JSONB,
    has_sectionals      INTEGER                 DEFAULT 0,
    has_race_shape      INTEGER                 DEFAULT 0,
    has_collision       INTEGER                 DEFAULT 0,
    sectional_metrics   JSONB                   DEFAULT '[]',
    race_shape          JSONB                   DEFAULT '{}',
    collision_metrics   JSONB                   DEFAULT '[]',
    created_at          TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_race_uid ON feature_snapshots(race_uid);

-- Backfill Phase 4 columns on feature_snapshots
ALTER TABLE feature_snapshots ADD COLUMN IF NOT EXISTS has_sectionals    INTEGER DEFAULT 0;
ALTER TABLE feature_snapshots ADD COLUMN IF NOT EXISTS has_race_shape    INTEGER DEFAULT 0;
ALTER TABLE feature_snapshots ADD COLUMN IF NOT EXISTS has_collision     INTEGER DEFAULT 0;
ALTER TABLE feature_snapshots ADD COLUMN IF NOT EXISTS sectional_metrics JSONB   DEFAULT '[]';
ALTER TABLE feature_snapshots ADD COLUMN IF NOT EXISTS race_shape        JSONB   DEFAULT '{}';
ALTER TABLE feature_snapshots ADD COLUMN IF NOT EXISTS collision_metrics JSONB   DEFAULT '[]';

-- ----------------------------------------------------------------
-- prediction_snapshots
-- One row per prediction run for a race.
-- Includes Phase 4 sectionals/shape/collision flags and Phase 4.6
-- enrichment/source_type fields.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prediction_snapshots (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_snapshot_id  TEXT        NOT NULL UNIQUE,
    race_uid                TEXT        NOT NULL    DEFAULT '',
    oddspro_race_id         TEXT                    DEFAULT '',
    model_version           TEXT                    DEFAULT 'baseline_v1',
    feature_snapshot_id     TEXT                    DEFAULT '',
    runner_count            INTEGER                 DEFAULT 0,
    has_sectionals          INTEGER                 DEFAULT 0,
    has_race_shape          INTEGER                 DEFAULT 0,
    has_collision           INTEGER                 DEFAULT 0,
    has_enrichment          INTEGER                 DEFAULT 0,
    source_type             TEXT                    DEFAULT 'pre_race',
    created_at              TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_race_uid  ON prediction_snapshots(race_uid);
CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_snap_id   ON prediction_snapshots(prediction_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_model     ON prediction_snapshots(model_version);

-- Backfill Phase 4 / Phase 4.6 columns on prediction_snapshots
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS has_sectionals INTEGER DEFAULT 0;
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS has_race_shape  INTEGER DEFAULT 0;
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS has_collision   INTEGER DEFAULT 0;
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS has_enrichment  INTEGER DEFAULT 0;
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS source_type     TEXT    DEFAULT 'pre_race';

-- ----------------------------------------------------------------
-- prediction_runner_outputs
-- Per-runner scores and predicted ranks within a prediction run.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prediction_runner_outputs (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_snapshot_id  TEXT        NOT NULL    DEFAULT '',
    race_uid                TEXT        NOT NULL    DEFAULT '',
    runner_name             TEXT                    DEFAULT '',
    box_num                 INTEGER,
    predicted_rank          INTEGER,
    score                   NUMERIC(10,6),
    model_version           TEXT                    DEFAULT 'baseline_v1',
    created_at              TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prediction_runner_outputs_snap_id  ON prediction_runner_outputs(prediction_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_prediction_runner_outputs_race_uid ON prediction_runner_outputs(race_uid);

-- ----------------------------------------------------------------
-- learning_evaluations
-- Post-result evaluation records.
-- One row per (prediction_snapshot_id) — unique on that key.
-- Includes Phase 4.6 enrichment and disagreement tracking.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS learning_evaluations (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_snapshot_id      TEXT        NOT NULL UNIQUE DEFAULT '',
    race_uid                    TEXT        NOT NULL    DEFAULT '',
    oddspro_race_id             TEXT                    DEFAULT '',
    model_version               TEXT                    DEFAULT 'baseline_v1',
    predicted_winner            TEXT                    DEFAULT '',
    actual_winner               TEXT                    DEFAULT '',
    winner_hit                  BOOLEAN                 DEFAULT FALSE,
    top2_hit                    BOOLEAN                 DEFAULT FALSE,
    top3_hit                    BOOLEAN                 DEFAULT FALSE,
    predicted_rank_of_winner    INTEGER,
    winner_odds                 NUMERIC(8,2),
    used_enrichment             BOOLEAN                 DEFAULT FALSE,
    disagreement_score          NUMERIC(8,4),
    formfav_rank                INTEGER,
    your_rank                   INTEGER,
    evaluation_source           TEXT                    DEFAULT 'oddspro',
    evaluated_at                TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learning_evaluations_race_uid ON learning_evaluations(race_uid);
CREATE INDEX IF NOT EXISTS idx_learning_evaluations_model    ON learning_evaluations(model_version);
CREATE INDEX IF NOT EXISTS idx_learning_evaluations_snap_id  ON learning_evaluations(prediction_snapshot_id);

-- Backfill Phase 4.6 columns on learning_evaluations
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS used_enrichment    BOOLEAN  DEFAULT FALSE;
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS disagreement_score NUMERIC(8,4);
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS formfav_rank       INTEGER;
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS your_rank          INTEGER;

-- ----------------------------------------------------------------
-- backtest_runs
-- High-level backtest run summaries.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_runs (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              TEXT        NOT NULL UNIQUE,
    date_from           DATE        NOT NULL,
    date_to             DATE        NOT NULL,
    code_filter         TEXT                    DEFAULT '',
    track_filter        TEXT                    DEFAULT '',
    model_version       TEXT                    DEFAULT 'baseline_v1',
    total_races         INTEGER                 DEFAULT 0,
    total_runners       INTEGER                 DEFAULT 0,
    winner_hit_count    INTEGER                 DEFAULT 0,
    top2_hit_count      INTEGER                 DEFAULT 0,
    top3_hit_count      INTEGER                 DEFAULT 0,
    hit_rate            NUMERIC(8,4)            DEFAULT 0,
    top2_rate           NUMERIC(8,4)            DEFAULT 0,
    top3_rate           NUMERIC(8,4)            DEFAULT 0,
    avg_winner_odds     NUMERIC(8,2),
    created_at          TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_run_id      ON backtest_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_model       ON backtest_runs(model_version);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_date_from   ON backtest_runs(date_from);

-- ----------------------------------------------------------------
-- backtest_run_items
-- Per-race results within a backtest run.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_run_items (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  TEXT        NOT NULL    DEFAULT '',
    race_uid                TEXT        NOT NULL    DEFAULT '',
    race_date               DATE,
    track                   TEXT                    DEFAULT '',
    code                    TEXT                    DEFAULT '',
    runner_count            INTEGER                 DEFAULT 0,
    predicted_winner        TEXT                    DEFAULT '',
    actual_winner           TEXT                    DEFAULT '',
    winner_hit              BOOLEAN                 DEFAULT FALSE,
    top2_hit                BOOLEAN                 DEFAULT FALSE,
    top3_hit                BOOLEAN                 DEFAULT FALSE,
    score                   NUMERIC(10,6),
    winner_odds             NUMERIC(8,2),
    model_version           TEXT                    DEFAULT 'baseline_v1',
    used_stored_snapshot    BOOLEAN                 DEFAULT FALSE,
    created_at              TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill Phase 4 columns on backtest_run_items BEFORE creating indexes
-- that reference them. On existing databases the CREATE TABLE above is skipped.
ALTER TABLE backtest_run_items ADD COLUMN IF NOT EXISTS model_version        TEXT    DEFAULT 'baseline_v1';
ALTER TABLE backtest_run_items ADD COLUMN IF NOT EXISTS used_stored_snapshot BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_backtest_run_items_run_id  ON backtest_run_items(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_run_items_race_uid ON backtest_run_items(race_uid);
CREATE INDEX IF NOT EXISTS idx_backtest_run_items_model   ON backtest_run_items(model_version);


-- ================================================================
-- SECTION 6: PHASE 4 — FEATURE ENGINE / SECTIONALS / RACE SHAPE
-- ================================================================

-- ----------------------------------------------------------------
-- sectional_snapshots
-- Per-runner OddsPro sectional metrics.
-- source_type: 'pre_race' (form data) or 'result' (official post-race).
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sectional_snapshots (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid                    TEXT        NOT NULL    DEFAULT '',
    oddspro_race_id             TEXT                    DEFAULT '',
    box_num                     INTEGER,
    runner_name                 TEXT                    DEFAULT '',
    early_speed_score           NUMERIC(10,6),
    late_speed_score            NUMERIC(10,6),
    closing_delta               NUMERIC(10,4),
    fatigue_index               NUMERIC(10,4),
    acceleration_index          NUMERIC(10,4),
    sectional_consistency_score NUMERIC(10,4),
    raw_early_time              NUMERIC(10,3),
    raw_mid_time                NUMERIC(10,3),
    raw_late_time               NUMERIC(10,3),
    raw_all_sections            JSONB,
    source                      TEXT                    DEFAULT 'oddspro_result',
    source_type                 TEXT                    DEFAULT 'pre_race',
    created_at                  TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill Phase 4.5 source_type column BEFORE creating indexes that
-- reference it. On existing databases the CREATE TABLE above is skipped.
ALTER TABLE sectional_snapshots ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'pre_race';

CREATE INDEX IF NOT EXISTS idx_sectional_snapshots_race_uid ON sectional_snapshots(race_uid);
CREATE INDEX IF NOT EXISTS idx_sectional_snapshots_box      ON sectional_snapshots(race_uid, box_num);
CREATE INDEX IF NOT EXISTS idx_sectional_snapshots_source_type ON sectional_snapshots(source_type);

-- ----------------------------------------------------------------
-- race_shape_snapshots
-- One row per race — race-level shape/tempo analysis.
-- Supports GREYHOUND, HARNESS, GALLOPS via is_greyhound flag.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS race_shape_snapshots (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid                    TEXT        NOT NULL UNIQUE DEFAULT '',
    oddspro_race_id             TEXT                    DEFAULT '',
    pace_scenario               TEXT                    DEFAULT 'UNKNOWN',
    early_speed_density         NUMERIC(8,4),
    leader_pressure             NUMERIC(8,4),
    likely_leader_runner_ids    JSONB,
    early_speed_conflict_score  NUMERIC(8,4),
    collapse_risk               NUMERIC(8,4),
    closer_advantage_score      NUMERIC(8,4),
    is_greyhound                BOOLEAN                 DEFAULT FALSE,
    sectionals_used             BOOLEAN                 DEFAULT FALSE,
    formfav_enrichment_used     BOOLEAN                 DEFAULT FALSE,
    created_at                  TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_race_shape_snapshots_race_uid ON race_shape_snapshots(race_uid);


-- ================================================================
-- SECTION 7: LEARNING ENGINE TABLES
-- ================================================================

-- ----------------------------------------------------------------
-- epr_data
-- Edge Performance Registry — per-bet outcome tracking.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS epr_data (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    edge_type       TEXT        NOT NULL,
    code            TEXT                    DEFAULT 'GREYHOUND',
    track           TEXT,
    distance        TEXT,
    condition       TEXT,
    confidence_tier TEXT,
    ev_at_analysis  NUMERIC(6,3),
    result          TEXT,
    pl              NUMERIC(10,2),
    execution_mode  TEXT,
    meeting_state   TEXT,
    session_id      TEXT,
    date            DATE                    DEFAULT CURRENT_DATE,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill any missing columns on epr_data BEFORE creating indexes that
-- reference them. On existing databases the CREATE TABLE above is skipped.
ALTER TABLE epr_data ADD COLUMN IF NOT EXISTS session_id    TEXT;
ALTER TABLE epr_data ADD COLUMN IF NOT EXISTS meeting_state TEXT;
ALTER TABLE epr_data ADD COLUMN IF NOT EXISTS condition     TEXT;
ALTER TABLE epr_data ADD COLUMN IF NOT EXISTS date         DATE DEFAULT CURRENT_DATE;

CREATE INDEX IF NOT EXISTS idx_epr_data_edge_type ON epr_data(edge_type);
CREATE INDEX IF NOT EXISTS idx_epr_data_code      ON epr_data(code);
CREATE INDEX IF NOT EXISTS idx_epr_data_date      ON epr_data(date);

-- ----------------------------------------------------------------
-- aeee_adjustments
-- Automatic Edge Expectation Engine adjustments.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aeee_adjustments (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID        REFERENCES sessions(id) ON DELETE SET NULL,
    edge_type       TEXT,
    direction       TEXT,
    amount          NUMERIC(5,3),
    reason          TEXT,
    roi_trigger     NUMERIC(6,2),
    bets_sample     INTEGER,
    applied         BOOLEAN                 DEFAULT FALSE,
    promoted        BOOLEAN                 DEFAULT FALSE,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill session_id on aeee_adjustments BEFORE creating the index that
-- references it. On existing databases the CREATE TABLE above is skipped and
-- migration 001 created this table without the session_id column.
ALTER TABLE aeee_adjustments ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES sessions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_aeee_adjustments_session_id ON aeee_adjustments(session_id);
CREATE INDEX IF NOT EXISTS idx_aeee_adjustments_edge_type  ON aeee_adjustments(edge_type);

-- ----------------------------------------------------------------
-- etg_tags
-- Error Tagging Guide — per-bet error classification.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etg_tags (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    bet_id          UUID,
    session_id      UUID        REFERENCES sessions(id) ON DELETE SET NULL,
    race_uid        TEXT,
    date            DATE                    DEFAULT CURRENT_DATE,
    track           TEXT,
    race_num        INTEGER,
    error_tag       TEXT        NOT NULL,
    notes           TEXT,
    manual_override BOOLEAN                 DEFAULT FALSE,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

-- Backfill missing columns on etg_tags BEFORE creating indexes that reference
-- them. On existing databases the CREATE TABLE above is skipped and migration
-- 001 created this table without session_id.
ALTER TABLE etg_tags ADD COLUMN IF NOT EXISTS session_id      UUID    REFERENCES sessions(id) ON DELETE SET NULL;
ALTER TABLE etg_tags ADD COLUMN IF NOT EXISTS manual_override BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_etg_tags_bet_id     ON etg_tags(bet_id);
CREATE INDEX IF NOT EXISTS idx_etg_tags_session_id ON etg_tags(session_id);
CREATE INDEX IF NOT EXISTS idx_etg_tags_race_uid   ON etg_tags(race_uid);

-- ----------------------------------------------------------------
-- pass_log
-- Auto-skip learning — log why races were passed.
-- Conflict key: race_uid
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pass_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid        TEXT        NOT NULL UNIQUE,
    pass_reason     TEXT,
    local_decision  TEXT,
    confidence      TEXT,
    date            DATE                    DEFAULT CURRENT_DATE,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pass_log_race_uid ON pass_log(race_uid);
CREATE INDEX IF NOT EXISTS idx_pass_log_date     ON pass_log(date);

-- ----------------------------------------------------------------
-- gpil_patterns
-- GPIL (Global Pattern Intelligence Layer) detected patterns.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gpil_patterns (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_type    TEXT,
    code            TEXT                    DEFAULT 'GREYHOUND',
    description     TEXT,
    bets_sample     INTEGER                 DEFAULT 0,
    roi             NUMERIC(6,2),
    status          TEXT                    DEFAULT 'INSUFFICIENT',
    mif_modifier    INTEGER                 DEFAULT 0,
    first_detected  TIMESTAMPTZ             DEFAULT NOW(),
    last_updated    TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gpil_patterns_pattern_type ON gpil_patterns(pattern_type);
CREATE INDEX IF NOT EXISTS idx_gpil_patterns_code         ON gpil_patterns(code);


-- ================================================================
-- SECTION 8: SIMULATION
-- ================================================================

-- ----------------------------------------------------------------
-- simulation_log
-- Persist every /api/simulator/run result.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS simulation_log (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid            TEXT,
    user_id             UUID        REFERENCES users(id) ON DELETE SET NULL,
    engine              TEXT        NOT NULL    DEFAULT 'monte_carlo',
    n_runs              INTEGER     NOT NULL    DEFAULT 0,
    race_code           TEXT                    DEFAULT 'GREYHOUND',
    track               TEXT,
    distance_m          INTEGER,
    condition           TEXT,
    decision            TEXT,
    confidence_score    NUMERIC(5,3),
    chaos_rating        TEXT,
    pace_type           TEXT,
    top_runner          TEXT,
    top_win_pct         NUMERIC(5,2),
    results_json        JSONB,
    simulation_summary  TEXT,
    created_at          TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_simulation_log_race_uid   ON simulation_log(race_uid);
CREATE INDEX IF NOT EXISTS idx_simulation_log_user_id    ON simulation_log(user_id);
CREATE INDEX IF NOT EXISTS idx_simulation_log_created_at ON simulation_log(created_at DESC);


-- ================================================================
-- SECTION 9: LOGGING AND SUPPORT TABLES
-- ================================================================

-- ----------------------------------------------------------------
-- source_log
-- HTTP request log for all data source calls.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date            DATE                    DEFAULT CURRENT_DATE,
    call_num        INTEGER,
    url             TEXT,
    method          TEXT,
    status          TEXT,
    grv_detected    BOOLEAN                 DEFAULT FALSE,
    rows_returned   INTEGER,
    created_at      TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_source_log_date ON source_log(date);

-- Backfill call_num column
ALTER TABLE source_log ADD COLUMN IF NOT EXISTS call_num INTEGER;

-- ----------------------------------------------------------------
-- activity_log
-- General application activity log.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_log (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  TEXT,
    event_type  TEXT,
    description TEXT,
    data        JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activity_log_session_id ON activity_log(session_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_created_at ON activity_log(created_at);

-- ----------------------------------------------------------------
-- chat_history
-- AI assistant conversation history.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_history (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    role        TEXT        NOT NULL,
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_history_session ON chat_history(session_id);

-- ----------------------------------------------------------------
-- changelog
-- Internal system changelog.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS changelog (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_date  TIMESTAMPTZ             DEFAULT NOW(),
    entry_type  TEXT,
    entry_id    TEXT,
    severity    TEXT,
    description TEXT,
    supersedes  TEXT,
    status      TEXT                    DEFAULT 'ACTIVE'
);

CREATE INDEX IF NOT EXISTS idx_changelog_entry_date ON changelog(entry_date);


-- ================================================================
-- SECTION 10: PERFORMANCE TRACKING TABLES
-- ================================================================

CREATE TABLE IF NOT EXISTS performance_daily (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    date        DATE        UNIQUE,
    total_bets  INTEGER                 DEFAULT 0,
    wins        INTEGER                 DEFAULT 0,
    losses      INTEGER                 DEFAULT 0,
    pl          NUMERIC(10,2)           DEFAULT 0,
    roi         NUMERIC(6,2)            DEFAULT 0,
    strike_rate NUMERIC(5,2)            DEFAULT 0,
    avg_odds    NUMERIC(6,2)            DEFAULT 0,
    code        TEXT                    DEFAULT 'GREYHOUND',
    updated_at  TIMESTAMPTZ             DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS performance_by_track (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    track       TEXT        NOT NULL,
    code        TEXT        NOT NULL    DEFAULT 'GREYHOUND',
    total_bets  INTEGER                 DEFAULT 0,
    wins        INTEGER                 DEFAULT 0,
    pl          NUMERIC(10,2)           DEFAULT 0,
    roi         NUMERIC(6,2)            DEFAULT 0,
    strike_rate NUMERIC(5,2)            DEFAULT 0,
    updated_at  TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (track, code)
);

CREATE TABLE IF NOT EXISTS performance_by_edge (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    edge_type   TEXT        NOT NULL,
    code        TEXT        NOT NULL    DEFAULT 'GREYHOUND',
    total_bets  INTEGER                 DEFAULT 0,
    wins        INTEGER                 DEFAULT 0,
    pl          NUMERIC(10,2)           DEFAULT 0,
    roi         NUMERIC(6,2)            DEFAULT 0,
    strike_rate NUMERIC(5,2)            DEFAULT 0,
    updated_at  TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (edge_type, code)
);


-- ================================================================
-- SECTION 11: SECONDARY / SUPPLEMENTAL TABLES
-- ================================================================

CREATE TABLE IF NOT EXISTS scratch_log (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid        TEXT,
    date            DATE                    DEFAULT CURRENT_DATE,
    track           TEXT,
    race_num        INTEGER,
    box_num         INTEGER,
    runner_name     TEXT,
    scratch_timing  TEXT                    DEFAULT 'early',
    confirmed_at    TIMESTAMPTZ             DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid        TEXT,
    date            DATE                    DEFAULT CURRENT_DATE,
    track           TEXT,
    race_num        INTEGER,
    runner_name     TEXT,
    box_num         INTEGER,
    opening_price   NUMERIC(8,2),
    analysis_price  NUMERIC(8,2),
    final_sp        NUMERIC(8,2),
    price_movement  TEXT,
    market_rank     INTEGER,
    overround       NUMERIC(6,2),
    steam_flag      BOOLEAN                 DEFAULT FALSE,
    drift_flag      BOOLEAN                 DEFAULT FALSE,
    mvi_score       INTEGER                 DEFAULT 0,
    snapshot_time   TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_date_track_race ON market_snapshots(date, track, race_num);

CREATE TABLE IF NOT EXISTS runner_profiles (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    runner_name         TEXT        NOT NULL,
    code                TEXT                    DEFAULT 'GREYHOUND',
    trainer             TEXT,
    career_starts       INTEGER                 DEFAULT 0,
    career_wins         INTEGER                 DEFAULT 0,
    career_places       INTEGER                 DEFAULT 0,
    career_prize_money  NUMERIC(10,2)           DEFAULT 0,
    consistency_index   INTEGER                 DEFAULT 0,
    updated_at          TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (runner_name, code, trainer)
);

CREATE TABLE IF NOT EXISTS form_runs (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    runner_name         TEXT        NOT NULL,
    trainer             TEXT,
    code                TEXT                    DEFAULT 'GREYHOUND',
    race_date           DATE,
    track               TEXT,
    race_num            INTEGER,
    distance            INTEGER,
    grade               TEXT,
    box_num             INTEGER,
    finish_position     INTEGER,
    margin              NUMERIC(6,2),
    starting_price      NUMERIC(8,2),
    split_1             NUMERIC(6,3),
    split_2             NUMERIC(6,3),
    final_time          NUMERIC(7,3),
    jockey_driver       TEXT,
    track_condition     TEXT,
    stewards_comments   TEXT,
    interference        BOOLEAN                 DEFAULT FALSE,
    prize_won           NUMERIC(10,2),
    created_at          TIMESTAMPTZ             DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_form_runs_runner_name ON form_runs(runner_name);
CREATE INDEX IF NOT EXISTS idx_form_runs_race_date   ON form_runs(race_date);
CREATE INDEX IF NOT EXISTS idx_form_runs_track       ON form_runs(track);

CREATE TABLE IF NOT EXISTS track_profiles (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    track_name      TEXT        NOT NULL,
    state           TEXT,
    code            TEXT                    DEFAULT 'GREYHOUND',
    inside_bias     NUMERIC(5,2),
    outside_bias    NUMERIC(5,2),
    early_speed_bias NUMERIC(5,2),
    closer_bias     NUMERIC(5,2),
    leader_win_pct  NUMERIC(5,2),
    condition_drift TEXT,
    updated_at      TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (track_name, code)
);

CREATE TABLE IF NOT EXISTS scored_races (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid                    TEXT        UNIQUE NOT NULL,
    decision                    TEXT,
    confidence                  TEXT,
    selection                   TEXT,
    box_num                     INTEGER,
    race_shape                  TEXT,
    pace_type                   TEXT,
    collapse_risk               TEXT,
    pressure_score              INTEGER,
    separation                  TEXT,
    crash_map                   TEXT,
    false_favourite_json        TEXT,
    filters_json                TEXT,
    audit_json                  TEXT,
    confidence_breakdown_json   TEXT,
    packet_snapshot             TEXT,
    packet_version              TEXT,
    scorer_version              TEXT,
    scored_at                   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scored_races_race_uid ON scored_races(race_uid);

CREATE TABLE IF NOT EXISTS training_logs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      TEXT,
    epoch           INTEGER,
    accuracy        REAL,
    roi             REAL,
    drawdown        REAL,
    win_rate        REAL,
    top3_rate       REAL,
    error_tempo     REAL,
    error_position  REAL,
    error_traffic   REAL,
    error_distance  REAL,
    error_condition REAL,
    error_variance  REAL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sectional_benchmarks (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    track           TEXT,
    distance        INTEGER,
    code            TEXT                    DEFAULT 'GREYHOUND',
    grade           TEXT,
    avg_split_1     NUMERIC(6,3),
    avg_split_2     NUMERIC(6,3),
    avg_final_time  NUMERIC(7,3),
    sample_size     INTEGER                 DEFAULT 0,
    updated_at      TIMESTAMPTZ             DEFAULT NOW(),
    UNIQUE (track, distance, code, grade)
);


-- ================================================================
-- SECTION 12: TEST-MODE MIRROR TABLES
-- In TEST mode, env.table() prefixes all testable tables with
-- "test_" so production data is never touched.
-- ================================================================

CREATE TABLE IF NOT EXISTS test_today_races (     LIKE today_races     INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_today_runners (   LIKE today_runners   INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_bet_log (         LIKE bet_log         INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_signals (         LIKE signals         INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_sessions (        LIKE sessions        INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_system_state (    LIKE system_state    INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_activity_log (    LIKE activity_log    INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_exotic_suggestions(LIKE exotic_suggestions INCLUDING ALL);
CREATE TABLE IF NOT EXISTS test_etg_tags (        LIKE etg_tags        INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_epr_data (        LIKE epr_data        INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_aeee_adjustments (LIKE aeee_adjustments INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_pass_log (        LIKE pass_log        INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_source_log (      LIKE source_log      INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_user_accounts (   LIKE user_accounts   INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_user_permissions (LIKE user_permissions INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_user_sessions (   LIKE user_sessions   INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_user_activity (   LIKE user_activity   INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_simulation_log (  LIKE simulation_log  INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_feature_snapshots (         LIKE feature_snapshots          INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_prediction_snapshots (      LIKE prediction_snapshots       INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_prediction_runner_outputs ( LIKE prediction_runner_outputs  INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_learning_evaluations (      LIKE learning_evaluations       INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_backtest_runs (             LIKE backtest_runs              INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_backtest_run_items (        LIKE backtest_run_items         INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_sectional_snapshots (       LIKE sectional_snapshots        INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_race_shape_snapshots (      LIKE race_shape_snapshots       INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_chat_history (    LIKE chat_history    INCLUDING ALL );
CREATE TABLE IF NOT EXISTS test_training_logs (   LIKE training_logs   INCLUDING ALL );

-- Remove NOT NULL on race_uid in test_today_races to allow test-mode inserts
-- without a pre-generated race_uid
ALTER TABLE test_today_races ALTER COLUMN race_uid DROP NOT NULL;

-- ----------------------------------------------------------------
-- Backfill new columns into test_ tables that were created by migration 003
-- (before 006 added new columns to their source tables).
-- The CREATE TABLE IF NOT EXISTS above is a no-op when these tables already
-- exist, so every new column must be added individually with ADD COLUMN IF
-- NOT EXISTS.
-- ----------------------------------------------------------------

-- test_today_races — new columns from 006 not present in 003-era table
ALTER TABLE test_today_races ADD COLUMN IF NOT EXISTS oddspro_race_id   TEXT         DEFAULT '';
ALTER TABLE test_today_races ADD COLUMN IF NOT EXISTS block_code        TEXT         DEFAULT '';
ALTER TABLE test_today_races ADD COLUMN IF NOT EXISTS source            TEXT         DEFAULT 'oddspro';
ALTER TABLE test_today_races ADD COLUMN IF NOT EXISTS condition         TEXT         DEFAULT '';
ALTER TABLE test_today_races ADD COLUMN IF NOT EXISTS race_name         TEXT         DEFAULT '';
ALTER TABLE test_today_races ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ  DEFAULT NOW();
ALTER TABLE test_today_races ADD COLUMN IF NOT EXISTS completed_at      TIMESTAMPTZ;

-- test_today_runners — new columns from 006 not present in 003-era table
ALTER TABLE test_today_runners ADD COLUMN IF NOT EXISTS oddspro_race_id  TEXT                 DEFAULT '';
ALTER TABLE test_today_runners ADD COLUMN IF NOT EXISTS number           INTEGER;
ALTER TABLE test_today_runners ADD COLUMN IF NOT EXISTS barrier          INTEGER;
ALTER TABLE test_today_runners ADD COLUMN IF NOT EXISTS jockey           TEXT                 DEFAULT '';
ALTER TABLE test_today_runners ADD COLUMN IF NOT EXISTS driver           TEXT                 DEFAULT '';
ALTER TABLE test_today_runners ADD COLUMN IF NOT EXISTS price            NUMERIC(10,4);
ALTER TABLE test_today_runners ADD COLUMN IF NOT EXISTS rating           NUMERIC(10,4);
ALTER TABLE test_today_runners ADD COLUMN IF NOT EXISTS source_confidence TEXT                DEFAULT 'official';
ALTER TABLE test_today_runners ADD COLUMN IF NOT EXISTS scratch_reason   TEXT                 DEFAULT '';

-- test_bet_log — user_id was added by migration 004 to bet_log; test_ table
-- from 003 predates that and needs the FK column added.
ALTER TABLE test_bet_log ADD COLUMN IF NOT EXISTS user_id             UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE test_bet_log ADD COLUMN IF NOT EXISTS placed_by           TEXT;
ALTER TABLE test_bet_log ADD COLUMN IF NOT EXISTS signal              TEXT;
ALTER TABLE test_bet_log ADD COLUMN IF NOT EXISTS exotic_type         TEXT;
ALTER TABLE test_bet_log ADD COLUMN IF NOT EXISTS manual_tag_override BOOLEAN DEFAULT FALSE;

-- test_etg_tags — session_id added by 006; 003-era table predates it
ALTER TABLE test_etg_tags ADD COLUMN IF NOT EXISTS session_id      UUID REFERENCES sessions(id) ON DELETE SET NULL;
ALTER TABLE test_etg_tags ADD COLUMN IF NOT EXISTS manual_override BOOLEAN DEFAULT FALSE;

-- test_aeee_adjustments — session_id added by 006; 003-era table predates it
ALTER TABLE test_aeee_adjustments ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES sessions(id) ON DELETE SET NULL;

-- test_epr_data — meeting_state, condition and session_id added by 006
ALTER TABLE test_epr_data ADD COLUMN IF NOT EXISTS session_id    TEXT;
ALTER TABLE test_epr_data ADD COLUMN IF NOT EXISTS meeting_state TEXT;
ALTER TABLE test_epr_data ADD COLUMN IF NOT EXISTS condition     TEXT;
ALTER TABLE test_epr_data ADD COLUMN IF NOT EXISTS date          DATE DEFAULT CURRENT_DATE;

-- test_source_log — call_num added by 006
ALTER TABLE test_source_log ADD COLUMN IF NOT EXISTS call_num INTEGER;

-- Seed test_system_state row
INSERT INTO test_system_state (id, bankroll, current_pl, bank_mode, active_code,
    posture, sys_state, variance, session_type, time_anchor)
VALUES (1, 10000, 0, 'STANDARD', 'GREYHOUND', 'NORMAL', 'STABLE', 'NORMAL', 'Test Session', '')
ON CONFLICT DO NOTHING;

-- Test-mode indexes
CREATE INDEX IF NOT EXISTS idx_test_today_races_date    ON test_today_races(date);
CREATE INDEX IF NOT EXISTS idx_test_today_runners_uid   ON test_today_runners(race_uid);
CREATE INDEX IF NOT EXISTS idx_test_bet_log_date        ON test_bet_log(date);
CREATE INDEX IF NOT EXISTS idx_test_signals_race_uid    ON test_signals(race_uid);
CREATE INDEX IF NOT EXISTS idx_test_source_log_date     ON test_source_log(date);


-- ================================================================
-- SECTION 13: HELPER FUNCTIONS AND TRIGGERS
-- ================================================================

-- ----------------------------------------------------------------
-- Function: auto-create user_accounts + user_permissions on user INSERT
-- ----------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_user_account()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO user_accounts (user_id)
    VALUES (NEW.id)
    ON CONFLICT (user_id) DO NOTHING;

    INSERT INTO user_permissions (user_id, effective)
    VALUES (NEW.id, '{}')
    ON CONFLICT (user_id) DO NOTHING;

    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'create_user_account trigger failed for user %: %', NEW.id, SQLERRM;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_create_user_account ON users;
CREATE TRIGGER trg_create_user_account
    AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION create_user_account();

-- ----------------------------------------------------------------
-- Function: atomic login counter increment (avoids read-modify-write race)
-- ----------------------------------------------------------------
CREATE OR REPLACE FUNCTION increment_login_count(p_user_id UUID)
RETURNS void AS $$
BEGIN
    UPDATE users
    SET login_count = COALESCE(login_count, 0) + 1
    WHERE id = p_user_id;
END;
$$ LANGUAGE plpgsql;


-- ================================================================
-- SECTION 14: BACKFILL — existing users missing account/permission rows
-- ================================================================

INSERT INTO user_accounts (user_id)
SELECT u.id FROM users u
WHERE NOT EXISTS (SELECT 1 FROM user_accounts a WHERE a.user_id = u.id)
ON CONFLICT DO NOTHING;

INSERT INTO user_permissions (user_id, effective)
SELECT u.id, '{}'
FROM users u
WHERE NOT EXISTS (SELECT 1 FROM user_permissions p WHERE p.user_id = u.id)
ON CONFLICT DO NOTHING;


-- ================================================================
-- SECTION 15: CROSS-SCHEMA CONSISTENCY GUARDS
-- Ensure every column referenced by Python repositories exists
-- regardless of which schema version was originally applied.
-- All statements are idempotent (IF NOT EXISTS).
-- ================================================================

-- audit_log: logs_repo.py inserts 'ip_address'; canonical schema (001) defines 'ip'.
-- Add ip_address so Python callers work on databases upgraded from the canonical schema.
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS ip_address TEXT;

-- user_activity: users_repo.py log_activity() inserts 'resource'
ALTER TABLE user_activity ADD COLUMN IF NOT EXISTS resource TEXT;

-- user_permissions: users_repo.py set_permission() now uses the array-based
-- approach (on_conflict="user_id"), so no per-page columns needed here.
-- Ensure UNIQUE(user_id) constraint exists for array upserts.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = 'user_permissions'::regclass
          AND c.contype  = 'u'
          AND array_length(c.conkey, 1) = 1
          AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey) AND attname = 'user_id')
    ) THEN
        ALTER TABLE user_permissions
            ADD CONSTRAINT user_permissions_user_id_key UNIQUE (user_id);
    END IF;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- learning_evaluations: learning_repo.py writes date, track, race_code,
-- score_at_prediction, win_price, pl_outcome.
-- race_code is also required by v_prediction_accuracy view in 003_views_optional.sql.
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS date                DATE;
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS track               TEXT        DEFAULT '';
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS race_code           TEXT        DEFAULT 'GREYHOUND';
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS score_at_prediction NUMERIC;
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS win_price           NUMERIC;
ALTER TABLE learning_evaluations ADD COLUMN IF NOT EXISTS pl_outcome          NUMERIC;

-- backtest_runs: backtesting_repo.py writes race_code, winner_hits, top3_hits,
-- winner_accuracy, total_pl, roi, status, notes.
-- race_code, winner_hits, winner_accuracy, status are also required by
-- v_backtest_summary view in 003_views_optional.sql.
-- updated_at is written by BacktestingRepo.update_run().
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS race_code        TEXT        DEFAULT 'GREYHOUND';
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS winner_hits      INTEGER     DEFAULT 0;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS top3_hits        INTEGER     DEFAULT 0;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS winner_accuracy  NUMERIC;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS total_pl         NUMERIC;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS roi              NUMERIC;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS status           TEXT        DEFAULT 'completed';
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS notes            TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMPTZ DEFAULT NOW();

-- backtest_run_items: backtesting_repo.py _build_item_payload writes 'date' and
-- 'race_code'; canonical schema uses 'race_date' and 'code'. Add alias columns so
-- Python callers work on databases upgraded from the canonical schema.
ALTER TABLE backtest_run_items ADD COLUMN IF NOT EXISTS date        DATE;
ALTER TABLE backtest_run_items ADD COLUMN IF NOT EXISTS race_code   TEXT        DEFAULT 'GREYHOUND';
ALTER TABLE backtest_run_items ADD COLUMN IF NOT EXISTS win_price   NUMERIC;
ALTER TABLE backtest_run_items ADD COLUMN IF NOT EXISTS pl          NUMERIC;

-- source_log: logs_repo.py log_source_call() inserts source, endpoint, method,
-- status_code, response_ms, success, error_msg, records_fetched.
-- Canonical schema uses a different column set (date, call_num, url, status…).
ALTER TABLE source_log ADD COLUMN IF NOT EXISTS source          TEXT;
ALTER TABLE source_log ADD COLUMN IF NOT EXISTS endpoint        TEXT;
ALTER TABLE source_log ADD COLUMN IF NOT EXISTS status_code     INTEGER;
ALTER TABLE source_log ADD COLUMN IF NOT EXISTS response_ms     INTEGER;
ALTER TABLE source_log ADD COLUMN IF NOT EXISTS success         BOOLEAN     DEFAULT TRUE;
ALTER TABLE source_log ADD COLUMN IF NOT EXISTS error_msg       TEXT;
ALTER TABLE source_log ADD COLUMN IF NOT EXISTS records_fetched INTEGER;

-- activity_log: logs_repo.py log_activity() inserts 'event', 'resource', 'detail'.
-- Canonical schema uses 'event_type', 'description', 'data'. Add alias columns so
-- Python callers work on databases upgraded from the canonical schema.
ALTER TABLE activity_log ADD COLUMN IF NOT EXISTS event    TEXT;
ALTER TABLE activity_log ADD COLUMN IF NOT EXISTS resource TEXT;
ALTER TABLE activity_log ADD COLUMN IF NOT EXISTS detail   JSONB;

-- ================================================================
-- DONE — Migration 006 complete.
--
-- What was reconciled:
--   today_races      — added block_code, source, oddspro_race_id,
--                      condition, race_name, updated_at, completeness_*,
--                      lifecycle_state, all lifecycle timestamps;
--                      all backfills moved before their indexes
--   today_runners    — added race_uid, oddspro_race_id, number, barrier,
--                      jockey, driver, price, rating, source_confidence;
--                      added scratch_reason (migration 001 used scratch_timing;
--                      app now writes scratch_reason — was missing from backfill
--                      list in all prior versions of this migration);
--                      backfills moved before their indexes
--   results_log      — ensured (date,track,race_num,code) unique constraint;
--                      race_uid backfill moved before its index
--   race_status      — race_uid backfill moved before its index
--   prediction_snapshots — added has_enrichment, source_type (Phase 4.6)
--   learning_evaluations — added used_enrichment, disagreement_score,
--                          formfav_rank, your_rank (Phase 4.6)
--   users            — added display_name, email, created_by, login_count,
--                      last_ip, updated_at
--   bet_log          — added user_id, race_uid, placed_by, signal, exotic_type;
--                      backfills moved before their indexes
--   source_log       — added call_num
--   audit_log        — added event_type (was missing from backfill list),
--                      resource, data, severity, ip; all backfills moved
--                      before their indexes (fixes "column does not exist" on
--                      existing databases)
--   backtest_run_items — model_version backfill moved before its index
--   sectional_snapshots — source_type backfill moved before its index
--   epr_data         — date backfill moved before its index
--   aeee_adjustments — added session_id backfill (ALTER TABLE … ADD COLUMN
--                      IF NOT EXISTS) before the session_id index; migration
--                      001 created this table without session_id causing
--                      "column session_id does not exist" on index creation
--   etg_tags         — added session_id backfill before session_id index;
--                      moved manual_override backfill to before all indexes
--                      (same root cause as aeee_adjustments.session_id)
--   All Phase 3/4/4.5/4.6 intelligence tables fully defined
--   All test_ mirror tables ensured for TEST mode isolation:
--     test_today_races     — backfilled oddspro_race_id, block_code, source,
--                            condition, race_name, updated_at, completed_at
--     test_today_runners   — backfilled oddspro_race_id, number, barrier,
--                            jockey, driver, price, rating, source_confidence,
--                            scratch_reason
--     test_bet_log         — backfilled user_id, placed_by, signal, exotic_type,
--                            manual_tag_override
--     test_etg_tags        — backfilled session_id, manual_override
--     test_aeee_adjustments— backfilled session_id
--     test_epr_data        — backfilled meeting_state, condition, date
--     test_source_log      — backfilled call_num
-- ================================================================
