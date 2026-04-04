"""
data_engine.py - DemonPulse Data Engine
=========================================
OddsPro is the PRIMARY and AUTHORITATIVE source of record.
FormFav is a PROVISIONAL OVERLAY only — never used for daily bootstrap
or official board state.

Core functions:
  full_sweep()       - OddsPro daily bootstrap via /api/external/meetings
  rolling_refresh()  - OddsPro meeting/race refresh via /api/external/meeting/:id
  check_results()    - OddsPro result sweep via /api/external/results
  formfav_overlay()  - FormFav provisional enrichment (near-jump only)

Architecture rules enforced here:
  - OddsPro builds the day (full_sweep)
  - OddsPro builds official board state (rolling_refresh)
  - OddsPro-confirmed data is official truth
  - FormFav never calls for bootstrap, never overwrites official fields
  - NTJ calculated from stored jump_time (race_status.compute_ntj)
  - Blocked races tracked explicitly in database
"""
import logging
from datetime import date, datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_oddspro_connector = None
_formfav_connector = None


# ------------------------------------------------------------
# CONNECTOR SETUP
# ------------------------------------------------------------

def _get_oddspro() -> "OddsProConnector":  # noqa: F821
    global _oddspro_connector
    if _oddspro_connector is None:
        from connectors.oddspro_connector import OddsProConnector
        _oddspro_connector = OddsProConnector()
        if not _oddspro_connector.is_enabled():
            log.warning(
                "OddsPro connector not enabled (ODDSPRO_BASE_URL or ODDSPRO_API_KEY missing). "
                "full_sweep and rolling_refresh will be no-ops until configured."
            )
        else:
            log.info("OddsPro connector loaded (primary source)")
    return _oddspro_connector


def _get_formfav() -> "FormFavConnector":  # noqa: F821
    global _formfav_connector
    if _formfav_connector is None:
        from connectors.formfav_connector import FormFavConnector
        _formfav_connector = FormFavConnector()
        if not _formfav_connector.is_enabled():
            log.info("FormFav connector not enabled (missing API key) — overlay inactive")
        else:
            log.info("FormFav connector loaded (provisional overlay only)")
    return _formfav_connector


# ------------------------------------------------------------
# FULL SWEEP — OddsPro daily bootstrap
# ------------------------------------------------------------

