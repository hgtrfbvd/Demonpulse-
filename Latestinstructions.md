# DEMONPULSE V2 — COMPLETE REBUILD PROMPT FOR GITHUB COPILOT

## Professional Punting Intelligence Platform — Horses + Greyhounds (AU)

-----

> **HOW TO USE THIS PROMPT**
> Paste this entire document into Copilot Workspace or use it as your agent session context.
> Work through each phase in order. Each phase produces self-contained modules with clear interfaces.
> Do not skip phases — later modules depend on earlier ones.

-----

## PROJECT OVERVIEW

Build **DemonPulse V2** — a professional Australian racing intelligence platform for serious punters.
Comparable in feel to a Bloomberg terminal crossed with a professional bookmaking back-office tool.
Target users: professional / semi-professional punters who want data, signals, and structured bet management.

**Stack:**

- Backend: Python 3.11 + Flask 3.x + Gunicorn
- Database: Supabase (PostgreSQL via supabase-py)
- Frontend: Vanilla JS + Jinja2 templates (NO React, NO build step)
- Scraping: Playwright (headless Chromium) + Anthropic Claude API
- Deployment: Render.com (single web service, 1 worker)

**Two race codes only:** `GREYHOUND` and `GALLOPS` (horses). No harness.

-----

## PHASE 0 — PROJECT SKELETON

### Directory Structure

```
demonpulse/
├── app.py                    # Flask app factory, blueprint registration
├── config.py                 # All env vars, constants, mode flags
├── requirements.txt
├── Procfile
├── render.yaml
├── gunicorn.conf.py
│
├── core/                     # Shared utilities used by everything
│   ├── db.py                 # Supabase client, T(), safe_query()
│   ├── env.py                # TEST/LIVE mode authority
│   ├── auth.py               # JWT auth, user management
│   ├── cache.py              # Simple in-memory TTL cache
│   └── clock.py              # AEST datetime helpers
│
├── data/                     # Data ingestion layer
│   ├── scheduler.py          # Background thread scheduler
│   ├── pipeline.py           # Orchestrates all ingestion sweeps
│   ├── horse_collector.py    # Claude API → horse race data
│   ├── dogs_collector.py     # Playwright → thedogs.com.au
│   ├── dogs_parser.py        # HTML → normalised race dicts
│   └── result_checker.py     # Race status / result detection
│
├── intelligence/             # Scoring + AI layer
│   ├── scorer.py             # Multi-signal race scoring engine
│   ├── features.py           # Feature extraction from race/runners
│   ├── signals.py            # Signal generation (BET/SESSION/PASS)
│   ├── predictor.py          # Prediction runner, snapshot saver
│   ├── race_shape.py         # Pace classification, collapse projection
│   ├── collision.py          # Greyhound box/collision model
│   ├── packet_builder.py     # Build pre-scored packet for Claude
│   └── system_prompt.py      # Claude AI interpreter instructions
│
├── betting/                  # Bet management layer
│   ├── bet_manager.py        # Place, settle, history, P/L
│   ├── staking.py            # Kelly criterion, EV, stake sizing
│   └── exotics.py            # Exacta, trifecta, first4, multi calculators
│
├── learning/                 # Self-improvement layer
│   ├── evaluator.py          # Post-race evaluation against results
│   ├── tagger.py             # ETG loss tagging
│   ├── adjustments.py        # AEEE weight adjustment system
│   └── backtester.py         # Historical replay engine
│
├── api/                      # All Flask blueprints
│   ├── races.py              # /api/races/*
│   ├── board.py              # /api/board/*
│   ├── predictions.py        # /api/predictions/*
│   ├── bets.py               # /api/bets/*
│   ├── analytics.py          # /api/analytics/*
│   ├── admin.py              # /api/admin/*
│   ├── auth.py               # /api/auth/*
│   └── health.py             # /api/health/* + /debug
│
├── static/
│   ├── css/
│   │   ├── tokens.css        # Design system variables
│   │   ├── terminal.css      # Dark terminal aesthetic
│   │   └── components.css    # Reusable UI components
│   └── js/
│       ├── core.js           # Auth, fetch wrapper, shared utils
│       ├── board.js          # Race board module
│       ├── live.js           # Live race view module
│       ├── bets.js           # Bet management module
│       ├── analytics.js      # Reports + P/L charts
│       ├── learning.js       # AI learning module
│       └── settings.js       # Settings module
│
├── templates/
│   ├── base.html             # Shell: topbar, nav, status cluster
│   ├── board.html            # Race board (home page)
│   ├── live.html             # Live race form guide + analysis
│   ├── bets.html             # Bet log + open bets
│   ├── analytics.html        # P/L reports, performance charts
│   ├── learning.html         # AI learning, backtesting
│   └── settings.html         # System settings, admin tools
│
└── sql/
    └── schema.sql            # Complete Supabase schema
```

-----

## PHASE 1 — CORE INFRASTRUCTURE

### `config.py` — All Configuration

```python
# All environment variables with typed defaults
# Sections: Database, API Keys, Auth, Scheduler, Racing Logic

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
ODDSPRO_API_KEY = os.environ.get("ODDSPRO_API_KEY", "")
FORMFAV_API_KEY = os.environ.get("FORMFAV_API_KEY", "")
JWT_SECRET = os.environ["JWT_SECRET"]
FLASK_SECRET = os.environ["FLASK_SECRET"]
DP_ENV = os.environ.get("DP_ENV", "LIVE")  # TEST or LIVE

# Scheduler intervals (seconds)
HORSE_SWEEP_INTERVAL = 600     # 10 min
DOGS_SWEEP_INTERVAL = 600      # 10 min
BOARD_REBUILD_INTERVAL = 90    # 90 sec
RESULT_CHECK_INTERVAL = 180    # 3 min

# AI/Scoring
CLAUDE_MODEL = "claude-haiku-4-5"
MAX_PACKET_CHARS = 1200

# Racing logic
VALID_CODES = {"GREYHOUND", "GALLOPS"}
AEST_TZ = ZoneInfo("Australia/Sydney")
```

