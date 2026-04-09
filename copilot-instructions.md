# DemonPulse — Copilot Instructions

## Project overview

DemonPulse is a greyhound racing AI betting terminal. Flask backend, React SPA frontend,
Supabase (Postgres) database, deployed on Render.

Race data comes exclusively from **thedogs.com.au** — scraped directly using plain HTTP
requests. No third-party racing APIs (OddsPro, FormFav) are used or required.

-----

## Data pipeline — the only approach that works

### Why not Playwright / browser automation

thedogs.com.au detects Playwright’s fingerprint and returns 404. Do NOT use Playwright,
Selenium, Puppeteer, or any browser automation tool to fetch this site.

### How to fetch thedogs.com.au

Use `requests` with real browser headers. The site is server-rendered — no JS execution
needed. The correct headers are defined in `connectors/thedogs_scraper.py` (`HEADERS` dict).

```python
from connectors.thedogs_scraper import TheDogsScaper
scraper = TheDogsScaper()
```

### Three page types that work reliably

|Page       |URL pattern                            |What it provides             |
|-----------|---------------------------------------|-----------------------------|
|Overview   |`/racing/YYYY-MM-DD`                   |Venue list + race times grid |
|Fields     |`/racing/SLUG/YYYY-MM-DD?trial=false`  |All runners, Last 4, Best T/D|
|Expert form|`/racing/SLUG/YYYY-MM-DD/N/expert-form`|Career stats, splits, win%   |

### Claude Vision fallback

When HTML parsing fails, send the page text to the Claude API for structured extraction.
The `TheDogsScaper._vision_extract_expert_form()` method handles this. Set `ANTHROPIC_API_KEY`
in environment. Never screenshot — text content is sufficient for Vision extraction.

### Pipeline entry points

```python
# Full day scrape — outputs board_DATE.json + pipeline_DATE.json to data/thedogs/
from thedogs_pipeline import run_pipeline
result = run_pipeline("2026-04-10")

# Board for Flask routes
from thedogs_board_service import get_board_for_today
board = get_board_for_today()   # returns {"ok": True, "items": [...], "count": N}

# Single race card
from thedogs_board_service import get_race_card
card = get_race_card("goulburn", "2026-04-10", 3)
```

-----

## Architecture

```
app.py                          Flask app + blueprint registration
api/
  board_routes.py               GET /api/board  →  thedogs_board_service
  race_routes.py                GET /api/races/upcoming, /api/races/<uid>/analysis
  bet_routes.py                 POST /api/bet/log, /api/bet/settle
  admin_routes.py               Admin-only endpoints
  prediction_routes.py          AI prediction endpoints
  market_routes.py              Market snapshot endpoints
connectors/
  thedogs_scraper.py            ← PRIMARY DATA SOURCE. requests-based. No Playwright.
  browser_client.py             ← LEGACY. Not used for thedogs. Keep for reference only.
  thedogs_connector.py          ← LEGACY. Replaced by thedogs_scraper.py.
thedogs_pipeline.py             Daily scrape orchestrator (schedule + fields + expert forms)
thedogs_board_service.py        Board service with TTL cache. Drop-in for board_builder.
board_builder.py                Legacy board builder (OddsPro-based). Kept for reference.
data_engine.py                  Legacy data engine (OddsPro + FormFav). Not active.
ai/
  predictor.py                  Race prediction model
  feature_builder.py            Feature engineering from runner data
  learning_store.py             Prediction evaluation + learning
  backtest_engine.py            Historical backtesting
  race_shape.py                 Race shape / pace scenario analysis
  collision_model.py            Box collision / interference model
core/
  domestic_tracks.py            Track name normalisation + AU/NZ whitelists
database.py                     Supabase upsert helpers
auth.py                         JWT auth, PBKDF2, role enforcement
scheduler.py                    APScheduler background tasks
cache.py                        In-memory TTL cache
```

-----

## Board data format

Every board item (from `thedogs_board_service.get_board_for_today()`):

```json
{
  "venue": "Goulburn",
  "state": "NSW",
  "slug": "goulburn",
  "date": "2026-04-10",
  "race_num": 3,
  "race_name": "Red TV",
  "grade": "3rd/4th Grade",
  "distance": "440m",
  "prize_money": "$3,455",
  "time": "12:47",
  "ntj_label": "NEAR",
  "seconds_to_jump": 480,
  "runner_count": 8,
  "runners": [
    {
      "box": 1,
      "name": "Fearless Bandit",
      "colour": "Black D",
      "trainer": "Jodie Lord",
      "last4": "1215",
      "best_time": "24.70",
      "scratched": false
    }
  ]
}
```

