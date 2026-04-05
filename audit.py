"""
audit.py — DemonPulse V8 Audit Log
====================================
Routes all audit events through the canonical Supabase layer.
audit_log is always-live (never test-prefixed) per supabase_config.py.
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def log_event(user_id=None, username=None, event_type="", resource="", data=None, severity="INFO"):
    """Write an event to the audit_log table via the canonical Supabase layer."""
    try:
        from repositories.logs_repo import LogsRepo
        LogsRepo.audit(
            event_type=event_type,
            resource=resource,
            data=data,
            user_id=user_id,
            username=username,
            severity=severity,
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