### `core/db.py` — Database Layer

```python
# Supabase client singleton
# T(name) → resolves table name for current env (test_ prefix in TEST mode)
# safe_query(fn, default) → wraps all DB calls with error handling
# TESTABLE_TABLES = complete set of all tables that use test_ prefix

# Key tables (always use T() for access):
# today_races, today_runners, bet_log, signals
# prediction_snapshots, learning_evaluations
# scored_races, results_log, sessions, system_state
```

### `core/env.py` — Environment Authority

```python
# Singleton: env.mode → "TEST" or "LIVE"
# env.table(name) → adds test_ prefix in TEST mode
# env.guard_destructive(op) → raises EnvViolation in LIVE mode
# env.is_live / env.is_test properties
# TEST mode: allows fake data, stress tests, auto-delete
# LIVE mode: real data only, non-destructive, audited
```

### `core/auth.py` — Authentication

```python
# Custom JWT (no external library dependency)
# Roles: admin, operator, viewer
# generate_token(user_id, username, role) → (access_token, refresh_token)
# decode_token(token) → payload dict or None
# require_auth decorator (Flask)
# require_role(*roles) decorator (Flask)
# bootstrap_admin() → creates admin user if none exists
# Rate limiting: max 5 failed attempts per IP per minute
```

-----

## PHASE 2 — DATABASE SCHEMA

### `sql/schema.sql` — Complete Supabase Schema

Design all tables with these principles:

- `race_uid` is always `DATE_CODE_TRACK_RACENUM` e.g. `2026-04-12_GREYHOUND_sandown_5`
- All timestamps in UTC, stored as ISO strings
- JSONB columns for flexible payload data (`raw_json`, `features_json`)
- Conflict keys explicitly defined for all upserts

**Required tables:**

```sql
-- Race data
today_races (
  id uuid PK,
  race_uid text UNIQUE,
  date date,
  track text,
  state text,
  country text DEFAULT 'AUS',
  code text CHECK (code IN ('GREYHOUND','GALLOPS')),
  race_num int,
  race_name text,
  distance int,
  grade text,
  condition text,
  prize_money text,
  jump_time timestamptz,
  runner_count int,
  status text DEFAULT 'upcoming', -- upcoming/open/jumped/resulted/abandoned
  source text,                     -- dogs_browser / claude_api
  raw_json jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
)

today_runners (
  id uuid PK,
  race_uid text REFERENCES today_races(race_uid),
  race_id uuid,
  box_num int,
  name text,
  trainer text,
  jockey text,            -- horses only
  weight numeric,         -- horses only
  barrier int,            -- horses only
  scratched boolean DEFAULT false,
  run_style text,
  best_time numeric,
  career text,            -- "starts:wins-places"
  win_odds numeric,
  place_odds numeric,
  tab_no int,
  form_json jsonb,        -- recent starts data
  source_confidence text,
  UNIQUE (race_uid, box_num)
)

-- Bet management
bet_log (
  id uuid PK,
  user_id uuid,
  race_uid text,
  date date,
  track text,
  race_num int,
  code text,
  runner_name text,
  box_num int,
  bet_type text,          -- WIN/PLACE/EACH_WAY/EXACTA/TRIFECTA/FIRST4/MULTI
  stake numeric,
  odds numeric,
  expected_return numeric,
  ev numeric,
  kelly_fraction numeric,
  confidence text,
  result text DEFAULT 'PENDING',  -- PENDING/WIN/LOSS/VOID
  return_amount numeric DEFAULT 0,
  pl numeric DEFAULT 0,
  signal_id uuid,
  settled_at timestamptz,
  created_at timestamptz DEFAULT now()
)

-- AI Intelligence
signals (
  id uuid PK,
  race_uid text UNIQUE,
  signal text,            -- BET/SESSION/PASS
  decision text,
  confidence text,
  ev numeric,
  selection text,
  box_num int,
  odds numeric,
  stake_rec numeric,
  race_shape text,
  pace_type text,
  collapse_risk text,
  filters_json jsonb,
  audit_json jsonb,
  model_version text,
  created_at timestamptz DEFAULT now()
)

prediction_snapshots (
  id uuid PK,
  race_uid text UNIQUE,
  race_date date,
  track text,
  race_num int,
  code text,
  signal text,
  decision text,
  confidence text,
  ev numeric,
  top_runner text,
  top_runner_box int,
  runner_scores_json jsonb,
  features_json jsonb,
  model_version text DEFAULT 'v2',
  created_at timestamptz DEFAULT now()
)

scored_races (
  id uuid PK,
  race_uid text UNIQUE,
  decision text,
  confidence text,
  selection text,
  box_num int,
  race_shape text,
  pace_type text,
  collapse_risk text,
  pressure_score int,
  separation text,
  crash_map text,
  filters_json jsonb,
  audit_json jsonb,
  packet_snapshot text,
  scorer_version text,
  scored_at timestamptz DEFAULT now()
)

-- Results
results_log (
  id uuid PK,
  race_uid text UNIQUE,
  date date,
  track text,
  race_num int,
  code text,
  winner text,
  winner_box int,
  second text,
  third text,
  fourth text,
  win_dividend numeric,
  place_dividend numeric,
  resulted_at timestamptz DEFAULT now()
)

-- Learning
learning_evaluations (
  id uuid PK,
  race_uid text UNIQUE,
  predicted_winner text,
  actual_winner text,
  was_correct boolean,
  confidence text,
  ev numeric,
  signal text,
  evaluated_at timestamptz DEFAULT now()
)

etg_tags (
  id uuid PK,
  bet_id uuid REFERENCES bet_log(id),
  race_uid text,
  tag text,         -- DATA_GAP/FORM_MISS/MARKET_MOVE/CHAOS/BOX_DRAW/TRAINER
  notes text,
  manual boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
)

-- System
system_state (
  id int PRIMARY KEY DEFAULT 1,
  bankroll numeric DEFAULT 1000,
  bank_mode text DEFAULT 'STANDARD',  -- SAFE/STANDARD/AGGRESSIVE
  active_code text DEFAULT 'GREYHOUND',
  ev_threshold numeric DEFAULT 0.08,
  confidence_threshold numeric DEFAULT 0.65,
  tempo_weight numeric DEFAULT 1.0,
  updated_at timestamptz DEFAULT now()
)

sessions (
  id uuid PK,
  date date UNIQUE,
  bankroll_start numeric,
  bankroll_end numeric,
  total_bets int DEFAULT 0,
  wins int DEFAULT 0,
  pl numeric DEFAULT 0,
  roi numeric DEFAULT 0,
  created_at timestamptz DEFAULT now()
)

users (
  id uuid PK,
  username text UNIQUE,
  password_hash text,
  role text DEFAULT 'operator',
  active boolean DEFAULT true,
  last_login timestamptz,
  created_at timestamptz DEFAULT now()
)
```

