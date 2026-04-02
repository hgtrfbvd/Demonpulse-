"""
audit.py - V8 Audit Log System
Tracks auth events, admin actions, bet ops, config changes.
Always writes to the LIVE audit_log table (never prefixed)
so test-mode actions are still traceable.
"""
import logging
from datetime import datetime
from env import env

log = logging.getLogger(__name__)

SEVERITY = {
    "LOGIN": "INFO",
    "LOGOUT": "INFO",
    "LOGIN_FAIL": "WARN",
    "TOKEN_EXPIRE": "INFO",
    "PASSWORD_CHANGE": "WARN",
    "BET_PLACED": "INFO",
    "BET_SETTLED": "INFO",
    "BET_CANCELLED": "WARN",
    "BANKROLL_RESET": "CRITICAL",
    "BANKROLL_SET": "WARN",
    "SETTINGS_UPDATE": "WARN",
    "USER_CREATE": "WARN",
    "USER_DELETE": "CRITICAL",
    "RACE_LOCK": "INFO",
    "RACE_SCRATCH": "WARN",
    "CACHE_CLEAR": "INFO",
    "SWEEP_MANUAL": "INFO",
    "LEARNING_PROMOTE": "WARN",
    "ACCESS_DENIED": "WARN",
    "ERROR": "ERROR",
    "ENV_MODE_SWITCH": "CRITICAL",
    "TEST_PURGE": "WARN",
    "STRESS_TEST_RUN": "INFO",
}

_BUFFER: list[dict] = []
MAX_BUFFER = 200


def log_event(user_id, username, event_type, resource="", data=None, ip=None, severity=None) -> dict:
    payload = {**(data or {}), "env_mode": env.mode}

    entry = {
        "user_id": str(user_id) if user_id else None,
        "username": username or "system",
        "action": event_type,
        "target": resource,
        "details": payload,
        "created_at": datetime.utcnow().isoformat(),
    }

    sev = severity or SEVERITY.get(event_type, "INFO")
    msg = f"[AUDIT/{env.mode}] {sev} {event_type} by {entry['username']} on {resource}"

    if sev == "CRITICAL":
        log.critical(msg)
    elif sev == "ERROR":
        log.error(msg)
    elif sev == "WARN":
        log.warning(msg)
    else:
        log.info(msg)

    try:
        from db import get_db, safe_query, T

        result = safe_query(
            lambda: get_db().table(T("audit_log")).insert(entry).execute()
        )
        if result is None:
            _BUFFER.append(entry)
            if len(_BUFFER) > MAX_BUFFER:
                _BUFFER.pop(0)
    except Exception as e:
        log.error(f"Audit DB write failed: {e}")
        _BUFFER.append(entry)
        if len(_BUFFER) > MAX_BUFFER:
            _BUFFER.pop(0)

    return entry


def get_recent_logs(limit=100, user_id=None, event_type=None) -> list:
    try:
        from db import get_db, safe_query, T

        q = get_db().table(T("audit_log")).select("*").order("created_at", desc=True).limit(limit)

        if user_id:
            q = q.eq("user_id", user_id)
        if event_type:
            q = q.eq("action", event_type)

        rows = safe_query(lambda: q.execute().data, []) or []

        normalized = []
        for row in rows:
            normalized.append({
                "user_id": row.get("user_id"),
                "username": row.get("username"),
                "event_type": row.get("action"),
                "resource": row.get("target"),
                "data": row.get("details") or {},
                "severity": SEVERITY.get(row.get("action"), "INFO"),
                "created_at": row.get("created_at"),
            })
        return normalized
    except Exception:
        return _BUFFER[-limit:]


def get_audit_summary() -> dict:
    try:
        from db import get_db, safe_query, T

        recent = safe_query(
            lambda: get_db().table(T("audit_log")).select("action,created_at")
            .order("created_at", desc=True).limit(500).execute().data,
            []
        ) or []

        by_type = {}
        by_sev = {"INFO": 0, "WARN": 0, "ERROR": 0, "CRITICAL": 0}

        for row in recent:
            action = row.get("action", "UNKNOWN")
            by_type[action] = by_type.get(action, 0) + 1
            sev = SEVERITY.get(action, "INFO")
            by_sev[sev] = by_sev.get(sev, 0) + 1

        return {
            "total": len(recent),
            "by_type": by_type,
            "by_severity": by_sev,
            "critical_count": by_sev.get("CRITICAL", 0),
        }
    except Exception:
        return {
            "total": len(_BUFFER),
            "by_type": {},
            "by_severity": {},
            "critical_count": 0,
        }


# Shorthand helpers
def log_login(uid, username, ip=None, success=True):
    log_event(uid, username, "LOGIN" if success else "LOGIN_FAIL", "auth", ip=ip)


def log_logout(uid, username, ip=None):
    log_event(uid, username, "LOGOUT", "auth", ip=ip)


def log_bet(uid, username, bet_data):
    log_event(uid, username, "BET_PLACED", "bet_log", data=bet_data)


def log_settle(uid, username, bet_id, result, pl):
    log_event(uid, username, "BET_SETTLED", f"bet/{bet_id}", data={"result": result, "pl": pl})


def log_settings(uid, username, changes):
    log_event(uid, username, "SETTINGS_UPDATE", "settings", data=changes)


def log_bankroll_reset(uid, username, new_amount):
    log_event(uid, username, "BANKROLL_RESET", "bankroll", data={"new_amount": new_amount})
