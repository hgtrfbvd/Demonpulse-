"""
board_service.py
================
Builds the live race board from stored data.
Sorts by time, marks past/upcoming/imminent.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from database import get_races_for_date, get_runners_for_race

log = logging.getLogger(__name__)

AEST = ZoneInfo("Australia/Sydney")

# NTJ windows in seconds
_IMMINENT_SECS = 120
_NEAR_SECS = 600


def get_board_for_today(target_date: str | None = None) -> dict:
    """
    Build the live race board for today (or target_date).

    Returns:
        {
            "ok": True,
            "items": [...],
            "count": N,
            "date": "YYYY-MM-DD"
        }
    """
    today = target_date or datetime.now(AEST).date().isoformat()
    try:
        races = get_races_for_date(today)
        board = []
        now_mins = _now_minutes()

        for race in races:
            # Skip hard-blocked races
            if (race.get("status") or "") in {"abandoned", "invalid", "blocked", "cancelled"}:
                continue

            runners = get_runners_for_race(race["race_uid"])
            race_mins = _time_to_minutes(race.get("jump_time") or race.get("race_time"))
            seconds_to_jump = (race_mins - now_mins) * 60 if race_mins is not None else None

            board.append({
                **race,
                "runners": runners,
                "seconds_to_jump": seconds_to_jump,
                "ntj_label": _ntj_label(seconds_to_jump),
            })

        board.sort(key=lambda x: x.get("seconds_to_jump") if x.get("seconds_to_jump") is not None else 999999)
        return {"ok": True, "items": board, "count": len(board), "date": today}
    except Exception as e:
        log.error(f"board_service.get_board_for_today failed: {e}")
        return {"ok": False, "items": [], "count": 0, "date": today, "error": str(e)}


def _now_minutes() -> float:
    """Return current time as minutes since midnight AEST."""
    now = datetime.now(AEST)
    return now.hour * 60 + now.minute + now.second / 60


def _time_to_minutes(time_str: str | None) -> float | None:
    """
    Parse a time string to minutes since midnight.
    Accepts HH:MM, HH:MM:SS, or ISO datetime strings.
    """
    if not time_str:
        return None
    try:
        # ISO datetime string (e.g. 2026-04-10T14:02:00+10:00)
        if "T" in str(time_str):
            dt = datetime.fromisoformat(str(time_str))
            dt_aest = dt.astimezone(AEST)
            return dt_aest.hour * 60 + dt_aest.minute + dt_aest.second / 60
        # HH:MM or HH:MM:SS
        parts = str(time_str).split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * 60 + m + s / 60
    except Exception:
        return None


def _ntj_label(seconds_to_jump: float | None) -> str:
    """Return a human-readable NTJ label."""
    if seconds_to_jump is None:
        return "UNKNOWN"
    if seconds_to_jump < 0:
        return "PAST"
    if seconds_to_jump <= _IMMINENT_SECS:
        return "IMMINENT"
    if seconds_to_jump <= _NEAR_SECS:
        return "NEAR"
    return "UPCOMING"
