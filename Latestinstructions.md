# DemonPulse — Copilot Instructions (V9)

## Project overview

DemonPulse is an Australian greyhound (Phase 1) and horse racing (Phase 2) AI betting terminal.

**Stack:** Flask · Supabase (Postgres) · Playwright · Render

**Phase status:**

- Greyhounds (GREYHOUND code): fully active — browser-based pipeline via thedogs.com.au
- Horses (GALLOPS/HARNESS): Phase 2 — Claude API pipeline (connectors/claude_scraper.py)

-----

## Environment

### Required variables

|Variable                |Purpose                                                          |
|------------------------|-----------------------------------------------------------------|
|`DP_ENV`                |`LIVE` (default/production) or `TEST` (dev/stress-test)          |
|`SUPABASE_URL`          |Supabase project URL (production)                                |
|`SUPABASE_KEY`          |Supabase anon key (production)                                   |
|`SUPABASE_TEST_URL`     |Supabase URL for TEST mode (optional; falls back to table prefix)|
|`SUPABASE_TEST_KEY`     |Supabase key for TEST mode                                       |
|`JWT_SECRET`            |HMAC-SHA256 signing secret for JWT tokens                        |
|`FLASK_SECRET`          |Flask session secret                                             |
|`ANTHROPIC_API_KEY`     |Claude API key (horse pipeline + AI commentary)                  |
|`ADMIN_PASSWORD`        |Override bootstrap admin password                                |
|`ADMIN_USERNAME`        |Override bootstrap admin username                                |
|`SESSION_TIMEOUT_MIN`   |JWT TTL in minutes (default 480)                                 |
|`DOGS_SCREENSHOT_DIR`   |Screenshot output dir (default `/tmp/demonpulse_dogs`)           |
|`DOGS_BOARD_MAX_RETRIES`|Playwright retry count (default 3)                               |
|`DOGS_PAGE_TIMEOUT_MS`  |Playwright page load timeout ms (default 30000)                  |
|`EXTRACTION_SERVICE_URL`|External screenshot-to-JSON extraction endpoint                  |

### Environment mode rules

`env.py` is the single source of truth. Never check `DP_ENV` directly — always import from `env`.

```python
from env import env
env.mode          # "TEST" or "LIVE"
env.is_live       # bool
env.is_test       # bool
env.require_test()        # raises EnvViolation if LIVE
env.guard_fake_data()     # raises EnvViolation if LIVE
env.guard_destructive(op) # raises EnvViolation if LIVE
```

**TEST mode:** fake/demo data allowed, demo user bootstrap, table names prefixed `test_`, stress endpoints active.
**LIVE mode:** no fake data, no auto-deletion, no destructive bulk ops, all data permanent and audited.

Tables in `_ALWAYS_LIVE_TABLES` (`users`, `audit_log`) always use production namespace regardless of mode.
Tables in `_TESTABLE_TABLES` use `test_` prefix in TEST mode.

-----

## Architecture

```
app.py                          Flask app, blueprint registration, startup
env.py                          TEST/LIVE authority — import this, never os.getenv("DP_ENV")
db.py                           Supabase client + safe_query + T() table resolver
database.py                     Race/runner/result CRUD helpers over db.py
auth.py                         JWT (HMAC-SHA256), PBKDF2 passwords, RBAC
scheduler.py                    Background thread — all polling cycles live here
pipeline.py                     Daily data pipeline entry point
board_service.py                Live race board builder from stored data
race_status.py                  Race state machine + NTJ calculator
safety.py                       Betting window, recommendation expiry, circuit breaker
scorer.py                       Scoring engines E23-E29: early speed, box, pace, EV, Kelly
signals.py                      Signal generation: SNIPER / VALUE / GEM / WATCH / RISK / NO_BET
integrity_filter.py             Race integrity guard (blocks invalid/incomplete races)
features.py                     Derived field computation for horse racing
exotics.py                      Exotic bet suggestions
audit.py                        Audit log helpers
cache.py                        In-memory TTL cache
learning_engine.py              EPR / AEEE / GPIL learning loop

api/
  health_routes.py              GET /api/health, /api/health/connectors, /scheduler, /live
  race_routes.py                GET/POST /api/races, /api/races/<uid>, /analysis, /result
  board_routes.py               GET /api/board, /blocked, /ntj
  bet_routes.py                 GET/POST /api/bets — summary, history, place, settle, reset-bank
  admin_routes.py               POST /api/admin — sweep, refresh, results, block, migrate, predict
  prediction_routes.py          GET/POST /api/predictions — race, today, performance, backtest

routes/
  dashboard_dogs.py             GET/POST /api/dogs — board, upcoming, race, health, collect

collectors/
  dogs_board_collector.py       Playwright → thedogs.com.au board page (meetings + race times)
  dogs_race_capturer.py         Playwright → thedogs.com.au race detail (runners + form)
  dogs_upcoming_selector.py     Selects next upcoming greyhound races for capture

parsers/
  dogs_source_parser.py         Parses raw Playwright DOM output into DogsBoardEntry objects

models/
  dogs_race_packet.py           DogsBoardEntry dataclass (canonical dogs board item)

services/
  dogs_board_service.py         Orchestrates greyhound board collection pipeline
  dogs_capture_service.py       Manages race capture + extraction service POST
  health_service.py             Thread-safe in-memory health metrics
  race_service.py               Race update helpers
  result_service.py             Result write + settlement
  data_integrity_service.py     Cross-table data validation
  migration_runner.py           Schema migration orchestrator
  schema_bootstrap.py           Idempotent schema bootstrap on startup

connectors/
  claude_scraper.py             Horse racing pipeline — Claude API structured extraction

ai/
  predictor.py                  Race prediction: baseline_v1 + v2_feature_engine
  feature_builder.py            Feature engineering from runner + race data
  learning_store.py             Prediction lineage, snapshots, evaluations
  backtest_engine.py            Historical backtesting (no-leakage rule enforced)
  race_shape.py                 Race shape / pace scenario analysis
  sectionals_engine.py          Per-runner OddsPro sectional metric extraction
  collision_model.py            Box collision / interference model (GREYHOUND only)
  enrichment_guard.py           FormFav enrichment application guard (non-authoritative cap)
  disagreement_engine.py        Model vs market disagreement detection

simulation/
  core_simulation_engine.py     Monte Carlo simulation orchestrator
  race_shape_engine.py          Pre-race shape analysis for simulation
  simulation_aggregator.py      Aggregates N simulation results
  filter_engine.py              Filter pipeline (hard blocks, value, risk, decision)
  expert_guide_integration.py   Generates ExpertGuide from simulation results
  crash_map_engine.py           Crash/interference probability mapping
  models.py                     Simulation dataclasses (RaceMeta, RunnerProfile, etc.)
  filters/
    base_filter.py
    hard_block_filters.py
    core_decision_filters.py
    value_filters.py
    risk_filters.py
    output_filters.py
  race_code_modules/
    greyhound_module.py
    thoroughbred_module.py
    harness_module.py

sql/
  001_canonical_schema.sql      SOLE SCHEMA AUTHORITY — run this in Supabase SQL Editor
```