-----

## PHASE 3 — DATA PIPELINE

### `data/horse_collector.py` — Horse Data via Claude API

```python
"""
Fetches today's Australian thoroughbred race data via Anthropic Claude API.
Source: racingaustralia.horse (Claude reads and extracts via API call)
Returns list of normalised race dicts ready for DB upsert.

IMPORTANT: anthropic must be in requirements.txt
"""
from anthropic import Anthropic
from config import CLAUDE_MODEL, CLAUDE_API_KEY

class HorseCollector:
    def __init__(self):
        self.client = Anthropic(api_key=CLAUDE_API_KEY)
    
    def discover_venues(self, date: str) -> list[dict]:
        """Return list of {name, state} venue dicts for today."""
        # Prompt Claude to list today's gallops meetings from racingaustralia.horse
        # Cache result to /tmp for rate limit fallback
        
    def fetch_venue(self, venue_name: str, date: str) -> list[dict]:
        """Fetch all races for one venue. Returns normalised race dicts."""
        # One Claude call per venue
        # Response is JSON array of races with runners
        # Fields: track, race_num, distance_m, race_class, race_time,
        #         track_condition, prize_money, runners[]
        #
        # Each runner: name, barrier, weight, jockey, trainer,
        #              career_starts, career_wins, career_places,
        #              best_time_distance_match, run_style
    
    def fetch_all(self, date: str) -> list[dict]:
        """Discover venues and fetch all. Returns normalised race list."""
        # Batch venues in groups of 3 to reduce API calls
        # On RateLimitError: use cached venues, skip venue detail fetch
        # Log [HORSE_COLLECT] prefix for all operations
```

### `data/dogs_collector.py` — Greyhound Data via Playwright

```python
"""
Collects today's greyhound race data via headless Playwright browser.
Source: https://www.thedogs.com.au/racing/{date}?trial=false
Returns list of normalised race dicts ready for DB upsert.

Playwright must be installed: playwright install chromium
"""
from playwright.sync_api import sync_playwright

class DogsCollector:
    def collect_board(self, date: str) -> list[dict]:
        """
        Open board page, extract all meetings and race entries.
        Returns list of {track, race_num, jump_time, state, url} dicts.
        On failure: saves screenshot + HTML to /tmp/dp_dogs/, returns []
        """
    
    def capture_race(self, race_url: str) -> str:
        """
        Navigate to individual race page, return rendered HTML.
        Retries up to 3 times on timeout.
        """
    
    def collect_all(self, date: str) -> list[dict]:
        """Board collection → race capture → parse → normalise. Full pipeline."""
        # Uses dogs_parser.py for HTML → structured data
```

### `data/dogs_parser.py` — HTML Parser

```python
"""
Parses rendered HTML from thedogs.com.au race pages.
Pure function: html_string → normalised race dict.
No external API calls. Missing fields stored as None.
"""
from bs4 import BeautifulSoup

def parse_race_page(html: str, meta: dict) -> dict:
    """
    Extract race data from rendered HTML.
    meta contains: track, race_num, jump_time, state from board collection.
    
    Returns dict with:
      race_uid, date, track, state, code='GREYHOUND', race_num,
      distance, grade, condition, jump_time, runner_count,
      _runners: list of runner dicts
    
    Each runner:
      box_num, name, trainer, scratched, best_time, run_style,
      career (starts:wins-places), win_odds, form_json
    """
```

### `data/pipeline.py` — Orchestrator

```python
"""
Orchestrates all data sweeps. Called by scheduler.
Two independent pipelines:
  - horse_sweep()     → HorseCollector → normalise → upsert to DB
  - dogs_sweep()      → DogsCollector → dogs_parser → upsert to DB
  - board_rebuild()   → reads DB → rebuilds in-memory board cache
  - result_check()    → detects jumped races, updates statuses

All functions return {ok: bool, date: str, stored: int, errors: int}
All use T() for table access. All log with structured prefixes.
"""

def horse_sweep(date: str | None = None) -> dict:
    """Horse pipeline: Claude API → normalise → DB upsert."""

def dogs_sweep(date: str | None = None) -> dict:
    """Dogs pipeline: Playwright → parse → DB upsert."""

def board_rebuild() -> dict:
    """Read today_races from DB → populate board cache."""

def result_check() -> dict:
    """Check race times, update statuses for jumped/resulted races."""

def _upsert_race(race: dict) -> bool:
    """Persist single race + runners. Uses T(). Returns True if written."""

def _normalise_race_uid(code: str, track: str, num: int, date: str) -> str:
    """Return canonical race_uid: DATE_CODE_TRACK_NUM (lowercase, underscores)."""
```

### `data/scheduler.py` — Background Scheduler

