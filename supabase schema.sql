-- ================================================================
-- DEMONPULSE SYNDICATE V7 - SUPABASE SCHEMA (FIXED)
-- Run this entire script in the Supabase SQL editor
-- ================================================================

-- ----------------------------------------------------------------
-- EXTENSIONS
-- ----------------------------------------------------------------
create extension if not exists pgcrypto;

-- ----------------------------------------------------------------
-- SESSION CONTROL
-- ----------------------------------------------------------------

create table if not exists sessions (
    id uuid primary key default gen_random_uuid(),
    date date default current_date,
    session_type text,
    account_type text,
    bankroll_start numeric(10,2),
    bankroll_end numeric(10,2),
    bank_mode text default 'STANDARD',
    active_code text default 'GREYHOUND',
    learning_mode text default 'Passive',
    execution_mode text default 'Quick',
    posture text default 'NORMAL',
    total_bets integer default 0,
    wins integer default 0,
    losses integer default 0,
    pl numeric(10,2) default 0,
    roi numeric(6,2) default 0,
    notes text,
    created_at timestamptz default now(),
    ended_at timestamptz
);

create table if not exists session_history (
    id uuid primary key default gen_random_uuid(),
    session_id uuid references sessions(id) on delete set null,
    date date,
    pl numeric(10,2),
    bets integer,
    wins integer,
    strike_rate numeric(5,2),
    roi numeric(6,2),
    bank_mode text,
    code text,
    created_at timestamptz default now()
);

create table if not exists system_state (
    id integer primary key,
    bankroll numeric(10,2) default 1000,
    current_pl numeric(10,2) default 0,
    bank_mode text default 'STANDARD',
    active_code text default 'GREYHOUND',
    posture text default 'NORMAL',
    sys_state text default 'STABLE',
    variance text default 'NORMAL',
    session_type text default 'Live Betting',
    time_anchor text default '',
    updated_at timestamptz default now()
);

insert into system_state (id)
values (1)
on conflict (id) do nothing;

-- ----------------------------------------------------------------
-- RACE DATA
-- ----------------------------------------------------------------

create table if not exists today_races (
    id uuid primary key default gen_random_uuid(),
    date date default current_date,
    track text not null,
    state text,
    race_num integer not null,
    code text default 'GREYHOUND',
    distance text,
    grade text,
    jump_time text,
    prize_money text,
    status text default 'upcoming',
    source_url text,
    fetched_at timestamptz default now(),
    completed_at timestamptz,
    unique (date, track, race_num, code)
);

create table if not exists today_runners (
    id uuid primary key default gen_random_uuid(),
    race_id uuid references today_races(id) on delete cascade,
    date date default current_date,
    track text,
    race_num integer,
    box_num integer,
    name text,
    trainer text,
    owner text,
    weight numeric(5,2),
    run_style text,
    early_speed text,
    best_time text,
    career text,
    scratched boolean default false,
    scratch_reason text,
    created_at timestamptz default now()
);

create table if not exists scratch_log (
    id uuid primary key default gen_random_uuid(),
    date date default current_date,
    track text,
    race_num integer,
    box_num integer,
    runner_name text,
    reason text,
    confirmed_at timestamptz default now()
);

create table if not exists race_status (
    id uuid primary key default gen_random_uuid(),
    date date default current_date,
    track text,
    race_num integer,
    code text default 'GREYHOUND',
    status text default 'upcoming',
    has_runners boolean default false,
    has_scratchings boolean default false,
    has_result boolean default false,
    jump_time text,
    time_status text default 'PARTIAL',
    updated_at timestamptz default now(),
    unique (date, track, race_num, code)
);

-- ----------------------------------------------------------------
-- RUNNER AND FORM DATABASE
-- ----------------------------------------------------------------

create table if not exists runner_profiles (
    id uuid primary key default gen_random_uuid(),
    runner_name text not null,
    code text default 'GREYHOUND',
    trainer text,
    owner text,
    colour text,
    sex text,
    date_of_birth date,
    sire text,
    dam text,
    career_starts integer default 0,
    career_wins integer default 0,
    career_places integer default 0,
    career_prize_money numeric(10,2) default 0,
    updated_at timestamptz default now(),
    unique (runner_name, code, trainer)
);

create table if not exists form_runs (
    id uuid primary key default gen_random_uuid(),
    runner_name text not null,
    trainer text,
    code text default 'GREYHOUND',
    race_date date,
    track text,
    state text,
    race_num integer,
    distance integer,
    grade text,
    box_num integer,
    finish_position integer,
    margin numeric(6,2),
    starting_price numeric(8,2),
    split_1 numeric(6,3),
    split_2 numeric(6,3),
    final_time numeric(7,3),
    winning_time numeric(7,3),
    weight numeric(5,2),
    jockey_driver text,
    track_condition text,
    stewards_comments text,
    interference boolean default false,
    gear_changes text,
    prize_won numeric(10,2),
    created_at timestamptz default now()
);