-----

## Data pipeline

### Greyhound pipeline (Phase 1 — ACTIVE)

```
scheduler._run_dogs_visual()
  └─ services/dogs_board_service.py: collect_dogs_board()
       └─ collectors/dogs_board_collector.py: collect_board(date)
            └─ Playwright → thedogs.com.au/racing/YYYY-MM-DD
               (headless, stealth, screenshots on failure)
       └─ collectors/dogs_upcoming_selector.py: select_upcoming(entries)
       └─ collectors/dogs_race_capturer.py: capture_race(entry)
            └─ Playwright → thedogs.com.au/racing/SLUG/DATE/N
               → screenshot (PNG)
               → POST to EXTRACTION_SERVICE_URL/extract/dogs/race (multipart, PNGs attached)
               → sync JSON response: {success, race_uid, extracted_data, warnings, errors}
       └─ parsers/dogs_source_parser.py: parse(raw)
       └─ database.upsert_race() + database.upsert_runners()
```

**Extraction service contract:**

```
POST /extract/dogs/race
Content-Type: multipart/form-data
  race_uid: str
  date: YYYY-MM-DD
  track: str
  race_num: int
  screenshots: PNG files (one or more)

Response (sync JSON):
  {
    "success": true,
    "race_uid": "...",
    "extracted_data": { runners: [...], race: {...} },
    "warnings": [],
    "errors": []
  }
```

No OCR inside DemonPulse. No Claude Vision inside DemonPulse. No file polling. All extraction is sync via the external service.

### Horse pipeline (Phase 2 — ACTIVE)

```
scheduler._run_full_sweep()
  └─ pipeline.full_sweep()
       └─ connectors/claude_scraper.py: ClaudeScraper
            → Claude API (claude-haiku-4-5) structured extraction
            → venues discovered, races + runners built
       └─ features.compute_horse_derived()
       └─ database.upsert_race() + database.upsert_runners()
```

Claude rate-limit (429) handling: `ClaudeRateLimitError` raised, pipeline falls back to `load_venue_cache()` and marks sweep as `partial_cached`.

### Pipeline invariants

- **GREYHOUND data source:** `thedogs_browser` (stored in `today_races.source`)
- **HORSE data source:** `oddspro` or `claude` (stored in `today_races.source`)
- OddsPro is the authoritative source label for confirmed results
- FormFav / external enrichment is non-authoritative; never overwrites official tables
- `race_uid` format: `{date}_{track}_{race_num}_{code}` (e.g. `2026-04-10_sandown_3_GREYHOUND`)
- Upsert conflict key for races: `(date, track, race_num, code)`
- Upsert conflict key for runners: `(race_uid, box_num)`

-----

## Scheduler cycles

Defined in `scheduler.py`. All cycles use `threading.Lock` with `acquire(blocking=False)` — skips if already running.

|Cycle              |Interval      |Function                                            |
|-------------------|--------------|----------------------------------------------------|
|`full_sweep`       |600 s (10 min)|`pipeline.full_sweep()` — horse races via Claude API|
|`board_rebuild`    |90 s          |`board_service.get_board_for_today()` rebuild       |
|`result_check`     |180 s (3 min) |`race_status.bulk_update_race_states()`             |
|`race_state_update`|90 s          |State machine transitions from stored jump_time     |
|`health_snapshot`  |120 s (2 min) |`services/health_service.py` aggregation            |
|`eval_backfill`    |3600 s (1 hr) |Learning evaluation backfill                        |
|`dogs_visual`      |600 s (10 min)|Playwright greyhound board collection               |

Scheduler is started per-worker in gunicorn `post_fork`. `GUNICORN_MANAGED=1` env var prevents double-start. Self-healing: `/api/system/status` and `/api/scheduler/watchdog` restart a dead thread.

-----

## Race state machine

States and transitions (managed by `race_status.py`):

```
upcoming
  → near_jump          (< 600 s to jump_time)
  → jumped_estimated   (jump_time passed, no result)
  → awaiting_result    (30+ min past jump, no result)
  → result_posted      (OddsPro result confirmed)
  → final              (terminal: result settled)
  → paying             (terminal: dividends paying)
  → abandoned          (terminal: race abandoned)

Any state → blocked    (hard block via integrity_filter or admin)
Any state → stale_unknown (no/unparseable jump_time, data stale)
```

`LIVE_STATUSES` = `{upcoming, open, interim, near_jump, jumped_estimated, awaiting_result}`
`SETTLED_STATUSES` = `{final, paying, abandoned, result_posted}`
`FINAL_STATES` = `{final, paying, abandoned}` — never downgraded by time-based logic

NTJ windows (from `race_status.compute_ntj()`):

- `IMMINENT`: 0–120 s
- `NEAR`: 120–600 s
- `UPCOMING`: 600+ s
- `PAST`: jump_time in the past

-----

## Board service

`board_service.get_board_for_today()` returns:

```json
{
  "ok": true,
  "items": [
    {
      "race_uid": "2026-04-10_sandown_3_GREYHOUND",
      "track": "sandown",
      "race_num": 3,
      "code": "GREYHOUND",
      "date": "2026-04-10",
      "jump_time": "14:02",
      "status": "near_jump",
      "distance": "515",
      "grade": "Grade 5",
      "runners": [...],
      "seconds_to_jump": 480,
      "ntj_label": "NEAR"
    }
  ],
  "count": 24,
  "date": "2026-04-10"
}
```

Board filters out `abandoned`, `invalid`, `blocked`, `cancelled` races. Sorted by `seconds_to_jump` ascending.

-----

## Authentication

`auth.py` — JWT (HMAC-SHA256, no external lib), PBKDF2-SHA256 passwords.

### Roles and permissions

|Role      |Permissions                                                                                                       |
|----------|------------------------------------------------------------------------------------------------------------------|
|`admin`   |home, live, betting, reports, simulator, ai_learning, settings, audit, users, backtest, data, quality, performance|
|`operator`|home, live, betting, reports                                                                                      |
|`viewer`  |home, reports                                                                                                     |

### JWT structure

```python
{
  "sub": "<user_id>",
  "username": "<username>",
  "role": "<role>",
  "iat": <epoch>,
  "exp": <epoch + TTL>,
  "jti": "<hex16>",
  "env": "LIVE" | "TEST"
}
```

Token delivered as `dp_token` httponly cookie (+ JSON body). TTL = `SESSION_TIMEOUT_MIN × 60`.

Token validation: checks signature, expiry, env match, user active status, session revocation via `user_sessions` table.

### Decorators

```python
from auth import require_role, get_current_user

@require_role("admin")          # 403 if not admin
@require_role("operator")       # 403 if not operator or admin
user = get_current_user()       # returns decoded JWT payload or None
```

### Rate limiting

Max 10 login attempts per IP per 5-minute window (in-memory `LOGIN_RATE` dict).

### Bootstrap

