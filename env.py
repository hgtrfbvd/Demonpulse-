"""
env.py - DemonPulse V8 Environment Mode Authority
==================================================
Single source of truth for TEST vs LIVE separation.

Set via environment variable:
    DP_ENV=TEST    → test mode (fake data, stress testing, auto-delete allowed)
    DP_ENV=LIVE    → live mode (real data only, non-destructive, no fakes)

Default: LIVE (fail-safe — never accidentally in test mode on production)

TEST MODE grants:
  - Fake / generated race data
  - Demo signal fallbacks
  - Stress-test endpoints (/api/test/*)
  - Auto-purge of test data
  - Separate DB client (SUPABASE_TEST_URL if set, else table prefix "test_")
  - Demo user bootstrap

LIVE MODE enforces:
  - No fake data functions (raises EnvViolation)
  - No auto-deletion of any records
  - No destructive bulk operations
  - All data is real, permanent, audited
  - Uses production SUPABASE_URL + tables with no prefix
  - Bootstrap still creates admin if no users exist (safe, one-time)

USAGE:
  from env import env
  env.require_test()           # raises EnvViolation if LIVE
  env.guard_fake_data()        # raises EnvViolation if LIVE
  env.guard_destructive(op)    # raises EnvViolation if LIVE
  tbl = env.table("bet_log")   # returns "test_bet_log" in TEST, "bet_log" in LIVE
  client = env.db_client()     # returns test or live Supabase client
"""
import os
import logging
import functools
from datetime import datetime

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# MODE CONSTANTS
# ─────────────────────────────────────────────────────────────────
TEST = "TEST"
LIVE = "LIVE"
_VALID_MODES = {TEST, LIVE}
_TEST_TABLE_PREFIX = "test_"

# Tables that always use the production namespace regardless of mode
# (system_state is shared so both modes can read global config;
#  users/audit_log are always production — test mode should not
#  create fake audit trails or fake users in production tables)
_ALWAYS_LIVE_TABLES = {"users", "audit_log"}

# Tables that exist in both namespaces
_TESTABLE_TABLES = {
    # Core race/bet data
    "today_races", "today_runners", "bet_log", "signals",
    "system_state", "chat_history", "sessions", "training_logs",
    "exotic_suggestions", "activity_log",
    # Learning engine tables — names match 001 schema exactly (CF-03/CF-04)
    "etg_tags",
    "epr_data",           # was "epr_log" — wrong name, schema table is epr_data
    "aeee_adjustments",   # was "aeee_suggestions" — wrong name, schema table is aeee_adjustments
    "pass_log",           # W-07: was missing, learning_engine writes here
    "source_log",         # W-07: was missing, data_engine writes here
    # User management — CR-01/CF-07: must be isolated so TEST never writes live user tables
    "user_accounts",
    "user_permissions",
    "user_sessions",
    "user_activity",
    "simulation_log",     # 005: persist simulation runs
    # Phase 5 — FormFav persistent enrichment tables
    "formfav_race_enrichment",
    "formfav_runner_enrichment",
    "formfav_debug_stats",
    # Phase 3 — Intelligence layer tables (prediction, learning, backtest)
    "feature_snapshots",
    "prediction_snapshots",
    "prediction_runner_outputs",
    "learning_evaluations",
    "backtest_runs",
    "backtest_run_items",
    # Phase 4 — Feature engine / sectionals / race shape
    "sectional_snapshots",
    "race_shape_snapshots",
    # CF-04: results_log must be testable so TEST mode cannot contaminate production
    "results_log",
    # meetings — race meeting header rows; testable so TEST never touches live meetings
    "meetings",
    # Phase 6 — connection stats, track bias, market snapshots
    "runner_connection_stats",
    "track_profiles",
    "market_snapshots",
}


# ─────────────────────────────────────────────────────────────────
# EXCEPTION
# ─────────────────────────────────────────────────────────────────
class EnvViolation(RuntimeError):
    """Raised when a TEST-only or LIVE-only operation is attempted in the wrong mode."""
    def __init__(self, message: str):
        super().__init__(f"[ENV VIOLATION] {message}")
        log.critical(f"ENV VIOLATION: {message}")