def full_sweep(target_date: str | None = None) -> dict[str, Any]:
    """
    Daily bootstrap via OddsPro GET /api/external/meetings.
    Fetches all meetings for the day, then fetches races for each meeting
    and writes them to the database as official truth.

    FormFav is NOT called here.
    """
    today = target_date or date.today().isoformat()
    conn = _get_oddspro()

    if not conn.is_enabled():
        log.warning("full_sweep skipped: OddsPro not configured")
        return {"ok": False, "reason": "oddspro_not_configured", "date": today}

    try:
        meetings = conn.fetch_meetings(today)
    except Exception as e:
        log.error(f"full_sweep: fetch_meetings failed: {e}")
        return {"ok": False, "error": "Data engine error", "date": today}

    if not meetings:
        log.info(f"full_sweep: no meetings returned for {today}")
        return {"ok": True, "meetings": 0, "races": 0, "date": today}

    races_written = 0
    for meeting in meetings:
        try:
            races = conn.fetch_meeting_races(meeting)
            for race in races:
                _store_with_pipeline(race)
                races_written += 1
        except Exception as e:
            log.error(f"full_sweep: failed for meeting {meeting.meeting_id}: {e}")

    log.info(
        f"full_sweep complete: {len(meetings)} meetings, {races_written} races for {today}"
    )
    return {
        "ok": True,
        "date": today,
        "meetings": len(meetings),
        "races": races_written,
        "source": "oddspro",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------
# ROLLING REFRESH — OddsPro meeting/race refresh
# ------------------------------------------------------------

def rolling_refresh(target_date: str | None = None) -> dict[str, Any]:
    """
    Refresh active meetings and races via OddsPro.
    Uses GET /api/external/meeting/:meetingId for each active meeting.

    Also triggers near-jump FormFav provisional overlays where appropriate
    (NTJ < 10 min), but FormFav data does NOT overwrite official fields.
    """
    today = target_date or date.today().isoformat()
    conn = _get_oddspro()

    if not conn.is_enabled():
        log.warning("rolling_refresh skipped: OddsPro not configured")
        return {"ok": False, "reason": "oddspro_not_configured", "date": today}

    races_refreshed = 0
    overlay_count = 0

    try:
        from database import get_active_races
        from race_status import should_trigger_formfav_overlay

        active_races = get_active_races(today)

        # Group by oddspro_race_id for individual race refresh
        for stored_race in active_races:
            oddspro_race_id = stored_race.get("oddspro_race_id") or ""
            if not oddspro_race_id:
                continue

            try:
                fresh_race, runners = conn.fetch_race_with_runners(oddspro_race_id)
                if fresh_race:
                    fresh_dict = _race_to_dict(fresh_race)
                    _store_with_pipeline(fresh_race)
                    races_refreshed += 1

                    # Near-jump FormFav provisional overlay (enrichment only)
                    if should_trigger_formfav_overlay(fresh_dict):
                        _apply_formfav_overlay(fresh_dict)
                        overlay_count += 1

            except Exception as e:
                log.error(
                    f"rolling_refresh: failed for race {oddspro_race_id}: {e}"
                )

    except Exception as e:
        log.error(f"rolling_refresh: outer error: {e}")
        return {"ok": False, "error": "Data engine error", "date": today}

    log.info(
        f"rolling_refresh: {races_refreshed} races refreshed, "
        f"{overlay_count} FormFav overlays applied"
    )
    return {
        "ok": True,
        "date": today,
        "races_refreshed": races_refreshed,
        "formfav_overlays": overlay_count,
        "source": "oddspro",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------
# CHECK RESULTS — OddsPro result sweep
# ------------------------------------------------------------

def check_results(target_date: str | None = None) -> dict[str, Any]:
    """
    Day-level result sweep via OddsPro GET /api/external/results.
    After each result, the individual race is confirmed via
    GET /api/races/:id/results before writing to results_log.

    Official truth is only written after OddsPro confirmation.
    """
    today = target_date or date.today().isoformat()
    conn = _get_oddspro()

    if not conn.is_enabled():
        log.warning("check_results skipped: OddsPro not configured")
        return {"ok": False, "reason": "oddspro_not_configured", "date": today}

    try:
        results = conn.fetch_results(today)
    except Exception as e:
        log.error(f"check_results: fetch_results failed: {e}")
        return {"ok": False, "error": "Data engine error", "date": today}

    written = 0
    skipped = 0
    for result in results:
        try:
            # Single-race confirmation before writing — never write without confirmation
            confirmed = conn.fetch_race_result(result.oddspro_race_id)
            if confirmed:
                _write_result(confirmed)
                written += 1
            else:
                log.warning(
                    f"check_results: single-race confirmation failed for "
                    f"{result.race_uid} — result not written"
                )
                skipped += 1
        except Exception as e:
            log.error(f"check_results: failed for race {result.race_uid}: {e}")
            skipped += 1

    log.info(
        f"check_results: {written} results confirmed and written, "
        f"{skipped} skipped (no confirmation) for {today}"
    )
    return {
        "ok": True,
        "date": today,
        "results_written": written,
        "results_skipped": skipped,
        "source": "oddspro",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------
# FORMFAV PROVISIONAL OVERLAY (near-jump only)
# ------------------------------------------------------------

def formfav_overlay(race_uid: str, race: dict[str, Any]) -> dict[str, Any]:
    """
    Fetch FormFav provisional enrichment for a single race.
    Only called near-jump (NTJ < 10 min).
    Does NOT overwrite OddsPro authoritative fields.
    Does NOT write to official tables.

    Returns enriched race dict (provisional, not stored as official truth).
    """
    ff = _get_formfav()
    if not ff.is_enabled():
        return race

    try:
        from integrity_filter import guard_formfav_overwrite

        ff_race, ff_runners = ff.fetch_race_form(
            target_date=race.get("date") or date.today().isoformat(),
            track=race.get("track") or "",
            race_num=int(race.get("race_num") or 0),
            code=race.get("code") or "HORSE",
        )

        provisional = ff_race.__dict__ if hasattr(ff_race, "__dict__") else {}

        # Guard: FormFav cannot overwrite OddsPro authoritative fields
        enriched = guard_formfav_overwrite(race, provisional)
        log.debug(f"data_engine: FormFav overlay applied to {race_uid}")
        return enriched

    except Exception as e:
        log.debug(f"data_engine: FormFav overlay failed for {race_uid}: {e}")
        return race


# ------------------------------------------------------------
# INTERNAL WRITE HELPERS
# ------------------------------------------------------------

def _write_race(race: Any) -> None:
    """Write an OddsPro RaceRecord to the database as official truth."""
    try:
        from database import upsert_race
        race_dict = _race_to_dict(race)
        upsert_race(race_dict)
    except Exception as e:
        log.error(f"data_engine: _write_race failed: {e}")


def _store_with_pipeline(race: Any) -> None:
    """
    Enforce pipeline order before storing an OddsPro race record:
      1. Normalize  (already done by connector)
      2. Validate   (log warning; OddsPro data is stored regardless)
      3. Integrity  (if blocked, mark before storing)
      4. Store

    Board building (step 5) happens separately in board_builder.py using
    the stored records. Blocked races are stored with status='blocked' so
    they are tracked explicitly rather than silently dropped.
    """
    try:
        from database import upsert_race
        from validation_engine import validate_race
        from integrity_filter import filter_race

        race_dict = _race_to_dict(race)
        race_uid = race_dict.get("race_uid") or "(no uid)"

        # Step 2 — Validate (informational; OddsPro data is always stored)
        passes, confidence, issues = validate_race(race_dict)
        if not passes:
            log.warning(
                f"data_engine: pipeline validate: race {race_uid} "
                f"confidence={confidence} issues={issues} — storing for tracking"
            )

        # Step 3 — Integrity filter (hard gate; mark as blocked if rejected)
        allowed, block_code = filter_race(race_dict)
        if not allowed:
            log.warning(
                f"data_engine: pipeline integrity: race {race_uid} blocked [{block_code}] — storing as blocked"
            )
            race_dict["status"] = "blocked"
            race_dict["block_code"] = block_code

        # Step 4 — Store
        upsert_race(race_dict)

    except Exception as e:
        log.error(f"data_engine: _store_with_pipeline failed: {e}")


def _write_result(result: Any) -> None:
    """Write an OddsPro RaceResult to results_log as official truth."""
    try:
        from database import upsert_result, update_race_status
        result_dict = result.__dict__ if hasattr(result, "__dict__") else result
        upsert_result(result_dict)
        # Mark the race as final in today_races
        race_uid = result_dict.get("race_uid") or ""
        if race_uid:
            update_race_status(race_uid, "final")
    except Exception as e:
        log.error(f"data_engine: _write_result failed: {e}")


def _apply_formfav_overlay(race: dict[str, Any]) -> None:
    """
    Apply FormFav provisional overlay in-memory.
    Does NOT write to official tables — overlay is ephemeral.
    """
    race_uid = race.get("race_uid") or ""
    if not race_uid:
        return
    enriched = formfav_overlay(race_uid, race)
    # Overlay is returned in-memory for board building; not persisted
    log.debug(f"data_engine: provisional overlay applied for {race_uid} (not persisted)")


def _race_to_dict(race: Any) -> dict[str, Any]:
    """Convert a RaceRecord dataclass or dict to a plain dict."""
    if hasattr(race, "__dict__"):
        return race.__dict__
    return race if isinstance(race, dict) else {}


# ------------------------------------------------------------
# LEGACY API HELPER (kept for backward compatibility)
# ------------------------------------------------------------

def get_board() -> dict[str, Any]:
    """
    Build and return the current racing board.
    Delegates to board_builder.get_board_for_today().
    """
    try:
        from board_builder import get_board_for_today
        return get_board_for_today()
    except Exception as e:
        log.error(f"get_board failed: {e}")
        return {"ok": False, "items": []}