`auth.bootstrap_admin()` — runs on startup. Creates admin user if no users exist. In TEST mode also creates `operator_demo` and `viewer_demo` accounts.

-----

## Database layer

### db.py

```python
from db import get_db, safe_query, T

# T() resolves table name respecting env mode prefix
T("bet_log")          # → "bet_log" (LIVE) or "test_bet_log" (TEST)
T("users")            # → always "users" (in _ALWAYS_LIVE_TABLES)

# safe_query wraps any Supabase query with exception handling
result = safe_query(lambda: get_db().table(T("signals")).select("*").execute().data, [])
```

### database.py

High-level CRUD helpers. Always use these instead of raw Supabase calls for race/runner/result data.

```python
from database import (
    upsert_race, get_race, get_races_for_date, get_active_races,
    upsert_runners, get_runners_for_race,
    upsert_meeting, get_meeting,
    mark_race_blocked, update_race_status,
    write_source_log, save_race_note,
    sync_result_statuses, get_result,
    get_blocked_races, get_formfav_enrichments_for_date,
)
```

-----

## Full database schema

Schema file: `sql/001_canonical_schema.sql` — sole authority. Run in Supabase SQL Editor. Idempotent (safe on existing databases). All `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` guards included.

### Section 1: Core runtime tables

#### `meetings`

Meeting-level identity. Conflict key: `(date, track, code)`.

|Column    |Type       |Notes                                             |
|----------|-----------|--------------------------------------------------|
|id        |UUID PK    |gen_random_uuid()                                 |
|date      |DATE       |                                                  |
|track     |TEXT       |                                                  |
|code      |TEXT       |CHECK IN (‘GREYHOUND’,‘HARNESS’,‘GALLOPS’,‘HORSE’)|
|state     |TEXT       |                                                  |
|country   |TEXT       |default ‘AUS’                                     |
|weather   |TEXT       |                                                  |
|rail      |TEXT       |                                                  |
|track_cond|TEXT       |                                                  |
|race_count|INTEGER    |                                                  |
|source    |TEXT       |default ‘oddspro’                                 |
|updated_at|TIMESTAMPTZ|                                                  |

Indexes: `date`, `code`, `(date, code)`

#### `today_races`

Primary race data. Authoritative source is OddsPro / thedogs_browser. Conflict key: `(date, track, race_num, code)`.

|Column              |Type       |Notes                                      |
|--------------------|-----------|-------------------------------------------|
|id                  |UUID PK    |                                           |
|race_uid            |TEXT       |Unique partial index (where != ‘’)         |
|oddspro_race_id     |TEXT       |                                           |
|date                |DATE       |                                           |
|track               |TEXT       |                                           |
|state               |TEXT       |                                           |
|country             |TEXT       |default ‘au’                               |
|race_num            |INTEGER    |                                           |
|code                |TEXT       |default ‘GREYHOUND’                        |
|distance            |TEXT       |                                           |
|grade               |TEXT       |                                           |
|jump_time           |TEXT       |HH:MM or ISO datetime                      |
|prize_money         |TEXT       |                                           |
|race_name           |TEXT       |                                           |
|condition           |TEXT       |                                           |
|status              |TEXT       |default ‘upcoming’ — see race state machine|
|block_code          |TEXT       |set when status=‘blocked’                  |
|source              |TEXT       |‘thedogs_browser’ or ‘oddspro’ or ‘claude’ |
|source_url          |TEXT       |                                           |
|time_status         |TEXT       |‘PARTIAL’ or ‘CONFIRMED’                   |
|completeness_score  |INTEGER    |0–100                                      |
|completeness_quality|TEXT       |‘LOW’/‘MEDIUM’/‘HIGH’                      |
|race_hash           |TEXT       |content hash for change detection          |
|lifecycle_state     |TEXT       |‘fetched’/‘normalised’/‘scored’/‘predicted’|
|runner_count        |INTEGER    |                                           |
|fetched_at          |TIMESTAMPTZ|                                           |
|updated_at          |TIMESTAMPTZ|                                           |
|completed_at        |TIMESTAMPTZ|                                           |
|normalized_at       |TIMESTAMPTZ|                                           |
|scored_at           |TIMESTAMPTZ|                                           |
|packet_built_at     |TIMESTAMPTZ|                                           |
|ai_reviewed_at      |TIMESTAMPTZ|                                           |
|bet_logged_at       |TIMESTAMPTZ|                                           |
|result_captured_at  |TIMESTAMPTZ|                                           |
|learned_at          |TIMESTAMPTZ|                                           |
|user_note           |TEXT       |user-written note                          |

Indexes: `date`, `status`, `lifecycle_state`, `(track, race_num)`, `oddspro_race_id`, `race_uid`

#### `today_runners`

Per-runner data. FK to `today_races`. Conflict key: `(race_uid, box_num)`.

|Column           |Type         |Notes                              |
|-----------------|-------------|-----------------------------------|
|id               |UUID PK      |                                   |
|race_id          |UUID FK      |→ today_races(id) ON DELETE CASCADE|
|race_uid         |TEXT         |                                   |
|oddspro_race_id  |TEXT         |                                   |
|date             |DATE         |                                   |
|track            |TEXT         |                                   |
|race_num         |INTEGER      |                                   |
|box_num          |INTEGER      |barrier / box position             |
|name             |TEXT         |runner name                        |
|number           |INTEGER      |race number (horses)               |
|barrier          |INTEGER      |                                   |
|trainer          |TEXT         |                                   |
|jockey           |TEXT         |                                   |
|driver           |TEXT         |harness driver                     |
|owner            |TEXT         |                                   |
|weight           |NUMERIC(5,2) |                                   |
|run_style        |TEXT         |‘RAILER’/‘LEADER’/‘CHASER’/‘WIDE’  |
|early_speed      |TEXT         |                                   |
|best_time        |TEXT         |                                   |
|career           |TEXT         |career stats string                |
|price            |NUMERIC(10,4)|win odds                           |
|rating           |NUMERIC(10,4)|                                   |
|is_fav           |BOOLEAN      |                                   |
|scratched        |BOOLEAN      |                                   |
|scratch_reason   |TEXT         |                                   |
|source_confidence|TEXT         |‘official’/‘provisional’           |
|raw_hash         |TEXT         |                                   |
|created_at       |TIMESTAMPTZ  |                                   |

Indexes: `race_id`, `race_uid`, `name`, `(track, race_num)`, `scratched`, `is_fav`

#### `results_log`

Official confirmed results only. Never write provisional/FormFav data here. Conflict key: `(date, track, race_num, code)`.

|Column      |Type        |Notes              |
|------------|------------|-------------------|
|id          |UUID PK     |                   |
|date        |DATE        |                   |
|track       |TEXT        |                   |
|race_num    |INTEGER     |                   |
|code        |TEXT        |default ‘GREYHOUND’|
|race_uid    |TEXT        |                   |
|winner      |TEXT        |                   |
|winner_box  |INTEGER     |                   |
|win_price   |NUMERIC(8,2)|                   |
|place_2     |TEXT        |                   |
|place_3     |TEXT        |                   |
|margin      |NUMERIC(6,2)|                   |
|winning_time|NUMERIC(7,3)|                   |
|source      |TEXT        |default ‘oddspro’  |
|recorded_at |TIMESTAMPTZ |                   |