```python
"""
Single background thread. Runs all pipeline sweeps on interval timers.
Uses threading.Lock per cycle to prevent overlapping runs.

Cycles:
  horse_sweep      every 10 min (HORSE_SWEEP_INTERVAL)
  dogs_sweep       every 10 min (DOGS_SWEEP_INTERVAL)
  board_rebuild    every 90 sec (BOARD_REBUILD_INTERVAL)
  result_check     every 3 min  (RESULT_CHECK_INTERVAL)
  eval_backfill    every 1 hr

start_scheduler() → starts daemon thread (called from gunicorn post_fork)
get_status() → returns {running, last_*_at, last_*_result, last_error}
"""
```

-----

## PHASE 4 — INTELLIGENCE ENGINE

### `intelligence/features.py` — Feature Extraction

```python
"""
Extracts machine-learning-ready features from raw race + runner data.
Input: race dict + runners list (from DB)
Output: list of per-runner feature dicts

Features extracted per runner:
  implied_probability   — 1 / win_odds (OddsPro authoritative)
  early_speed_score     — derived from best_time vs field
  late_speed_score      — derived from career form
  sectional_consistency — career win % stability
  race_shape_fit        — how runner style matches predicted race shape
  box_bias_score        — track-specific box advantage (GREYHOUND only)
  collision_risk_score  — first bend collision probability (GREYHOUND only)
  barrier_score         — barrier draw quality (GALLOPS only)
  weight_adj_score      — weight for age / handicap adj (GALLOPS only)
  form_trajectory       — ACCELERATING / STABLE / PEAKING / DECLINING
  freshness             — FRESH / NORMAL / TIRED / HIGH_RISK
  consistency_index     — place% over career

All features 0-1 normalised. Missing data → 0.0 with flag.
"""
```

### `intelligence/scorer.py` — Multi-Signal Scorer

```python
"""
Weighted multi-signal scoring. Produces final ranked runner list.

V2 Weights (configurable from system_state):
  implied_probability       × 0.30  (market — authoritative)
  early_speed_score         × 0.12
  late_speed_score          × 0.12
  sectional_consistency     × 0.08
  race_shape_fit            × 0.12
  collision_risk_score      × -0.10 (subtracted; GREYHOUND only)
  box_bias_score            × 0.10  (GREYHOUND only)
  barrier_score             × 0.10  (GALLOPS only)

E39 Filters (must all pass for BET decision):
  DIF — Data Integrity Filter (min 60/100)
  TDF — True Dominance Filter (TRUE_DOM or CO_DOM)
  CHF — Chaos Harmony Filter (max 2 fast runners or HARD_BLOCK)
  VEF — Value/EV Filter (EV >= configured threshold)
  MTF — Market Trap Filter (no FALSE_FAV or STEAM)

Functions:
  score_race(race, runners) → {decision, selection, box, scores, filters, audit}
  score_runners(features, race_code) → ranked list with scores
  apply_filters(scored, settings) → {pass: bool, blocks: list}
"""
```

### `intelligence/race_shape.py` — Pace Analysis

```python
"""
Classifies race tempo and projects outcomes.
Used by both GREYHOUND and GALLOPS scoring.

pace_type: SLOW / MODERATE / FAST / HOT
collapse_risk: LOW / MODERATE / HIGH
beneficiary: LEADER / CHASER / WIDE

Track bias database (greyhounds):
  cannington, sandown, meadows, angle_park, etc.
  bias_type: INSIDE / EARLY / NEUTRAL
  favours: RAILER / LEADER / ANY
"""
```

### `intelligence/collision.py` — Greyhound Collision Model

```python
"""
GREYHOUND ONLY. Models first-bend collision probability.
Inputs: box positions, run styles, track profile
Output: per-runner collision_risk_score (0-1, subtracted in scorer)

Box profiles per track (hardcoded from historical analysis):
  box 1-2: STRONG (inside advantage)
  box 3-5: HIGH collision risk
  box 6-8: WIDE runner advantage

Functions:
  build_collision_metrics(runners, track) → list of {box_num, collision_risk, box_score}
  get_track_profile(track) → {bias_type, strength, favours}
"""
```

### `intelligence/signals.py` — Signal Generation

```python
"""
Converts scored race output to actionable trading signal.

Signal types:
  BET     — clear edge, positive EV, all E39 filters pass
  SESSION — moderate confidence, EV marginal, worth watching
  PASS    — insufficient edge or data

Output includes:
  signal, decision, confidence_tier, ev, recommended_stake,
  alert_level (CRITICAL/HIGH/MODERATE/LOW)
  
functions:
  generate_signal(scored, settings) → signal dict
  generate_signals_for_board(races_scored) → list of signal dicts
"""
```

### `intelligence/predictor.py` — Prediction Orchestrator

```python
"""
Orchestrates the full intelligence pipeline for a race.
Called by scheduler (predict_today) and API endpoints.

predict_race(race_uid) → runs features → scorer → signals → saves snapshot
predict_today() → runs predict_race for all open/upcoming races

Saves to:
  prediction_snapshots — one row per race
  scored_races — scoring detail
  signals — final signal

Never modifies today_races or today_runners (read-only inputs).
"""
```

### `intelligence/packet_builder.py` — Claude Packet

```python
"""
Builds compact pre-scored packet for Claude AI interpretation.
Claude receives ONLY this. Never raw data.
Max 1200 characters.

Format:
=== DEMONPULSE INTELLIGENCE PACKET ===
RACE: {track} R{num} | {distance}m | {code} | Jump {time}
SHAPE: {race_shape_summary}
PRE-DECISION: {BET/SESSION/PASS} | CONFIDENCE: {tier}
SELECTION: Box {N} {runner} | {style} | {trainer}
FILTERS: DIF:{score} TDF:{score} CHF:{score} VEF:{score} MTF:{score}
TOP 4: {box} {name} {score} | {box} {name} {score} | ...
SESSION: Bankroll=${amount} | Mode={mode}
=== END PACKET ===
"""
```

