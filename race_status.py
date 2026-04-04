"""
race_status.py - Race status computation and timing logic.
All NTJ/near-jump logic is internal from stored jump times.
NO bookmaker or external NTJ scraping.
"""

import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

STATUS_SCHEDULED = "scheduled"
STATUS_OPEN = "open"
STATUS_NEAR_JUMP = "near_jump"
STATUS_CLOSED = "closed"
STATUS_SETTLED = "settled"
STATUS_ABANDONED = "abandoned"

NEAR_JUMP_MINUTES = 30  # within 30 min = near_jump
CLOSED_MINUTES = 5      # within 5 min or past = closed (if not settled)

_TERMINAL_STATUSES = {STATUS_SETTLED, STATUS_ABANDONED}


def parse_jump_time(jump_time_str: str | None) -> datetime | None:
    """Parse ISO jump time string to UTC datetime."""
    if not jump_time_str:
        return None
    try:
        dt = datetime.fromisoformat(str(jump_time_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError) as e:
        log.debug(f"Could not parse jump_time '{jump_time_str}': {e}")
        return None


def minutes_to_jump(race: dict) -> float | None:
    """Return minutes to jump (negative = past jump). None if no jump_time."""
    jt = parse_jump_time(race.get("jump_time"))
    if jt is None:
        return None
    now = datetime.now(timezone.utc)
    return (jt - now).total_seconds() / 60.0


def get_race_status(race: dict) -> str:
    """
    Compute current status from stored jump_time + existing status.
    If race is settled/abandoned, keep that status.
    Otherwise derive from time-to-jump.
    """
    current = (race.get("status") or STATUS_SCHEDULED).lower()

    if current in _TERMINAL_STATUSES:
        return current

    mtj = minutes_to_jump(race)
    if mtj is None:
        return current

    if mtj <= -CLOSED_MINUTES:
        return STATUS_CLOSED
    if mtj <= CLOSED_MINUTES:
        return STATUS_CLOSED
    if mtj <= NEAR_JUMP_MINUTES:
        return STATUS_NEAR_JUMP
    return STATUS_OPEN


def is_near_jump(race: dict, minutes: int = NEAR_JUMP_MINUTES) -> bool:
    """True if race is within `minutes` of jump time."""
    mtj = minutes_to_jump(race)
    if mtj is None:
        return False
    return 0 <= mtj <= minutes


def is_active(race: dict) -> bool:
    """True if race should be refreshed (not settled/abandoned/blocked)."""
    status = (race.get("status") or "").lower()
    if race.get("blocked"):
        return False
    return status not in _TERMINAL_STATUSES


def is_settled(race: dict) -> bool:
    """True if race has official result."""
    return bool(race.get("result_official")) or race.get("status") == STATUS_SETTLED


def calculate_ntj(races: list[dict]) -> dict | None:
    """
    Find the next race to jump from a list of races.
    Returns the race dict or None.
    Uses stored jump_time only. No external scraping.
    """
    now = datetime.now(timezone.utc)
    candidates = []
    for race in races:
        if race.get("blocked"):
            continue
        status = (race.get("status") or "").lower()
        if status in _TERMINAL_STATUSES:
            continue
        jt = parse_jump_time(race.get("jump_time"))
        if jt and jt > now:
            candidates.append((jt, race))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def sort_board(races: list[dict]) -> list[dict]:
    """Sort races by jump_time for board display."""
    def sort_key(r):
        jt = r.get("jump_time") or ""
        return jt

    return sorted(races, key=sort_key)