#### `race_status`

Per-race status tracking (parallel to today_races.status). Conflict key: `(date, track, race_num, code)`.

|Column                                   |Type       |
|-----------------------------------------|-----------|
|id, date, track, race_num, code, race_uid|standard   |
|status                                   |TEXT       |
|has_runners, has_scratchings, has_result |BOOLEAN    |
|jump_time, time_status                   |TEXT       |
|updated_at                               |TIMESTAMPTZ|

### Section 2: Session and system state

#### `system_state`

Singleton row (id=1). Global engine config.

|Column              |Type         |Default       |
|--------------------|-------------|--------------|
|id                  |INTEGER PK   |1             |
|bankroll            |NUMERIC(10,2)|1000          |
|current_pl          |NUMERIC(10,2)|0             |
|bank_mode           |TEXT         |‘STANDARD’    |
|active_code         |TEXT         |‘GREYHOUND’   |
|posture             |TEXT         |‘NORMAL’      |
|sys_state           |TEXT         |‘STABLE’      |
|variance            |TEXT         |‘NORMAL’      |
|session_type        |TEXT         |‘Live Betting’|
|confidence_threshold|NUMERIC(4,2) |0.65          |
|ev_threshold        |NUMERIC(4,2) |0.08          |
|staking_mode        |TEXT         |‘KELLY’       |
|tempo_weight        |NUMERIC(4,2) |1.0           |
|traffic_penalty     |NUMERIC(4,2) |0.8           |
|closer_boost        |NUMERIC(4,2) |1.1           |
|fade_penalty        |NUMERIC(4,2) |0.9           |
|simulation_depth    |INTEGER      |1000          |
|updated_at          |TIMESTAMPTZ  |              |

#### `sessions`

Daily betting sessions.

|Column                                                        |Type         |
|--------------------------------------------------------------|-------------|
|id UUID PK, date, session_type, account_type                  |standard     |
|bankroll_start, bankroll_end                                  |NUMERIC(10,2)|
|bank_mode, active_code, learning_mode, execution_mode, posture|TEXT         |
|total_bets, wins, losses                                      |INTEGER      |
|pl, roi                                                       |NUMERIC      |
|notes                                                         |TEXT         |
|created_at, ended_at                                          |TIMESTAMPTZ  |

#### `session_history`

Rolled-up session P&L history. FK to `sessions`.

### Section 3: Authentication and user management

#### `users`

Application user accounts (not Supabase auth).

|Column                          |Type        |Notes                                 |
|--------------------------------|------------|--------------------------------------|
|id                              |UUID PK     |                                      |
|username                        |TEXT UNIQUE |                                      |
|password_hash                   |TEXT        |PBKDF2-SHA256:260000                  |
|role                            |TEXT        |CHECK IN (‘admin’,‘operator’,‘viewer’)|
|active                          |BOOLEAN     |                                      |
|last_login, login_count, last_ip|audit fields|                                      |
|display_name, email, created_by |TEXT        |                                      |

#### `user_accounts`

Per-user bankroll. One row per user. FK `users.id` UNIQUE.

|Column                                   |Type         |
|-----------------------------------------|-------------|
|bankroll, total_pl, session_pl, peak_bank|NUMERIC(12,2)|
|total_bets, total_wins                   |INTEGER      |
|settings, alerts                         |JSONB        |
|admin_notes                              |TEXT         |
|last_session_reset                       |TIMESTAMPTZ  |

#### `user_permissions`

Per-user permission overrides (granted/revoked arrays on top of role defaults).

#### `user_sessions`

Active JWT tracking. `token_jti` maps 1:1 to JWT `jti` claim. Used for force-logout and session revocation checking.

|Column                                     |Type|
|-------------------------------------------|----|
|user_id, token_jti UNIQUE                  |    |
|ip_address, user_agent                     |TEXT|
|expires_at, revoked, revoked_at, revoked_by|    |

#### `user_activity`

Searchable per-user action history. `BIGSERIAL` PK for high-volume inserts.

#### `audit_log`

Immutable system-wide audit trail. `BIGSERIAL` PK. Severity CHECK IN (‘INFO’,‘WARN’,‘ERROR’,‘CRITICAL’).

### Section 4: Betting layer

#### `bet_log`

All bet records. FK to `sessions` and `users`.

|Column                                                 |Type         |Notes                                 |
|-------------------------------------------------------|-------------|--------------------------------------|
|race_uid, date, track, race_num, code                  |identity     |                                      |
|runner, box_num, bet_type                              |TEXT/INT     |                                      |
|odds, stake, ev                                        |NUMERIC      |                                      |
|ev_status, confidence, edge_type, edge_status, decision|TEXT         |                                      |
|race_shape, result                                     |TEXT         |result: ‘PENDING’/‘WIN’/‘LOSE’/‘PLACE’|
|pl                                                     |NUMERIC(10,2)|                                      |
|error_tag                                              |TEXT         |ETG tag                               |
|manual_tag_override                                    |BOOLEAN      |                                      |
|placed_by, signal                                      |TEXT         |                                      |
|exotic_type                                            |TEXT         |                                      |
|created_at, settled_at                                 |TIMESTAMPTZ  |                                      |

#### `signals`

Generated race signals. One row per `race_uid` (UNIQUE).

|Column                       |Type        |Notes                                                    |
|-----------------------------|------------|---------------------------------------------------------|
|race_uid                     |TEXT UNIQUE |                                                         |
|signal                       |TEXT        |CHECK IN (‘SNIPER’,‘VALUE’,‘GEM’,‘WATCH’,‘RISK’,‘NO_BET’)|
|confidence                   |NUMERIC(5,3)|0.0–1.0                                                  |
|ev                           |NUMERIC(6,3)|expected value                                           |
|alert_level                  |TEXT        |‘HOT’/‘HIGH’/‘MEDIUM’/‘LOW’/‘NONE’                       |
|hot_bet                      |BOOLEAN     |                                                         |
|risk_flags                   |JSONB       |array of flag strings                                    |
|top_runner, top_box, top_odds|            |                                                         |
|generated_at                 |TIMESTAMPTZ |                                                         |

#### `exotic_suggestions`

Exotics (quinella, trifecta, first-4) suggestions per race.

### Section 5: Intelligence / AI tables

#### `feature_snapshots`

Serialised feature arrays with full race lineage. Used by backtest (contamination-safe).

|Column                                          |Notes                      |
|------------------------------------------------|---------------------------|
|race_uid                                        |                           |
|features                                        |JSONB — full feature vector|
|has_sectionals, has_race_shape, has_collision   |INTEGER 0/1                |
|sectional_metrics, race_shape, collision_metrics|JSONB                      |

#### `prediction_snapshots`

One row per prediction run. Includes phase flags for sectionals/shape/collision/enrichment.

