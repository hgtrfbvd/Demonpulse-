"""
integrity_filter.py - DemonPulse Hard Block Gate (simplified)
=============================================================
Simple gate that blocks races with bad statuses or blocked tracks.
No OddsPro/FormFav specifics.

BLOCK CODES:
  BAD_STATUS   - race has an unacceptable status (abandoned etc.)
  BLOCKED_TRACK - track is on the admin block list
  DUPLICATE_RACE - race uid appears more than once
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

BLOCKED_STATUSES = {"abandoned", "invalid", "blocked", "cancelled", "void"}


def filter_race(
    race: dict[str, Any],
    *,
    near_jump: bool = False,
    blocked_tracks: set[str] | None = None,
) -> tuple[bool, str | None]:
    """
    Check whether a race should be blocked from the board.

    Returns:
        (allowed, block_code)  — block_code is None when allowed=True
    """
    status = (race.get("status") or "").lower()
    if status in BLOCKED_STATUSES:
        return False, "BAD_STATUS"

    if blocked_tracks:
        track = (race.get("track") or race.get("track_name") or "").lower()
        if track in {t.lower() for t in blocked_tracks}:
            return False, "BLOCKED_TRACK"

    return True, None


def guard_formfav_overwrite(
    existing: dict[str, Any],
    new_data: dict[str, Any],
) -> tuple[bool, str | None]:
    """Stub — no FormFav in new pipeline. Always allows."""
    return True, None


def is_duplicate(race_uid: str, seen_uids: set[str]) -> bool:
    """Return True if race_uid was already seen; adds it to seen_uids."""
    if race_uid in seen_uids:
        return True
    seen_uids.add(race_uid)
    return False
