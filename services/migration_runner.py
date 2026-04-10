"""
services/migration_runner.py — DemonPulse V9 Migration Runner
==============================================================
Runs the canonical SQL schema files against the connected Supabase
instance via the Supabase Management API (if available) or by
executing statements through the Python client.

Primary use: fresh installs and CI/CD pipelines.
For production upgrades: paste sql/001_canonical_schema.sql directly
into the Supabase SQL Editor — it is fully idempotent.

Usage:
    from services.migration_runner import MigrationRunner
    MigrationRunner.run_all()
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Ordered list of SQL files to apply.
# Each file must be idempotent (CREATE TABLE IF NOT EXISTS, etc.)
_SQL_FILES = [
    "001_canonical_schema.sql",
    "002_indexes_constraints.sql",
    "003_views_optional.sql",
]

_SQL_DIR = Path(__file__).parent.parent / "sql"


class MigrationRunner:
    """Applies canonical SQL migration files to the Supabase instance."""

    @staticmethod
    def run_all(stop_on_error: bool = True) -> dict:
        """
        Run all SQL migration files in order.

        Args:
            stop_on_error: If True, stop after the first failure.

        Returns:
            {"ok": bool, "applied": list[str], "failed": list[str]}
        """
        summary = {"ok": True, "applied": [], "failed": []}

        for filename in _SQL_FILES:
            path = _SQL_DIR / filename
            if not path.exists():
                log.warning(f"MigrationRunner: SQL file not found: {path}")
                continue

            log.info(f"MigrationRunner: applying {filename}")
            ok = MigrationRunner._apply_file(path)
            if ok:
                summary["applied"].append(filename)
                log.info(f"MigrationRunner: ✓ {filename} applied")
            else:
                summary["failed"].append(filename)
                summary["ok"] = False
                log.error(f"MigrationRunner: ✗ {filename} FAILED")
                if stop_on_error:
                    break

        return summary

    @staticmethod
    def run_file(filename: str) -> bool:
        """Run a single SQL file by name (must be in sql/ directory)."""
        path = _SQL_DIR / filename
        if not path.exists():
            log.error(f"MigrationRunner: file not found: {path}")
            return False
        return MigrationRunner._apply_file(path)

    @staticmethod
    def sql_content(filename: str) -> Optional[str]:
        """Return the raw SQL content for a given migration file."""
        path = _SQL_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    # ── INTERNAL ─────────────────────────────────────────────────

    @staticmethod
    def _apply_file(path: Path) -> bool:
        """
        Apply a SQL file.  Execution strategy:
          1. Try Supabase RPC `exec_sql` if available (requires the function
             to be installed in the Supabase project).
          2. Fall back to executing individual statements via the REST API.

        NOTE: Supabase's PostgREST API does not support raw DDL execution.
        For production, the recommended approach is to paste the SQL into
        the Supabase SQL editor.  This runner supports automated pipelines
        where the `exec_sql` RPC is available.
        """
        sql = path.read_text(encoding="utf-8").strip()
        if not sql:
            return True

        # Strategy 1: exec_sql RPC
        try:
            from db import get_db
            db = get_db()
            db.rpc("exec_sql", {"sql": sql}).execute()
            return True
        except Exception as rpc_err:
            log.debug(f"MigrationRunner: exec_sql RPC not available ({rpc_err}); "
                      f"file must be applied via Supabase SQL Editor: {path.name}")
            # Not a fatal error — the SQL files are provided for manual application
            return False