|Column                                                       |Notes                                                       |
|-------------------------------------------------------------|------------------------------------------------------------|
|prediction_snapshot_id                                       |TEXT UNIQUE — used as lineage key                           |
|race_uid, oddspro_race_id                                    |                                                            |
|model_version                                                |‘baseline_v1’ or ‘v2_feature_engine’ or ‘v2_with_enrichment’|
|feature_snapshot_id                                          |links to feature_snapshots                                  |
|runner_count                                                 |                                                            |
|has_sectionals, has_race_shape, has_collision, has_enrichment|INTEGER 0/1                                                 |
|source_type                                                  |‘pre_race’ or ‘result’                                      |
|race_date, track, race_num, code, top_runner                 |context columns for activity feed                           |

#### `prediction_runner_outputs`

Per-runner scores and predicted ranks within one prediction run.

|Column                        |Notes                       |
|------------------------------|----------------------------|
|prediction_snapshot_id        |FK (soft)                   |
|race_uid, runner_name, box_num|                            |
|predicted_rank                |1 = top pick                |
|score                         |NUMERIC(10,6) normalised 0–1|
|model_version                 |                            |

#### `learning_evaluations`

Post-result evaluation records. One row per `prediction_snapshot_id` (UNIQUE).

|Column                         |Notes                                        |
|-------------------------------|---------------------------------------------|
|race_uid, oddspro_race_id      |                                             |
|predicted_winner, actual_winner|                                             |
|winner_hit, top2_hit, top3_hit |BOOLEAN                                      |
|predicted_rank_of_winner       |                                             |
|winner_odds                    |                                             |
|used_enrichment                |BOOLEAN                                      |
|disagreement_score             |NUMERIC                                      |
|formfav_rank, your_rank        |INTEGER                                      |
|evaluation_source              |‘oddspro’ — FormFav never used for evaluation|

#### `backtest_runs`

High-level backtest run summaries. `run_id` UNIQUE.

|Column                                          |Notes       |
|------------------------------------------------|------------|
|date_from, date_to                              |range       |
|code_filter, track_filter, model_version        |            |
|total_races, total_runners                      |            |
|winner_hit_count, top2_hit_count, top3_hit_count|            |
|hit_rate, top2_rate, top3_rate                  |NUMERIC(8,4)|
|avg_winner_odds                                 |            |

#### `backtest_run_items`

Per-race results within a backtest run.

### Section 6: Feature engine / sectionals / race shape

#### `sectional_snapshots`

Per-runner OddsPro sectional metrics.

|Column                                          |Notes                 |
|------------------------------------------------|----------------------|
|race_uid, oddspro_race_id, box_num, runner_name |                      |
|early_speed_score, late_speed_score             |NUMERIC               |
|closing_delta, fatigue_index, acceleration_index|NUMERIC               |
|sectional_consistency_score                     |NUMERIC               |
|raw_early_time, raw_mid_time, raw_late_time     |NUMERIC               |
|raw_all_sections                                |JSONB                 |
|source                                          |‘oddspro_result’      |
|source_type                                     |‘pre_race’ or ‘result’|

#### `race_shape_snapshots`

One row per race (UNIQUE on `race_uid`). Race-level shape/tempo analysis.

|Column                                                           |Notes  |
|-----------------------------------------------------------------|-------|
|pace_scenario                                                    |TEXT   |
|early_speed_density, leader_pressure                             |NUMERIC|
|likely_leader_runner_ids                                         |JSONB  |
|early_speed_conflict_score, collapse_risk, closer_advantage_score|NUMERIC|
|is_greyhound                                                     |BOOLEAN|
|sectionals_used, formfav_enrichment_used                         |BOOLEAN|

### Section 7: Learning engine tables

#### `epr_data`

Edge Performance Registry — per-bet outcome tracking for AEEE.

|Column                                         |Notes        |
|-----------------------------------------------|-------------|
|edge_type, code, track, distance, condition    |context      |
|confidence_tier, ev_at_analysis                |pre-bet state|
|result, pl                                     |outcome      |
|execution_mode, meeting_state, session_id, date|             |

#### `aeee_adjustments`

Automatic Edge Expectation Engine adjustments. Applied multipliers are `direction='RAISE'` → threshold × (1+amount) or `direction='LOWER'` → threshold × (1-amount).

|Column                          |Notes                                           |
|--------------------------------|------------------------------------------------|
|edge_type, direction, amount    |                                                |
|reason, roi_trigger, bets_sample|                                                |
|applied, promoted               |BOOLEAN — promoted + applied = active multiplier|

#### `etg_tags`

Error Tagging Guide — per-bet error classification.

#### `pass_log`

Records why races were passed. `race_uid` UNIQUE.

#### `gpil_patterns`

Global Pattern Intelligence Layer detected patterns.

|Column                  |Notes                            |
|------------------------|---------------------------------|
|pattern_type, code      |                                 |
|bets_sample, roi, status|                                 |
|mif_modifier            |INTEGER — bet multiplier modifier|

### Section 8: Simulation

#### `simulation_log`

Persists every `/api/simulator/run` result.

|Column                                                                      |Notes        |
|----------------------------------------------------------------------------|-------------|
|race_uid, user_id                                                           |             |
|engine                                                                      |‘monte_carlo’|
|n_runs, race_code, track, distance_m, condition                             |             |
|decision, confidence_score, chaos_rating, pace_type, top_runner, top_win_pct|             |
|results_json                                                                |JSONB        |
|simulation_summary                                                          |TEXT         |

### Section 9: Logging and support

#### `source_log`

HTTP request log for all data source calls. Written by ingestion paths.

#### `activity_log`

General application activity log.

#### `chat_history`

AI assistant conversation history. `session_id` groups turns.

#### `changelog`

Internal system changelog.

### Section 10: Performance tracking

#### `performance_daily`

Rolled-up daily P&L and strike rate. `date` UNIQUE.

-----

## Scoring engine (scorer.py)

### E23 — Early speed + pressure score

Classifies runners as `FAST` / `MID` / `SLOW` based on `best_time` vs fastest in field (±0.15 s / ±0.30 s). Assigns `run_style` from box position if not set (`RAILER` box 1–2, `WIDE` box 7–8, `LEADER` if FAST, else `CHASER`). Returns `pressure_score` 0–10.

### E24 — First bend map

Per-track box profiles mapping `box_num → STRONG / NEUTRAL / WEAK / AVOID`. Covers Horsham, Bendigo, Ballarat, Sandown, The Meadows, Cannington, Mandurah, Angle Park + default. Assigns `collision_risk` HIGH/MODERATE/LOW.

### E25 — Race shape + tempo/collapse projection

`classify_pace()` → `SLOW / MODERATE / FAST / HOT` from fast-runner count.
`project_tempo_collapse()` → `collapse_risk LOW / MODERATE / HIGH`.
`build_race_shape()` → beneficiary `LEADER` or `CHASER`.

### EV and Kelly

Scoring settings loaded from `system_state` (cached 60 s). AEEE multipliers loaded from `aeee_adjustments` (cached 60 s). Promoted+applied adjustments modify confidence and EV thresholds.

