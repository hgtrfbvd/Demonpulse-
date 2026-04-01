-- ================================================================
-- DEMONPULSE SYNDICATE V7 - SUPABASE SCHEMA
-- Run this entire script in the Supabase SQL editor
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
    created_at TIMESTAMP DEFAULT NOW(),
    ended_at TIMESTAMP
);

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
    created_at TIMESTAMP DEFAULT NOW()
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
    updated_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO system_state (id) VALUES (1) ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------
-- RACE DATA
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS today_races (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE DEFAULT CURRENT_DATE,
    track TEXT NOT NULL,
    state TEXT,
    race_num INTEGER NOT NULL,
    code TEXT DEFAULT 'GREYHOUND',
    distance TEXT,
    grade TEXT,
    jump_time TEXT,
    prize_money TEXT,
    status TEXT DEFAULT 'upcoming',
    source_url TEXT,
    fetched_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    UNIQUE(date, track, race_num, code)
);

CREATE TABLE IF NOT EXISTS today_runners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_id UUID REFERENCES today_races(id) ON DELETE CASCADE,
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    box_num INTEGER,
    name TEXT,
    trainer TEXT,
    owner TEXT,
    weight DECIMAL(5,2),
    run_style TEXT,
    early_speed TEXT,
    best_time TEXT,
    career TEXT,
    scratched BOOLEAN DEFAULT FALSE,
    scratch_reason TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scratch_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    box_num INTEGER,
    runner_name TEXT,
    reason TEXT,
    confirmed_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS race_status (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    code TEXT DEFAULT 'GREYHOUND',
    status TEXT DEFAULT 'upcoming',
    has_runners BOOLEAN DEFAULT FALSE,
    has_scratchings BOOLEAN DEFAULT FALSE,
    has_result BOOLEAN DEFAULT FALSE,
    jump_time TEXT,
    time_status TEXT DEFAULT 'PARTIAL',
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(date, track, race_num, code)
);

-- ----------------------------------------------------------------
-- RUNNER AND FORM DATABASE
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS runner_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    runner_name TEXT NOT NULL,
    code TEXT DEFAULT 'GREYHOUND',
    trainer TEXT,
    owner TEXT,
    colour TEXT,
    sex TEXT,
    date_of_birth DATE,
    sire TEXT,
    dam TEXT,
    career_starts INTEGER DEFAULT 0,
    career_wins INTEGER DEFAULT 0,
    career_places INTEGER DEFAULT 0,
    career_prize_money DECIMAL(10,2) DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(runner_name, code, trainer)
);

CREATE TABLE IF NOT EXISTS form_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    runner_name TEXT NOT NULL,
    trainer TEXT,
    code TEXT DEFAULT 'GREYHOUND',
    race_date DATE,
    track TEXT,
    state TEXT,
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
    winning_time DECIMAL(7,3),
    weight DECIMAL(5,2),
    jockey_driver TEXT,
    track_condition TEXT,
    stewards_comments TEXT,
    interference BOOLEAN DEFAULT FALSE,
    gear_changes TEXT,
    prize_won DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT NOW()
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
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(track_name, code)
);

CREATE TABLE IF NOT EXISTS sectional_benchmarks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track TEXT,
    distance INTEGER,
    code TEXT DEFAULT 'GREYHOUND',
    grade TEXT,
    avg_split_1 DECIMAL(6,3),
    avg_split_2 DECIMAL(6,3),
    avg_final_time DECIMAL(7,3),
    sample_size INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(track, distance, code, grade)
);

-- ----------------------------------------------------------------
-- MARKET DATA
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS market_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
    snapshot_time TIMESTAMP DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- BETTING
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bet_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
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
    result TEXT DEFAULT 'PENDING',
    pl DECIMAL(10,2) DEFAULT 0,
    error_tag TEXT,
    race_shape TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    settled_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS results_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
    recorded_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(date, track, race_num, code)
);