`ntj_label` values: `IMMINENT` (<2 min), `NEAR` (<15 min), `UPCOMING`, `PAST`, `NO_TIME`

-----

## Coding rules

### Never do these

- Never use Playwright, Selenium, or any headless browser to fetch thedogs.com.au
- Never import from `connectors.oddspro_connector` or `connectors.formfav_connector`
  for live board data — those APIs are not active
- Never call `data_engine.full_sweep()` or `data_engine.rolling_refresh()` for
  thedogs data — use `thedogs_pipeline.run_pipeline()` instead
- Never store raw HTML in Supabase
- Never skip the TTL cache in `thedogs_board_service` — board builds are expensive

### Always do these

- Use `TheDogsScaper` from `connectors/thedogs_scraper.py` for all thedogs fetches
- Add a `time.sleep(0.5)` to `0.8` between sequential requests to the same domain
- Log with `[THEDOGS]`, `[PIPELINE]`, `[VISION]`, `[BOARD]` prefixes
- Return `{"ok": False, "error": "..."}` on failure from service functions
- Use `core.domestic_tracks.normalize_track()` when comparing track names

### Adding a new feature

1. Data fetch → `connectors/thedogs_scraper.py`
1. Pipeline step → `thedogs_pipeline.py` (add to `run_pipeline`)
1. Service layer → `thedogs_board_service.py`
1. API route → `api/` blueprint
1. Update `CHANGES.md`

-----

## Scheduler tasks (scheduler.py)

|Task           |Frequency       |Function                                  |
|---------------|----------------|------------------------------------------|
|Board refresh  |every 90s       |`thedogs_board_service.refresh_schedule()`|
|Full day scrape|every 5 min     |`thedogs_pipeline.run_pipeline()`         |
|Expert forms   |every 10 min    |`run_pipeline(fetch_expert_forms=True)`   |
|AI predictions |on new race data|`ai.predictor`                            |

-----

## Signal system

Each race gets exactly one signal. Never assign two signals to the same race.

|Signal  |Trigger condition                                      |
|--------|-------------------------------------------------------|
|`SNIPER`|High confidence + low risk + positive EV + market wrong|
|`VALUE` |Positive EV edge identified                            |
|`GEM`   |Overlooked runner, market overrating favourite         |
|`WATCH` |Potential but not confirmed                            |
|`RISK`  |Warning flags present                                  |
|`NO_BET`|Default — pass this race                               |

-----

## Environment variables

|Variable           |Required|Purpose                                        |
|-------------------|--------|-----------------------------------------------|
|`ANTHROPIC_API_KEY`|Yes     |Claude API for Vision fallback + AI predictions|
|`SUPABASE_URL`     |Yes     |Database                                       |
|`SUPABASE_KEY`     |Yes     |Database anon key                              |
|`JWT_SECRET`       |Yes     |Auth token signing                             |
|`FLASK_SECRET`     |Yes     |Flask session                                  |
|`ADMIN_PASSWORD`   |No      |Override default admin password                |

-----

## Testing a scrape locally

```bash
# Quick smoke test — 2 venues, no expert forms, no disk write
python thedogs_pipeline.py --date $(date +%Y-%m-%d) --max-venues 2 --no-expert-forms --dry-run

# Full scrape for tomorrow
python thedogs_pipeline.py --date 2026-04-11

# Check what was saved
cat data/thedogs/board_2026-04-11.json | python -m json.tool | head -80
```

-----

## Supabase tables (active)

- `today_races` — race records (track, date, grade, distance, jump_time, status)
- `runners` — runner records linked to races
- `bet_log` — paper bets with P&L
- `results_log` — confirmed race results
- `users` — auth users with roles
- `audit_log` — all sensitive events
- `signals` — AI-generated signals per race
- `formfav_race_enrichment` — FormFav enrichment (legacy, keep schema)
- `formfav_runner_enrichment` — FormFav runner enrichment (legacy)

-----

## GitHub Copilot network access

thedogs.com.au must be accessible during implementation and testing.
Update `.github/copilot-setup-steps.yml`:

```yaml
allowed_network_hostnames:
  - www.thedogs.com.au
  - thedogs.com.au
  - api.anthropic.com
  - nqfxxacxysegwhbjavhm.supabase.co
```
