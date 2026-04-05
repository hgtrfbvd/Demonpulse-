"""
supabase_client.py — DemonPulse V8 Canonical Supabase Client
=============================================================
Single entry point for obtaining a Supabase client instance.

All other modules must import from here (or from repositories/*).
No module outside this file should call supabase.create_client() directly.

Design:
- Delegates to env.py for mode-aware client selection (maintains
  existing TEST/LIVE separation logic).
- Provides get_client() for direct access when needed.
- Provides safe_execute() for uniform error handling.
- Exposes resolve_table() for callers that need the correct table name.

Usage:
    from supabase_client import get_client, safe_execute, resolve_table

    db = get_client()
    rows = safe_execute(
        lambda: db.table(resolve_table("today_races")).select("*").execute().data,
        default=[]
    )
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# ─────────────────────────────────────────────────────────────────
# CLIENT ACCESS
# ─────────────────────────────────────────────────────────────────

def get_client():
    """
    Return the correct Supabase client for the current runtime mode.

    - LIVE  → SUPABASE_URL / SUPABASE_KEY
    - TEST  → SUPABASE_TEST_URL / SUPABASE_TEST_KEY  (or shared DB with prefix)

    Raises RuntimeError if required environment variables are not set.
    """
    from env import env
    return env.db_client()


# ─────────────────────────────────────────────────────────────────
# TABLE RESOLUTION
# ─────────────────────────────────────────────────────────────────

def resolve_table(name: str) -> str:
    """
    Return the correct table name for the current runtime mode.

    Delegates to env.table() to honour TEST/LIVE separation rules
    and the always-live table list.

    Example:
        resolve_table("today_races")  → "today_races"   (LIVE)
                                      → "test_today_races" (TEST)
        resolve_table("users")        → "users"   (always)
        resolve_table("audit_log")    → "audit_log" (always)
    """
    from env import env
    return env.table(name)


# ─────────────────────────────────────────────────────────────────
# SAFE EXECUTION WRAPPER
# ─────────────────────────────────────────────────────────────────

def safe_execute(fn: Callable[[], T], default: T = None, context: str = "") -> T:
    """
    Execute a Supabase query function, returning *default* on failure.

    All errors are logged with context so nothing is silently swallowed.

    Args:
        fn:      Zero-argument callable that performs a Supabase query.
        default: Value returned if fn() raises an exception.
        context: Optional label for log messages (e.g. "races_repo.upsert").

    Returns:
        The return value of fn(), or *default* on any exception.
    """
    try:
        return fn()
    except Exception as exc:
        label = f"[{context}] " if context else ""
        log.error(f"{label}Supabase query failed: {exc}")
        return default


# ─────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────

def health_check() -> dict:
    """
    Probe the Supabase connection and return a status dict.

    Returns:
        {"ok": bool, "mode": str, "error": str | None}
    """
    from env import env
    result: dict[str, Any] = {"ok": False, "mode": env.mode, "error": None}
    try:
        db = get_client()
        # Lightweight probe: read the system_state row (always exists)
        data = db.table(resolve_table("system_state")).select("id").limit(1).execute().data
        result["ok"] = data is not None
    except Exception as exc:
        result["error"] = str(exc)
        log.warning(f"Supabase health check failed: {exc}")
    return result
