# DemonPulse V8 — Supabase Rebuild Notes

## Status: COMPLETE REBUILD

The previous Supabase integration was fragmented across multiple migration
files, had a direct `create_client()` in `audit.py` bypassing all env-mode
controls, had no repository pattern, and had schema drift between the
Python code and the actual database.

This document describes the new canonical architecture.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     APPLICATION LAYER                            │
│  app.py · api/* · scheduler.py · data_engine.py · ai/*          │
└────────────────────────────┬─────────────────────────────────────┘
                             │ uses
┌────────────────────────────▼─────────────────────────────────────┐
│              REPOSITORY LAYER  (repositories/)                   │
│  races_repo · runners_repo · results_repo · predictions_repo     │
│  learning_repo · backtesting_repo · users_repo · logs_repo       │
│  meetings_repo                                                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │ calls
┌────────────────────────────▼─────────────────────────────────────┐
│              CANONICAL CLIENT  (supabase_client.py)              │
│  get_client() · resolve_table() · safe_execute() · health_check()│
└────────────────────────────┬─────────────────────────────────────┘
                             │ delegates to
┌────────────────────────────▼─────────────────────────────────────┐
│              ENVIRONMENT AUTHORITY  (env.py)                     │
│  TEST / LIVE mode · table prefix · client caching               │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│                        SUPABASE                                  │
│  SUPABASE_URL · SUPABASE_KEY  (env vars)                         │
└──────────────────────────────────────────────────────────────────┘
```

---

## Files Created / Changed

### New Files

| File | Purpose |
|---|---|
| `supabase_config.py` | Canonical config constants, table names, upsert keys, valid race codes |
| `supabase_client.py` | Single entry point for client + safe_execute + resolve_table |
| `repositories/__init__.py` | Package init |
| `repositories/races_repo.py` | today_races CRUD |
| `repositories/runners_repo.py` | today_runners CRUD |
| `repositories/results_repo.py` | results_log CRUD |
| `repositories/meetings_repo.py` | meetings CRUD (new table) |
| `repositories/predictions_repo.py` | feature_snapshots, prediction_*, sectionals, race_shape |
| `repositories/learning_repo.py` | learning_evaluations CRUD |
| `repositories/backtesting_repo.py` | backtest_runs, backtest_run_items CRUD |
| `repositories/users_repo.py` | users, user_accounts, user_sessions, user_activity |
| `repositories/logs_repo.py` | audit_log, source_log, activity_log, simulation_log |
| `services/schema_bootstrap.py` | Startup schema verification |
| `services/migration_runner.py` | SQL migration runner |
| `services/data_integrity_service.py` | Integrity checks and validation |
| `sql/001_canonical_schema.sql` | Single canonical SQL schema (idempotent) |
| `sql/002_indexes_constraints.sql` | Supplementary indexes |
| `sql/003_views_optional.sql` | Dashboard/reporting views |

### Modified Files

| File | Change |
|---|---|
| `audit.py` | **Fixed**: removed direct `create_client()`, now routes through `repositories/logs_repo.LogsRepo.audit()` |
| `supabase/migrations/*.sql` | **Deprecated**: added header notice, do not run |

### Existing Files (unchanged — still valid)

| File | Notes |
|---|---|
| `env.py` | Still the mode authority; `supabase_client.py` delegates to it |
| `db.py` | Low-level helpers still used by services; routes through `env.py` |
| `database.py` | Shim layer; still valid for legacy callers |
| `migrations.py` | Column-level migration runner; still usable alongside `migration_runner.py` |

---

## Environment Variables

All required environment variables (none hardcoded):

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | ✅ (LIVE) | Production Supabase project URL |
| `SUPABASE_KEY` | ✅ (LIVE) | Service-role or anon key |
| `SUPABASE_TEST_URL` | ⚠️ (TEST) | Dedicated test instance URL |
| `SUPABASE_TEST_KEY` | ⚠️ (TEST) | Dedicated test instance key |
| `DP_ENV` | ✅ | `LIVE` or `TEST` (default: `LIVE`) |
| `JWT_SECRET` | ✅ | Secret for JWT auth tokens |
| `SESSION_TIMEOUT_MIN` | Optional | Token TTL in minutes (default: 480) |

---

## Schema

The single canonical schema is in `sql/001_canonical_schema.sql`.

### Tables

| Table | Purpose | Conflict Key |
|---|---|---|
| `meetings` | Meeting-level identity (date/track/code) | `(date, track, code)` |
| `today_races` | Race data (OddsPro authoritative) | `(date, track, race_num, code)` |
| `today_runners` | Per-runner data | `(race_uid, box_num)` |
| `results_log` | Official results summary | `(date, track, race_num, code)` |
| `race_status` | Race status tracking | `(date, track, race_num, code)` |
| `users` | User accounts | `username` |
| `user_accounts` | User betting accounts | `user_id` |
| `user_permissions` | Per-user page permissions | `(user_id, page)` |
| `user_sessions` | Auth sessions | — |
| `user_activity` | User activity log | — |
| `audit_log` | Always-live audit trail | — |
| `bet_log` | Bet records | — |
| `signals` | AI signals per race | `race_uid` |
| `sessions` | Betting sessions | — |
| `system_state` | Global app state (id=1) | `id` |
| `feature_snapshots` | AI feature arrays | — |
| `prediction_snapshots` | Prediction run metadata | `prediction_snapshot_id` |
| `prediction_runner_outputs` | Per-runner scores | — |
| `learning_evaluations` | Post-result AI evaluations | — |
| `sectional_snapshots` | Per-runner sectionals | — |
| `race_shape_snapshots` | Race-level shape analysis | `race_uid` |
| `backtest_runs` | Backtest run summaries | `run_id` |
| `backtest_run_items` | Per-race backtest items | — |
| `simulation_log` | Simulation run results | — |
| `source_log` | External API call log | — |
| `activity_log` | App activity log | — |
| `etg_tags` | Error tagging | — |
| `epr_data` | Edge performance registry | — |
| `aeee_adjustments` | Auto edge adjustments | — |
| `pass_log` | Race pass reasons | `race_uid` |

---

## Race Code Rules

All data is scoped to one of: `GREYHOUND`, `HARNESS`, `GALLOPS`.

**Contamination prevention:**
- `VALID_RACE_CODES` in `supabase_config.py` is the single source of truth
- All repositories validate code before writes
- All queries accept optional code filter to scope results
- `meetings` table has a `CHECK` constraint on `code`
- `DataIntegrityService._check_race_code_validity()` runs at startup

---

## Upsert Strategy (Idempotent Writes)

All upserts use stable natural keys:

| Entity | Conflict Key | Notes |
|---|---|---|
| Meeting | `(date, track, code)` | Stable across repeated pulls |
| Race | `(date, track, race_num, code)` | OddsPro is authoritative |
| Runner | `(race_uid, box_num)` | Box number is stable identity |
| Result | `(date, track, race_num, code)` | One result per race |
| Prediction | `prediction_snapshot_id` | UUID generated at prediction time |
| Race shape | `race_uid` | One shape analysis per race |
| Pass log | `race_uid` | One pass record per race |
| Signal | `race_uid` | One signal per race |

---

## Migration Instructions

### Fresh Installation

1. Go to Supabase SQL Editor
2. Paste contents of `sql/001_canonical_schema.sql`
3. Run (idempotent — safe on empty DB)
4. Optionally run `sql/002_indexes_constraints.sql` for performance indexes
5. Optionally run `sql/003_views_optional.sql` for reporting views
6. Set environment variables (see above)
7. Deploy — `SchemaBootstrap.run()` verifies schema at startup

### Existing Database Upgrade

The canonical SQL is fully idempotent:
- `CREATE TABLE IF NOT EXISTS` — never destroys existing data
- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — never drops columns
- `CREATE INDEX IF NOT EXISTS` — no-op if already exists
- `DO $$ ... EXCEPTION WHEN duplicate_object THEN NULL` — safe constraint adds

Simply paste `sql/001_canonical_schema.sql` into the Supabase SQL Editor and run.

### DO NOT run files in `supabase/migrations/`

Those files are V7-era fragments with overlapping, conflicting DDL.
They are kept for reference only.

---

## Startup Sequence

```python
# app.py startup
from services.schema_bootstrap import SchemaBootstrap
SchemaBootstrap.run()  # verifies required tables exist

from services.data_integrity_service import DataIntegrityService
DataIntegrityService.run_checks()  # validates data linkage
```

---

## Legacy Cleanup

The following were identified as bad patterns and have been replaced:

| Old Pattern | Problem | Replacement |
|---|---|---|
| `audit.py: create_client()` | Bypassed env.py, hardcoded Supabase creds | `repositories/logs_repo.LogsRepo.audit()` |
| `supabase/migrations/*.sql` | V7-era fragments, conflicting DDL | `sql/001_canonical_schema.sql` |
| Scattered `get_db().table(...)` | No repository pattern, no validation | `repositories/*_repo.py` |
| No integrity checks at startup | Silent data corruption possible | `services/data_integrity_service.py` |
| Multiple competing schema definitions | Schema drift | Single `sql/001_canonical_schema.sql` |

---

## Render Deployment Checklist

- [ ] `SUPABASE_URL` set in Render environment
- [ ] `SUPABASE_KEY` set in Render environment (service role key)
- [ ] `JWT_SECRET` set in Render environment
- [ ] `DP_ENV=LIVE` set in Render environment
- [ ] SQL schema applied in Supabase SQL Editor before first deploy
- [ ] Health endpoint `/api/health` returns 200 after deploy