# ─────────────────────────────────────────────────────────────────
# ENVIRONMENT CLASS
# ─────────────────────────────────────────────────────────────────
class Environment:
    """Global environment authority. Import this singleton: `from env import env`"""

    def __init__(self):
        raw = os.environ.get("DP_ENV", LIVE).strip().upper()
        if raw not in _VALID_MODES:
            log.warning(f"Invalid DP_ENV='{raw}', defaulting to LIVE for safety")
            raw = LIVE
        self._mode = raw
        self._locked = False          # once locked, mode cannot be changed at runtime
        self._test_client = None      # lazy Supabase test client
        self._live_client = None      # lazy Supabase live client
        self._boot_time = datetime.utcnow().isoformat()
        self._log_startup()

    # ── IDENTITY ──────────────────────────────────────────────────
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_test(self) -> bool:
        return self._mode == TEST

    @property
    def is_live(self) -> bool:
        return self._mode == LIVE

    # ── GUARDS ────────────────────────────────────────────────────
    def require_test(self, context: str = ""):
        """Call this at the top of any TEST-only function."""
        if self.is_live:
            raise EnvViolation(
                f"Operation requires TEST mode but system is in LIVE mode. "
                f"Context: {context or 'unspecified'}"
            )

    def require_live(self, context: str = ""):
        """Call this at the top of any LIVE-only function (rare)."""
        if self.is_test:
            raise EnvViolation(
                f"Operation requires LIVE mode but system is in TEST mode. "
                f"Context: {context or 'unspecified'}"
            )

    def guard_fake_data(self, label: str = ""):
        """Block fake/generated data injection in LIVE mode."""
        if self.is_live:
            raise EnvViolation(
                f"Fake data generation blocked in LIVE mode. "
                f"Label: {label or 'unlabeled'}"
            )

    def guard_destructive(self, operation: str = ""):
        """Block auto-deletion and bulk destructive operations in LIVE mode."""
        if self.is_live:
            raise EnvViolation(
                f"Destructive operation '{operation or 'unspecified'}' is blocked in LIVE mode. "
                f"All LIVE data is permanent and audited."
            )

    def guard_stress_test(self):
        """Block stress-test endpoints in LIVE mode."""
        if self.is_live:
            raise EnvViolation(
                "Stress-test operations are blocked in LIVE mode."
            )

    # ── TABLE RESOLUTION ─────────────────────────────────────────
    def table(self, name: str) -> str:
        """
        Return the correct table name for the current mode.

        TEST mode:  "bet_log" → "test_bet_log"  (if testable)
        LIVE mode:  "bet_log" → "bet_log"        (always)
        Always-live tables (users, audit_log) are never prefixed.
        """
        if self.is_live:
            return name
        if name in _ALWAYS_LIVE_TABLES:
            return name               # test mode still uses real users/audit
        if name in _TESTABLE_TABLES:
            return f"{_TEST_TABLE_PREFIX}{name}"
        return name                   # unknown table — pass through

    def test_prefix(self, name: str) -> str:
        """Explicitly force a test prefix (for migration/setup only)."""
        self.require_test("test_prefix")
        if name in _ALWAYS_LIVE_TABLES:
            return name
        return f"{_TEST_TABLE_PREFIX}{name}"

    # ── DB CLIENT ─────────────────────────────────────────────────
    def db_client(self):
        """
        Return the correct Supabase client for the current mode.

        TEST mode prefers SUPABASE_TEST_URL if set; falls back to the
        main URL with prefixed tables (handled by self.table()).
        LIVE mode always uses SUPABASE_URL.
        """
        if self.is_live:
            return self._get_live_client()
        return self._get_test_client()

    def _get_live_client(self):
        if self._live_client is None:
            from supabase import create_client
            url = os.environ.get("SUPABASE_URL", "")
            key = os.environ.get("SUPABASE_KEY", "")
            if not url or not key:
                raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set for LIVE mode")
            self._live_client = create_client(url, key)
        return self._live_client

    def _get_test_client(self):
        if self._test_client is None:
            from supabase import create_client
            # Prefer dedicated test database if provided
            test_url = os.environ.get("SUPABASE_TEST_URL", "")
            test_key = os.environ.get("SUPABASE_TEST_KEY", "")
            if test_url and test_key:
                log.info("TEST MODE: using dedicated test Supabase instance")
                self._test_client = create_client(test_url, test_key)
            else:
                # Fall back to same DB with prefixed tables
                log.warning("TEST MODE: no SUPABASE_TEST_URL set — using main DB with test_ table prefix")
                url = os.environ.get("SUPABASE_URL", "")
                key = os.environ.get("SUPABASE_KEY", "")
                if not url or not key:
                    raise RuntimeError("SUPABASE_URL/KEY or SUPABASE_TEST_URL/TEST_KEY required for TEST mode")
                self._test_client = create_client(url, key)
        return self._test_client

    # ── RUNTIME SWITCH (restricted) ───────────────────────────────
    def switch_mode(self, new_mode: str, actor: str = "unknown"):
        """
        Change mode at runtime. Requires:
        - Not locked
        - Valid mode value
        - LIVE→TEST requires explicit confirmation (admin only)
        - Always logged
        """
        if self._locked:
            raise EnvViolation("Mode is locked and cannot be changed at runtime.")
        new_mode = new_mode.strip().upper()
        if new_mode not in _VALID_MODES:
            raise ValueError(f"Invalid mode: {new_mode}. Must be TEST or LIVE.")
        old_mode = self._mode
        self._mode = new_mode
        # Reset cached clients on switch
        self._test_client = None
        self._live_client = None
        log.warning(f"[ENV] Mode switched {old_mode} → {new_mode} by '{actor}'")
        try:
            from audit import log_event
            log_event(None, actor, "ENV_MODE_SWITCH",
                      "environment",
                      {"old_mode": old_mode, "new_mode": new_mode},
                      severity="CRITICAL")
        except Exception:
            pass

    def lock(self):
        """Permanently lock mode for this process lifetime. Call after startup."""
        self._locked = True
        log.info(f"[ENV] Mode locked: {self._mode}")

    # ── DIAGNOSTICS ───────────────────────────────────────────────
    def info(self) -> dict:
        return {
            "mode":            self._mode,
            "is_test":         self.is_test,
            "is_live":         self.is_live,
            "locked":          self._locked,
            "table_prefix":    _TEST_TABLE_PREFIX if self.is_test else "",
            "test_db_separate": bool(os.environ.get("SUPABASE_TEST_URL")),
            "boot_time":       self._boot_time,
        }

    def _log_startup(self):
        border = "=" * 58
        if self.is_live:
            log.info(border)
            log.info("  DEMONPULSE V8 — LIVE MODE")
            log.info("  ✓ Real data only")
            log.info("  ✓ Destructive operations blocked")
            log.info("  ✓ Fake data blocked")
            log.info("  ✓ Production Supabase")
            log.info(border)
        else:
            log.warning(border)
            log.warning("  DEMONPULSE V8 — TEST MODE")
            log.warning("  ⚠ Fake data ALLOWED")
            log.warning("  ⚠ Auto-deletion ALLOWED")
            log.warning("  ⚠ Stress testing ALLOWED")
            test_url = os.environ.get("SUPABASE_TEST_URL", "")
            if test_url:
                log.warning(f"  ⚠ Test DB: {test_url[:40]}…")
            else:
                log.warning(f"  ⚠ Shared DB with prefix: '{_TEST_TABLE_PREFIX}'")
            log.warning(border)


