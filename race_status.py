"""
race_status.py - DemonPulse Race Status & NTJ Calculator
=========================================================
Manages race status transitions and calculates Next-to-Jump (NTJ) from
stored jump_time. No bookmaker or external scraping is used here.

Architecture rules:
  - NTJ is calculated internally from the stored jump_time column
  - No bookmaker NTJ scraping in the core path
  - Status transitions: upcoming → open → interim → final / abandoned

NTJ Windows (seconds before jump_time):
  IMMINENT  : 0 – 120 s  (< 2 min)
  NEAR      : 120 – 600 s  (2–10 min)
  UPCOMING  : 600+ s (> 10 min)
  PAST      : jump_time is in the past
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, date
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# Australian Eastern timezone — handles both AEST (UTC+10) and AEDT (UTC+11, DST)
_AEST = ZoneInfo("Australia/Sydney")

# NTJ threshold windows in seconds
NTJ_IMMINENT_MAX = 120      # < 2 minutes
NTJ_NEAR_MAX = 600          # < 10 minutes
NTJ_OVERLAY_TRIGGER = 600   # FormFav overlay triggered when NTJ < 10 min

# Status constants
STATUS_UPCOMING = "upcoming"
STATUS_OPEN = "open"
STATUS_INTERIM = "interim"
STATUS_FINAL = "final"
STATUS_ABANDONED = "abandoned"
STATUS_PAYING = "paying"

# Statuses that indicate a race is still live (may appear on board)
LIVE_STATUSES = {STATUS_UPCOMING, STATUS_OPEN, STATUS_INTERIM}

# Statuses that indicate a race is settled (remove from board)
SETTLED_STATUSES = {STATUS_FINAL, STATUS_PAYING, STATUS_ABANDONED}


def parse_jump_time(jump_time: str | None, race_date: str | None = None) -> datetime | None:
    """
    Parse jump_time into a timezone-aware datetime.

    jump_time may be:
      - "HH:MM" or "HH:MM:SS" (time only — assumes race_date for the date)
      - "YYYY-MM-DDTHH:MM:SS" (ISO datetime)
      - "YYYY-MM-DDTHH:MM:SSZ" or with timezone offset
    """
    if not jump_time:
        return None

    jump_time = jump_time.strip()

    # ISO datetime formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            dt = datetime.strptime(jump_time, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Time-only: "HH:MM" or "HH:MM:SS" — combine with race_date
    base_date = None
    if race_date:
        try:
            base_date = date.fromisoformat(race_date)
        except ValueError:
            pass

    if not base_date:
        base_date = date.today()

    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(jump_time, fmt).time()
            # Time-only values represent Australian local time (AEST/AEDT)
            dt = datetime.combine(base_date, t, tzinfo=_AEST)
            return dt
        except ValueError:
            continue

    log.debug(f"race_status: could not parse jump_time={jump_time!r}")
    return None


def compute_ntj(jump_time: str | None, race_date: str | None = None) -> dict[str, Any]:
    """
    Compute Next-to-Jump metadata from a stored jump_time.

    Returns dict with:
      seconds_to_jump  : int or None
      ntj_label        : "IMMINENT" | "NEAR" | "UPCOMING" | "PAST" | "UNKNOWN"
      is_near_jump     : bool (True when < NTJ_OVERLAY_TRIGGER seconds)
      jump_dt_iso      : ISO string of parsed jump datetime or None
    """
    dt = parse_jump_time(jump_time, race_date)
    if dt is None:
        return {
            "seconds_to_jump": None,
            "ntj_label": "UNKNOWN",
            "is_near_jump": False,
            "jump_dt_iso": None,
        }

    now = datetime.now(timezone.utc)
    diff = (dt - now).total_seconds()
    seconds_to_jump = int(diff)

    if seconds_to_jump < 0:
        label = "PAST"
        is_near_jump = False
    elif seconds_to_jump <= NTJ_IMMINENT_MAX:
        label = "IMMINENT"
        is_near_jump = True
    elif seconds_to_jump <= NTJ_NEAR_MAX:
        label = "NEAR"
        is_near_jump = True
    else:
        label = "UPCOMING"
        is_near_jump = False

    return {
        "seconds_to_jump": seconds_to_jump,
        "ntj_label": label,
        "is_near_jump": is_near_jump,
        "jump_dt_iso": dt.isoformat(),
    }


def should_trigger_formfav_overlay(race: dict[str, Any]) -> bool:
    """
    Return True when FormFav provisional overlay should be fetched for a race.
    Triggered when race is within NTJ_OVERLAY_TRIGGER seconds of jump.
    """
    ntj = compute_ntj(race.get("jump_time"), race.get("date"))
    secs = ntj.get("seconds_to_jump")
    if secs is None:
        return False
    return 0 <= secs <= NTJ_OVERLAY_TRIGGER


def is_race_live(race: dict[str, Any]) -> bool:
    """Return True if the race is in a live (board-eligible) status."""
    return (race.get("status") or "").lower() in LIVE_STATUSES


def is_race_settled(race: dict[str, Any]) -> bool:
    """Return True if the race is settled and should leave the board."""
    return (race.get("status") or "").lower() in SETTLED_STATUSES


def get_active_race_uids_from_db(db_client, table_name: str, target_date: str) -> list[str]:
    """
    Fetch race_uids for all active (board-eligible) races from the DB.
    Used by rolling_refresh to know which races need refreshing.
    """
    try:
        rows = (
            db_client.table(table_name)
            .select("race_uid,oddspro_race_id")
            .eq("date", target_date)
            .in_("status", list(LIVE_STATUSES))
            .execute()
            .data
        ) or []
        return [r["race_uid"] for r in rows if r.get("race_uid")]
    except Exception as e:
        log.error(f"race_status: get_active_race_uids_from_db failed: {e}")
        return []