### `intelligence/system_prompt.py` — Claude Interpreter

```python
"""
System prompt for Claude acting as race intelligence interpreter.
Claude receives pre-scored packets. Does NOT fetch data. Does NOT score.
Claude's job: final judgment, explanation, stake recommendation.

Key laws:
  1. Never fabricate race data
  2. PASS is valid — never force a bet
  3. Positive EV required for BET
  4. No cross-code contamination
  5. Source trace on every analysis
  6. Prior race result never influences next selection

Output format:
  BET: CODE | RACE | SELECTION | BET_TYPE | ODDS | STAKE | CONFIDENCE | EV | EDGE | REASON
  PASS: DECISION: PASS | REASON: {one line}
"""
```

-----

## PHASE 5 — BETTING MANAGEMENT

### `betting/staking.py` — Kelly + EV

```python
"""
Staking calculations. All pure functions, no DB calls.

Functions:
  calculate_ev(win_prob, decimal_odds) → float
  kelly_stake(bankroll, win_prob, odds, confidence_tier, bank_mode) → float
    - ELITE 40% Kelly, max 7% bankroll
    - HIGH 30% Kelly, max 7%
    - MODERATE 20% Kelly, max 5%
    - LOW 10% Kelly, max 3%
  bank_mode_multiplier(mode) → float
    - SAFE: 0.60  STANDARD: 1.00  AGGRESSIVE: 1.15
  ev_threshold(confidence_tier) → float
    - ELITE: +0.08  HIGH: +0.10  MODERATE: +0.12
"""
```

### `betting/exotics.py` — Exotic Bets

```python
"""
Exotic bet calculators. All pure functions.
UNIT_COST = 1.00 (all calculations per $1 unit)

Functions:
  calc_exacta(first, second, odds_first, odds_second, unit) → dict
  calc_exacta_box(runners, odds, unit) → dict
  calc_trifecta(runners, odds, unit) → dict
  calc_trifecta_box(runners, odds, unit) → dict
  calc_first4_box(runners, odds, unit) → dict
  calc_multi(legs, unit) → dict
  auto_suggest(signal, runners, unit) → list[dict]
    — auto-generates exotic suggestions based on signal output
"""
```

### `betting/bet_manager.py` — Bet CRUD

```python
"""
Bet placement, settlement, history, P/L tracking.
All functions require user_id for scoping.

Functions:
  place_bet(user_id, race_uid, bet_params) → bet_log row
  settle_bet(bet_id, result, return_amount) → updated bet_log row
  get_open_bets(user_id) → list
  get_bet_history(user_id, date_from, date_to) → list
  get_session_pl(user_id, date) → {total, bets, wins, pending, roi}
  reset_bank(user_id, new_amount) → system_state update

P/L is always computed from bet_log. Never stored as running total.
"""
```

-----

## PHASE 6 — LEARNING ENGINE

### `learning/evaluator.py` — Post-Race Evaluation

```python
"""
Runs after results come in. Compares predictions to actuals.
Writes to learning_evaluations.

Functions:
  evaluate_prediction(race_uid, result) → evaluation dict
  backfill_evaluations() → evaluates all un-evaluated snapshots with results
  get_accuracy_summary(days=30) → {accuracy, total, by_confidence, by_code}
"""
```

### `learning/tagger.py` — Loss Analysis

```python
"""
Automatically tags losing bets with reason categories (ETG = Edge Type Grading).

Tags:
  DATA_GAP      — missing key data (best_time, form, etc.)
  FORM_MISS     — form data suggested wrong
  MARKET_MOVE   — market moved significantly post-signal
  CHAOS         — race shape collapsed, chaos scenario
  BOX_DRAW      — unfavourable box draw overcame edge
  TRAINER_MOVE  — trainer/jockey change not in data

Functions:
  auto_tag_loss(bet, race_scored, result) → list of tag strings
  save_tag(bet_id, race_uid, tag, notes) → etg_tags row
"""
```

### `learning/adjustments.py` — AEEE Weight Adjustments

```python
"""
AEEE = Adaptive Edge Evaluation Engine.
Analyses recent performance by edge type and suggests weight adjustments.

Functions:
  review_adjustments() → list of suggested {edge_type, direction, amount}
  apply_adjustment(adjustment_id) → updates system_state weights
  get_active_multipliers() → {edge_type: float} currently active
  performance_by_edge_type(days=30) → breakdown of results by tag
"""
```

### `learning/backtester.py` — Historical Replay

```python
"""
Replays historical race data through the current scoring engine.
Uses results_log as ground truth. Never uses future data.

Functions:
  run_backtest(date_from, date_to, code=None) → backtest result dict
  _replay_race(race_uid) → single race simulation
  
Returns: {
  date_from, date_to, total_races, signals_generated,
  bets_triggered, wins, roi, pl, by_track, by_confidence,
  by_race_shape, kelly_simulation
}
"""
```

-----

## PHASE 7 — API LAYER

### Blueprint: `api/races.py`

```
GET  /api/races              → list races (params: date, code, status)
GET  /api/races/<race_uid>   → single race + runners
GET  /api/races/board        → today's board (from cache)
POST /api/races/refresh/<race_uid> → force refresh single race
```

### Blueprint: `api/predictions.py`

```
POST /api/predictions/race/<race_uid>  → trigger prediction
GET  /api/predictions/race/<race_uid>  → stored prediction
POST /api/predictions/today            → predict all open races
GET  /api/predictions/today            → all today's predictions
GET  /api/predictions/performance      → accuracy summary
```

### Blueprint: `api/bets.py`

