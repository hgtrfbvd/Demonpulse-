"""
migrations.py - DemonPulse Database Migrations
================================================
Ensures the Supabase schema has all columns required by the V8 architecture.
Run this once after deploying new code that adds columns.

New columns added by this migration set:
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
    - scratch_reason    TEXT  (already in schema, keep for safety)
    - source_confidence TEXT

  results_log:
    (no new columns — existing schema is sufficient)
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