-- ----------------------------------------------------------------
-- DECISIONS AND ANALYSIS
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    code TEXT,
    selection TEXT,
    box_num INTEGER,
    bet_type TEXT,
    odds DECIMAL(8,2),
    stake DECIMAL(10,2),
    ev DECIMAL(6,3),
    confidence TEXT,
    flames INTEGER,
    decision TEXT,
    edge_type TEXT,
    edge_status TEXT,
    race_shape TEXT,
    alert_type TEXT,
    pass_reason TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS filter_scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id UUID REFERENCES decisions(id),
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    dif_score INTEGER,
    dif_action TEXT,
    tdf_score INTEGER,
    tdf_action TEXT,
    vef_score INTEGER,
    vef_action TEXT,
    eqf_score INTEGER,
    eqf_action TEXT,
    chf_score INTEGER,
    chf_action TEXT,
    mtf_score INTEGER,
    mtf_action TEXT,
    mif_score INTEGER,
    mif_action TEXT,
    srf_score INTEGER,
    srf_action TEXT,
    tmf_score INTEGER,
    tmf_action TEXT,
    tbf_score INTEGER,
    esf_score INTEGER,
    pbf_score INTEGER,
    crf_score INTEGER,
    frf_score INTEGER,
    mcf_score INTEGER,
    primary_driver TEXT,
    created_at TIMESTAMP DEFAULT NOW()
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
    condition TEXT,
    confidence_tier TEXT,
    ev_at_analysis DECIMAL(6,3),
    result TEXT,
    pl DECIMAL(10,2),
    execution_mode TEXT,
    meeting_state TEXT,
    session_id UUID,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aeee_adjustments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID,
    edge_type TEXT,
    direction TEXT,
    amount DECIMAL(5,3),
    reason TEXT,
    roi_trigger DECIMAL(6,2),
    bets_sample INTEGER,
    applied BOOLEAN DEFAULT FALSE,
    promoted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
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
    first_detected TIMESTAMP DEFAULT NOW(),
    last_updated TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS etg_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bet_id UUID REFERENCES bet_log(id),
    session_id UUID,
    date DATE DEFAULT CURRENT_DATE,
    track TEXT,
    race_num INTEGER,
    error_tag TEXT NOT NULL,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
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
    updated_at TIMESTAMP DEFAULT NOW()
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
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(track, code)
);

CREATE TABLE IF NOT EXISTS performance_by_edge (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    edge_type TEXT NOT NULL,
    code TEXT DEFAULT 'GREYHOUND',
    total_bets INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    pl DECIMAL(10,2) DEFAULT 0,
    roi DECIMAL(6,2) DEFAULT 0,
    strike_rate DECIMAL(5,2) DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(edge_type, code)
);

-- ----------------------------------------------------------------
-- LOGS
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS activity_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT,
    event_type TEXT,
    description TEXT,
    data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE DEFAULT CURRENT_DATE,
    call_num INTEGER,
    url TEXT,
    method TEXT,
    status TEXT,
    grv_detected BOOLEAN DEFAULT FALSE,
    rows_returned INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS changelog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_date TIMESTAMP DEFAULT NOW(),
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
CREATE INDEX IF NOT EXISTS idx_today_races_status ON today_races(status);
CREATE INDEX IF NOT EXISTS idx_race_status_date ON race_status(date);
CREATE INDEX IF NOT EXISTS idx_bet_log_date ON bet_log(date);
CREATE INDEX IF NOT EXISTS idx_bet_log_result ON bet_log(result);
CREATE INDEX IF NOT EXISTS idx_form_runs_runner ON form_runs(runner_name);
CREATE INDEX IF NOT EXISTS idx_form_runs_track ON form_runs(track);
CREATE INDEX IF NOT EXISTS idx_form_runs_date ON form_runs(race_date);
CREATE INDEX IF NOT EXISTS idx_epr_edge_type ON epr_data(edge_type);

-- ================================================================
-- DONE - 25 tables created
-- ================================================================