```
POST /api/bets/place          → place bet
POST /api/bets/settle/<id>    → settle bet
GET  /api/bets/open           → open bets
GET  /api/bets/history        → bet history (params: date_from, date_to)
GET  /api/bets/summary        → today's P/L summary
POST /api/bets/reset-bank     → reset bankroll
GET  /api/bets/exotics/suggest/<race_uid> → exotic suggestions
POST /api/bets/exotics/calculate → calculate exotic bet cost
```

### Blueprint: `api/analytics.py`

```
GET /api/analytics/pl          → P/L by period (day/week/month)
GET /api/analytics/performance → model accuracy, ROI, by-track breakdown
GET /api/analytics/by-track    → stats grouped by track
GET /api/analytics/by-code     → GREYHOUND vs GALLOPS comparison
GET /api/analytics/streaks     → win/loss streak detection
```

### Blueprint: `api/admin.py` (require_role(“admin”))

```
POST /api/admin/sweep          → trigger manual data sweep
POST /api/admin/sweep/horses   → horse pipeline only
POST /api/admin/sweep/dogs     → dogs pipeline only
POST /api/admin/rebuild-board  → force board rebuild
POST /api/admin/predict-today  → force prediction run
POST /api/admin/backtest       → run backtest (body: date_from, date_to)
GET  /api/admin/scheduler      → scheduler status
POST /api/admin/settings       → update system_state settings
GET  /api/admin/users          → list users
POST /api/admin/users          → create user
```

### Blueprint: `api/health.py`

```
GET /api/health               → {ok, db_connected, scheduler_running, last_sweep_at}
GET /api/health/detailed      → full system status
GET /debug                    → human-readable debug page
```

-----

## PHASE 8 — FRONTEND

### Design System

**Aesthetic: Professional dark terminal. Think Bloomberg + Betfair Pro + Racenet.**

```css
/* tokens.css — Design system */
:root {
  /* Backgrounds */
  --bg-base:     #0a0c10;    /* deepest background */
  --bg-surface:  #111318;    /* cards, panels */
  --bg-elevated: #1a1d26;    /* hover states, modals */
  --bg-input:    #16192100;  /* input fields */

  /* Borders */
  --border:      #232838;
  --border-dim:  #1a1e2a;
  --border-focus: #3b82f6;

  /* Text */
  --text:        #e2e8f0;    /* primary */
  --text-dim:    #64748b;    /* secondary / labels */
  --text-muted:  #374151;    /* disabled */

  /* Accent colours — racing signals */
  --green:       #22c55e;    /* BET / WIN */
  --green-dim:   #166534;
  --red:         #ef4444;    /* LOSS / HARD_BLOCK */
  --red-dim:     #7f1d1d;
  --amber:       #f59e0b;    /* SESSION / WARNING */
  --amber-dim:   #78350f;
  --blue:        #3b82f6;    /* INFO / LINK */
  --blue-dim:    #1e3a5f;
  --purple:      #a855f7;    /* AI / PREDICTION */
  --purple-dim:  #4c1d95;

  /* Typography */
  --font-mono:   'JetBrains Mono', 'Fira Code', monospace;  /* data/numbers */
  --font-ui:     'Inter', system-ui, sans-serif;             /* labels/body */
  --font-display: 'DM Sans', 'Inter', sans-serif;            /* headings */
  
  /* Spacing */
  --space-xs: 4px;  --space-sm: 8px;  --space-md: 16px;
  --space-lg: 24px; --space-xl: 32px;

  /* Radius */
  --radius-sm: 4px;  --radius-md: 8px;  --radius-lg: 12px;
}
```

### Page: `templates/board.html` — Race Board (Home)

Layout: Full-width race board. Professional race meeting format.

```
┌─────────────────────────────────────────────────────────────────┐
│  DEMONPULSE             AEST 14:32:08    ● LIVE    ENV: LIVE    │
│  [Board] [Live] [Bets] [Analytics] [Learning] [Settings]        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  TODAY'S RACES  ·  Sunday 12 Apr          ○ DOGS  ○ HORSES     │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ GREYHOUND MEETINGS                                        │   │
│  │                                                           │   │
│  │  SANDOWN PARK                      VIC   ● Live          │   │
│  │  R1 14:30 · R2 14:50 · R3 15:10 · R4 15:30 · R5 15:50  │   │
│  │  ↑ chips showing race status: upcoming/open/resulted     │   │
│  │                                                           │   │
│  │  ANGLE PARK                        SA    ↑ 25m           │   │
│  │  R1 14:35 · R2 14:55 · R3 15:15 · R4 15:35             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ GALLOPS MEETINGS                                          │   │
│  │  RANDWICK                          NSW   ↑ 1h 20m        │   │
│  │  R1 15:00 · R2 15:40 · R3 16:20 ·  ...                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  SIGNALS TODAY:  3 BET  ·  7 SESSION  ·  12 PASS              │
│  SESSION P/L: +$47.20   ROI: +4.7%                             │
└─────────────────────────────────────────────────────────────────┘
```

Race chips: colour-coded by status.

- Grey: upcoming
- Blue pulse: open (<5 min to jump)
- Green: resulted
- Red: abandoned
- Amber: pending result

Click any race → navigate to `/live?race=<race_uid>`

### Page: `templates/live.html` — Live Race Intelligence View

Layout: Two-column. Left 62%: form guide. Right 38%: intelligence panel.

