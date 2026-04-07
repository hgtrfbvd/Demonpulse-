"""
race_status.py - DemonPulse Race Status & NTJ Calculator
=========================================================
Manages race status transitions and calculates Next-to-Jump (NTJ) from
stored jump_time. No bookmaker or external scraping is used here.

Architecture rules:
  - NTJ is calculated internally from the stored jump_time column
  - No bookmaker NTJ scraping in the core path
  - Status transitions driven by stored data and authoritative OddsPro updates

NTJ Windows (seconds before jump_time):
  IMMINENT  : 0 – 120 s  (< 2 min)
  NEAR      : 120 – 600 s  (2–10 min)
  UPCOMING  : 600+ s (> 10 min)
  PAST      : jump_time is in the past

Phase 2 Race Status Machine:
  upcoming           → standard pre-race state
  near_jump          → < NTJ_NEAR_MAX seconds from jump (FormFav overlay eligible)
  open               → OddsPro-confirmed open/active
  interim            → interim result
  jumped_estimated   → jump_time passed, no OddsPro result yet (estimated)
  awaiting_result    → jump_time passed > 30 min ago, no result (waiting)
  result_posted      → OddsPro result confirmed and written
  final              → OddsPro terminal state
  paying             → OddsPro paying dividends state
  abandoned          → OddsPro abandoned
  blocked            → hard-blocked by integrity filter
  stale_unknown      → no jump_time and data is stale
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

# Seconds past jump_time before a race is considered awaiting_result
_AWAITING_RESULT_THRESHOLD = 1800  # 30 minutes

# Seconds past jump_time before a race is considered expired and removed from
# the live board (applies when no authoritative settled status has been received).
# Configurable grace period: 30 minutes matches _AWAITING_RESULT_THRESHOLD.
BOARD_EXPIRED_GRACE_SECS = 3600  # remove from board 60 min after jump

# Status constants — Phase 1 originals
STATUS_UPCOMING = "upcoming"
STATUS_OPEN = "open"
STATUS_INTERIM = "interim"
STATUS_FINAL = "final"
STATUS_ABANDONED = "abandoned"
STATUS_PAYING = "paying"

# Status constants — Phase 2 additions
STATUS_NEAR_JUMP = "near_jump"              # < 10 min to jump, overlay eligible
STATUS_JUMPED_ESTIMATED = "jumped_estimated"  # jump_time passed, no result yet
STATUS_AWAITING_RESULT = "awaiting_result"   # 30+ min past jump, still no result
STATUS_RESULT_POSTED = "result_posted"       # OddsPro result confirmed and written
STATUS_BLOCKED = "blocked"                   # hard-blocked by integrity filter
STATUS_STALE_UNKNOWN = "stale_unknown"       # no/unparseable jump_time, data stale
STATUS_INTERNATIONAL = "international"       # stored but excluded from betting pipeline

# Statuses that indicate a race is still live (may appear on board)
LIVE_STATUSES = {
    STATUS_UPCOMING,
    STATUS_OPEN,
    STATUS_INTERIM,
    STATUS_NEAR_JUMP,
    STATUS_JUMPED_ESTIMATED,
    STATUS_AWAITING_RESULT,
}

# Statuses that indicate a race is settled (remove from board)
SETTLED_STATUSES = {STATUS_FINAL, STATUS_PAYING, STATUS_ABANDONED, STATUS_RESULT_POSTED}

# Authoritative terminal states — result-confirmed races must never be downgraded
# by time-based logic. Always return immediately if a race holds one of these states.
FINAL_STATES = {STATUS_FINAL, STATUS_PAYING, STATUS_ABANDONED}

# All statuses known to Phase 2
ALL_STATUSES = LIVE_STATUSES | SETTLED_STATUSES | {STATUS_BLOCKED, STATUS_STALE_UNKNOWN, STATUS_INTERNATIONAL}


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
    # Try Python's built-in fromisoformat first — handles microseconds, offsets, etc.
    try:
        dt = datetime.fromisoformat(jump_time.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        pass

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


def is_race_expired_by_time(
    race: dict[str, Any],
    grace_secs: int = BOARD_EXPIRED_GRACE_SECS,
) -> bool:
    """
    Return True when the race's jump time has passed by more than *grace_secs*
    and no authoritative settled status has been received yet.

    This handles the case where the DB status column is stale (still "upcoming"
    or "open") even though the race jumped long ago.  Races already in a
    SETTLED_STATUSES state are never considered expired by this function —
    their status is authoritative.

    Args:
        race:       Race dict with at least "jump_time", "date", and "status".
        grace_secs: Seconds after jump before expiry kicks in (default 30 min).
    """
    current = (race.get("status") or "").lower()
    # Already officially settled — not "expired by time"
    if current in SETTLED_STATUSES:
        return False

    ntj = compute_ntj(race.get("jump_time"), race.get("date"))
    secs = ntj.get("seconds_to_jump")
    if secs is None:
        return False

    return secs < -grace_secs


def is_invalid_jump_time(jump_time: str | None, race_date: str | None = None) -> bool:
    """
    Return True if *jump_time* is missing, unparseable, or appears to be a
    date-only/midnight fallback (time component is exactly 00:00:00).

    Such values must not be used as the NEXT-UP race or for live-board ordering
    because they represent a data quality issue rather than a real scheduled time.

    Note: a race whose midnight timestamp is already in the past will also be
    caught by is_race_expired_by_time(), providing defence-in-depth.
    """
    if not jump_time:
        return True

    dt = parse_jump_time(jump_time, race_date)
    if dt is None:
        return True

    # Convert to local AEST/AEDT for the time-of-day check
    dt_local = dt.astimezone(_AEST)
    return dt_local.hour == 0 and dt_local.minute == 0 and dt_local.second == 0


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


# ---------------------------------------------------------------------------
# PHASE 2 — AUTOMATED STATE MACHINE
# ---------------------------------------------------------------------------

def compute_race_status(race: dict[str, Any]) -> str:
    """
    Compute the appropriate race status from stored data and current time.

    Drives automated status transitions without external scraping.
    OddsPro-authoritative terminal states (final, paying, abandoned) are
    always preserved. Transitions for non-terminal races are derived from
    stored jump_time.

    Transition logic:
      blocked           → preserved (never changed here)
      stale_unknown     → re-evaluated from jump_time
      settled states    → preserved (final/paying/abandoned/result_posted)
      no jump_time      → stale_unknown
      secs > NTJ_NEAR   → upcoming or open (from OddsPro)
      0 < secs <= NTJ_NEAR → near_jump
      secs <= 0 (< 30 min)  → jumped_estimated
      secs <= 0 (>= 30 min) → awaiting_result
    """
    current = (race.get("status") or STATUS_UPCOMING).lower()

    # Preserve hard block — never auto-transition away from blocked
    if current == STATUS_BLOCKED or race.get("blocked"):
        return STATUS_BLOCKED

    # Explicit guard: authoritative terminal states are never overridden by time-based logic.
    # FINAL_STATES is intentionally checked first and separately from SETTLED_STATUSES to
    # make the protection of result-confirmed races unambiguous at the call site.
    if current in FINAL_STATES:
        return current

    # Preserve other OddsPro settled states (e.g. result_posted)
    if current in SETTLED_STATUSES:
        return current

    # Derive status from stored jump_time
    ntj = compute_ntj(race.get("jump_time"), race.get("date"))
    secs = ntj.get("seconds_to_jump")

    if secs is None:
        # No parseable jump_time — cannot determine state
        return STATUS_STALE_UNKNOWN

    if secs > NTJ_NEAR_MAX:
        # More than 10 min away — keep OddsPro status or default to upcoming
        if current in (STATUS_OPEN, STATUS_UPCOMING, STATUS_NEAR_JUMP):
            return STATUS_OPEN if current == STATUS_OPEN else STATUS_UPCOMING
        return STATUS_UPCOMING

    if 0 <= secs <= NTJ_NEAR_MAX:
        # Within 10 min of jump — near_jump
        return STATUS_NEAR_JUMP

    # Jump time has passed (secs < 0)
    if secs >= -_AWAITING_RESULT_THRESHOLD:
        # Less than 30 min past jump — estimated
        return STATUS_JUMPED_ESTIMATED

    # More than 30 min past jump — actively awaiting OddsPro result
    return STATUS_AWAITING_RESULT


def update_race_state(race: dict[str, Any]) -> tuple[str, bool]:
    """
    Compute the new status for a race and return (new_status, changed).

    Does NOT write to the database — caller is responsible for persisting.
    Returns (new_status, True) when the status would change, (current, False) if not.
    """
    current = (race.get("status") or STATUS_UPCOMING).lower()
    new_status = compute_race_status(race)
    changed = new_status != current
    return new_status, changed


def bulk_update_race_states(races: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """
    Compute status transitions for a list of races.

    Returns list of (race_uid, old_status, new_status) for races that changed.
    Does NOT write to the database.
    """
    changes: list[tuple[str, str, str]] = []
    for race in races:
        race_uid = race.get("race_uid") or ""
        if not race_uid:
            continue
        new_status, changed = update_race_state(race)
        if changed:
            old_status = (race.get("status") or STATUS_UPCOMING).lower()
            changes.append((race_uid, old_status, new_status))
    return changes