create table if not exists track_profiles (
    id uuid primary key default gen_random_uuid(),
    track_name text not null,
    state text,
    code text default 'GREYHOUND',
    inside_bias numeric(5,2),
    outside_bias numeric(5,2),
    early_speed_bias numeric(5,2),
    closer_bias numeric(5,2),
    leader_win_pct numeric(5,2),
    updated_at timestamptz default now(),
    unique (track_name, code)
);

create table if not exists sectional_benchmarks (
    id uuid primary key default gen_random_uuid(),
    track text,
    distance integer,
    code text default 'GREYHOUND',
    grade text,
    avg_split_1 numeric(6,3),
    avg_split_2 numeric(6,3),
    avg_final_time numeric(7,3),
    sample_size integer default 0,
    updated_at timestamptz default now(),
    unique (track, distance, code, grade)
);

-- ----------------------------------------------------------------
-- MARKET DATA
-- ----------------------------------------------------------------

create table if not exists market_snapshots (
    id uuid primary key default gen_random_uuid(),
    date date default current_date,
    track text,
    race_num integer,
    runner_name text,
    box_num integer,
    opening_price numeric(8,2),
    analysis_price numeric(8,2),
    final_sp numeric(8,2),
    price_movement text,
    market_rank integer,
    overround numeric(6,2),
    steam_flag boolean default false,
    drift_flag boolean default false,
    snapshot_time timestamptz default now()
);

-- ----------------------------------------------------------------
-- BETTING
-- ----------------------------------------------------------------

create table if not exists bet_log (
    id uuid primary key default gen_random_uuid(),
    session_id uuid references sessions(id) on delete set null,
    date date default current_date,
    track text,
    race_num integer,
    code text default 'GREYHOUND',
    runner text,
    box_num integer,
    bet_type text,
    odds numeric(8,2),
    stake numeric(10,2),
    ev numeric(6,3),
    ev_status text,
    confidence text,
    edge_type text,
    edge_status text,
    decision text,
    result text default 'PENDING',
    pl numeric(10,2) default 0,
    error_tag text,
    race_shape text,
    created_at timestamptz default now(),
    settled_at timestamptz
);

create table if not exists results_log (
    id uuid primary key default gen_random_uuid(),
    date date default current_date,
    track text,
    race_num integer,
    code text default 'GREYHOUND',
    winner text,
    winner_box integer,
    win_price numeric(8,2),
    place_2 text,
    place_3 text,
    margin numeric(6,2),
    winning_time numeric(7,3),
    source text,
    recorded_at timestamptz default now(),
    unique (date, track, race_num, code)
);

-- ----------------------------------------------------------------
-- DECISIONS AND ANALYSIS
-- ----------------------------------------------------------------

create table if not exists decisions (
    id uuid primary key default gen_random_uuid(),
    session_id uuid references sessions(id) on delete set null,
    date date default current_date,
    track text,
    race_num integer,
    code text,
    selection text,
    box_num integer,
    bet_type text,
    odds numeric(8,2),
    stake numeric(10,2),
    ev numeric(6,3),
    confidence text,
    flames integer,
    decision text,
    edge_type text,
    edge_status text,
    race_shape text,
    alert_type text,
    pass_reason text,
    created_at timestamptz default now()
);

create table if not exists filter_scores (
    id uuid primary key default gen_random_uuid(),
    decision_id uuid references decisions(id) on delete cascade,
    date date default current_date,
    track text,
    race_num integer,
    dif_score integer,
    dif_action text,
    tdf_score integer,
    tdf_action text,
    vef_score integer,
    vef_action text,
    eqf_score integer,
    eqf_action text,
    chf_score integer,
    chf_action text,
    mtf_score integer,
    mtf_action text,
    mif_score integer,
    mif_action text,
    srf_score integer,
    srf_action text,
    tmf_score integer,
    tmf_action text,
    tbf_score integer,
    esf_score integer,
    pbf_score integer,
    crf_score integer,
    frf_score integer,
    mcf_score integer,
    primary_driver text,
    created_at timestamptz default now()
);

-- ----------------------------------------------------------------
-- AI LEARNING LAYER
-- ----------------------------------------------------------------

create table if not exists epr_data (
    id uuid primary key default gen_random_uuid(),
    edge_type text not null,
    code text default 'GREYHOUND',
    track text,
    distance text,
    condition text,
    confidence_tier text,
    ev_at_analysis numeric(6,3),
    result text,
    pl numeric(10,2),
    execution_mode text,
    meeting_state text,
    session_id uuid references sessions(id) on delete set null,
    created_at timestamptz default now()
);

create table if not exists aeee_adjustments (
    id uuid primary key default gen_random_uuid(),
    session_id uuid references sessions(id) on delete set null,
    edge_type text,
    direction text,
    amount numeric(5,3),
    reason text,
    roi_trigger numeric(6,2),
    bets_sample integer,
    applied boolean default false,
    promoted boolean default false,
    created_at timestamptz default now()
);