-----

## Signal system (signals.py)

`generate_signal(scored, settings)` → one signal per race. Never assign two signals to the same race.

### Signal thresholds (defaults)

|Threshold        |Value|
|-----------------|-----|
|confidence_sniper|0.80 |
|confidence_value |0.65 |
|confidence_gem   |0.55 |
|ev_sniper        |0.15 |
|ev_value         |0.08 |
|ev_gem           |0.04 |
|risk_cap         |0.45 |

### Signal decision tree

```
risk_flags ≥ 3  OR  (risk_flags ≥ 2 AND NEGATIVE_EV)   → RISK
confidence < 0.40 AND ev < 0                              → NO_BET
confidence ≥ sniper AND ev ≥ sniper AND no flags AND CLEAR → SNIPER
confidence ≥ value AND ev ≥ value AND flags ≤ 1          → VALUE
confidence ≥ gem AND ev ≥ gem AND false_favourite         → GEM
confidence ≥ 0.50 AND ev ≥ 0                             → WATCH
risk_flags ≥ 2                                           → RISK
else                                                     → NO_BET
```

### Risk flags

`HIGH_CHAOS`, `COLLAPSE_RISK`, `FALSE_FAVOURITE`, `CLUSTER_FIELD`, `NEGATIVE_EV`, `CHF_FAIL`

### Alert levels

|Signal           |Level |
|-----------------|------|
|SNIPER           |HOT   |
|VALUE (ev ≥ 0.15)|HIGH  |
|VALUE, GEM       |MEDIUM|
|WATCH            |LOW   |
|RISK, NO_BET     |NONE  |

`hot_bet = true` when `signal == SNIPER` or `signal == VALUE AND ev >= 0.18`.

`save_signal(race_uid, signal_data)` upserts to `signals` table on conflict `race_uid`.

In TEST mode only: `demo_signal(race_num)` returns deterministic fake signal. Blocked in LIVE by `@no_fake_data` decorator.

-----

## AI predictor (ai/predictor.py)

### Models

**baseline_v1** (default):

- Score = `1 / odds` (implied probability) with small box-position tiebreak
- Deterministic, no randomness
- Fully contamination-free

**v2_feature_engine**:

- Weighted multi-signal score (weights below)
- Normalised to sum 1.0 across field
- Falls back to baseline if feature columns missing

**v2_with_enrichment** (v2 + FormFav):

- Same as v2 but enrichment signals applied (capped at 0.05 each)

### v2 weights

|Feature                    |Weight|Notes                              |
|---------------------------|------|-----------------------------------|
|implied_probability        |0.30  |1/odds                             |
|early_speed_score          |0.12  |OddsPro sectionals                 |
|late_speed_score           |0.12  |OddsPro sectionals                 |
|sectional_consistency_score|0.08  |OddsPro sectionals                 |
|race_shape_fit             |0.12  |derived from field shape           |
|enrichment_win_prob        |0.05  |FormFav — non-authoritative, capped|
|enrichment_class_rating    |0.05  |FormFav — non-authoritative, capped|
|collision_risk_score       |-0.10 |GREYHOUND only (subtracted)        |

### Multi-code rules

- `collision_risk_score` subtracted for GREYHOUND only
- Box bias applied for GREYHOUND only
- Leader pressure boosted for HARNESS
- Late speed boosted for GALLOPS
- No default fallback to GREYHOUND

### Entry points

```python
from ai.predictor import predict_race, predict_today, predict_from_snapshot

result = predict_race("2026-04-10_sandown_3_GREYHOUND")
# result = {ok, race_uid, prediction_snapshot_id, model_version,
#           runner_predictions: [{runner_name, box_num, predicted_rank, score}],
#           created_at, lineage_saved}

result = predict_today()
# runs predict_race() for all upcoming/open races today
```

-----

## Learning store (ai/learning_store.py)

### Rules

- Predictions never overwrite official race/result tables
- Evaluation always uses official confirmed results only (OddsPro-sourced)
- No provisional FormFav data may trigger final evaluation
- Clean lineage: prediction → features → race → official result

### Key functions

```python
from ai.learning_store import (
    save_prediction_snapshot,   # saves snapshot + feature lineage + runner outputs
    save_sectional_snapshot,    # saves per-runner sectional metrics
    save_race_shape_snapshot,   # saves race shape dict
    evaluate_prediction,        # post-result evaluation (OddsPro only)
    get_stored_prediction,      # retrieve latest prediction for a race
    backfill_evaluations,       # batch evaluation of all unreviewed predictions
)
```

`save_prediction_snapshot(prediction, features, sectional_metrics, race_shape, collision_metrics)` — writes to `feature_snapshots`, `prediction_snapshots`, `prediction_runner_outputs`.

`evaluate_prediction(race_uid)` — fetches result from `results_log`, evaluates against stored prediction, writes to `learning_evaluations`. Never called if result source is FormFav.

-----

## Backtest engine (ai/backtest_engine.py)

### Contamination rules (enforced, not just guidelines)

- No future leakage: feature inputs use only pre-result race/runner/odds data
- Official results (`results_log`) used ONLY for evaluation
- FormFav provisional data never used as evaluation source
- Historical `feature_snapshots` take priority over rebuilding from mutable tables

### Usage

```python
from ai.backtest_engine import backtest_date_range

result = backtest_date_range(
    date_from="2026-01-01",
    date_to="2026-03-31",
    code_filter="GREYHOUND",   # optional
    track_filter="sandown",    # optional substring match
    model_version="baseline_v1",
    compare_models=False,      # True = also runs v2 side-by-side
)
```

No-leakage guard: future dates rejected at the API layer (`/api/admin/backtest`, `/api/predictions/backtest`).

### Output

```python
{
  "ok": True,
  "run_id": "bt_2026-01-01_2026-03-31_abc123",
  "total_races": 412,
  "total_runners": 3240,
  "winner_hit_count": 87,
  "hit_rate": 0.211,
  "top2_rate": 0.398,
  "top3_rate": 0.571,
  "avg_winner_odds": 4.2,
  "model_version": "baseline_v1",
  "model_comparison": {...}  # only when compare_models=True
}
```

-----

## Simulation engine (simulation/)

Monte Carlo simulation for race outcome modelling.

### Entry point

```python
from simulation.core_simulation_engine import SimulationEngine
from simulation.models import RaceMeta, RunnerProfile, normalize_race_code

engine = SimulationEngine()
guide = engine.run(race_meta, runner_profiles)
# guide.decision, guide.confidence_rating, guide.chaos_rating,
# guide.top_runner, guide.simulation_summary, guide.filter_results_panel
```

### Flow

1. `RaceShapeEngine.analyse(runners)` — pre-race shape
1. Select module: `GreyhoundModule` / `ThoroughbredModule` / `HarnessModule`
1. Run N simulations (100–500, capped by `race_meta.n_sims`)
1. `SimulationAggregator` → `AggregatedResult`
1. `FilterEngine` → apply hard block, value, risk, decision filters
1. `ExpertGuideGenerator` → `ExpertGuide`

