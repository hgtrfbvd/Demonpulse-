"""
migrations.py - DemonPulse Database Migrations
================================================
Ensures the Supabase schema has all columns and tables required by the
V8 architecture and Phase 3/4/4.6 intelligence layers.

Run once after deploying new code that adds columns or tables.
Safe to re-run — uses ADD COLUMN IF NOT EXISTS throughout.

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
    - recorded_at  TIMESTAMPTZ  (canonical name; old schema used created_at)

Phase 3 — Intelligence Layer new tables:
  feature_snapshots       — serialized feature arrays per race with lineage
  prediction_snapshots    — prediction run metadata
  prediction_runner_outputs — per-runner scores and predicted ranks
  learning_evaluations    — post-result evaluation records
  backtest_runs           — backtest run summaries
  backtest_run_items      — per-race results within a backtest run

Phase 4.6 — Schema Alignment:
  Brings existing databases seeded from the legacy supabase_schema.sql
  into alignment with 001_canonical_schema.sql. Every column written by
  the application code is guaranteed to exist after this phase runs.
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
    ("today_races", "country",         "TEXT",         "DEFAULT 'au'"),
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

# ---------------------------------------------------------------------------
# PHASE 4.6 — SCHEMA ALIGNMENT
# Brings databases seeded from the *previous* version of supabase_schema.sql
# (before it was aligned with 001_canonical_schema.sql) into full canonical
# alignment.  Every column that the application code writes is guaranteed to
# exist after this phase runs.
# All entries use ADD COLUMN IF NOT EXISTS — safe to re-run.
# ---------------------------------------------------------------------------
_SCHEMA_ALIGN_MIGRATIONS: list[tuple[str, str, str, str]] = [

    # ── results_log ─────────────────────────────────────────────────────────
    # Code writes "recorded_at"; legacy schema had "created_at" only.
    ("results_log",      "recorded_at",    "TIMESTAMPTZ",  "DEFAULT NOW()"),
    ("test_results_log", "recorded_at",    "TIMESTAMPTZ",  "DEFAULT NOW()"),

    # ── audit_log ───────────────────────────────────────────────────────────
    # Code writes "ip"; legacy schema named this column "ip_address".
    # audit_log is always-live (no test_ mirror).
    ("audit_log", "ip", "TEXT", ""),

    # ── today_races ──────────────────────────────────────────────────────────
    # FormFav integration: country field added to allow reliable AU/NZ filtering.
    # International races (e.g. Bath, Hanshin) must be excluded from FormFav;
    # relying only on the empty-state heuristic was incorrect.
    ("today_races",      "country", "TEXT", "DEFAULT 'au'"),
    ("test_today_races", "country", "TEXT", "DEFAULT 'au'"),

    # ── today_runners ────────────────────────────────────────────────────────
    # Code writes race_id (FK), date, track, race_num, is_fav, raw_hash.
    # Legacy schema lacked all six; canonical schema defines them all.
    ("today_runners",      "race_id",  "UUID",     ""),
    ("today_runners",      "date",     "DATE",     "DEFAULT CURRENT_DATE"),
    ("today_runners",      "track",    "TEXT",     "DEFAULT ''"),
    ("today_runners",      "race_num", "INTEGER",  ""),
    ("today_runners",      "is_fav",   "BOOLEAN",  "DEFAULT FALSE"),
    ("today_runners",      "raw_hash", "TEXT",     "DEFAULT ''"),
    ("test_today_runners", "race_id",  "UUID",     ""),
    ("test_today_runners", "date",     "DATE",     "DEFAULT CURRENT_DATE"),
    ("test_today_runners", "track",    "TEXT",     "DEFAULT ''"),
    ("test_today_runners", "race_num", "INTEGER",  ""),
    ("test_today_runners", "is_fav",   "BOOLEAN",  "DEFAULT FALSE"),
    ("test_today_runners", "raw_hash", "TEXT",     "DEFAULT ''"),

    # ── signals ─────────────────────────────────────────────────────────────
    # Legacy schema used different field names (signal_type, decision, score …).
    # Code writes the canonical fields; all are added here so upserts succeed.
    ("signals",      "signal",       "TEXT",          ""),
    ("signals",      "ev",           "NUMERIC(6,3)",  ""),
    ("signals",      "alert_level",  "TEXT",          "DEFAULT 'NONE'"),
    ("signals",      "hot_bet",      "BOOLEAN",       "DEFAULT FALSE"),
    ("signals",      "risk_flags",   "JSONB",         "DEFAULT '[]'::jsonb"),
    ("signals",      "top_runner",   "TEXT",          ""),
    ("signals",      "top_box",      "INTEGER",       ""),
    ("signals",      "top_odds",     "NUMERIC(6,2)",  ""),
    ("signals",      "generated_at", "TIMESTAMPTZ",   "DEFAULT NOW()"),
    ("test_signals", "signal",       "TEXT",          ""),
    ("test_signals", "ev",           "NUMERIC(6,3)",  ""),
    ("test_signals", "alert_level",  "TEXT",          "DEFAULT 'NONE'"),
    ("test_signals", "hot_bet",      "BOOLEAN",       "DEFAULT FALSE"),
    ("test_signals", "risk_flags",   "JSONB",         "DEFAULT '[]'::jsonb"),
    ("test_signals", "top_runner",   "TEXT",          ""),
    ("test_signals", "top_box",      "INTEGER",       ""),
    ("test_signals", "top_odds",     "NUMERIC(6,2)",  ""),
    ("test_signals", "generated_at", "TIMESTAMPTZ",   "DEFAULT NOW()"),

    # ── prediction_snapshots ─────────────────────────────────────────────────
    # Phase 4.6 adds has_enrichment, source_type, feature_snapshot_id.
    ("prediction_snapshots",      "has_enrichment",       "INTEGER",  "DEFAULT 0"),
    ("prediction_snapshots",      "source_type",          "TEXT",     "DEFAULT 'pre_race'"),
    ("prediction_snapshots",      "feature_snapshot_id",  "TEXT",     "DEFAULT ''"),
    ("test_prediction_snapshots", "has_enrichment",       "INTEGER",  "DEFAULT 0"),
    ("test_prediction_snapshots", "source_type",          "TEXT",     "DEFAULT 'pre_race'"),
    ("test_prediction_snapshots", "feature_snapshot_id",  "TEXT",     "DEFAULT ''"),

    # ── learning_evaluations ─────────────────────────────────────────────────
    # Phase 4.6 adds enrichment tracking and disagreement metrics.
    ("learning_evaluations",      "oddspro_race_id",      "TEXT",         "DEFAULT ''"),
    ("learning_evaluations",      "used_enrichment",      "BOOLEAN",      "DEFAULT FALSE"),
    ("learning_evaluations",      "disagreement_score",   "NUMERIC(8,4)", ""),
    ("learning_evaluations",      "formfav_rank",         "INTEGER",      ""),
    ("learning_evaluations",      "your_rank",            "INTEGER",      ""),
    ("test_learning_evaluations", "oddspro_race_id",      "TEXT",         "DEFAULT ''"),
    ("test_learning_evaluations", "used_enrichment",      "BOOLEAN",      "DEFAULT FALSE"),
    ("test_learning_evaluations", "disagreement_score",   "NUMERIC(8,4)", ""),
    ("test_learning_evaluations", "formfav_rank",         "INTEGER",      ""),
    ("test_learning_evaluations", "your_rank",            "INTEGER",      ""),

    # ── pass_log ─────────────────────────────────────────────────────────────
    # Code writes pass_reason, local_decision, confidence, date.
    # Legacy schema used reason, block_code, score.
    ("pass_log",      "pass_reason",    "TEXT",  ""),
    ("pass_log",      "local_decision", "TEXT",  ""),
    ("pass_log",      "confidence",     "TEXT",  ""),
    ("pass_log",      "date",           "DATE",  "DEFAULT CURRENT_DATE"),
    ("test_pass_log", "pass_reason",    "TEXT",  ""),
    ("test_pass_log", "local_decision", "TEXT",  ""),
    ("test_pass_log", "confidence",     "TEXT",  ""),
    ("test_pass_log", "date",           "DATE",  "DEFAULT CURRENT_DATE"),

    # ── etg_tags ─────────────────────────────────────────────────────────────
    # Code writes bet_id, error_tag, notes, manual_override, date.
    # Legacy schema used "tag" not "error_tag".
    ("etg_tags",      "bet_id",          "UUID",     ""),
    ("etg_tags",      "error_tag",       "TEXT",     ""),
    ("etg_tags",      "notes",           "TEXT",     ""),
    ("etg_tags",      "date",            "DATE",     "DEFAULT CURRENT_DATE"),
    ("etg_tags",      "track",           "TEXT",     ""),
    ("etg_tags",      "race_num",        "INTEGER",  ""),
    ("test_etg_tags", "bet_id",          "UUID",     ""),
    ("test_etg_tags", "error_tag",       "TEXT",     ""),
    ("test_etg_tags", "notes",           "TEXT",     ""),
    ("test_etg_tags", "date",            "DATE",     "DEFAULT CURRENT_DATE"),
    ("test_etg_tags", "track",           "TEXT",     ""),
    ("test_etg_tags", "race_num",        "INTEGER",  ""),

    # ── epr_data ─────────────────────────────────────────────────────────────
    # Code writes code, track, confidence_tier, ev_at_analysis,
    # execution_mode, date. Legacy schema lacked these.
    ("epr_data",      "code",            "TEXT",         "DEFAULT 'GREYHOUND'"),
    ("epr_data",      "track",           "TEXT",         ""),
    ("epr_data",      "confidence_tier", "TEXT",         ""),
    ("epr_data",      "ev_at_analysis",  "NUMERIC(6,3)", ""),
    ("epr_data",      "execution_mode",  "TEXT",         ""),
    ("epr_data",      "date",            "DATE",         "DEFAULT CURRENT_DATE"),
    ("test_epr_data", "code",            "TEXT",         "DEFAULT 'GREYHOUND'"),
    ("test_epr_data", "track",           "TEXT",         ""),
    ("test_epr_data", "confidence_tier", "TEXT",         ""),
    ("test_epr_data", "ev_at_analysis",  "NUMERIC(6,3)", ""),
    ("test_epr_data", "execution_mode",  "TEXT",         ""),
    ("test_epr_data", "date",            "DATE",         "DEFAULT CURRENT_DATE"),

    # ── aeee_adjustments ─────────────────────────────────────────────────────
    # Code writes direction, amount, roi_trigger, bets_sample, applied,
    # promoted. Legacy schema used "adjustment" instead of "amount".
    ("aeee_adjustments",      "direction",   "TEXT",         ""),
    ("aeee_adjustments",      "amount",      "NUMERIC(5,3)", ""),
    ("aeee_adjustments",      "roi_trigger", "NUMERIC(6,2)", ""),
    ("aeee_adjustments",      "bets_sample", "INTEGER",      ""),
    ("aeee_adjustments",      "applied",     "BOOLEAN",      "DEFAULT FALSE"),
    ("aeee_adjustments",      "promoted",    "BOOLEAN",      "DEFAULT FALSE"),
    ("test_aeee_adjustments", "direction",   "TEXT",         ""),
    ("test_aeee_adjustments", "amount",      "NUMERIC(5,3)", ""),
    ("test_aeee_adjustments", "roi_trigger", "NUMERIC(6,2)", ""),
    ("test_aeee_adjustments", "bets_sample", "INTEGER",      ""),
    ("test_aeee_adjustments", "applied",     "BOOLEAN",      "DEFAULT FALSE"),
    ("test_aeee_adjustments", "promoted",    "BOOLEAN",      "DEFAULT FALSE"),

    # ── system_state ─────────────────────────────────────────────────────────
    # Canonical adds tuning/simulation columns absent from legacy schema.
    ("system_state",      "confidence_threshold", "NUMERIC(4,2)", "DEFAULT 0.65"),
    ("system_state",      "ev_threshold",         "NUMERIC(4,2)", "DEFAULT 0.08"),
    ("system_state",      "staking_mode",         "TEXT",         "DEFAULT 'KELLY'"),
    ("system_state",      "tempo_weight",         "NUMERIC(4,2)", "DEFAULT 1.0"),
    ("system_state",      "traffic_penalty",      "NUMERIC(4,2)", "DEFAULT 0.8"),
    ("system_state",      "closer_boost",         "NUMERIC(4,2)", "DEFAULT 1.1"),
    ("system_state",      "fade_penalty",         "NUMERIC(4,2)", "DEFAULT 0.9"),
    ("system_state",      "simulation_depth",     "INTEGER",      "DEFAULT 1000"),
    ("test_system_state", "confidence_threshold", "NUMERIC(4,2)", "DEFAULT 0.65"),
    ("test_system_state", "ev_threshold",         "NUMERIC(4,2)", "DEFAULT 0.08"),
    ("test_system_state", "staking_mode",         "TEXT",         "DEFAULT 'KELLY'"),
    ("test_system_state", "tempo_weight",         "NUMERIC(4,2)", "DEFAULT 1.0"),
    ("test_system_state", "traffic_penalty",      "NUMERIC(4,2)", "DEFAULT 0.8"),
    ("test_system_state", "closer_boost",         "NUMERIC(4,2)", "DEFAULT 1.1"),
    ("test_system_state", "fade_penalty",         "NUMERIC(4,2)", "DEFAULT 0.9"),
    ("test_system_state", "simulation_depth",     "INTEGER",      "DEFAULT 1000"),

    # ── source_log ───────────────────────────────────────────────────────────
    # Code writes date, call_num, url, status, rows_returned.
    # Legacy schema used source (NOT NULL), endpoint, status_code, response_ms,
    # success, error_msg, records_fetched. The canonical fields are all new.
    ("source_log",      "date",          "DATE",    "DEFAULT CURRENT_DATE"),
    ("source_log",      "call_num",      "INTEGER", ""),
    ("source_log",      "url",           "TEXT",    ""),
    ("source_log",      "status",        "TEXT",    ""),
    ("source_log",      "rows_returned", "INTEGER", ""),
    ("test_source_log", "date",          "DATE",    "DEFAULT CURRENT_DATE"),
    ("test_source_log", "call_num",      "INTEGER", ""),
    ("test_source_log", "url",           "TEXT",    ""),
    ("test_source_log", "status",        "TEXT",    ""),
    ("test_source_log", "rows_returned", "INTEGER", ""),
]


def run_schema_alignment(db_client: Any = None) -> dict[str, Any]:
    """
    Phase 4.6: bring any database that was seeded from the *previous* version
    of supabase_schema.sql (before it was aligned with 001_canonical_schema.sql)
    into full alignment with the current canonical schema.

    Adds every column that the application code writes but that was absent
    from the pre-alignment schema.  Uses ADD COLUMN IF NOT EXISTS — safe to
    re-run against a database that already has the canonical structure.

    Also removes blocking NOT NULL constraints on columns that existed only
    in the old schema and are no longer written by the application
    (source_log.source, etg_tags.tag).

    Returns dict with keys: applied, skipped, errors.
    """
    if db_client is None:
        from db import get_db
        db_client = get_db()

    results: dict[str, Any] = {"applied": [], "skipped": [], "errors": []}

    # ── ADD COLUMN IF NOT EXISTS ──────────────────────────────────────────────
    for table, column, sql_type, default_clause in _SCHEMA_ALIGN_MIGRATIONS:
        sql = (
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS {column} {sql_type} {default_clause};"
        ).strip()

        try:
            db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
            results["applied"].append(f"{table}.{column}")
            log.info(f"migrations: schema_align applied {table}.{column}")
        except Exception as e:
            err_str = str(e)
            if "already exists" in err_str.lower() or "duplicate" in err_str.lower():
                results["skipped"].append(f"{table}.{column}")
            else:
                log.warning(
                    f"migrations: schema_align could not apply {table}.{column}: {e}"
                )
                results["errors"].append(
                    {"migration": f"{table}.{column}", "error": err_str}
                )

    # ── REMOVE BLOCKING NOT NULL CONSTRAINTS ─────────────────────────────────
    # The legacy schema had NOT NULL on columns that the canonical code never
    # writes.  These must be relaxed so inserts do not fail.
    #
    # source_log.source  — legacy had TEXT NOT NULL; canonical has no "source"
    #                       column at all.  Code inserts without "source".
    # etg_tags.tag       — legacy had TEXT NOT NULL; canonical uses "error_tag".
    #                       Code inserts "error_tag", not "tag".
    # test_source_log / test_etg_tags: same problem on the test mirrors.
    _null_relaxations = [
        "ALTER TABLE source_log      ALTER COLUMN source DROP NOT NULL;",
        "ALTER TABLE source_log      ALTER COLUMN source SET DEFAULT '';",
        "ALTER TABLE test_source_log ALTER COLUMN source DROP NOT NULL;",
        "ALTER TABLE test_source_log ALTER COLUMN source SET DEFAULT '';",
        "ALTER TABLE etg_tags        ALTER COLUMN tag    DROP NOT NULL;",
        "ALTER TABLE etg_tags        ALTER COLUMN tag    SET DEFAULT '';",
        "ALTER TABLE test_etg_tags   ALTER COLUMN tag    DROP NOT NULL;",
        "ALTER TABLE test_etg_tags   ALTER COLUMN tag    SET DEFAULT '';",
    ]
    for sql in _null_relaxations:
        label = sql.split(";")[0].strip()
        try:
            db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
            results["applied"].append(label)
            log.info(f"migrations: schema_align applied '{label}'")
        except Exception as e:
            err_str = str(e)
            # "column does not exist" means the column is already gone (canonical
            # schema deployment — no action needed).
            # "already" / "no such constraint" indicate idempotent re-run.
            if any(phrase in err_str.lower() for phrase in (
                "does not exist", "already", "no constraint"
            )):
                results["skipped"].append(label)
            else:
                log.debug(f"migrations: schema_align null relax skipped: {label}: {e}")
                results["skipped"].append(label)

    log.info(
        f"migrations: schema_align done — "
        f"applied={len(results['applied'])} "
        f"skipped={len(results['skipped'])} "
        f"errors={len(results['errors'])}"
    )
    return results


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


# ---------------------------------------------------------------------------
# PHASE 5 — FORMFAV FULL-COVERAGE ENRICHMENT TABLES
# Stores complete FormFav API responses for race and runner enrichment.
# Separate from primary OddsPro tables. Keyed by race_uid + runner number.
# ---------------------------------------------------------------------------

_PHASE5_TABLES: list[tuple[str, str]] = [
    (
        "formfav_race_enrichment",
        """
        CREATE TABLE IF NOT EXISTS formfav_race_enrichment (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            race_uid TEXT NOT NULL,
            date DATE,
            track TEXT DEFAULT '',
            race_num INTEGER,
            race_code TEXT DEFAULT '',
            race_name TEXT DEFAULT '',
            distance TEXT DEFAULT '',
            grade TEXT DEFAULT '',
            condition TEXT DEFAULT '',
            weather TEXT DEFAULT '',
            start_time TEXT DEFAULT '',
            start_time_utc TEXT DEFAULT '',
            timezone TEXT DEFAULT '',
            abandoned BOOLEAN DEFAULT false,
            number_of_runners INTEGER DEFAULT 0,
            pace_scenario TEXT DEFAULT '',
            raw_response JSONB,
            fetched_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (race_uid)
        )
        """,
    ),
    (
        "formfav_runner_enrichment",
        """
        CREATE TABLE IF NOT EXISTS formfav_runner_enrichment (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            race_uid TEXT NOT NULL,
            runner_name TEXT DEFAULT '',
            number INTEGER,
            barrier INTEGER,
            age TEXT DEFAULT '',
            claim TEXT DEFAULT '',
            scratched BOOLEAN DEFAULT false,
            form_string TEXT DEFAULT '',
            trainer TEXT DEFAULT '',
            jockey TEXT DEFAULT '',
            driver TEXT DEFAULT '',
            weight NUMERIC,
            decorators JSONB DEFAULT '[]'::jsonb,
            speed_map JSONB,
            class_profile JSONB,
            race_class_fit JSONB,
            stats_overall JSONB,
            stats_track JSONB,
            stats_distance JSONB,
            stats_condition JSONB,
            stats_track_distance JSONB,
            stats_full JSONB,
            win_prob NUMERIC,
            place_prob NUMERIC,
            model_rank INTEGER,
            confidence TEXT DEFAULT '',
            model_version TEXT DEFAULT '',
            fetched_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (race_uid, number)
        )
        """,
    ),
]


def run_phase5_formfav_migrations(db_client: Any = None) -> dict[str, Any]:
    """
    Phase 5 — Create FormFav full-coverage enrichment tables.
    Safe to re-run (uses CREATE TABLE IF NOT EXISTS).
    """
    if db_client is None:
        from db import get_db
        db_client = get_db()

    results: dict[str, Any] = {"created": [], "skipped": [], "errors": []}

    for table_name, create_sql in _PHASE5_TABLES:
        try:
            db_client.rpc(
                "run_migration_sql",
                {"sql_statement": create_sql.strip()},
            ).execute()
            results["created"].append(table_name)
            log.info(f"migrations: phase5 table ensured: {table_name}")
        except Exception as e:
            err_str = str(e)
            if "already exists" in err_str.lower() or "duplicate" in err_str.lower():
                results["skipped"].append(table_name)
            else:
                log.warning(f"migrations: could not create {table_name}: {e}")
                results["errors"].append({"table": table_name, "error": err_str})

    _ensure_phase5_indexes(db_client, results)

    log.info(
        f"migrations: phase5 done — "
        f"created={len(results['created'])} "
        f"skipped={len(results['skipped'])} "
        f"errors={len(results['errors'])}"
    )
    return results


def run_all_migrations(db_client: Any = None) -> dict[str, Any]:
    """
    Run all migration phases in order:
      1. Column migrations (Phase 1/2 ADD COLUMN)
      2. Phase 3 intelligence-layer table creation
      3. Phase 4 feature-engine / sectionals table creation + column additions
      4. Phase 4.6 schema alignment (brings legacy supabase_schema.sql DBs to canonical)
      5. Phase 5 FormFav full-coverage enrichment tables

    Safe to re-run.
    """
    if db_client is None:
        from db import get_db
        db_client = get_db()

    combined: dict[str, Any] = {
        "column_migrations": {},
        "phase3": {},
        "phase4": {},
        "schema_alignment": {},
        "phase5_formfav": {},
    }

    combined["column_migrations"] = run_migrations(db_client)
    combined["phase3"] = run_phase3_migrations(db_client)
    combined["phase4"] = run_phase4_migrations(db_client)
    combined["schema_alignment"] = run_schema_alignment(db_client)
    combined["phase5_formfav"] = run_phase5_formfav_migrations(db_client)

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


def _ensure_phase5_indexes(db_client: Any, results: dict[str, Any]) -> None:
    """Create Phase 5 indexes for FormFav enrichment tables."""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_formfav_race_enrichment_race_uid ON formfav_race_enrichment(race_uid);",
        "CREATE INDEX IF NOT EXISTS idx_formfav_race_enrichment_date ON formfav_race_enrichment(date);",
        "CREATE INDEX IF NOT EXISTS idx_formfav_runner_enrichment_race_uid ON formfav_runner_enrichment(race_uid);",
        "CREATE INDEX IF NOT EXISTS idx_formfav_runner_enrichment_race_num ON formfav_runner_enrichment(race_uid, number);",
    ]
    for sql in indexes:
        try:
            db_client.rpc("run_migration_sql", {"sql_statement": sql}).execute()
        except Exception as e:
            log.debug(f"migrations: phase5 index skipped/failed: {e}")