create table if not exists gpil_patterns (
    id uuid primary key default gen_random_uuid(),
    pattern_type text,
    code text default 'GREYHOUND',
    description text,
    bets_sample integer default 0,
    roi numeric(6,2),
    status text default 'INSUFFICIENT',
    mif_modifier integer default 0,
    first_detected timestamptz default now(),
    last_updated timestamptz default now()
);

create table if not exists etg_tags (
    id uuid primary key default gen_random_uuid(),
    bet_id uuid references bet_log(id) on delete cascade,
    session_id uuid references sessions(id) on delete set null,
    date date default current_date,
    track text,
    race_num integer,
    error_tag text not null,
    notes text,
    created_at timestamptz default now()
);

-- ----------------------------------------------------------------
-- PERFORMANCE TRACKING
-- ----------------------------------------------------------------

create table if not exists performance_daily (
    id uuid primary key default gen_random_uuid(),
    date date unique,
    total_bets integer default 0,
    wins integer default 0,
    losses integer default 0,
    pl numeric(10,2) default 0,
    roi numeric(6,2) default 0,
    strike_rate numeric(5,2) default 0,
    avg_odds numeric(6,2) default 0,
    code text default 'GREYHOUND',
    updated_at timestamptz default now()
);

create table if not exists performance_by_track (
    id uuid primary key default gen_random_uuid(),
    track text not null,
    code text default 'GREYHOUND',
    total_bets integer default 0,
    wins integer default 0,
    pl numeric(10,2) default 0,
    roi numeric(6,2) default 0,
    strike_rate numeric(5,2) default 0,
    updated_at timestamptz default now(),
    unique (track, code)
);

create table if not exists performance_by_edge (
    id uuid primary key default gen_random_uuid(),
    edge_type text not null,
    code text default 'GREYHOUND',
    total_bets integer default 0,
    wins integer default 0,
    pl numeric(10,2) default 0,
    roi numeric(6,2) default 0,
    strike_rate numeric(5,2) default 0,
    updated_at timestamptz default now(),
    unique (edge_type, code)
);

-- ----------------------------------------------------------------
-- LOGS
-- ----------------------------------------------------------------

create table if not exists activity_log (
    id uuid primary key default gen_random_uuid(),
    session_id text,
    event_type text,
    description text,
    data jsonb,
    created_at timestamptz default now()
);

create table if not exists source_log (
    id uuid primary key default gen_random_uuid(),
    date date default current_date,
    call_num integer,
    url text,
    method text,
    status text,
    grv_detected boolean default false,
    rows_returned integer,
    created_at timestamptz default now()
);

create table if not exists changelog (
    id uuid primary key default gen_random_uuid(),
    entry_date timestamptz default now(),
    entry_type text,
    entry_id text,
    severity text,
    description text,
    supersedes text,
    status text default 'ACTIVE'
);

-- ----------------------------------------------------------------
-- INDEXES
-- ----------------------------------------------------------------

create index if not exists idx_sessions_date on sessions(date);

create index if not exists idx_today_races_date on today_races(date);
create index if not exists idx_today_races_status on today_races(status);
create index if not exists idx_today_races_track_race on today_races(track, race_num);

create index if not exists idx_today_runners_race_id on today_runners(race_id);
create index if not exists idx_today_runners_track_race on today_runners(track, race_num);
create index if not exists idx_today_runners_name on today_runners(name);

create index if not exists idx_race_status_date on race_status(date);
create index if not exists idx_race_status_track_race on race_status(track, race_num);

create index if not exists idx_form_runs_runner_name on form_runs(runner_name);
create index if not exists idx_form_runs_race_date on form_runs(race_date);
create index if not exists idx_form_runs_track on form_runs(track);

create index if not exists idx_market_snapshots_date_track_race on market_snapshots(date, track, race_num);
create index if not exists idx_market_snapshots_runner_name on market_snapshots(runner_name);

create index if not exists idx_bet_log_session_id on bet_log(session_id);
create index if not exists idx_bet_log_date on bet_log(date);
create index if not exists idx_bet_log_track_race on bet_log(track, race_num);
create index if not exists idx_bet_log_runner on bet_log(runner);
create index if not exists idx_bet_log_result on bet_log(result);

create index if not exists idx_results_log_date_track_race on results_log(date, track, race_num);

create index if not exists idx_decisions_session_id on decisions(session_id);
create index if not exists idx_decisions_date on decisions(date);
create index if not exists idx_decisions_track_race on decisions(track, race_num);

create index if not exists idx_filter_scores_decision_id on filter_scores(decision_id);

create index if not exists idx_epr_data_session_id on epr_data(session_id);
create index if not exists idx_epr_data_edge_type on epr_data(edge_type);

create index if not exists idx_aeee_adjustments_session_id on aeee_adjustments(session_id);

create index if not exists idx_etg_tags_bet_id on etg_tags(bet_id);
create index if not exists idx_etg_tags_session_id on etg_tags(session_id);

create index if not exists idx_activity_log_session_id on activity_log(session_id);
create index if not exists idx_activity_log_created_at on activity_log(created_at);

create index if not exists idx_source_log_date on source_log(date);
create index if not exists idx_changelog_entry_date on changelog(entry_date);