### RaceMeta fields

```python
@dataclass
class RaceMeta:
    race_uid: str
    track: str
    race_code: RaceCode        # RaceCode.GREYHOUND / .THOROUGHBRED / .HARNESS
    distance_m: int
    grade: str
    condition: str
    field_size: int
    n_sims: int = 200
```

### Filter categories

- `hard_block_filters`: field size, data quality, integrity failures
- `value_filters`: EV, odds range, market efficiency
- `risk_filters`: chaos, collapse risk, false favourite
- `core_decision_filters`: BET / PASS / SESSION decision logic
- `output_filters`: formatting, confidence tiers

-----

## Safety layer (safety.py)

### Betting window

`check_betting_window(jump_time_str, anchor_time_str)` → `"VALID"` / `"TOO_EARLY"` / `"TOO_LATE"` / `"UNKNOWN"`

- `TOO_LATE`: < 2 min to jump
- `TOO_EARLY`: > 90 min to jump

### Recommendation expiry

Recommendations cached in `_recommendations` dict per `race_uid`. Expires after 10 min or when window transitions to `TOO_LATE`.

### Circuit breaker

In-memory. Tracks consecutive failures per edge type. Trips at threshold, logs warning, blocks further bets until reset.

-----

## API routes

All API routes return `{"ok": true/false, ...}`. JSON only.

### Health (`/api/health`)

|Method|Path                  |Auth|Description                      |
|------|----------------------|----|---------------------------------|
|GET   |/api/health           |None|Basic liveness probe             |
|GET   |/api/health/          |None|Same                             |
|GET   |/api/health/connectors|None|Claude API + dogs pipeline health|
|GET   |/api/health/scheduler |None|Scheduler thread status          |
|GET   |/api/health/live      |None|Full live engine health metrics  |

### Races (`/api/races`)

|Method|Path                          |Auth|Description                       |
|------|------------------------------|----|----------------------------------|
|GET   |/api/races                    |None|List races for today (or `?date=`)|
|GET   |/api/races?status=active      |None|Active races only                 |
|GET   |/api/races/upcoming           |None|Upcoming races (board alias)      |
|GET   |/api/races/{race_uid}         |None|Single race + runners             |
|GET   |/api/races/{race_uid}/analysis|None|Race + runners for analysis       |
|GET   |/api/races/{race_uid}/result  |None|Settled result                    |

### Board (`/api/board`)

|Method|Path              |Auth|Description             |
|------|------------------|----|------------------------|
|GET   |/api/board        |None|Live racing board       |
|GET   |/api/board/blocked|None|Blocked races for today |
|GET   |/api/board/ntj    |None|Next-to-jump sorted list|

### Bets (`/api/bets`)

|Method|Path                |Auth|Description                                            |
|------|--------------------|----|-------------------------------------------------------|
|GET   |/api/bets/summary   |None|Win rate, P&L, ROI                                     |
|GET   |/api/bets/history   |None|Last 200 bets                                          |
|GET   |/api/bets/open      |None|Pending bets                                           |
|POST  |/api/bets/place     |None|Place a bet `{race_uid, runner, odds, stake, bet_type}`|
|POST  |/api/bets/settle    |None|Settle `{bet_id, result}` (WIN/LOSE/PLACE)             |
|POST  |/api/bets/reset-bank|None|Reset bankroll `{amount}`                              |

### Predictions (`/api/predictions`)

|Method|Path                              |Auth|Description                                                   |
|------|----------------------------------|----|--------------------------------------------------------------|
|POST  |/api/predictions/race/{race_uid}  |None|Trigger prediction for one race                               |
|POST  |/api/predictions/today            |None|Trigger predictions for today’s board                         |
|GET   |/api/predictions/today            |None|Stored predictions + outcomes for today                       |
|GET   |/api/predictions/race/{race_uid}  |None|Stored prediction for a race                                  |
|GET   |/api/predictions/performance      |None|Model performance summary                                     |
|POST  |/api/predictions/backtest         |None|Run backtest `{date_from, date_to, code_filter, track_filter}`|
|GET   |/api/predictions/backtest/{run_id}|None|Inspect stored backtest run                                   |

### Dogs dashboard (`/api/dogs`)

|Method|Path                             |Auth|Description                              |
|------|---------------------------------|----|-----------------------------------------|
|GET   |/api/dogs/board                  |None|Today’s greyhound board from stored races|
|GET   |/api/dogs/upcoming               |None|Next upcoming greyhound race             |
|GET   |/api/dogs/race/{race_uid}        |None|Race detail + runners                    |
|GET   |/api/dogs/health                 |None|Pipeline health / last collection state  |
|POST  |/api/dogs/collect                |None|Trigger manual board collection          |
|POST  |/api/dogs/race/{race_uid}/refresh|None|Refresh a single race                    |

### Admin (`/api/admin`) — `@require_role("admin")`

|Method  |Path                            |Description                                |
|--------|--------------------------------|-------------------------------------------|
|POST    |/api/admin/sweep                |Trigger full pipeline sweep                |
|POST    |/api/admin/refresh              |Alias for sweep                            |
|POST    |/api/admin/results              |Trigger race state check (result detection)|
|POST    |/api/admin/block                |Block race `{race_uid, block_code}`        |
|POST    |/api/admin/migrate              |Run DB schema migrations                   |
|GET     |/api/admin/scheduler            |Scheduler status                           |
|POST    |/api/admin/predict/race         |Trigger prediction `{race_uid}`            |
|POST    |/api/admin/predict/today        |Trigger predictions for all today’s races  |
|POST    |/api/admin/backtest             |Run backtest `{date_from, date_to, ...}`   |
|GET/POST|/api/admin/users                |List users                                 |
|POST    |/api/admin/users/create         |Create user                                |
|POST    |/api/admin/users/{id}/deactivate|Deactivate user                            |
|POST    |/api/admin/users/{id}/password  |Change password                            |
|GET     |/api/admin/system-state         |Read system_state                          |
|POST    |/api/admin/system-state         |Update system_state                        |
|GET     |/api/admin/audit                |Audit log                                  |
|GET     |/api/admin/signals              |Recent signals                             |
|GET     |/api/admin/learning             |Learning evaluation stats                  |
|GET     |/api/admin/health               |Detailed health metrics                    |

### Auth (`/api/auth`)

|Method|Path            |Description                                                      |
|------|----------------|-----------------------------------------------------------------|
|POST  |/api/auth/login |`{username, password}` → `{token, user}` + sets `dp_token` cookie|
|GET   |/api/auth/me    |Returns current user + permissions                               |
|POST  |/api/auth/logout|Clears `dp_token` cookie                                         |

### System (`/api/system`)

