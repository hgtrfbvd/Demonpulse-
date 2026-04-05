"""
supabase_config.py — DemonPulse V8 Supabase Configuration
==========================================================
Canonical source of truth for all Supabase-related constants,
environment variable declarations, and validation helpers.

Usage:
    from supabase_config import SupabaseConfig, VALID_RACE_CODES

Environment variables (all required for LIVE mode):
    SUPABASE_URL        Production Supabase project URL
    SUPABASE_KEY        Production service-role or anon key
    SUPABASE_TEST_URL   (Optional) Dedicated test instance URL
    SUPABASE_TEST_KEY   (Optional) Dedicated test instance key
    DP_ENV              TEST | LIVE  (default: LIVE)
    JWT_SECRET          Secret for internal JWT tokens
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# VALID RACING CODES
# ─────────────────────────────────────────────────────────────────
VALID_RACE_CODES = {"GREYHOUND", "HARNESS", "GALLOPS"}

# Default if not specified — always explicit, never silently assumed
DEFAULT_RACE_CODE = "GREYHOUND"

# ─────────────────────────────────────────────────────────────────
# TABLE NAMES — single canonical list
# These are the production table names; TEST mode prefixes with test_
# ─────────────────────────────────────────────────────────────────

# Core race/meeting tables
TABLE_MEETINGS        = "meetings"          # race meetings by date/track/code
TABLE_RACES           = "today_races"       # individual races
TABLE_RUNNERS         = "today_runners"     # runners per race
TABLE_RESULTS         = "results_log"       # official race results

# Prediction / AI
TABLE_FEATURE_SNAPS   = "feature_snapshots"
TABLE_PRED_SNAPS      = "prediction_snapshots"
TABLE_PRED_OUTPUTS    = "prediction_runner_outputs"
TABLE_LEARNING_EVALS  = "learning_evaluations"
TABLE_SECTIONALS      = "sectional_snapshots"
TABLE_RACE_SHAPE      = "race_shape_snapshots"

# Backtest
TABLE_BACKTEST_RUNS   = "backtest_runs"
TABLE_BACKTEST_ITEMS  = "backtest_run_items"

# Users / Auth
TABLE_USERS           = "users"
TABLE_USER_ACCOUNTS   = "user_accounts"
TABLE_USER_PERMS      = "user_permissions"
TABLE_USER_SESSIONS   = "user_sessions"
TABLE_USER_ACTIVITY   = "user_activity"

# Betting
TABLE_BET_LOG         = "bet_log"
TABLE_SIGNALS         = "signals"
TABLE_SESSIONS        = "sessions"
TABLE_SESSION_HISTORY = "session_history"
TABLE_EXOTIC_SUGG     = "exotic_suggestions"

# System
TABLE_SYSTEM_STATE    = "system_state"
TABLE_AUDIT_LOG       = "audit_log"
TABLE_SOURCE_LOG      = "source_log"
TABLE_ACTIVITY_LOG    = "activity_log"
TABLE_SIMULATION_LOG  = "simulation_log"
TABLE_CHAT_HISTORY    = "chat_history"
TABLE_TRAINING_LOGS   = "training_logs"
TABLE_SCRATCH_LOG     = "scratch_log"

# Learning engine
TABLE_ETG_TAGS        = "etg_tags"
TABLE_EPR_DATA        = "epr_data"
TABLE_AEEE_ADJ        = "aeee_adjustments"
TABLE_PASS_LOG        = "pass_log"
TABLE_GPIL_PATTERNS   = "gpil_patterns"

# Performance
TABLE_PERF_DAILY      = "performance_daily"
TABLE_PERF_TRACK      = "performance_by_track"
TABLE_PERF_EDGE       = "performance_by_edge"

# Market / scoring
TABLE_MARKET_SNAPS    = "market_snapshots"
TABLE_RUNNER_PROFILES = "runner_profiles"
TABLE_FORM_RUNS       = "form_runs"
TABLE_TRACK_PROFILES  = "track_profiles"
TABLE_SCORED_RACES    = "scored_races"
TABLE_SECT_BENCHMARKS = "sectional_benchmarks"

# Plugin/extended
TABLE_CHANGELOG       = "changelog"

# Tables that are always in the production namespace (never test-prefixed)
# These are shared identity stores; test runs never write fake users or audit rows
ALWAYS_LIVE_TABLES = frozenset({
    TABLE_USERS,
    TABLE_AUDIT_LOG,
})

# Tables that can be test-prefixed in TEST mode
TESTABLE_TABLES = frozenset({
    TABLE_RACES,
    TABLE_RUNNERS,
    TABLE_RESULTS,
    TABLE_BET_LOG,
    TABLE_SIGNALS,
    TABLE_SESSIONS,
    TABLE_SYSTEM_STATE,
    TABLE_ACTIVITY_LOG,
    TABLE_CHAT_HISTORY,
    TABLE_TRAINING_LOGS,
    TABLE_EXOTIC_SUGG,
    TABLE_ETG_TAGS,
    TABLE_EPR_DATA,
    TABLE_AEEE_ADJ,
    TABLE_PASS_LOG,
    TABLE_SOURCE_LOG,
    TABLE_USER_ACCOUNTS,
    TABLE_USER_PERMS,
    TABLE_USER_SESSIONS,
    TABLE_USER_ACTIVITY,
    TABLE_SIMULATION_LOG,
    TABLE_FEATURE_SNAPS,
    TABLE_PRED_SNAPS,
    TABLE_PRED_OUTPUTS,
    TABLE_LEARNING_EVALS,
    TABLE_BACKTEST_RUNS,
    TABLE_BACKTEST_ITEMS,
    TABLE_SECTIONALS,
    TABLE_RACE_SHAPE,
})

TEST_TABLE_PREFIX = "test_"

# ─────────────────────────────────────────────────────────────────
# UPSERT CONFLICT KEYS — deterministic identity per entity type
# ─────────────────────────────────────────────────────────────────
UPSERT_KEYS = {
    TABLE_RACES:         "date,track,race_num,code",
    TABLE_RUNNERS:       "race_uid,box_num",
    TABLE_RESULTS:       "date,track,race_num,code",
    TABLE_PRED_SNAPS:    "prediction_snapshot_id",
    TABLE_RACE_SHAPE:    "race_uid",
    TABLE_PASS_LOG:      "race_uid",
    TABLE_SIGNALS:       "race_uid",
}

# ─────────────────────────────────────────────────────────────────
# ROLE CONSTANTS
# ─────────────────────────────────────────────────────────────────
ROLE_ADMIN    = "admin"
ROLE_OPERATOR = "operator"
ROLE_VIEWER   = "viewer"
VALID_ROLES   = {ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER}

# ─────────────────────────────────────────────────────────────────
# SEVERITY LEVELS
# ─────────────────────────────────────────────────────────────────
SEVERITY_INFO     = "INFO"
SEVERITY_WARN     = "WARN"
SEVERITY_ERROR    = "ERROR"
SEVERITY_CRITICAL = "CRITICAL"
VALID_SEVERITIES  = {SEVERITY_INFO, SEVERITY_WARN, SEVERITY_ERROR, SEVERITY_CRITICAL}

# ─────────────────────────────────────────────────────────────────
# CONFIG DATACLASS
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SupabaseConfig:
    """
    Immutable snapshot of environment configuration for the Supabase layer.
    Build once at startup via SupabaseConfig.from_env().
    """
    supabase_url:      str
    supabase_key:      str
    test_url:          Optional[str]
    test_key:          Optional[str]
    dp_env:            str          # "LIVE" or "TEST"
    jwt_secret:        str
    session_timeout:   int          # minutes

    @classmethod
    def from_env(cls) -> "SupabaseConfig":
        """Read configuration from environment variables."""
        url  = os.environ.get("SUPABASE_URL", "").strip()
        key  = os.environ.get("SUPABASE_KEY", "").strip()
        mode = os.environ.get("DP_ENV", "LIVE").strip().upper()

        if mode not in ("LIVE", "TEST"):
            log.warning(f"SupabaseConfig: invalid DP_ENV='{mode}', defaulting to LIVE")
            mode = "LIVE"

        if mode == "LIVE" and (not url or not key):
            log.error("SUPABASE_URL and SUPABASE_KEY must be set for LIVE mode")

        return cls(
            supabase_url    = url,
            supabase_key    = key,
            test_url        = os.environ.get("SUPABASE_TEST_URL", "").strip() or None,
            test_key        = os.environ.get("SUPABASE_TEST_KEY", "").strip() or None,
            dp_env          = mode,
            jwt_secret      = os.environ.get("JWT_SECRET", "").strip(),
            session_timeout = int(os.environ.get("SESSION_TIMEOUT_MIN", "480")),
        )

    def validate(self) -> list[str]:
        """
        Return a list of configuration errors.
        Empty list means configuration is valid for the current mode.
        """
        errors: list[str] = []
        if not self.supabase_url:
            errors.append("SUPABASE_URL is not set")
        if not self.supabase_key:
            errors.append("SUPABASE_KEY is not set")
        if not self.jwt_secret:
            errors.append("JWT_SECRET is not set (auth tokens will be insecure)")
        if self.dp_env == "TEST" and not self.test_url:
            errors.append(
                "SUPABASE_TEST_URL is not set; TEST mode will use production DB with table prefix"
            )
        return errors

    def is_live(self) -> bool:
        return self.dp_env == "LIVE"

    def is_test(self) -> bool:
        return self.dp_env == "TEST"

    def resolve_table(self, name: str) -> str:
        """Return the correct table name for the current mode."""
        if self.is_live():
            return name
        if name in ALWAYS_LIVE_TABLES:
            return name
        if name in TESTABLE_TABLES:
            return f"{TEST_TABLE_PREFIX}{name}"
        return name


# Module-level singleton loaded once from environment
_config: Optional[SupabaseConfig] = None


def get_config() -> SupabaseConfig:
    """Return the module-level SupabaseConfig singleton."""
    global _config
    if _config is None:
        _config = SupabaseConfig.from_env()
    return _config
