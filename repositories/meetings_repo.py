"""
repositories/meetings_repo.py — Race meetings access
=====================================================
Provides stable meeting-level identity across all racing codes.

A "meeting" is a (date, track, code) combination. Meeting identity is
stable so races and runners can always be linked back to their source event.

Race-code contamination prevention:
- code is always validated against VALID_RACE_CODES
- queries always filter by code when provided
- never returns GREYHOUND data when querying for HARNESS, etc.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import VALID_RACE_CODES, DEFAULT_RACE_CODE

log = logging.getLogger(__name__)

# meetings table is part of the canonical schema (may be added via schema_bootstrap)
_TABLE = "meetings"


class MeetingsRepo:
    """Repository for the meetings table."""

    # ── UPSERT ───────────────────────────────────────────────────

    @staticmethod
    def upsert(meeting: dict[str, Any]) -> Optional[dict]:
        """
        Insert or update a meeting record.

        Conflict key: (date, track, code)

        Args:
            meeting: Meeting data dict.  Must include date, track, code.

        Returns:
            Saved record dict, or None on failure.
        """
        payload = MeetingsRepo._build_payload(meeting)
        if not payload:
            return None

        saved = safe_execute(
            lambda: get_client()
                .table(resolve_table(_TABLE))
                .upsert(payload, on_conflict="date,track,code")
                .execute()
                .data,
            default=None,
            context="MeetingsRepo.upsert",
        )
        if saved:
            return saved[0] if isinstance(saved, list) else saved
        return None

    # ── READS ────────────────────────────────────────────────────

    @staticmethod
    def get_by_date(target_date: str, code: Optional[str] = None) -> list[dict]:
        """Fetch all meetings for a date, optionally filtered by racing code."""
        code = _validate_code(code)
        q = (
            get_client()
                .table(resolve_table(_TABLE))
                .select("*")
                .eq("date", target_date)
        )
        if code:
            q = q.eq("code", code)
        return safe_execute(
            lambda: q.order("track").execute().data,
            default=[],
            context="MeetingsRepo.get_by_date",
        ) or []

    @staticmethod
    def get(meeting_date: str, track: str, code: str) -> Optional[dict]:
        """Fetch a specific meeting by its natural key."""
        code = _validate_code(code) or DEFAULT_RACE_CODE
        rows = safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .select("*")
                    .eq("date", meeting_date)
                    .eq("track", track)
                    .eq("code", code)
                    .limit(1)
                    .execute()
                    .data
            ),
            default=[],
            context="MeetingsRepo.get",
        ) or []
        return rows[0] if rows else None

    # ── INTERNAL ─────────────────────────────────────────────────

    @staticmethod
    def _build_payload(meeting: dict[str, Any]) -> Optional[dict]:
        required = {"date", "track", "code"}
        if not required.issubset(meeting.keys()):
            log.warning(f"MeetingsRepo: missing required fields {required - meeting.keys()}")
            return None

        code = str(meeting["code"]).upper()
        if code not in VALID_RACE_CODES:
            log.warning(f"MeetingsRepo: invalid code '{code}', rejected")
            return None

        return {
            "date":        str(meeting["date"]),
            "track":       str(meeting["track"]),
            "code":        code,
            "state":       meeting.get("state", ""),
            "country":     meeting.get("country", "AUS"),
            "weather":     meeting.get("weather", ""),
            "rail":        meeting.get("rail", ""),
            "track_cond":  meeting.get("track_cond", ""),
            "race_count":  int(meeting.get("race_count") or 0),
            "source":      meeting.get("source", "oddspro"),
            "updated_at":  _now(),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_code(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    upper = code.upper()
    if upper not in VALID_RACE_CODES:
        log.warning(f"MeetingsRepo: ignoring invalid code filter '{code}'")
        return None
    return upper
