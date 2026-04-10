"""
audit.py — DemonPulse V9 Audit Log
====================================
Routes all audit events through the canonical db.py layer.
audit_log is always-live (never test-prefixed) per env.py.
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_VALID_SEVERITIES = {"INFO", "WARN", "ERROR", "CRITICAL"}


def log_event(user_id=None, username=None, event_type="", resource="", data=None, severity="INFO"):
    """Write an event to the audit_log table via db.py."""
    try:
        from db import get_db, safe_query, T
        if severity not in _VALID_SEVERITIES:
            log.warning(f"[AUDIT] Unknown severity '{severity}'; normalising to INFO")
            severity = "INFO"
        safe_query(
            lambda: get_db()
            .table(T("audit_log"))
            .insert({
                "user_id":    str(user_id) if user_id else None,
                "username":   username,
                "event_type": event_type,
                "resource":   resource,
                "data":       data or {},
                "ip":         None,
                "severity":   severity,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            .execute()
        )
    except Exception as exc:
        log.error(f"[AUDIT ERROR] {exc}")



def log_action(user_id=None, username=None, action="", target="", details=None):
    log_event(
        user_id=user_id,
        username=username,
        event_type=action,
        resource=target,
        data=details,
    )

