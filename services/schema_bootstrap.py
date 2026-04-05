"""
services/schema_bootstrap.py — DemonPulse V8 Schema Bootstrap
==============================================================
Responsible for verifying that the database has the required schema,
and (optionally) running a fresh SQL schema setup on first deploy.

This module does NOT patch old schemas — it either confirms the schema
is correct or raises clear errors so the operator knows what to fix.

Usage:
    from services.schema_bootstrap import SchemaBootstrap
    SchemaBootstrap.run()  # call at application startup
"""
from __future__ import annotations

import logging
import os
from typing import Any

from db import get_db, safe_query, T

log = logging.getLogger(__name__)

# Minimum set of tables that must exist for the application to function.
# If any of these are missing, startup should fail loudly.
_REQUIRED_TABLES = [
    "today_races",
    "today_runners",
    "results_log",
    "users",
    "audit_log",
    "sessions",
    "system_state",
    "bet_log",
    "signals",
]

# Tables whose absence is a warning but not a fatal error
_OPTIONAL_TABLES = [
    "meetings",
    "feature_snapshots",
    "prediction_snapshots",
    "prediction_runner_outputs",
    "learning_evaluations",
    "backtest_runs",
    "backtest_run_items",
    "sectional_snapshots",
    "race_shape_snapshots",
    "user_accounts",
    "user_permissions",
    "user_sessions",
    "user_activity",
    "simulation_log",
    "source_log",
    "activity_log",
    "audit_log",
    "etg_tags",
    "epr_data",
    "aeee_adjustments",
    "pass_log",
]


class SchemaBootstrap:
    """Validates and bootstraps the Supabase schema at startup."""

    @staticmethod
    def run(fatal_on_missing: bool = True) -> dict[str, Any]:
        """
        Run the schema bootstrap sequence:
          1. Check Supabase connectivity
          2. Verify required tables exist
          3. Ensure system_state singleton row exists
          4. Report warnings for optional missing tables

        Args:
            fatal_on_missing: If True, raises RuntimeError when required
                              tables are missing. Set False in TEST mode.

        Returns:
            Summary dict with keys: ok, missing_required, missing_optional, warnings.
        """
        log.info("SchemaBootstrap: starting schema verification")

        summary: dict[str, Any] = {
            "ok":               True,
            "missing_required": [],
            "missing_optional": [],
            "warnings":         [],
        }

        # ── 1. Connectivity check ──────────────────────────────
        db = safe_query(get_db)
        if db is None:
            msg = "SchemaBootstrap: cannot connect to Supabase — check SUPABASE_URL/KEY"
            log.error(msg)
            summary["ok"] = False
            summary["warnings"].append(msg)
            if fatal_on_missing:
                raise RuntimeError(msg)
            return summary

        # ── 2. Table existence checks ──────────────────────────
        existing = _list_existing_tables(db)
        log.info(f"SchemaBootstrap: found {len(existing)} tables in schema")

        for tbl in _REQUIRED_TABLES:
            if tbl not in existing:
                summary["missing_required"].append(tbl)

        for tbl in _OPTIONAL_TABLES:
            if tbl not in existing:
                summary["missing_optional"].append(tbl)

        if summary["missing_required"]:
            msg = (
                f"SchemaBootstrap: MISSING REQUIRED TABLES: {summary['missing_required']}. "
                f"Run sql/001_canonical_schema.sql in the Supabase SQL editor."
            )
            log.error(msg)
            summary["ok"] = False
            if fatal_on_missing:
                raise RuntimeError(msg)

        if summary["missing_optional"]:
            log.warning(
                f"SchemaBootstrap: optional tables missing (non-fatal): "
                f"{summary['missing_optional']}"
            )

        # ── 3. system_state singleton ──────────────────────────
        if "system_state" in existing:
            _ensure_system_state(db)

        log.info(
            f"SchemaBootstrap: complete — "
            f"ok={summary['ok']}, "
            f"missing_required={summary['missing_required']}, "
            f"missing_optional={len(summary['missing_optional'])}"
        )
        return summary

    @staticmethod
    def sql_path() -> str:
        """Return the path to the canonical SQL schema file."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(here, "sql", "001_canonical_schema.sql")


# ─────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────

def _list_existing_tables(db) -> set[str]:
    """
    Query information_schema.tables for user-created tables in the
    public schema.  Returns a set of table names (without test_ prefix).
    """
    try:
        result = db.table("information_schema.tables").select(
            "table_name"
        ).eq("table_schema", "public").execute()
        rows = result.data or []
        return {r["table_name"] for r in rows}
    except Exception:
        # Fallback: probe each required table directly
        found: set[str] = set()
        for tbl in _REQUIRED_TABLES + _OPTIONAL_TABLES:
            try:
                db.table(tbl).select("id").limit(0).execute()
                found.add(tbl)
            except Exception:
                pass
        return found


def _ensure_system_state(db) -> None:
    """Ensure the system_state singleton row (id=1) exists."""
    try:
        rows = db.table(T("system_state")).select("id").eq("id", 1).execute().data
        if not rows:
            db.table(T("system_state")).insert({"id": 1}).execute()
            log.info("SchemaBootstrap: created system_state singleton row")
    except Exception as exc:
        log.warning(f"SchemaBootstrap: could not ensure system_state row: {exc}")
