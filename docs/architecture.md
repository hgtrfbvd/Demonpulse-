# DemonPulse — Architecture Reference

> Consolidated from `Latestinstructions.md` (V9) and `uiupdate2.md`.
> This is the single reference document for system architecture and UI conventions.

---

## Project Overview

DemonPulse is an Australian greyhound (Phase 1) and horse racing (Phase 2) AI betting terminal.

**Stack:** Flask · Supabase (Postgres) · Playwright · Render

**Phase status:**
- **Greyhounds (GREYHOUND code):** Fully active — browser-based pipeline via thedogs.com.au
- **Horses (GALLOPS/HARNESS):** Phase 2 — currently superseded; `connectors/claude_scraper.py` archived to `legacy/`

---

## Environment Variables

| Variable                 | Purpose                                                          |
|--------------------------|------------------------------------------------------------------|
| `DP_ENV`                 | `LIVE` (default/production) or `TEST` (dev/stress-test)          |
| `SUPABASE_URL`           | Supabase project URL (production)                                |
| `SUPABASE_KEY`           | Supabase anon key (production)                                   |
| `SUPABASE_TEST_URL`      | Supabase URL for TEST mode (optional; falls back to table prefix) |
| `SUPABASE_TEST_KEY`      | Supabase key for TEST mode                                       |
| `JWT_SECRET`             | HMAC-SHA256 signing secret for JWT tokens                        |
| `FLASK_SECRET`           | Flask session secret                                             |
| `ADMIN_PASSWORD`         | Override bootstrap admin password                                |
| `ADMIN_USERNAME`         | Override bootstrap admin username                                |
| `SESSION_TIMEOUT_MIN`    | JWT TTL in minutes (default 480)                                 |
| `DOGS_DATA_ROOT`         | Data output root for screenshots/packets (default `data/dogs`)   |
| `DOGS_BOARD_MAX_RETRIES` | Playwright retry count (default 3)                               |
| `DOGS_PAGE_TIMEOUT_MS`   | Playwright page load timeout ms (default 30000)                  |
| `EXTRACTION_SERVICE_URL` | External screenshot-to-JSON extraction endpoint                  |
| `RESULTS_BUFFER_SECS`    | Buffer after race_time before results capture (default 300)      |

### Environment Mode Rules

`env.py` is the single source of truth. Never check `DP_ENV` directly.

```python
from env import env
env.mode          # "TEST" or "LIVE"
env.is_live       # bool
env.is_test       # bool
env.require_test()        # raises EnvViolation if LIVE
env.guard_fake_data()     # raises EnvViolation if LIVE
env.guard_destructive(op) # raises EnvViolation if LIVE
```

**TEST mode:** fake/demo data allowed, demo user bootstrap, table names prefixed `test_`.  
**LIVE mode:** no fake data, no auto-deletion, all data permanent and audited.

---

## Module System Architecture (V10)

As of V10, DemonPulse uses a manifest-based module loader for the greyhound pipeline:

```
modules/
  __init__.py              Module loader framework (ModuleLoader, get_loader())
  base_module.py           Abstract BaseModule class
  module_registry.json     Per-module enable/disable overrides
  dogs_capture/            Playwright capture + extraction pipeline
    __init__.py
    module.json
    capture_pipeline.py    DogsCaptureModule
  dogs_analysis/           V7 greyhound analysis engine
    __init__.py
    module.json
    v7_engine.py           DogsAnalysisModule
  simulation/              Monte Carlo simulation (1000 cycles)
    __init__.py
    module.json
    monte_carlo.py         SimulationModule
  results/                 Post-race results capture
    __init__.py
    module.json
    results_capturer.py    ResultsModule
  learning/                Prediction vs result comparison
    __init__.py
    module.json
    learning_engine.py     LearningModule
  dashboard_ui/            Pro 3-panel greyhound dashboard
    __init__.py
    module.json
    routes.py              dashboard_ui_bp Flask blueprint
    templates/             Module-local templates
```

