"""
repositories/results_repo.py — Race results access (results_log)
=================================================================
Single canonical access point for official race results.

results_log stores race-level result summaries (winner, places, times).
Identity: (date, track, race_num, code) — one row per race.
Only OddsPro-confirmed results should be written here.
FormFav / provisional data must NOT flow through this repository.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import TABLE_RESULTS, VALID_RACE_CODES, DEFAULT_RACE_CODE

log = logging.getLogger(__name__)

_TABLE = TABLE_RESULTS

# Conflict key for upserts — matches schema UNIQUE constraint
_CONFLICT_KEY = "date,track,race_num,code"


class ResultsRepo:
    """Repository for results_log table."""

    # ── UPSERT ───────────────────────────────────────────────────

    @staticmethod
    def upsert(result: dict[str, Any]) -> Optional[dict]:
        """
        Insert or update a race result record.

        Conflict key: (date, track, race_num, code)
        One row per race — stores winner and places summary.

        Args:
            result: Result data dict. Must include date, track, race_num, code.

        Returns:
            Saved record dict, or None on failure.
        """
        payload = ResultsRepo._build_payload(result)
        if not payload:
            return None

        saved = safe_execute(
            lambda: get_client()
                .table(resolve_table(_TABLE))
                .upsert(payload, on_conflict=_CONFLICT_KEY)
                .execute()
                .data,
            default=None,
            context="ResultsRepo.upsert",
        )
        if saved:
            log.debug(
                f"ResultsRepo: saved result for "
                f"{payload.get('track')} R{payload.get('race_num')} "
                f"({payload.get('code')})"
            )
            return saved[0] if isinstance(saved, list) else saved
        return None

    @staticmethod
    def upsert_many(results: list[dict[str, Any]]) -> int:
        """Upsert a list of results. Returns count saved."""
        return sum(1 for r in results if ResultsRepo.upsert(r))

    # ── READS ────────────────────────────────────────────────────

    @staticmethod
    def get_for_race(race_uid: str) -> Optional[dict]:
        """Fetch the result record for a race by race_uid."""
        rows = safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .select("*")
                    .eq("race_uid", race_uid)
                    .limit(1)
                    .execute()
                    .data
            ),
            default=[],
            context="ResultsRepo.get_for_race",
        ) or []
        return rows[0] if rows else None

    @staticmethod
    def get_by_key(date: str, track: str, race_num: int, code: str) -> Optional[dict]:
        """Fetch result by natural key."""
        rows = safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .select("*")
                    .eq("date", date)
                    .eq("track", track)
                    .eq("race_num", race_num)
                    .eq("code", code)
                    .limit(1)
                    .execute()
                    .data
            ),
            default=[],
            context="ResultsRepo.get_by_key",
        ) or []
        return rows[0] if rows else None

    @staticmethod
    def get_by_date(target_date: str, code: Optional[str] = None) -> list[dict]:
        """Fetch all results for a date, optionally filtered by code."""
        q = (
            get_client()
                .table(resolve_table(_TABLE))
                .select("*")
                .eq("date", target_date)
        )
        if code:
            upper = code.upper()
            if upper in VALID_RACE_CODES:
                q = q.eq("code", upper)
        return safe_execute(
            lambda: q.order("race_num").execute().data,
            default=[],
            context="ResultsRepo.get_by_date",
        ) or []

    @staticmethod
    def has_result(race_uid: str) -> bool:
        """Return True if a result row exists for this race."""
        rows = safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .select("id")
                    .eq("race_uid", race_uid)
                    .limit(1)
                    .execute()
                    .data
            ),
            default=[],
            context="ResultsRepo.has_result",
        ) or []
        return len(rows) > 0

    # ── INTERNAL ─────────────────────────────────────────────────

    @staticmethod
    def _build_payload(result: dict[str, Any]) -> Optional[dict]:
        required = {"date", "track", "race_num", "code"}
        missing = required - result.keys()
        if missing:
            log.warning(f"ResultsRepo: missing required fields {missing}")
            return None

        code = str(result.get("code", DEFAULT_RACE_CODE)).upper()
        if code not in VALID_RACE_CODES:
            log.warning(f"ResultsRepo: invalid race code '{code}', rejected")
            return None

        return {
            "date":         str(result["date"]),
            "track":        str(result["track"]),
            "race_num":     int(result["race_num"]),
            "code":         code,
            "race_uid":     result.get("race_uid", ""),
            "winner":       result.get("winner", ""),
            "winner_box":   result.get("winner_box"),
            "win_price":    _to_numeric(result.get("win_price")),
            "place_2":      result.get("place_2", ""),
            "place_3":      result.get("place_3", ""),
            "margin":       _to_numeric(result.get("margin")),
            "winning_time": _to_numeric(result.get("winning_time")),
            "source":       result.get("source", "oddspro"),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_numeric(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