|Method  |Path                          |Description                              |
|--------|------------------------------|-----------------------------------------|
|GET     |/api/system/status            |Env mode, shadow_active, scheduler status|
|POST/GET|/api/scheduler/watchdog       |Restart dead scheduler thread            |
|POST    |/api/sweep                    |Trigger full pipeline sweep              |
|GET     |/api/env                      |Env info                                 |
|GET     |/api/health                   |App health                               |
|POST    |/api/ai/commentary            |Claude Haiku commentary `{prompt}`       |
|GET     |/api/ai/learning/status       |Learning engine status                   |
|GET     |/api/live/race/{race_uid}     |Live race data + signal + analysis       |
|POST    |/api/live/watch-sim/{race_uid}|Trigger simulation for live race         |
|POST    |/api/live/mark-watched        |Record race watched `{race_uid}`         |
|GET     |/api/home/board               |Home board (delegates to board_service)  |
|GET     |/api/debug/board-status       |Board build health diagnostics           |
|GET     |/api/debug/claude-pipeline    |Horse pipeline diagnostics               |
|GET     |/api/debug/formfav            |Pipeline stats (FormFav removed)         |
|GET     |/api/smoke-test               |Run smoke tests (DP_ENV=TEST only)       |

-----

## UI pages

All pages are rendered by Flask (`render_template`). SPA-style, JS fetches API endpoints.
Dark navy aesthetic matching Sportsbet/Ladbrokes UI. Not terminal red.

|Route       |Template        |Description                              |
|------------|----------------|-----------------------------------------|
|/           |redirect → /home|                                         |
|/home       |home.html       |Home board — next races, signals overview|
|/live       |live.html       |Live race view — NTJ, analysis, signal   |
|/simulator  |simulator.html  |Monte Carlo simulator UI                 |
|/betting    |betting.html    |Bet placement + open bets                |
|/reports    |reports.html    |P&L, session history, ROI                |
|/learning   |learning.html   |AI learning status, evaluation stats     |
|/backtesting|backtesting.html|Backtest runner + results                |
|/race       |race_view.html  |Single race deep-dive                    |
|/settings   |settings.html   |System state, thresholds, user prefs     |

-----

## Health monitoring (services/health_service.py)

Thread-safe in-memory `_state` dict. Updated by scheduler cycles and services. Exposed via `/api/health/live`.

Tracked metrics:

- `last_bootstrap_at/ok/error/count`
- `last_broad_refresh_at/ok/races/error`
- `last_near_jump_refresh_at/ok/races/error`
- `last_result_check_at/ok/error`
- `result_confirmation_count`
- `last_formfav_overlay_at/ok`
- `last_health_snapshot_at`
- `blocked_race_count`, `stale_race_count`, `board_count`, `stored_race_count_today`
- `last_prediction_run_at/count`
- `last_backtest_run_at/id`
- `last_evaluation_run_at/count`
- `active_model_version`
- `last_feature_build_at/count`
- `last_sectional_extraction_at/count`
- `last_race_shape_build_at/count`
- `enrichment_usage_count/total`
- `disagreement_flagged_count/total`
- `oddspro_public_mode`, `oddspro_api_key_present`

-----

## Invariants and coding rules

### Never do these

- Never use `os.getenv("DP_ENV")` directly — always use `from env import env`
- Never call fake/demo data functions in LIVE mode (guarded by `@no_fake_data`)
- Never write provisional/FormFav data to `results_log`
- Never use future dates in backtest (no-leakage rule enforced at API layer)
- Never downgrade a race from a `FINAL_STATE` (`final`, `paying`, `abandoned`)
- Never OCR inside DemonPulse — extraction is delegated to external service
- Never use Claude Vision inside DemonPulse for race screenshots
- Never file-poll for extraction results — all extraction is sync JSON response
- Never assign two signals to the same `race_uid`
- Never write OddsPro/provisional data with `source_confidence='official'` unless confirmed
- Never call `board_service` or `pipeline` functions outside the scheduler/API boundary without exception handling — they degrade safely and must never crash the process

### Always do these

- Use `T("table_name")` from `db.py` for all table references (respects env prefix)
- Use `safe_query(lambda: ..., fallback)` for all Supabase calls
- Log with module-specific prefix: `[DOGS_BOARD]`, `[PIPELINE]`, `[PREDICTOR]`, etc.
- Return `{"ok": False, "error": "..."}` from all service functions on failure
- Use `database.py` helpers for race/runner/result CRUD (not raw db.py)
- Add `updated_at` timestamp on all race/runner upserts
- Check `race.get("status")` before state transitions — terminal states are immutable

### Adding a feature

1. Data fetch → `collectors/` or `connectors/`
1. Parsing → `parsers/`
1. Storage → `database.py` (add helper if new table)
1. Service logic → `services/`
1. API route → `api/` blueprint
1. UI → `templates/` + `static/`
1. Schema change → `sql/001_canonical_schema.sql` (add `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)
1. Update `migrations.py` if backfill needed

### db.py T() table resolution

Tables in `_TESTABLE_TABLES` → `test_{table}` in TEST mode.
Tables in `_ALWAYS_LIVE_TABLES` (`users`, `audit_log`) → always production name.
All other tables → production name regardless of mode.

### Race UID format

`{YYYY-MM-DD}_{track_slug}_{race_num}_{CODE}`
Example: `2026-04-10_sandown-park_3_GREYHOUND`

Track slug is lowercase, spaces replaced with `-`.

-----

## Deployment (Render)

`render.yaml` and `Procfile` define the service.

```
# Procfile
web: gunicorn --config gunicorn.conf.py app:app
```

`gunicorn.conf.py`:

- Sets `GUNICORN_MANAGED=1` in `on_starting` (prevents scheduler double-start)
- Starts scheduler in `post_fork` per worker
- Playwright browser installs handled in build step

`runtime` file pins Python version.

`.github/copilot-setup-steps.yml` configures allowed network hostnames for CI:

- `thedogs.com.au`, `api.anthropic.com`, Supabase project host

-----

## Greyhound extraction service handoff contract

DemonPulse posts screenshots to an external service and receives structured JSON back synchronously. DemonPulse does **not** do OCR, does **not** call Claude Vision, does **not** poll files.

```
POST {EXTRACTION_SERVICE_URL}/extract/dogs/race
Content-Type: multipart/form-data

Fields:
  race_uid    TEXT
  date        TEXT  (YYYY-MM-DD)
  track       TEXT
  race_num    INTEGER
  [screenshots as file uploads — one or more PNG attachments]

Response 200 JSON:
{
  "success": true,
  "race_uid": "...",
  "extracted_data": {
    "race": {
      "track": "...", "race_num": N, "distance": "...", "grade": "...",
      "jump_time": "HH:MM", "condition": "...", "prize_money": "..."
    },
    "runners": [
      {
        "box_num": N, "name": "...", "trainer": "...",
        "run_style": "...", "best_time": "...", "career": "...",
        "price": N.NN, "scratched": false
      }
    ]
  },
  "warnings": [],
  "errors": []
}

On failure:
{
  "success": false,
  "race_uid": "...",
  "extracted_data": null,
  "warnings": [],
  "errors": ["reason..."]
}
```

DemonPulse validates `extracted_data` before writing to Supabase. Warnings are logged but do not block writes. Errors cause the race to be skipped for this cycle (retried next collection run).