### Module Lifecycle

Each module implements `BaseModule.process(packet: dict) -> dict` and returns updated fields to merge into the `DogsRacePacket`.

```
dogs_capture  →  dogs_analysis  →  simulation  →  [race runs]  →  results  →  learning
CAPTURED         EXTRACTED           ANALYSED                       SETTLED
```

---

## DogsRacePacket Schema (V10)

The canonical packet lives in `models/dogs_race_packet.py`. The `PacketStatus` enum defines the lifecycle:

```
CAPTURED → EXTRACTED → ANALYSED → SETTLED
```

Key top-level fields added in V10:

| Field              | Type              | Set by           |
|--------------------|-------------------|------------------|
| `status`           | `str`             | Each module      |
| `screenshots`      | `dict[str, str]`  | dogs_capture     |
| `extracted_data`   | `dict`            | dogs_capture     |
| `engine_output`    | `dict`            | dogs_analysis    |
| `simulation_output`| `dict`            | simulation       |
| `result`           | `dict`            | results          |
| `learning`         | `dict`            | learning         |

---

## V7 Analysis Engine

Scoring weights:

| Factor         | Weight | Notes                              |
|----------------|--------|------------------------------------|
| Early speed    | 40%    | Highest weight                     |
| Box draw       | 20%    | Box 1 = 1.00 advantage             |
| Track/distance | 15%    | % record at track/distance         |
| Collision risk | 15%    | Penalty                            |
| Form           | 10%    | Confirmation only, not leading     |

**Tempo classification:** FAST | MODERATE | SLOW  
**PASS filter:** skip if confidence < 0.55 or top-3 spread < 0.05

---

## Simulation Engine

1000-cycle Monte Carlo. Models per cycle:
- Early speed variance (Gaussian noise)
- Collision events (6% per runner per race)
- Track bias (box 1 favoured on standard ovals)
- Finish strength variance

**Output:** `win_probabilities`, `top3_probabilities`, `most_likely_scenario`, `chaos_rating`, `lead_at_first_bend_pct`

---

## Data Pipeline

### Greyhound Pipeline (Phase 1 — ACTIVE)

```
packet_builder.build_packet_for_race(race_uid)
  └─ modules/dogs_capture/capture_pipeline.py: DogsCaptureModule
       └─ Playwright → thedogs.com.au
          → board, header, expert_form, box_history, results screenshots
          → POST to EXTRACTION_SERVICE_URL/extract/dogs/race
          → extracted_data: {runners[], form_lines[], times, splits, ...}
  └─ modules/dogs_analysis/v7_engine.py: DogsAnalysisModule
       └─ Scores runners with V7 weights
       → engine_output: {tempo, primary, secondary, confidence, pass_filter, ...}
  └─ modules/simulation/monte_carlo.py: SimulationModule
       → simulation_output: {win_probabilities, chaos_rating, ...}
  [race runs]
  └─ modules/results/results_capturer.py: ResultsModule
       → result: {finishing_order, margins, official_time}
  └─ modules/learning/learning_engine.py: LearningModule
       → learning: {error_tags, adjustments, notes}
```

### Extraction Service Contract

```
POST /extract/dogs/race
Content-Type: multipart/form-data
  race_uid: str
  date: YYYY-MM-DD
  <name>: PNG file (board, header, expert_form, box_history, results)

Response (sync JSON):
  {
    "success": true,
    "race_uid": "...",
    "runners": [...],
    "form_lines": [...],
    "times": {},
    "splits": {},
    "box_history_metrics": {},
    "derived_features": {}
  }
```

---

## Dashboard Routes

| Method | Path                              | Description                          |
|--------|-----------------------------------|--------------------------------------|
| GET    | `/dogs/pro`                       | Pro 3-panel dashboard UI             |
| GET    | `/api/packet/<race_uid>`          | Full race packet JSON                |
| GET    | `/api/packet/list`                | Today's packets list                 |
| POST   | `/api/packet/<race_uid>/run`      | Trigger pipeline for a packet        |

