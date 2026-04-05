-- [LEGACY] This file is superseded by sql/001_canonical_schema.sql.
-- Do NOT run this file. It is kept for historical reference only.
-- See docs/supabase_rebuild_notes.md for migration instructions.

-- ================================================================
-- DEMONPULSE V7 - COMPLETE SUPABASE SCHEMA
-- Run this entire script in Supabase SQL editor
-- ================================================================

-- ----------------------------------------------------------------
-- SESSION CONTROL
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE DEFAULT CURRENT_DATE,
    session_type TEXT,
    account_type TEXT,
    bankroll_start DECIMAL(10,2),
    bankroll_end DECIMAL(10,2),
    bank_mode TEXT DEFAULT 'STANDARD',
    active_code TEXT DEFAULT 'GREYHOUND',
    learning_mode TEXT DEFAULT 'Passive',
    execution_mode TEXT DEFAULT 'Quick',
    posture TEXT DEFAULT 'NORMAL',
    total_bets INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    pl DECIMAL(10,2) DEFAULT 0,
    roi DECIMAL(6,2) DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS system_state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    bankroll DECIMAL(10,2) DEFAULT 1000,
    current_pl DECIMAL(10,2) DEFAULT 0,
    bank_mode TEXT DEFAULT 'STANDARD',
    active_code TEXT DEFAULT 'GREYHOUND',
    posture TEXT DEFAULT 'NORMAL',
    sys_state TEXT DEFAULT 'STABLE',
    variance TEXT DEFAULT 'NORMAL',
    session_type TEXT DEFAULT 'Live Betting',
    time_anchor TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO system_state (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS session_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    date DATE,
    pl DECIMAL(10,2),
    bets INTEGER,
    wins INTEGER,
    strike_rate DECIMAL(5,2),
    roi DECIMAL(6,2),
    bank_mode TEXT,
    code TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- RAW RACE DATA (A4 - raw/clean/scored separation)
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS today_races (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid TEXT UNIQUE NOT NULL,
    date DATE DEFAULT CURRENT_DATE,
    track TEXT NOT NULL,
    state TEXT,
    race_num INTEGER NOT NULL,
    code TEXT DEFAULT 'GREYHOUND',
    distance TEXT,
    grade TEXT,
    jump_time TEXT,
    time_status TEXT DEFAULT 'PARTIAL',
    prize_money TEXT,
    status TEXT DEFAULT 'upcoming',
    source_url TEXT,
    completeness_score INTEGER DEFAULT 0,
    completeness_quality TEXT DEFAULT 'LOW',
    race_hash TEXT,
    lifecycle_state TEXT DEFAULT 'fetched',
    fetched_at TIMESTAMPTZ,
    normalized_at TIMESTAMPTZ,
    scored_at TIMESTAMPTZ,
    packet_built_at TIMESTAMPTZ,
    ai_reviewed_at TIMESTAMPTZ,
    bet_logged_at TIMESTAMPTZ,
    result_captured_at TIMESTAMPTZ,
    learned_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS today_runners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_id UUID REFERENCES today_races(id) ON DELETE CASCADE,
    race_uid TEXT NOT NULL,
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    box_num INTEGER NOT NULL,
    name TEXT NOT NULL,
    trainer TEXT,
    owner TEXT,
    weight DECIMAL(5,2),
    run_style TEXT,
    early_speed TEXT,
    best_time TEXT,
    career TEXT,
    scratched BOOLEAN DEFAULT FALSE,
    scratch_timing TEXT,
    raw_hash TEXT,
    source_confidence TEXT DEFAULT 'official',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- SCORED RACE DATA (A4 - separate from raw)
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS scored_races (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid TEXT UNIQUE NOT NULL,
    decision TEXT,
    confidence TEXT,
    selection TEXT,
    box_num INTEGER,
    race_shape TEXT,
    pace_type TEXT,
    collapse_risk TEXT,
    pressure_score INTEGER,
    separation TEXT,
    crash_map TEXT,
    false_favourite_json TEXT,
    filters_json TEXT,
    audit_json TEXT,
    confidence_breakdown_json TEXT,
    packet_snapshot TEXT,
    packet_version TEXT,
    scorer_version TEXT,
    scored_at TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- SCRATCHINGS
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS scratch_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid TEXT,
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    box_num INTEGER,
    runner_name TEXT,
    scratch_timing TEXT DEFAULT 'early',
    confirmed_at TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- MARKET DATA
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS market_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid TEXT,
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    runner_name TEXT,
    box_num INTEGER,
    opening_price DECIMAL(8,2),
    analysis_price DECIMAL(8,2),
    final_sp DECIMAL(8,2),
    price_movement TEXT,
    market_rank INTEGER,
    overround DECIMAL(6,2),
    steam_flag BOOLEAN DEFAULT FALSE,
    drift_flag BOOLEAN DEFAULT FALSE,
    mvi_score INTEGER DEFAULT 0,
    snapshot_time TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- BETTING
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bet_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    race_uid TEXT,
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    code TEXT DEFAULT 'GREYHOUND',
    runner TEXT,
    box_num INTEGER,
    bet_type TEXT,
    odds DECIMAL(8,2),
    stake DECIMAL(10,2),
    ev DECIMAL(6,3),
    ev_status TEXT,
    confidence TEXT,
    edge_type TEXT,
    edge_status TEXT,
    decision TEXT,
    race_shape TEXT,
    result TEXT DEFAULT 'PENDING',
    pl DECIMAL(10,2) DEFAULT 0,
    error_tag TEXT,
    manual_tag_override BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    settled_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS results_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid TEXT UNIQUE NOT NULL,
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    code TEXT DEFAULT 'GREYHOUND',
    winner TEXT,
    winner_box INTEGER,
    win_price DECIMAL(8,2),
    place_2 TEXT,
    place_3 TEXT,
    margin DECIMAL(6,2),
    winning_time DECIMAL(7,3),
    source TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- PASS LOG (feature 27 - auto skip learning)
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pass_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid TEXT UNIQUE NOT NULL,
    pass_reason TEXT,
    local_decision TEXT,
    confidence TEXT,
    date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- RUNNER PROFILES AND FORM
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS runner_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    runner_name TEXT NOT NULL,
    code TEXT DEFAULT 'GREYHOUND',
    trainer TEXT,
    career_starts INTEGER DEFAULT 0,
    career_wins INTEGER DEFAULT 0,
    career_places INTEGER DEFAULT 0,
    career_prize_money DECIMAL(10,2) DEFAULT 0,
    consistency_index INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(runner_name, code, trainer)
);

CREATE TABLE IF NOT EXISTS form_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    runner_name TEXT NOT NULL,
    trainer TEXT,
    code TEXT DEFAULT 'GREYHOUND',
    race_date DATE,
    track TEXT,
    race_num INTEGER,
    distance INTEGER,
    grade TEXT,
    box_num INTEGER,
    finish_position INTEGER,
    margin DECIMAL(6,2),
    starting_price DECIMAL(8,2),
    split_1 DECIMAL(6,3),
    split_2 DECIMAL(6,3),
    final_time DECIMAL(7,3),
    jockey_driver TEXT,
    track_condition TEXT,
    stewards_comments TEXT,
    interference BOOLEAN DEFAULT FALSE,
    prize_won DECIMAL(10,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS track_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_name TEXT NOT NULL,
    state TEXT,
    code TEXT DEFAULT 'GREYHOUND',
    inside_bias DECIMAL(5,2),
    outside_bias DECIMAL(5,2),
    early_speed_bias DECIMAL(5,2),
    closer_bias DECIMAL(5,2),
    leader_win_pct DECIMAL(5,2),
    condition_drift TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(track_name, code)
);

-- ----------------------------------------------------------------
-- AI LEARNING LAYER
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS epr_data (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    edge_type TEXT NOT NULL,
    code TEXT DEFAULT 'GREYHOUND',
    track TEXT,
    distance TEXT,
    confidence_tier TEXT,
    ev_at_analysis DECIMAL(6,3),
    result TEXT,
    pl DECIMAL(10,2),
    execution_mode TEXT,
    session_id TEXT,
    date DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aeee_adjustments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    edge_type TEXT,
    direction TEXT,
    amount DECIMAL(5,3),
    reason TEXT,
    roi_trigger DECIMAL(6,2),
    bets_sample INTEGER,
    applied BOOLEAN DEFAULT FALSE,
    promoted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gpil_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_type TEXT,
    code TEXT DEFAULT 'GREYHOUND',
    description TEXT,
    bets_sample INTEGER DEFAULT 0,
    roi DECIMAL(6,2),
    status TEXT DEFAULT 'INSUFFICIENT',
    mif_modifier INTEGER DEFAULT 0,
    first_detected TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etg_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bet_id UUID,
    race_uid TEXT,
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    error_tag TEXT NOT NULL,
    notes TEXT,
    manual_override BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- PERFORMANCE TRACKING
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS performance_daily (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE UNIQUE,
    total_bets INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    pl DECIMAL(10,2) DEFAULT 0,
    roi DECIMAL(6,2) DEFAULT 0,
    strike_rate DECIMAL(5,2) DEFAULT 0,
    avg_odds DECIMAL(6,2) DEFAULT 0,
    code TEXT DEFAULT 'GREYHOUND',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS performance_by_track (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track TEXT NOT NULL,
    code TEXT DEFAULT 'GREYHOUND',
    total_bets INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    pl DECIMAL(10,2) DEFAULT 0,
    roi DECIMAL(6,2) DEFAULT 0,
    strike_rate DECIMAL(5,2) DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(track, code)
);

-- ----------------------------------------------------------------
-- BACKTESTING
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS training_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT,
    epoch INTEGER,
    accuracy REAL,
    roi REAL,
    drawdown REAL,
    win_rate REAL,
    top3_rate REAL,
    error_tempo REAL,
    error_position REAL,
    error_traffic REAL,
    error_distance REAL,
    error_condition REAL,
    error_variance REAL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- LOGS
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS activity_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT,
    event_type TEXT,
    description TEXT,
    data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE DEFAULT CURRENT_DATE,
    url TEXT,
    method TEXT,
    status TEXT,
    grv_detected BOOLEAN DEFAULT FALSE,
    rows_returned INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS changelog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_date TIMESTAMPTZ DEFAULT NOW(),
    entry_type TEXT,
    entry_id TEXT,
    severity TEXT,
    description TEXT,
    supersedes TEXT,
    status TEXT DEFAULT 'ACTIVE'
);

-- ----------------------------------------------------------------
-- INDEXES
-- ----------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_today_races_date ON today_races(date);
CREATE INDEX IF NOT EXISTS idx_today_races_uid ON today_races(race_uid);
CREATE INDEX IF NOT EXISTS idx_today_races_status ON today_races(status);
CREATE INDEX IF NOT EXISTS idx_today_races_lifecycle ON today_races(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_today_runners_uid ON today_runners(race_uid);
CREATE INDEX IF NOT EXISTS idx_today_runners_scratched ON today_runners(scratched);
CREATE INDEX IF NOT EXISTS idx_scored_races_uid ON scored_races(race_uid);
CREATE INDEX IF NOT EXISTS idx_bet_log_date ON bet_log(date);
CREATE INDEX IF NOT EXISTS idx_bet_log_result ON bet_log(result);
CREATE INDEX IF NOT EXISTS idx_bet_log_race_uid ON bet_log(race_uid);
CREATE INDEX IF NOT EXISTS idx_results_uid ON results_log(race_uid);
CREATE INDEX IF NOT EXISTS idx_form_runs_runner ON form_runs(runner_name);
CREATE INDEX IF NOT EXISTS idx_form_runs_track ON form_runs(track);
CREATE INDEX IF NOT EXISTS idx_epr_edge_type ON epr_data(edge_type);
CREATE INDEX IF NOT EXISTS idx_chat_history_session ON chat_history(session_id);
CREATE INDEX IF NOT EXISTS idx_pass_log_uid ON pass_log(race_uid);

-- ================================================================
-- DONE - 28 tables ready
-- ================================================================
