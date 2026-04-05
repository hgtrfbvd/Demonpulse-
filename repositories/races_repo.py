"""
repositories/races_repo.py — Race data access (today_races)
============================================================
Single canonical access point for all race reads/writes.

Identity rule: (date, track, race_num, code) is the stable natural key.
               race_uid is a derived display key, not the conflict key.

All writes are upserts — safe to call repeatedly as OddsPro pulls refresh.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import (
    TABLE_RACES,
    VALID_RACE_CODES,
    DEFAULT_RACE_CODE,
    UPSERT_KEYS,
)

log = logging.getLogger(__name__)

_TABLE = TABLE_RACES


class RacesRepo:
    """Repository for today_races table."""

    # ── UPSERT ───────────────────────────────────────────────────

    @staticmethod
    def upsert(race: dict[str, Any]) -> Optional[dict]:
        """
        Insert or update a race record.

        Conflict key: (date, track, race_num, code)

        Args:
            race: Race data dict. Must include date, track, race_num, code.

        Returns:
            Saved record dict, or None on failure.
        """
        payload = RacesRepo._build_payload(race)
        if not payload:
            return None

        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(_TABLE))
                .upsert(payload, on_conflict=UPSERT_KEYS[_TABLE])
                .execute()
                .data,
            default=None,
            context="RacesRepo.upsert",
        )
        if result:
            log.debug(f"RacesRepo: upserted race {payload.get('race_uid')}")
            return result[0] if isinstance(result, list) else result
        return None

    # ── READS ────────────────────────────────────────────────────

    @staticmethod
    def get_by_uid(race_uid: str) -> Optional[dict]:
        """Fetch a race by race_uid."""
        return safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .select("*")
                    .eq("race_uid", race_uid)
                    .limit(1)
                    .execute()
                    .data or [None]
            )[0],
            default=None,
            context="RacesRepo.get_by_uid",
        )

    @staticmethod
    def get_by_date(target_date: str, code: Optional[str] = None) -> list[dict]:
        """Fetch all races for a date, optionally filtered by race code."""
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
            lambda: q.order("jump_time").execute().data,
            default=[],
            context="RacesRepo.get_by_date",
        ) or []

    @staticmethod
    def get_active(target_date: str) -> list[dict]:
        """Fetch upcoming/open races for a date."""
        return safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .select("*")
                    .eq("date", target_date)
                    .in_("status", ["upcoming", "open"])
                    .order("jump_time")
                    .execute()
                    .data
            ),
            default=[],
            context="RacesRepo.get_active",
        ) or []

    @staticmethod
    def get_by_oddspro_id(oddspro_race_id: str) -> Optional[dict]:
        """Fetch a race by OddsPro native ID."""
        return safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .select("*")
                    .eq("oddspro_race_id", oddspro_race_id)
                    .limit(1)
                    .execute()
                    .data or [None]
            )[0],
            default=None,
            context="RacesRepo.get_by_oddspro_id",
        )

    # ── UPDATE LIFECYCLE ─────────────────────────────────────────

    @staticmethod
    def update_status(race_uid: str, status: str, **extra) -> bool:
        """Update race status (and optional extra fields)."""
        payload: dict = {"status": status, "updated_at": _now()}
        payload.update(extra)
        result = safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .update(payload)
                    .eq("race_uid", race_uid)
                    .execute()
                    .data
            ),
            default=None,
            context="RacesRepo.update_status",
        )
        return bool(result)

    @staticmethod
    def mark_result_captured(race_uid: str) -> bool:
        return RacesRepo.update_status(
            race_uid, "result_posted",
            result_captured_at=_now(),
        )

    @staticmethod
    def mark_learned(race_uid: str) -> bool:
        return RacesRepo.update_status(
            race_uid, "result_posted",
            learned_at=_now(),
        )

    # ── INTERNAL ─────────────────────────────────────────────────

    @staticmethod
    def _build_payload(race: dict[str, Any]) -> Optional[dict]:
        required = {"date", "track", "race_num", "code"}
        missing = required - race.keys()
        if missing:
            log.warning(f"RacesRepo: missing required fields {missing}")
            return None

        code = str(race.get("code", DEFAULT_RACE_CODE)).upper()
        if code not in VALID_RACE_CODES:
            log.warning(f"RacesRepo: invalid race code '{code}', rejected")
            return None

        return {
            "race_uid":           race.get("race_uid", ""),
            "oddspro_race_id":    race.get("oddspro_race_id", ""),
            "date":               str(race["date"]),
            "track":              str(race["track"]),
            "state":              race.get("state", ""),
            "race_num":           int(race["race_num"]),
            "code":               code,
            "distance":           race.get("distance", ""),
            "grade":              race.get("grade", ""),
            "jump_time":          race.get("jump_time", ""),
            "prize_money":        race.get("prize_money", ""),
            "race_name":          race.get("race_name", ""),
            "condition":          race.get("condition", ""),
            "status":             race.get("status", "upcoming"),
            "block_code":         race.get("block_code", ""),
            "source":             race.get("source", "oddspro"),
            "source_url":         race.get("source_url", ""),
            "time_status":        race.get("time_status", "PARTIAL"),
            "completeness_score": int(race.get("completeness_score") or 0),
            "completeness_quality": race.get("completeness_quality", "LOW"),
            "race_hash":          race.get("race_hash", ""),
            "lifecycle_state":    race.get("lifecycle_state", "fetched"),
            "updated_at":         _now(),
        }


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_code(code: Optional[str]) -> Optional[str]:
    if code is None:
        return None
    upper = code.upper()
    if upper not in VALID_RACE_CODES:
        log.warning(f"RacesRepo: ignoring invalid race code filter '{code}'")
        return None
    return upper
