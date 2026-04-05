"""
repositories/logs_repo.py — Logging and audit data access
==========================================================
Covers: audit_log, source_log, activity_log, simulation_log, system_logs.

audit_log is always-live (never test-prefixed).
All other log tables follow the TEST/LIVE prefix rules.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import (
    TABLE_AUDIT_LOG,
    TABLE_SOURCE_LOG,
    TABLE_ACTIVITY_LOG,
    TABLE_SIMULATION_LOG,
    VALID_SEVERITIES,
    SEVERITY_INFO,
)

log = logging.getLogger(__name__)


class LogsRepo:
    """Repository for logging and audit tables."""

    # ── AUDIT LOG ─────────────────────────────────────────────────

    @staticmethod
    def audit(
        event_type: str,
        resource: str = "",
        data: Optional[dict] = None,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        ip: str = "",
        severity: str = SEVERITY_INFO,
    ) -> None:
        """
        Write an audit log event.

        audit_log is always written to the production table (never test-prefixed).
        Never raises — audit failures are logged to Python logger only.
        """
        severity = severity if severity in VALID_SEVERITIES else SEVERITY_INFO
        try:
            get_client().table(resolve_table(TABLE_AUDIT_LOG)).insert({
                "user_id":    str(user_id) if user_id else None,
                "username":   username,
                "event_type": event_type,
                "resource":   resource,
                "data":       data or {},
                "ip":         ip,
                "severity":   severity,
                "created_at": _now(),
            }).execute()
        except Exception as exc:
            log.error(f"LogsRepo.audit: failed to write audit event '{event_type}': {exc}")

    @staticmethod
    def get_audit(limit: int = 100, event_type: Optional[str] = None) -> list[dict]:
        """Fetch recent audit log entries."""
        q = (
            get_client()
                .table(resolve_table(TABLE_AUDIT_LOG))
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
        )
        if event_type:
            q = q.eq("event_type", event_type)
        return safe_execute(
            lambda: q.execute().data,
            default=[],
            context="LogsRepo.get_audit",
        ) or []

    # ── SOURCE LOG ────────────────────────────────────────────────

    @staticmethod
    def log_source_call(
        url: str,
        method: str = "GET",
        status: str = "OK",
        rows_returned: int = 0,
        grv_detected: bool = False,
        call_num: Optional[int] = None,
    ) -> None:
        """Record an external data source HTTP call."""
        safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_SOURCE_LOG))
                .insert({
                    "url":           url,
                    "method":        method,
                    "status":        status,
                    "rows_returned": rows_returned,
                    "grv_detected":  grv_detected,
                    "call_num":      call_num,
                    "created_at":    _now(),
                })
                .execute(),
            context="LogsRepo.log_source_call",
        )

    # ── ACTIVITY LOG ──────────────────────────────────────────────

    @staticmethod
    def log_activity(
        event_type: str,
        description: str = "",
        session_id: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> None:
        """Record general application activity."""
        safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_ACTIVITY_LOG))
                .insert({
                    "event_type":  event_type,
                    "description": description,
                    "session_id":  session_id,
                    "data":        data or {},
                    "created_at":  _now(),
                })
                .execute(),
            context="LogsRepo.log_activity",
        )

    # ── SIMULATION LOG ────────────────────────────────────────────

    @staticmethod
    def save_simulation(sim: dict[str, Any]) -> Optional[dict]:
        """Persist a simulation run result."""
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_SIMULATION_LOG))
                .insert(sim)
                .execute()
                .data,
            default=None,
            context="LogsRepo.save_simulation",
        )
        return (result[0] if isinstance(result, list) else result) if result else None

    @staticmethod
    def get_simulations(race_uid: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Fetch recent simulation runs, optionally filtered by race."""
        q = (
            get_client()
                .table(resolve_table(TABLE_SIMULATION_LOG))
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
        )
        if race_uid:
            q = q.eq("race_uid", race_uid)
        return safe_execute(
            lambda: q.execute().data,
            default=[],
            context="LogsRepo.get_simulations",
        ) or []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
