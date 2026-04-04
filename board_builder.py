"""
board_builder.py - DemonPulse Board Builder
=============================================
Builds the live racing board from OddsPro-authoritative data.

Architecture rules:
  - Board is built exclusively from OddsPro-confirmed records
  - FormFav provisional overlays are applied after the board is built
  - integrity_filter.py gates every race before it reaches the board
  - validation_engine.py rejects low-confidence records
  - NTJ is computed from stored jump_time (no external scraping)
  - Blocked races are explicitly excluded (not silently dropped)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from integrity_filter import filter_race, guard_formfav_overwrite, is_duplicate
from race_status import compute_ntj, should_trigger_formfav_overlay, is_race_live
from validation_engine import validate_race

log = logging.getLogger(__name__)

_NTJ_SORT_ORDER = {"IMMINENT": 0, "NEAR": 1, "UPCOMING": 2, "PAST": 3, "UNKNOWN": 4}
_SORT_FALLBACK_TIME = "99:99"


def build_board(
    races: list[dict[str, Any]],
    *,
    blocked_tracks: set[str] | None = None,
    formfav_overlays: dict[str, dict[str, Any]] | None = None,
    include_blocked: bool = False,
) -> list[dict[str, Any]]:
    """
    Build the live racing board from a list of OddsPro race records.

    Args:
        races: List of race dicts (from database.get_active_races or similar)
        blocked_tracks: Optional set of track names to hard-block
        formfav_overlays: Optional dict mapping race_uid → FormFav provisional data
        include_blocked: If True, include blocked races in output (for admin/debug)

    Returns:
        List of board items (only valid, unblocked, live races)
    """
    board: list[dict[str, Any]] = []
    seen_uids: set[str] = set()
    blocked_count = 0
    rejected_count = 0

    for race in races:
        race_uid = race.get("race_uid") or ""

        # Duplicate guard
        if race_uid and is_duplicate(race_uid, seen_uids):
            continue

        # Skip settled races
        if not is_race_live(race):
            continue

        # Compute NTJ from stored jump_time (needed for filter and sort)
        ntj = compute_ntj(race.get("jump_time"), race.get("date"))

        # Integrity hard gate
        allowed, block_code = filter_race(
            race,
            imminent=(ntj.get("ntj_label") == "IMMINENT"),
            blocked_tracks=blocked_tracks,
        )
        if not allowed:
            blocked_count += 1
            log.debug(f"board_builder: BLOCKED {race_uid} [{block_code}]")
            if include_blocked:
                board.append(_blocked_item(race, block_code))
            continue

        # Apply FormFav provisional overlay (non-authoritative enrichment only)
        if formfav_overlays and race_uid in formfav_overlays:
            race = guard_formfav_overwrite(race, formfav_overlays[race_uid])

        # Validation confidence gate
        passes, confidence, issues = validate_race(race)
        if not passes:
            rejected_count += 1
            log.debug(
                f"board_builder: REJECTED {race_uid} confidence={confidence} issues={issues}"
            )
            continue

        board.append(_board_item(race, ntj, confidence))

    # Sort by NTJ label order, then by jump time within each group
    board.sort(key=lambda x: (
        _NTJ_SORT_ORDER.get(x.get("ntj_label", "UNKNOWN"), 4),
        x.get("jump_time") or _SORT_FALLBACK_TIME,
    ))

    log.info(
        f"board_builder: {len(board)} races on board "
        f"(blocked={blocked_count}, rejected={rejected_count})"
    )
    return board


def _board_item(race: dict[str, Any], ntj: dict[str, Any], confidence: float) -> dict[str, Any]:
    """Format a race dict into a board item."""
    return {
        "race_uid": race.get("race_uid"),
        "oddspro_race_id": race.get("oddspro_race_id"),
        "track": race.get("track"),
        "state": race.get("state") or "",
        "race_num": race.get("race_num"),
        "race_name": race.get("race_name") or "",
        "code": race.get("code"),
        "date": race.get("date"),
        "distance": race.get("distance") or "",
        "grade": race.get("grade") or "",
        "condition": race.get("condition") or "",
        "jump_time": race.get("jump_time"),
        "status": race.get("status") or "upcoming",
        "source": race.get("source") or "oddspro",
        "confidence": confidence,
        "has_provisional_overlay": bool(race.get("has_provisional_overlay")),
        # NTJ from stored jump_time — no scraping
        "seconds_to_jump": ntj.get("seconds_to_jump"),
        "ntj_label": ntj.get("ntj_label"),
        "is_near_jump": ntj.get("is_near_jump"),
    }


def _blocked_item(race: dict[str, Any], block_code: str) -> dict[str, Any]:
    """Format a blocked race for admin/debug output."""
    return {
        "race_uid": race.get("race_uid"),
        "track": race.get("track"),
        "race_num": race.get("race_num"),
        "code": race.get("code"),
        "status": "BLOCKED",
        "block_code": block_code,
        "_blocked": True,
    }


def get_board_for_today(
    blocked_tracks: set[str] | None = None,
    formfav_overlays: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Convenience function: fetch active races from DB and build the board.
    Used by API routes.
    """
    try:
        from database import get_active_races
        today = date.today().isoformat()
        races = get_active_races(today)
        board = build_board(
            races,
            blocked_tracks=blocked_tracks,
            formfav_overlays=formfav_overlays,
        )
        return {"ok": True, "items": board, "count": len(board), "date": today}
    except Exception as e:
        log.error(f"board_builder: get_board_for_today failed: {e}")
        return {"ok": False, "items": [], "count": 0, "error": "Board unavailable"}