---

## Legacy Architecture (pre-V10)

The original `scheduler._run_dogs_visual()` → `services/dogs_board_service.py` →
`collectors/dogs_race_capturer.py` flow remains active for backward compatibility.
The new module system runs alongside it via `packet_builder.build_packet_for_race()`.

---

## Race State Machine

```
upcoming → near_jump → jumped_estimated → awaiting_result → result_posted → final
                                                                          → paying
                                                                          → abandoned
Any state → blocked | stale_unknown
```

NTJ windows: `IMMINENT` (0–120 s), `NEAR` (120–600 s), `UPCOMING` (600+ s), `PAST`

---

## Authentication

JWT (HMAC-SHA256), PBKDF2-SHA256 passwords. Token as `dp_token` httponly cookie.

| Role       | Permissions                                                      |
|------------|------------------------------------------------------------------|
| `admin`    | home, live, betting, reports, simulator, ai_learning, settings, audit, users, backtest, data, quality, performance |
| `operator` | home, live, betting, reports                                     |
| `viewer`   | home, reports                                                    |

---

## Scheduler Cycles

| Cycle               | Interval     | Function                                          |
|---------------------|--------------|---------------------------------------------------|
| `full_sweep`        | 600 s        | Horse races via Claude API (Phase 2)              |
| `board_rebuild`     | 90 s         | `board_service.get_board_for_today()` rebuild     |
| `result_check`      | 180 s        | `race_status.bulk_update_race_states()`           |
| `race_state_update` | 90 s         | State machine transitions                         |
| `health_snapshot`   | 120 s        | `services/health_service.py` aggregation          |
| `eval_backfill`     | 3600 s       | Learning evaluation backfill                      |
| `dogs_visual`       | 600 s        | Playwright greyhound board collection             |

---

## UI Architecture (V10)

### Pro 3-Panel Dashboard (`/dogs/pro`)

Three-column Bootstrap 5 layout:

- **Left panel:** meetings list, races list, next-up priority queue
- **Center panel:** race workstation — runners table, odds, expert form, engine output
- **Right panel:** analyst rail — tempo badge, sim win probabilities, confidence meter, bet panel
- **Bottom tabs:** Screenshots | Raw Data | Learning History | Results | Logs

All data loaded via JSON API (`/api/packet/list`, `/api/packet/<uid>`). No server-side rendering of race data.

### Key UI Conventions (from uiupdate2.md)

- `formatTrack(slug)`: replace hyphens with spaces, title-case (e.g. `"angle-park"` → `"Angle Park"`)
- `formatJumpTime(item)`: use `item.jump_dt_iso` → parse as Date → format to AEST using `toLocaleTimeString("en-AU", {timeZone:"Australia/Sydney"})`
- Board items grouped by `{track}_{code}`, sorted by soonest race's `seconds_to_jump`
- Race countdown updated every second via `setInterval`
- Filter tabs filter `item.code` against `"GREYHOUND"`, `"HORSE"`, `"HARNESS"`

---

## `race_uid` Format

```
{date}_{track}_{race_num}_{code}
e.g. 2026-04-10_sandown_3_GREYHOUND
```

Upsert conflict keys:
- Races: `(date, track, race_num, code)`
- Runners: `(race_uid, box_num)`

---

## Key Invariants

- `env.py` is the single source of truth for environment mode — never `os.getenv("DP_ENV")` directly
- No OCR inside DemonPulse — all extraction via external service or stub
- No Claude Vision inside DemonPulse (horse Phase 2 is archived)
- All module `process()` methods must not raise — return `{}` on failure
- Learning adjustments are **suggestions only** — never auto-applied to engine weights
- Schema authority: `sql/001_canonical_schema.sql` — run in Supabase SQL Editor
