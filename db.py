"""
db.py - V8 Supabase connection — env-aware
All table access goes through env.table() so TEST and LIVE
never touch the same rows.
"""
import logging
from datetime import datetime, date

log = logging.getLogger(__name__)

from env import env


def get_db():
    """Return the correct Supabase client for the current mode."""
    return env.db_client()


def safe_query(fn, default=None):
    try:
        return fn()
    except Exception as e:
        log.error(f"DB query failed: {e}")
        return default


def T(name: str) -> str:
    """Resolve table name for current mode. Use everywhere instead of raw strings."""
    return env.table(name)


def get_state() -> dict:
    db = get_db()
    row = safe_query(
        lambda: db.table(T("system_state")).select("*").eq("id", 1).single().execute().data,
        {}
    )
    return row or {}


def update_state(**kwargs):
    allowed = [
        "bankroll", "current_pl", "bank_mode", "active_code",
        "posture", "sys_state", "variance", "session_type", "time_anchor"
    ]
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    if not filtered:
        return
    filtered["updated_at"] = datetime.utcnow().isoformat()
    safe_query(lambda: get_db().table(T("system_state")).update(filtered).eq("id", 1).execute())


def get_session_pl(user_id: str | None = None) -> dict:
    """
    Return today's P/L summary.
    W-01: pass user_id to scope to one user; pass None for global admin summary.
    """
    today = date.today().isoformat()
    if user_id:
        rows = safe_query(
            lambda: get_db().table(T("bet_log")).select("pl,result")
                    .eq("date", today).eq("user_id", user_id).execute().data, []
        ) or []
    else:
        rows = safe_query(
            lambda: get_db().table(T("bet_log")).select("pl,result")
                    .eq("date", today).execute().data, []
        ) or []
    return {
        "total":   round(sum(r.get("pl") or 0 for r in rows), 2),
        "bets":    len(rows),
        "wins":    sum(1 for r in rows if r.get("result") == "WIN"),
        "pending": sum(1 for r in rows if r.get("result") == "PENDING"),
    }


def list_users() -> list:
    return safe_query(
        lambda: get_db().table(T("users"))
                .select("id,username,role,active,created_at")
                .order("created_at").execute().data, []
    ) or []


def update_user(user_id: str, **kwargs):
    allowed = {"role", "active"}
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    if filtered:
        safe_query(lambda: get_db().table(T("users")).update(filtered).eq("id", user_id).execute())


def safe_delete(table_name: str, column: str, value) -> bool:
    """LIVE: raises EnvViolation. TEST: deletes from test_ table only."""
    env.guard_destructive(operation=f"DELETE FROM {table_name} WHERE {column}={value}")
    tbl = T(table_name)
    result = safe_query(lambda: get_db().table(tbl).delete().eq(column, value).execute())
    return result is not None



def get_or_create_daily_session(state: dict | None = None) -> str | None:
    """
    Return the UUID of today's betting session, creating one if it doesn't exist.
    'sessions' is in _TESTABLE_TABLES so TEST mode writes to test_sessions.
    Returns session id string or None on failure.
    """
    today = date.today().isoformat()
    try:
        existing = safe_query(
            lambda: get_db().table(T("sessions")).select("id")
                    .eq("date", today).limit(1).execute().data
        )
        if existing:
            return existing[0]["id"]
        # Create a new session for today
        st = state or get_state()
        row = safe_query(
            lambda: get_db().table(T("sessions")).insert({
                "date":          today,
                "session_type":  "Live Betting",
                "account_type":  "Standard",
                "bankroll_start": float(st.get("bankroll") or 1000),
                "bank_mode":     st.get("bank_mode", "STANDARD"),
                "active_code":   st.get("active_code", "GREYHOUND"),
                "posture":       st.get("posture", "NORMAL"),
                "created_at":    datetime.utcnow().isoformat(),
            }).execute().data
        )
        if row:
            return row[0]["id"]
    except Exception as e:
        log.warning(f"get_or_create_daily_session failed: {e}")
    return None

def safe_truncate(table_name: str) -> bool:
    """Bulk delete — TEST only."""
    env.guard_destructive(operation=f"TRUNCATE {table_name}")
    tbl = T(table_name)
    result = safe_query(lambda: get_db().table(tbl).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute())
    return result is not None
