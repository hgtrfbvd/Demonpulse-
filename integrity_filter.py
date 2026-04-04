"""
integrity_filter.py - Hard gate. Never allow bad data to reach the board.
All block reasons are explicit codes.
"""

import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

MIN_RUNNERS = 3
MAX_DATA_AGE_MINUTES = 60


class BlockCode:
    NO_JUMP_TIME = "NO_JUMP_TIME"
    NO_RUNNERS = "NO_RUNNERS"
    STALE_DATA = "STALE_DATA"
    INSUFFICIENT_RUNNERS = "INSUFFICIENT_RUNNERS"
    RACE_ABANDONED = "RACE_ABANDONED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MISSING_MEETING = "MISSING_MEETING"


def check_race_integrity(
    race: dict,
    runners: list[dict],
    fetched_at: str | None = None,
) -> tuple[bool, str | None]:
    """
    Returns (passes, block_reason_or_None).
    Hard blocks: no jump time, no runners, < MIN_RUNNERS, abandoned, data too stale.
    """
    status = (race.get("status") or "").lower()

    if status == "abandoned":
        return False, BlockCode.RACE_ABANDONED

    if not race.get("jump_time"):
        return False, BlockCode.NO_JUMP_TIME

    if not runners:
        return False, BlockCode.NO_RUNNERS

    active_runners = [r for r in runners if not r.get("scratched")]
    if len(active_runners) < MIN_RUNNERS:
        return False, BlockCode.INSUFFICIENT_RUNNERS

    if fetched_at:
        try:
            now = datetime.now(timezone.utc)
            fa = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
            if fa.tzinfo is None:
                fa = fa.replace(tzinfo=timezone.utc)
            age_minutes = (now - fa).total_seconds() / 60
            if age_minutes > MAX_DATA_AGE_MINUTES:
                return False, BlockCode.STALE_DATA
        except Exception:
            pass

    return True, None


def filter_board_races(
    races: list[dict],
    runners_by_race: dict,
) -> tuple[list[dict], list[dict]]:
    """
    Filter races for board display.
    Returns (valid_races, blocked_races_with_reasons).
    """
    valid = []
    blocked = []

    for race in races:
        if race.get("blocked"):
            blocked.append({**race, "_block_reason": race.get("block_reason", BlockCode.VALIDATION_FAILED)})
            continue

        race_id = race.get("race_id", "")
        runners = runners_by_race.get(race_id, [])
        fetched_at = race.get("fetched_at")

        passes, reason = check_race_integrity(race, runners, fetched_at)
        if passes:
            valid.append(race)
        else:
            blocked.append({**race, "_block_reason": reason})

    return valid, blocked