```
┌─────────────── LIVE INTELLIGENCE VIEW ──────────────────────────┐
│  SANDOWN PARK  ·  R5  ·  520m  ·  Grade 5  ·  Jump 15:50      │
│  [← Prev Race]                              [Next Race →]       │
├───────────────────────────────────┬─────────────────────────────┤
│  FORM GUIDE                       │  INTELLIGENCE               │
│                                   │                             │
│  Box Runner         Trainer  Odds │  ┌─────────────────────┐   │
│  ──────────────────────────────── │  │ SIGNAL              │   │
│  1   TURBO DRIVE    J.Smith  $3.20│  │ ████ BET            │   │
│      5-3-2 | RAILER | 29.85s      │  │ HIGH CONFIDENCE     │   │
│      [Career stats mini-bar]      │  │ EV: +0.14           │   │
│                                   │  └─────────────────────┘   │
│  2   FAST LANE      M.Jones  $4.50│                             │
│      10-4-3 | LEADER| 30.10s      │  DECISION: BET WIN         │
│                                   │  Selection: Box 1           │
│  3   DARK HORSE     T.Brown  $6.00│  Stake Rec: $42 (4.2%)     │
│      8-2-3 | CHASER | 30.40s      │                             │
│  ...                              │  RACE SHAPE                 │
│                                   │  FAST pace  ·  CHASER adv  │
│  [AI COMMENTARY TAB]              │  Collapse risk: MODERATE    │
│  [RECENT STARTS TAB]              │                             │
│  [EXOTICS TAB]                    │  E39 FILTERS               │
│                                   │  DIF ██ 82  TDF ██ 76      │
│                                   │  CHF ██ 70  VEF ██ 68      │
│                                   │  MTF ██ 80                  │
│                                   │                             │
│                                   │  [PLACE BET]               │
│                                   │  [AI ANALYSIS]             │
└───────────────────────────────────┴─────────────────────────────┘
```

Form guide requirements:

- Per-runner row with box draw highlight (coloured by box bias)
- Career stats bar (starts, win%, place%)
- Run style badge (RAILER/LEADER/CHASER/WIDE)
- Best time displayed
- Odds chip (updates live)
- Scratched runners greyed out with strikethrough
- Expandable “Recent Starts” section per runner

Intelligence panel requirements:

- Signal badge: large, colour-coded (green=BET, amber=SESSION, grey=PASS)
- Confidence tier display
- EV displayed
- Race shape summary
- E39 filter scores as mini progress bars
- Place Bet button → opens bet modal
- AI Analysis button → calls Claude with packet

### Page: `templates/bets.html` — Bet Management

Three tabs: Open Bets | Bet Log | Exotics Calculator

```
SESSION: Sun 12 Apr   BANKROLL: $1,024.50   P/L: +$24.50   ROI: +2.4%
[SAFE]  [STANDARD]  [AGGRESSIVE]  ←→ bank mode selector

OPEN BETS tab:
┌──────┬────────────┬─────┬─────────┬───────┬────────┬─────────┐
│ Race │ Selection  │ Box │ Bet     │ Stake │ Odds   │ E.Return│
├──────┼────────────┼─────┼─────────┼───────┼────────┼─────────┤
│ SAN5 │ TURBO DRIV │  1  │ WIN     │ $42   │ $3.20  │ $134.40 │
│ ANG3 │ FAST LANE  │  2  │ WIN     │ $28   │ $4.50  │ $126.00 │
└──────┴────────────┴─────┴─────────┴───────┴────────┴─────────┘

BET LOG tab: scrollable history with filters (date, code, result)
Shows: Race | Selection | Bet | Stake | Odds | Return | P/L | Tag

EXOTICS CALCULATOR tab:
Exacta / Trifecta / First4 tabs
Runner selector → calculates cost and return
Auto-suggest based on current signal
```

### Page: `templates/analytics.html` — Performance Analytics

```
P/L CHART:  [Day] [Week] [Month] [Year]  ←→ period selector
Line chart showing cumulative P/L over time

STATS CARDS ROW:
┌──────────┬──────────┬──────────┬──────────┬──────────┐
│ Total P/L│  ROI     │  Win Rate│ Avg Stake│ Bets     │
│ +$247.50 │  +4.8%   │  34.2%   │  $35.20  │ 142      │
└──────────┴──────────┴──────────┴──────────┴──────────┘

PERFORMANCE BY CONFIDENCE:
  ELITE:    W:8/10   ROI:+18.2%  ████████░░
  HIGH:     W:21/48  ROI:+6.4%   ████░░░░░░
  MODERATE: W:19/84  ROI:-1.2%   ███░░░░░░░

PERFORMANCE BY TRACK: table sortable by ROI
EDGE TYPE BREAKDOWN: bar chart by ETG tag category
```

### Page: `templates/learning.html` — AI Learning Centre

Three tabs: Model Performance | Adjustments | Backtesting

```
MODEL PERFORMANCE tab:
  Accuracy chart over rolling 30 days
  Win/loss heat map by confidence tier
  Top performing tracks
  Bottom performing tracks

ADJUSTMENTS tab:
  Active AEEE adjustments displayed as badges
  Suggested adjustments with rationale
  Apply / reject controls (admin only)
  Weight history log

BACKTESTING tab:
  Date range selector
  Code filter (GREYHOUND / GALLOPS / Both)
  Run Backtest button
  Results: simulated P/L, ROI, signal accuracy
  Comparison: current weights vs proposed weights
```

### `static/js/core.js` — Shared JavaScript

```javascript
// Authentication: reads JWT from localStorage, attaches to all API calls
// apiFetch(url, options) — wrapper around fetch() with auth header + error handling
// countdown(jumpTime) → returns {mins, secs, label} for display
// formatOdds(decimal) → "$3.20"
// formatPL(pl) → "+$47.20" or "-$12.00" with colour class
// pollBoard(interval) — polls /api/races/board on interval, updates DOM
// showToast(message, type) — BET/SESSION/PASS/ERROR toast notifications
```

-----

## PHASE 9 — GUNICORN + DEPLOYMENT

### `gunicorn.conf.py`

```python
def on_starting(server):
    import os
    os.environ["GUNICORN_MANAGED"] = "1"

def post_fork(server, worker):
    try:
        from data.scheduler import start_scheduler
        start_scheduler()
        server.log.info("DemonPulse: scheduler started in worker")
    except Exception as e:
        server.log.warning(f"DemonPulse: scheduler start failed: {e}")
```

### `app.py` — Flask App Factory