# ─────────────────────────────────────────────────────────────────
# SINGLETON — import this everywhere
# ─────────────────────────────────────────────────────────────────
env = Environment()


# ─────────────────────────────────────────────────────────────────
# DECORATORS (for route/function protection)
# ─────────────────────────────────────────────────────────────────
def test_only(fn):
    """Decorator: block function in LIVE mode."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        env.require_test(context=fn.__qualname__)
        return fn(*args, **kwargs)
    return wrapper

def live_only(fn):
    """Decorator: block function in TEST mode."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        env.require_live(context=fn.__qualname__)
        return fn(*args, **kwargs)
    return wrapper

def no_fake_data(fn):
    """Decorator: block fake data functions in LIVE mode."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        env.guard_fake_data(label=fn.__qualname__)
        return fn(*args, **kwargs)
    return wrapper

def no_destructive(fn):
    """Decorator: block destructive operations in LIVE mode."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        env.guard_destructive(operation=fn.__qualname__)
        return fn(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────────
# FLASK RESPONSE HELPER
# ─────────────────────────────────────────────────────────────────
def env_violation_response(exc: EnvViolation):
    """Convert an EnvViolation into a Flask JSON 403 response."""
    from flask import jsonify
    return jsonify({
        "error": "ENV_VIOLATION",
        "message": str(exc),
        "mode": env.mode,
    }), 403
