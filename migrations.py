"""
migrations.py - DemonPulse Database Migrations
================================================
Ensures the Supabase schema has all columns and tables required by the
V8 architecture and Phase 3 intelligence layer.

Run once after deploying new code that adds columns or tables.

Existing column migrations (today_races / today_runners):
  today_races:
    - race_uid          TEXT  (composite key for display/lookup)
    - oddspro_race_id   TEXT  (OddsPro native race ID for re-fetching)
    - block_code        TEXT  (explicit BLOCK code if race is blocked)
    - source            TEXT  (data source: 'oddspro', 'formfav', etc.)
    - time_status       TEXT  (PARTIAL | VERIFIED)
    - condition         TEXT  (track condition)
    - race_name         TEXT  (race name / title)
    - updated_at        TIMESTAMPTZ

  today_runners:
    - oddspro_race_id   TEXT
    - number            INTEGER
    - barrier           INTEGER
    - jockey            TEXT
    - driver            TEXT
    - price             NUMERIC
    - rating            NUMERIC
    - run_style         TEXT  (already in schema, keep for safety)
    - scratch_reason    TEXT  (added here; was noted in comment but missing from list)
    - source_confidence TEXT

  results_log:
    (no new columns — existing schema is sufficient)

Phase 3 — Intelligence Layer new tables:
  feature_snapshots       — serialized feature arrays per race with lineage
  prediction_snapshots    — prediction run metadata
  prediction_runner_outputs — per-runner scores and predicted ranks
  learning_evaluations    — post-result evaluation records
  backtest_runs           — backtest run summaries
  backtest_run_items      — per-race results within a backtest run
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Each migration is (table, column, sql_type, default_clause)
# Only ALTER TABLE ADD COLUMN IF NOT EXISTS is used — safe to re-run.
_MIGRATIONS: list[tuple[str, str, str, str]] = [
    # today_races additions
    ("today_races", "race_uid",        "TEXT",         "DEFAULT ''"),
    ("today_races", "oddspro_race_id", "TEXT",         "DEFAULT ''"),
    ("today_races", "block_code",      "TEXT",         "DEFAULT ''"),
    ("today_races", "source",          "TEXT",         "DEFAULT 'oddspro'"),
    ("today_races", "time_status",     "TEXT",         "DEFAULT 'PARTIAL'"),
    ("today_races", "condition",       "TEXT",         "DEFAULT ''"),
    ("today_races", "race_name",       "TEXT",         "DEFAULT ''"),
    ("today_races", "updated_at",      "TIMESTAMPTZ",  "DEFAULT now()"),

    # today_runners additions
    ("today_runners", "oddspro_race_id",   "TEXT",     "DEFAULT ''"),
    ("today_runners", "number",            "INTEGER",  ""),
    ("today_runners", "barrier",           "INTEGER",  ""),
    ("today_runners", "jockey",            "TEXT",     "DEFAULT ''"),
    ("today_runners", "driver",            "TEXT",     "DEFAULT ''"),
    ("today_runners", "price",             "NUMERIC",  ""),
    ("today_runners", "rating",            "NUMERIC",  ""),
    ("today_runners", "source_confidence", "TEXT",     "DEFAULT 'official'"),
    # scratch_reason is the canonical column; migration 001 used scratch_timing.
    # Both the SQL migration and this Python fallback must add it.
    ("today_runners", "scratch_reason",    "TEXT",     "DEFAULT ''"),

    # Migration 006 — session_id backfill
    # epr_data: session_id may be absent on databases seeded from a pre-006
    # version of migration 001 that did not yet include the column.
    # epr_data uses TEXT (no FK) to allow flexible session identifiers
    # including non-UUID tokens written by the learning engine.
    ("epr_data",          "session_id", "TEXT",    ""),
    # aeee_adjustments and etg_tags: migration 001 created these tables without
    # session_id; the column must exist before any index on it can be created.
    # These tables use UUID FK to sessions(id) for referential integrity since
    # they record structured adjustments/tags tied to formal betting sessions.
    ("aeee_adjustments",  "session_id", "UUID",    "REFERENCES sessions(id) ON DELETE SET NULL"),
    ("etg_tags",          "session_id", "UUID",    "REFERENCES sessions(id) ON DELETE SET NULL"),
    ("etg_tags",          "manual_override", "BOOLEAN", "DEFAULT FALSE"),
    # test_ mirrors: created by migration 003 with LIKE <source> INCLUDING ALL,
    # so they also predate session_id and need the same backfill.
    ("test_epr_data",         "session_id", "TEXT",    ""),
    ("test_aeee_adjustments", "session_id", "UUID",    "REFERENCES sessions(id) ON DELETE SET NULL"),
    ("test_etg_tags",         "session_id", "UUID",    "REFERENCES sessions(id) ON DELETE SET NULL"),
    ("test_etg_tags",         "manual_override", "BOOLEAN", "DEFAULT FALSE"),
]


def run_migrations(db_client: Any = None) -> dict[str, Any]:
    """
    Execute schema migrations using raw SQL via Supabase rpc.
    Returns dict with results per migration.

    Note: Requires the Supabase service-role key or equivalent DDL privileges.
    Falls back gracefully if DDL is not permitted.
    """
    if db_client is None:
        from db import get_db
        db_client = get_db()

    results: dict[str, Any] = {"applied": [], "skipped": [], "errors": []}

    for table, column, sql_type, default_clause in _MIGRATIONS:
        sql = (
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS {column} {sql_type} {default_clause};"
        ).strip()

        try:
            db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
            results["applied"].append(f"{table}.{column}")
            log.info(f"migrations: applied {table}.{column}")
        except Exception as e:
            err_str = str(e)
            if "already exists" in err_str.lower() or "duplicate" in err_str.lower():
                results["skipped"].append(f"{table}.{column}")
            else:
                log.warning(f"migrations: could not apply {table}.{column}: {e}")
                results["errors"].append({"migration": f"{table}.{column}", "error": err_str})

    log.info(
        f"migrations: done — "
        f"applied={len(results['applied'])} "
        f"skipped={len(results['skipped'])} "
        f"errors={len(results['errors'])}"
    )
    return results


# ---------------------------------------------------------------------------
# PHASE 3 — INTELLIGENCE LAYER TABLE DEFINITIONS
# ---------------------------------------------------------------------------

_PHASE3_TABLES: list[tuple[str, str]] = [
    # feature_snapshots — serialized feature arrays with full race lineage
    (
        "feature_snapshots",
        """
        CREATE TABLE IF NOT EXISTS feature_snapshots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            race_uid TEXT NOT NULL DEFAULT '',
            oddspro_race_id TEXT DEFAULT '',
            snapshot_date DATE,
            runner_count INTEGER DEFAULT 0,
            features JSONB,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """,
    ),
    # prediction_snapshots — one row per prediction run for a race
    (
        "prediction_snapshots",
        """
        CREATE TABLE IF NOT EXISTS prediction_snapshots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prediction_snapshot_id TEXT UNIQUE NOT NULL,
            race_uid TEXT NOT NULL DEFAULT '',
            oddspro_race_id TEXT DEFAULT '',
            model_version TEXT DEFAULT 'baseline_v1',
            feature_snapshot_id TEXT DEFAULT '',
            runner_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """,
    ),
    # prediction_runner_outputs — per-runner scores and ranks
    (
        "prediction_runner_outputs",
        """
        CREATE TABLE IF NOT EXISTS prediction_runner_outputs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prediction_snapshot_id TEXT NOT NULL DEFAULT '',
            race_uid TEXT NOT NULL DEFAULT '',
            runner_name TEXT DEFAULT '',
            box_num INTEGER,
            predicted_rank INTEGER,
            score NUMERIC(10, 6),
            model_version TEXT DEFAULT 'baseline_v1',
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """,
    ),
    # learning_evaluations — post-result evaluation records
    # prediction_snapshot_id is unique: one evaluation per prediction
    (
        "learning_evaluations",
        """
        CREATE TABLE IF NOT EXISTS learning_evaluations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prediction_snapshot_id TEXT UNIQUE NOT NULL DEFAULT '',
            race_uid TEXT NOT NULL DEFAULT '',
            oddspro_race_id TEXT DEFAULT '',
            model_version TEXT DEFAULT 'baseline_v1',
            predicted_winner TEXT DEFAULT '',
            actual_winner TEXT DEFAULT '',
            winner_hit BOOLEAN DEFAULT false,
            top2_hit BOOLEAN DEFAULT false,
            top3_hit BOOLEAN DEFAULT false,
            predicted_rank_of_winner INTEGER,
            winner_odds NUMERIC(8, 2),
            evaluation_source TEXT DEFAULT 'oddspro',
            evaluated_at TIMESTAMPTZ DEFAULT now()
        )
        """,
    ),
    # backtest_runs — high-level backtest run summaries
    (
        "backtest_runs",
        """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id TEXT UNIQUE NOT NULL,
            date_from DATE NOT NULL,
            date_to DATE NOT NULL,
            code_filter TEXT DEFAULT '',
            track_filter TEXT DEFAULT '',
            model_version TEXT DEFAULT 'baseline_v1',
            total_races INTEGER DEFAULT 0,
            total_runners INTEGER DEFAULT 0,
            winner_hit_count INTEGER DEFAULT 0,
            top2_hit_count INTEGER DEFAULT 0,
            top3_hit_count INTEGER DEFAULT 0,
            hit_rate NUMERIC(8, 4) DEFAULT 0,
            top2_rate NUMERIC(8, 4) DEFAULT 0,
            top3_rate NUMERIC(8, 4) DEFAULT 0,
            avg_winner_odds NUMERIC(8, 2),
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """,
    ),
    # backtest_run_items — per-race results within a backtest run
    (
        "backtest_run_items",
        """
        CREATE TABLE IF NOT EXISTS backtest_run_items (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id TEXT NOT NULL DEFAULT '',
            race_uid TEXT NOT NULL DEFAULT '',
            race_date DATE,
            track TEXT DEFAULT '',
            code TEXT DEFAULT '',
            runner_count INTEGER DEFAULT 0,
            predicted_winner TEXT DEFAULT '',
            actual_winner TEXT DEFAULT '',
            winner_hit BOOLEAN DEFAULT false,
            top2_hit BOOLEAN DEFAULT false,
            top3_hit BOOLEAN DEFAULT false,
            score NUMERIC(10, 6),
            winner_odds NUMERIC(8, 2),
            model_version TEXT DEFAULT 'baseline_v1',
            used_stored_snapshot BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """,
    ),
]

# ---------------------------------------------------------------------------
# PHASE 4 — FEATURE ENGINE / SECTIONALS / RACE SHAPE / SCHEMA ALIGNMENT
# ---------------------------------------------------------------------------

_PHASE4_TABLES: list[tuple[str, str]] = [
    # sectional_snapshots — per-runner OddsPro sectional metrics
    (
        "sectional_snapshots",
        """
        CREATE TABLE IF NOT EXISTS sectional_snapshots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            race_uid TEXT NOT NULL DEFAULT '',
            oddspro_race_id TEXT DEFAULT '',
            box_num INTEGER,
            runner_name TEXT DEFAULT '',
            early_speed_score NUMERIC(10, 6),
            late_speed_score NUMERIC(10, 6),
            closing_delta NUMERIC(10, 4),
            fatigue_index NUMERIC(10, 4),
            acceleration_index NUMERIC(10, 4),
            sectional_consistency_score NUMERIC(10, 4),
            raw_early_time NUMERIC(10, 3),
            raw_mid_time NUMERIC(10, 3),
            raw_late_time NUMERIC(10, 3),
            raw_all_sections JSONB,
            source TEXT DEFAULT 'oddspro_result',
            source_type TEXT DEFAULT 'pre_race',
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """,
    ),
    # race_shape_snapshots — one row per race, race-level shape/tempo
    (
        "race_shape_snapshots",
        """
        CREATE TABLE IF NOT EXISTS race_shape_snapshots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            race_uid TEXT UNIQUE NOT NULL DEFAULT '',
            oddspro_race_id TEXT DEFAULT '',
            pace_scenario TEXT DEFAULT 'UNKNOWN',
            early_speed_density NUMERIC(8, 4),
            leader_pressure NUMERIC(8, 4),
            likely_leader_runner_ids JSONB,
            early_speed_conflict_score NUMERIC(8, 4),
            collapse_risk NUMERIC(8, 4),
            closer_advantage_score NUMERIC(8, 4),
            is_greyhound BOOLEAN DEFAULT false,
            sectionals_used BOOLEAN DEFAULT false,
            formfav_enrichment_used BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """,
    ),
]

# Phase 4 — additional columns on existing tables
_PHASE4_MIGRATIONS: list[tuple[str, str, str, str]] = [
    # sectional_snapshots — Phase 4.5 extension column
    ("sectional_snapshots", "source_type", "TEXT", "DEFAULT 'pre_race'"),

    # feature_snapshots — Phase 4 extension columns
    ("feature_snapshots", "has_sectionals",    "INTEGER",  "DEFAULT 0"),
    ("feature_snapshots", "has_race_shape",    "INTEGER",  "DEFAULT 0"),
    ("feature_snapshots", "has_collision",     "INTEGER",  "DEFAULT 0"),
    ("feature_snapshots", "sectional_metrics", "JSONB",    "DEFAULT '[]'::jsonb"),
    ("feature_snapshots", "race_shape",        "JSONB",    "DEFAULT '{}'::jsonb"),
    ("feature_snapshots", "collision_metrics", "JSONB",    "DEFAULT '[]'::jsonb"),

    # prediction_snapshots — Phase 4 extension columns
    ("prediction_snapshots", "has_sectionals", "INTEGER",  "DEFAULT 0"),
    ("prediction_snapshots", "has_race_shape", "INTEGER",  "DEFAULT 0"),
    ("prediction_snapshots", "has_collision",  "INTEGER",  "DEFAULT 0"),

    # backtest_run_items — Phase 4 extension columns
    ("backtest_run_items", "model_version",        "TEXT",     "DEFAULT 'baseline_v1'"),
    ("backtest_run_items", "used_stored_snapshot", "BOOLEAN",  "DEFAULT false"),
]


def run_phase3_migrations(db_client: Any = None) -> dict[str, Any]:
    """
    Create Phase 3 intelligence-layer tables if they do not already exist.

    Uses CREATE TABLE IF NOT EXISTS — safe to re-run.
    Returns dict with results per table.
    """
    if db_client is None:
        from db import get_db
        db_client = get_db()

    results: dict[str, Any] = {"created": [], "skipped": [], "errors": []}

    for table_name, create_sql in _PHASE3_TABLES:
        sql = create_sql.strip()
        try:
            db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
            results["created"].append(table_name)
            log.info(f"migrations: phase3 table ensured: {table_name}")
        except Exception as e:
            err_str = str(e)
            if "already exists" in err_str.lower():
                results["skipped"].append(table_name)
            else:
                log.warning(
                    f"migrations: could not create {table_name}: {e}"
                )
                results["errors"].append(
                    {"table": table_name, "error": err_str}
                )

    # Indexes for efficient lookups
    _ensure_phase3_indexes(db_client, results)

    log.info(
        f"migrations: phase3 done — "
        f"created={len(results['created'])} "
        f"skipped={len(results['skipped'])} "
        f"errors={len(results['errors'])}"
    )
    return results


def run_phase4_migrations(db_client: Any = None) -> dict[str, Any]:
    """
    Create Phase 4 feature-engine / sectionals / race-shape tables and add
    new columns to existing Phase 3 tables.

    Safe to re-run (uses IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
    Returns dict with results.
    """
    if db_client is None:
        from db import get_db
        db_client = get_db()

    results: dict[str, Any] = {"created": [], "altered": [], "skipped": [], "errors": []}

    # Create new Phase 4 tables
    for table_name, create_sql in _PHASE4_TABLES:
        sql = create_sql.strip()
        try:
            db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
            results["created"].append(table_name)
            log.info(f"migrations: phase4 table ensured: {table_name}")
        except Exception as e:
            err_str = str(e)
            if "already exists" in err_str.lower():
                results["skipped"].append(table_name)
            else:
                log.warning(f"migrations: could not create {table_name}: {e}")
                results["errors"].append({"table": table_name, "error": err_str})

    # Add new columns to existing tables
    for table, column, sql_type, default_clause in _PHASE4_MIGRATIONS:
        sql = (
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS {column} {sql_type} {default_clause};"
        ).strip()
        try:
            db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
            results["altered"].append(f"{table}.{column}")
            log.info(f"migrations: phase4 column ensured: {table}.{column}")
        except Exception as e:
            err_str = str(e)
            if "already exists" in err_str.lower() or "duplicate" in err_str.lower():
                results["skipped"].append(f"{table}.{column}")
            else:
                log.warning(f"migrations: could not add {table}.{column}: {e}")
                results["errors"].append({"migration": f"{table}.{column}", "error": err_str})

    # Phase 4 indexes
    _ensure_phase4_indexes(db_client, results)

    log.info(
        f"migrations: phase4 done — "
        f"created={len(results['created'])} "
        f"altered={len(results['altered'])} "
        f"skipped={len(results['skipped'])} "
        f"errors={len(results['errors'])}"
    )
    return results


def run_all_migrations(db_client: Any = None) -> dict[str, Any]:
    """
    Run all migration phases in order: column migrations → Phase 3 → Phase 4.
    Safe to re-run.
    """
    if db_client is None:
        from db import get_db
        db_client = get_db()

    combined: dict[str, Any] = {
        "column_migrations": {},
        "phase3": {},
        "phase4": {},
    }

    combined["column_migrations"] = run_migrations(db_client)
    combined["phase3"] = run_phase3_migrations(db_client)
    combined["phase4"] = run_phase4_migrations(db_client)

    log.info("migrations: run_all_migrations complete")
    return combined


def _ensure_phase3_indexes(db_client: Any, results: dict[str, Any]) -> None:
    """Create Phase 3 indexes for efficient lookups."""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_feature_snapshots_race_uid ON feature_snapshots(race_uid);",
        "CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_race_uid ON prediction_snapshots(race_uid);",
        "CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_snap_id ON prediction_snapshots(prediction_snapshot_id);",
        "CREATE INDEX IF NOT EXISTS idx_prediction_runner_outputs_snap_id ON prediction_runner_outputs(prediction_snapshot_id);",
        "CREATE INDEX IF NOT EXISTS idx_learning_evaluations_race_uid ON learning_evaluations(race_uid);",
        "CREATE INDEX IF NOT EXISTS idx_learning_evaluations_model ON learning_evaluations(model_version);",
        "CREATE INDEX IF NOT EXISTS idx_backtest_runs_run_id ON backtest_runs(run_id);",
        "CREATE INDEX IF NOT EXISTS idx_backtest_run_items_run_id ON backtest_run_items(run_id);",
    ]
    for sql in indexes:
        try:
            db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
        except Exception as e:
            log.debug(f"migrations: index skipped/failed: {e}")


def _ensure_phase4_indexes(db_client: Any, results: dict[str, Any]) -> None:
    """Create Phase 4 indexes for efficient lookups."""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_sectional_snapshots_race_uid ON sectional_snapshots(race_uid);",
        "CREATE INDEX IF NOT EXISTS idx_sectional_snapshots_box ON sectional_snapshots(race_uid, box_num);",
        "CREATE INDEX IF NOT EXISTS idx_race_shape_snapshots_race_uid ON race_shape_snapshots(race_uid);",
        "CREATE INDEX IF NOT EXISTS idx_backtest_run_items_model ON backtest_run_items(model_version);",
    ]
    for sql in indexes:
        try:
            db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
        except Exception as e:
            log.debug(f"migrations: phase4 index skipped/failed: {e}")


def ensure_race_uid_index(db_client: Any = None) -> bool:
    """Create an index on today_races.race_uid for fast lookup."""
    if db_client is None:
        from db import get_db
        db_client = get_db()

    sql = "CREATE INDEX IF NOT EXISTS idx_today_races_race_uid ON today_races(race_uid);"
    try:
        db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
        log.info("migrations: race_uid index ensured")
        return True
    except Exception as e:
        log.warning(f"migrations: could not create race_uid index: {e}")
        return False