```python
def create_app():
    app = Flask(__name__)
    
    # Register all blueprints
    from api.races import races_bp
    from api.predictions import predictions_bp
    from api.bets import bets_bp
    from api.analytics import analytics_bp
    from api.admin import admin_bp
    from api.auth import auth_bp
    from api.health import health_bp
    
    for bp in [races_bp, predictions_bp, bets_bp, analytics_bp, 
               admin_bp, auth_bp, health_bp]:
        app.register_blueprint(bp)
    
    # Page routes (return templates)
    register_page_routes(app)
    
    # If not running under gunicorn, start scheduler here
    if not os.environ.get("GUNICORN_MANAGED"):
        from data.scheduler import start_scheduler
        start_scheduler()
    
    return app

app = create_app()
```

### `requirements.txt`

```
flask==3.0.3
gunicorn==22.0.0
requests==2.32.3
httpx==0.25.2
anthropic>=0.40.0        # CRITICAL — do not remove
supabase==2.3.4
gotrue==2.8.1
python-dotenv==1.0.1
beautifulsoup4==4.12.3
lxml==5.2.2
pandas==2.2.2
numpy==1.26.4
playwright==1.50.0
playwright-stealth==2.0.3
pillow==10.4.0
```

### `render.yaml`

```yaml
services:
  - type: web
    name: demonpulse
    env: python
    pythonVersion: "3.11.9"
    buildCommand: pip install -r requirements.txt && python -m playwright install chromium
    startCommand: gunicorn app:app --workers 1 --timeout 120 --bind 0.0.0.0:$PORT --preload --config gunicorn.conf.py
    envVars:
      - key: SUPABASE_URL
        sync: false
      - key: SUPABASE_KEY
        sync: false
      - key: CLAUDE_API_KEY
        sync: false
      - key: JWT_SECRET
        generateValue: true
      - key: FLASK_SECRET
        generateValue: true
      - key: ADMIN_PASSWORD
        sync: false
      - key: DP_ENV
        value: LIVE
      - key: PLAYWRIGHT_BROWSERS_PATH
        value: "0"
```

-----

## PHASE 10 — DATA ISSUE RESOLUTION

The current data pipeline has one structural problem causing empty boards:
**Horse data relies on Claude API → racingaustralia.horse, but this source may have limited public data.**

Recommended data strategy:

### Option A: OddsPro API (already have key)

```
OddsPro provides: meetings, races, runners, odds for AU racing
Endpoint: https://oddspro.com.au/api/external/
Use for: authoritative race card data for BOTH greyhounds and horses
Replace Claude horse scraper with direct OddsPro API calls

data/oddspro_collector.py:
  fetch_meetings(date, code) → list of meeting dicts
  fetch_races(meeting_id) → list of race dicts with runners
  fetch_odds(race_id) → live odds per runner
```

### Option B: Keep thedogs.com.au for greyhounds + add a second horse source

```
Greyhounds: thedogs.com.au (working, keep as-is)
Horses: racing.com / racenet.com.au via Playwright scraper
         OR pointsbet/sportsbet public race cards
         OR OddsPro API
```

### Option C: Simplified hybrid (recommended for V2 start)

```
GREYHOUND: thedogs.com.au browser (proven working)
GALLOPS:   OddsPro API (most reliable, you have the key)

data/pipeline.py dogs_sweep() → unchanged (Playwright)
data/pipeline.py horse_sweep() → rewrite to use OddsPro API directly
  1. GET /meetings?date={date}&code=R  → list of today's gallops meetings
  2. For each meeting: GET /races?meeting_id={id} → race list
  3. For each race: GET /runners?race_id={id} → runner list with odds
  4. Normalise → upsert to today_races + today_runners
```

-----

## COPILOT IMPLEMENTATION ORDER

Work through phases in sequence. Each phase should be fully working before starting the next.

1. **Phase 0+1**: Skeleton + core infrastructure (config, db, env, auth)
1. **Phase 2**: Full SQL schema deployed to Supabase
1. **Phase 3**: Data pipeline (start with dogs only, verify to DB, then add horses)
1. **Phase 4**: Intelligence engine (features → scorer → signals → predictor)
1. **Phase 5**: Betting management (staking, exotics, bet_manager)
1. **Phase 6**: Learning engine (evaluator, tagger, adjustments, backtester)
1. **Phase 7**: Full API layer (all blueprints)
1. **Phase 8**: Frontend (base template first, then board, then live view)
1. **Phase 9**: Gunicorn + deployment config
1. **Phase 10**: Data source resolution (OddsPro integration)

### At each phase, verify with:

```bash
# Phase 3 check: does data flow to DB?
curl https://yourapp.onrender.com/api/health/detailed

# Phase 4 check: does scoring work?
curl https://yourapp.onrender.com/api/predictions/race/{uid}

# Phase 7 check: do all endpoints return valid JSON?
curl https://yourapp.onrender.com/api/races/board
```

-----

## KEY CODING RULES FOR COPILOT

1. **Always use `T(table_name)` for all DB table access** — never raw strings
1. **`anthropic` must be in requirements.txt** — this was missing in v1 and crashed the scheduler
1. **Scheduler runs in gunicorn `post_fork` only** — never start it twice
1. **All race_uids follow this exact format**: `{date}_{code}_{track_lower_underscore}_{race_num}`
1. **AEST timezone for all race dates** — UTC server date ≠ AEST race date
1. **`safe_query(fn, default)`** wraps every DB call — never let a DB error crash a request
1. **Greyhound features** (box bias, collision) never apply to Gallops, and vice versa (barrier, weight)
1. **Predictions never overwrite** today_races or today_runners — read-only inputs
1. **All module imports inside functions** (lazy imports) to avoid circular dependency issues
1. **Every API endpoint returns** `{ok: bool, ...}` — never raise exceptions to the client

-----

*End of DemonPulse V2 Rebuild Specification*
*Generated: April 2026*
