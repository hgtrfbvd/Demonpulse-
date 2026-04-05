"""
repositories/runners_repo.py — Runner data access (today_runners)
==================================================================
Single canonical access point for all runner reads/writes.

Identity rule: (race_uid, box_num) is the stable natural key.
               On upsert this prevents duplicate runners from repeated pulls.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import TABLE_RUNNERS, UPSERT_KEYS

log = logging.getLogger(__name__)

_TABLE = TABLE_RUNNERS


class RunnersRepo:
    """Repository for today_runners table."""

    # ── UPSERT ───────────────────────────────────────────────────

    @staticmethod
    def upsert(runner: dict[str, Any]) -> Optional[dict]:
        """
        Insert or update a runner record.

        Conflict key: (race_uid, box_num)

        Args:
            runner: Runner data dict. Must include race_uid and box_num.

        Returns:
            Saved record dict, or None on failure.
        """
        payload = RunnersRepo._build_payload(runner)
        if not payload:
            return None

        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(_TABLE))
                .upsert(payload, on_conflict=UPSERT_KEYS[_TABLE])
                .execute()
                .data,
            default=None,
            context="RunnersRepo.upsert",
        )
        if result:
            return result[0] if isinstance(result, list) else result
        return None

    @staticmethod
    def upsert_many(runners: list[dict[str, Any]]) -> int:
        """Upsert a list of runners. Returns count of successfully saved rows."""
        saved = 0
        for r in runners:
            if RunnersRepo.upsert(r):
                saved += 1
        return saved

    # ── READS ────────────────────────────────────────────────────

    @staticmethod
    def get_for_race(race_uid: str) -> list[dict]:
        """Fetch all runners for a race, ordered by box number."""
        return safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .select("*")
                    .eq("race_uid", race_uid)
                    .order("box_num")
                    .execute()
                    .data
            ),
            default=[],
            context="RunnersRepo.get_for_race",
        ) or []

    @staticmethod
    def get_active_for_race(race_uid: str) -> list[dict]:
        """Fetch non-scratched runners for a race."""
        return safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .select("*")
                    .eq("race_uid", race_uid)
                    .eq("scratched", False)
                    .order("box_num")
                    .execute()
                    .data
            ),
            default=[],
            context="RunnersRepo.get_active_for_race",
        ) or []

    # ── MARK SCRATCHED ───────────────────────────────────────────

    @staticmethod
    def scratch(race_uid: str, box_num: int, reason: str = "") -> bool:
        """Mark a runner as scratched."""
        result = safe_execute(
            lambda: (
                get_client()
                    .table(resolve_table(_TABLE))
                    .update({"scratched": True, "scratch_reason": reason})
                    .eq("race_uid", race_uid)
                    .eq("box_num", box_num)
                    .execute()
                    .data
            ),
            default=None,
            context="RunnersRepo.scratch",
        )
        return bool(result)

    # ── INTERNAL ─────────────────────────────────────────────────

    @staticmethod
    def _build_payload(runner: dict[str, Any]) -> Optional[dict]:
        if not runner.get("race_uid") or runner.get("box_num") is None:
            log.warning("RunnersRepo: missing race_uid or box_num")
            return None

        return {
            "race_uid":          str(runner["race_uid"]),
            "oddspro_race_id":   runner.get("oddspro_race_id", ""),
            "box_num":           int(runner["box_num"]),
            "number":            runner.get("number"),
            "barrier":           runner.get("barrier"),
            "name":              runner.get("name", ""),
            "jockey":            runner.get("jockey", ""),
            "driver":            runner.get("driver", ""),
            "trainer":           runner.get("trainer", ""),
            "owner":             runner.get("owner", ""),
            "weight":            _to_numeric(runner.get("weight")),
            "price":             _to_numeric(runner.get("price")),
            "rating":            _to_numeric(runner.get("rating")),
            "run_style":         runner.get("run_style", ""),
            "early_speed":       runner.get("early_speed", ""),
            "best_time":         runner.get("best_time", ""),
            "career":            runner.get("career", ""),
            "scratched":         bool(runner.get("scratched", False)),
            # CF-06: normalise scratch field — connectors may provide scratch_timing
            # (OddsPro/FormFav) or scratch_reason (canonical column name)
            "scratch_reason":    runner.get("scratch_reason") or runner.get("scratch_timing") or "",
            "source_confidence": runner.get("source_confidence", ""),
            "updated_at":        _now(),
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
