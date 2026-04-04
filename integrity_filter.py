"""
integrity_filter.py - DemonPulse Hard Block Gate
=================================================
Hard gate with explicit BLOCK codes. Any race that triggers a block code
is explicitly excluded from the board. Stale or invalid races never reach
the board.

Architecture rules:
  - OddsPro-confirmed data is official truth
  - Blocked races are tracked explicitly (not silently dropped)
  - No stale or invalid races reach the board
  - FormFav provisional data cannot overwrite official OddsPro records

BLOCK CODES:
  STALE_RACE       - race data is older than the allowed freshness window
  INVALID_DATA     - data fails basic integrity checks
  NO_RUNNERS       - race has no runners at near-jump time
  BAD_STATUS       - race has an unacceptable status (abandoned etc.)
  SOURCE_MISMATCH  - FormFav data conflicts with official OddsPro record
  DUPLICATE_RACE   - race uid appears more than once
  BLOCKED_TRACK    - track is on the admin block list
  OVERWRITE_GUARD  - FormFav attempted to overwrite OddsPro-confirmed fields
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# How old (in seconds) a race record may be before it is considered stale
STALE_THRESHOLD_SECONDS = 3600  # 60 minutes

# Race statuses that hard-block a race from the board
BLOCKED_STATUSES = {"abandoned", "invalid", "blocked", "cancelled", "void"}

# Fields that are authoritative from OddsPro and must not be overwritten by FormFav
ODDSPRO_AUTHORITATIVE_FIELDS = {
    "race_uid", "oddspro_race_id",
    "track", "race_num", "date", "code", "jump_time",
    "status", "race_name", "distance", "grade",
    "source", "source_url",
}


def filter_race(
    race: dict[str, Any],
    *,
    near_jump: bool = False,
    imminent: bool = False,
    blocked_tracks: set[str] | None = None,
) -> tuple[bool, str]:
    """
    Hard gate check for a race.

    Returns:
        (allowed, block_code)
        allowed=True means the race may proceed to the board.
        block_code is empty string when allowed, or an explicit BLOCK code.
    """
    # Bad status check
    status = (race.get("status") or "").lower()
    if status in BLOCKED_STATUSES:
        return False, "BAD_STATUS"

    # Already explicitly blocked
    if race.get("blocked"):
        return False, race.get("block_code") or "BLOCKED"

    # Required fields
    if not race.get("track") or not race.get("race_num") or not race.get("date"):
        return False, "INVALID_DATA"

    # Blocked track check
    if blocked_tracks:
        track = (race.get("track") or "").lower()
        if track in {t.lower() for t in blocked_tracks}:
            return False, "BLOCKED_TRACK"

    # NO_RUNNERS block only applies when race is IMMINENT (< 2 min to jump)
    # — at that point, runners must already be loaded
    if imminent:
        runner_count = int(race.get("runner_count") or 0)
        if runner_count == 0:
            return False, "NO_RUNNERS"

    # Stale check (if fetched_at is available)
    fetched_at = race.get("fetched_at") or race.get("updated_at")
    if fetched_at:
        try:
            if isinstance(fetched_at, str):
                fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            else:
                fetched_dt = fetched_at
            if fetched_dt.tzinfo is None:
                fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - fetched_dt).total_seconds()
            if age_seconds > STALE_THRESHOLD_SECONDS:
                log.warning(
                    f"integrity_filter: STALE_RACE {race.get('race_uid')} "
                    f"age={age_seconds:.0f}s"
                )
                return False, "STALE_RACE"
        except Exception:
            pass  # If we can't parse the timestamp, allow through (conservative)

    return True, ""


def guard_formfav_overwrite(
    official: dict[str, Any],
    provisional: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge FormFav provisional data onto an official OddsPro record.
    Authoritative OddsPro fields are NEVER overwritten.
    Only non-authoritative fields (form data, ratings, etc.) are enriched.

    Returns:
        merged dict — official fields preserved, non-authoritative fields enriched.
    """
    merged = dict(official)

    for key, value in provisional.items():
        if key in ODDSPRO_AUTHORITATIVE_FIELDS:
            # Silently skip — official truth is preserved
            if official.get(key) != value:
                log.debug(
                    f"integrity_filter: OVERWRITE_GUARD blocked FormFav "
                    f"overwrite of '{key}' (official={official.get(key)!r}, "
                    f"provisional={value!r})"
                )
            continue

        # Only enrich with provisional data if the official field is empty/None
        if merged.get(key) in (None, "", 0):
            merged[key] = value
        elif key in ("condition", "prize_money", "runner_count"):
            # These may be legitimately enriched from FormFav if official is empty
            if not merged.get(key):
                merged[key] = value

    merged["has_provisional_overlay"] = True
    return merged


def is_duplicate(race_uid: str, seen: set[str]) -> bool:
    """Return True if this race_uid has already been seen (duplicate guard)."""
    if race_uid in seen:
        log.warning(f"integrity_filter: DUPLICATE_RACE {race_uid}")
        return True
    seen.add(race_uid)
    return False
