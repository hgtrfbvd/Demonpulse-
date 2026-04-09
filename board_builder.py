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
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from integrity_filter import filter_race, guard_formfav_overwrite, is_duplicate
from race_status import (
    compute_ntj,
    should_trigger_formfav_overlay,
    is_race_live,
    is_race_expired_by_time,
    is_invalid_jump_time,
    BOARD_EXPIRED_GRACE_SECS,
)
from validation_engine import validate_race

log = logging.getLogger(__name__)

_AEST = ZoneInfo("Australia/Sydney")
_NTJ_SORT_ORDER = {"IMMINENT": 0, "NEAR": 1, "UPCOMING": 2, "PAST": 3, "UNKNOWN": 4}
_RESULT_RETENTION_SECS = 7200  # keep resulted races visible for 2 hours post-jump


def build_board(
    races: list[dict[str, Any]],
    *,
    blocked_tracks: set[str] | None = None,
    formfav_overlays: dict[str, dict[str, Any]] | None = None,
    include_blocked: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
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
    settled_count = 0
    expired_count = 0
    invalid_time_count = 0

    now_aest = datetime.now(timezone.utc).astimezone(_AEST)

    for race in races:
        race_uid = race.get("race_uid") or ""

        # Duplicate guard
        if race_uid and is_duplicate(race_uid, seen_uids):
            continue

        # Skip settled races — but keep recent results visible for 2 hours
        if not is_race_live(race):
            jump_time_raw = race.get("jump_time")
            ntj_check = compute_ntj(jump_time_raw, race.get("date"))
            secs = ntj_check.get("seconds_to_jump")
            status = (race.get("status") or "").lower()
            is_recent_result = (
                status in {"final", "paying", "result_posted"} and
                secs is not None and secs > -_RESULT_RETENTION_SECS
            )
            if not is_recent_result:
                settled_count += 1
                continue
            # Fall through — recent resulted race stays on board

        # Compute NTJ from stored jump_time (needed for filter, sort, and expiry)
        ntj = compute_ntj(race.get("jump_time"), race.get("date"))

        # --- DATETIME VALIDATION ---
        # Reject races whose jump_time is missing, unparseable, or a
        # midnight/date-only fallback.  These must not appear in next-up or
        # live-board ordering.
        jump_time_raw = race.get("jump_time")
        if is_invalid_jump_time(jump_time_raw, race.get("date")):
            log.debug(
                f"board_builder: INVALID_TIME {race_uid} "
                f"jump_time={jump_time_raw!r} "
                f"now_aest={now_aest.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                f"reason=midnight_or_unparseable excluded=True"
            )
            invalid_time_count += 1
            continue

        # --- EXPIRED / RESULTED FILTER ---
        # Races are no longer removed by time alone — the status machine handles
        # removal when a confirmed settled status arrives from OddsPro.
        # (BOARD_EXPIRED_GRACE_SECS is kept as a last-resort 24-hour fallback via
        # is_race_expired_by_time, but we skip the early-exit here so jumped races
        # remain visible as "Pending Result" until a result is confirmed.)

        log.debug(
            f"board_builder: INCLUDED {race_uid} "
            f"jump_dt_iso={ntj.get('jump_dt_iso')!r} "
            f"ntj_label={ntj.get('ntj_label')} "
            f"seconds_to_jump={ntj.get('seconds_to_jump')} "
            f"now_aest={now_aest.strftime('%Y-%m-%d %H:%M:%S %Z')} "
            f"status={race.get('status')!r}"
        )

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

    # Sort by NTJ label order, then by seconds_to_jump within each group
    board.sort(key=lambda x: (
        _NTJ_SORT_ORDER.get(x.get("ntj_label", "UNKNOWN"), 4),
        x.get("seconds_to_jump") if x.get("seconds_to_jump") is not None else 999999,
    ))

    if not board:
        reasons = []
        if not races:
            reasons.append("no_races_in_db")
        if settled_count:
            reasons.append(f"settled={settled_count}")
        if expired_count:
            reasons.append(f"expired_by_time={expired_count}")
        if invalid_time_count:
            reasons.append(f"invalid_time={invalid_time_count}")
        if blocked_count:
            reasons.append(f"blocked={blocked_count}")
        if rejected_count:
            reasons.append(f"validation_rejected={rejected_count}")
        log.warning(
            f"board_builder: board is EMPTY — "
            f"input={len(races)} settled={settled_count} "
            f"expired={expired_count} invalid_time={invalid_time_count} "
            f"blocked={blocked_count} rejected={rejected_count} "
            f"reasons=[{', '.join(reasons) or 'unknown'}]"
        )
    else:
        log.info(
            f"board_builder: {len(board)} races on board "
            f"(input={len(races)} settled={settled_count} "
            f"expired={expired_count} invalid_time={invalid_time_count} "
            f"blocked={blocked_count} rejected={rejected_count})"
        )

    stats = {
        "settled_count": settled_count,
        "expired_count": expired_count,
        "invalid_time_count": invalid_time_count,
        "blocked_count": blocked_count,
        "validation_rejected_count": rejected_count,
    }
    return board, stats


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
        # ISO datetime of jump (UTC-aware) — use this in frontend for exact countdowns
        "jump_dt_iso": ntj.get("jump_dt_iso"),
        # FormFav persistent enrichment (attached separately; None if not yet synced)
        "formfav": race.get("formfav"),
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
    Used by API routes and scheduler board-rebuild triggers.

    If formfav_overlays is not provided, provisional overlays are loaded
    automatically from the in-memory store in data_engine.

    Stored FormFav enrichment (formfav_race_enrichment table) is attached
    to each board item under the "formfav" key.
    Per-runner FormFav enrichment is NOT included in the board payload —
    it is served on demand from /api/live/race/<race_uid> only.
    """
    from cache import cache_get, cache_set
    today = date.today().isoformat()
    CACHE_KEY = f"board:{today}"
    CACHE_TTL = 20  # seconds — short enough for live feel

    cached = cache_get(CACHE_KEY)
    if cached is not None:
        return cached

    try:
        from database import get_active_races, get_races_for_date, get_blocked_races
        races = get_active_races(today)
        all_today = get_races_for_date(today)
        blocked_today = get_blocked_races(today)

        # Auto-load provisional overlays if not provided by caller
        if formfav_overlays is None:
            try:
                from data_engine import get_provisional_overlays
                formfav_overlays = get_provisional_overlays()
            except Exception:
                formfav_overlays = {}

        # Load stored FormFav race-level enrichment and index by race_uid
        ff_enrichment: dict[str, dict[str, Any]] = {}
        try:
            from database import get_formfav_enrichments_for_date
            for ff_row in get_formfav_enrichments_for_date(today):
                uid = ff_row.get("race_uid") or ""
                if uid:
                    ff_enrichment[uid] = ff_row
        except Exception:
            pass

        # Attach stored FormFav race-level enrichment to each race before board build
        if ff_enrichment:
            enriched_races = []
            for race in races:
                uid = race.get("race_uid") or ""
                if uid in ff_enrichment:
                    race = {**race, "formfav": ff_enrichment[uid]}
                enriched_races.append(race)
            races = enriched_races

        board, build_stats = build_board(
            races,
            blocked_tracks=blocked_tracks,
            formfav_overlays=formfav_overlays,
        )

        board_count = len(board)
        active_count = len(races)
        blocked_pre_stored = len(blocked_today)

        diagnostics: dict[str, Any] = {
            "stored_race_count_today": len(all_today),
            "active_race_count": active_count,
            "blocked_race_count": blocked_pre_stored,
            "settled_count": build_stats["settled_count"],
            "expired_count": build_stats["expired_count"],
            "invalid_time_count": build_stats["invalid_time_count"],
            "integrity_blocked_count": build_stats["blocked_count"],
            "validation_rejected_count": build_stats["validation_rejected_count"],
            "formfav_enriched_count": len(ff_enrichment),
        }

        if not board:
            if not all_today:
                diagnostics["empty_reason"] = "no_races_stored_today"
            elif not races:
                diagnostics["empty_reason"] = "no_active_races_all_blocked_or_settled"
            else:
                diagnostics["empty_reason"] = "all_active_races_failed_board_gate"

        result = {
            "ok": True,
            "items": board,
            "count": len(board),
            "date": today,
            "diagnostics": diagnostics,
        }
        cache_set(CACHE_KEY, result, ttl=CACHE_TTL)
        return result
    except Exception as e:
        log.error(f"board_builder: get_board_for_today failed: {e}")
        return {"ok": False, "items": [], "count": 0, "error": "Board unavailable"}
