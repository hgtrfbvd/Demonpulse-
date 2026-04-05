"""
data_engine.py - DemonPulse Data Engine
=========================================
OddsPro is the PRIMARY and AUTHORITATIVE source of record.
FormFav is a PROVISIONAL OVERLAY only — never used for daily bootstrap
or official board state.

Core functions:
  full_sweep()          - OddsPro daily bootstrap via /api/external/meetings
                          (with /api/meetings discovery fallback for meeting ID resolution)
  rolling_refresh()     - OddsPro meeting/race refresh via /api/external/meeting/:id
  near_jump_refresh()   - OddsPro near-jump refresh + FormFav provisional overlay
  check_results()       - OddsPro result sweep via /api/external/results
  formfav_overlay()     - FormFav provisional enrichment (near-jump only)
  get_provisional_overlays() - Return current provisional overlay store

Architecture rules enforced here:
  - OddsPro builds the day (full_sweep)
  - OddsPro builds official board state (rolling_refresh, near_jump_refresh)
  - OddsPro-confirmed data is official truth
  - FormFav never calls for bootstrap, never overwrites official fields
  - FormFav overlays stored in-memory separately from official tables
  - NTJ calculated from stored jump_time (race_status.compute_ntj)
  - Blocked races tracked explicitly in database
"""
import logging
import threading
from datetime import date, datetime, timezone
from typing import Any

import requests as _requests_lib

log = logging.getLogger(__name__)

_oddspro_connector = None
_formfav_connector = None

# ---------------------------------------------------------------------------
# PROVISIONAL OVERLAY STORE — in-memory, never persisted to official tables
# ---------------------------------------------------------------------------
_provisional_overlays: dict[str, dict[str, Any]] = {}
_provisional_overlay_lock = threading.Lock()


def _store_provisional_overlay(race_uid: str, overlay: dict[str, Any]) -> None:
    """
    Store a FormFav provisional overlay in-memory.
    Not written to official tables. Cleared on next OddsPro authoritative sweep.
    """
    with _provisional_overlay_lock:
        _provisional_overlays[race_uid] = {
            **overlay,
            "_provisional": True,
            "_overlay_source": "formfav",
            "_overlay_at": datetime.now(timezone.utc).isoformat(),
        }


def get_provisional_overlays() -> dict[str, dict[str, Any]]:
    """
    Return a snapshot of all current provisional overlays.
    Used by board_builder to apply non-authoritative enrichment.
    """
    with _provisional_overlay_lock:
        return dict(_provisional_overlays)


def clear_provisional_overlay_for_race(race_uid: str) -> None:
    """
    Clear the provisional overlay for a specific race.
    Must only be called after that race has been successfully refreshed from OddsPro
    AND validation + integrity checks have passed.
    """
    with _provisional_overlay_lock:
        _provisional_overlays.pop(race_uid, None)


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
                "OddsPro connector not enabled (ODDSPRO_BASE_URL missing). "
                "full_sweep and rolling_refresh will be no-ops until configured."
            )
        else:
            mode_label = "public endpoint mode" if _oddspro_connector.is_public_mode() else "authenticated mode"
            log.info(f"OddsPro connector loaded (primary source, {mode_label})")
            try:
                from services.health_service import record_oddspro_mode
                record_oddspro_mode(
                    public_mode=_oddspro_connector.is_public_mode(),
                    api_key_present=bool(_oddspro_connector.api_key),
                )
            except Exception as exc:
                log.debug(f"_get_oddspro: could not record mode in health service: {exc}")
    return _oddspro_connector


def get_oddspro_connector() -> "OddsProConnector":  # noqa: F821
    """
    Return the OddsPro connector singleton.
    Used by diagnostic routes to inspect _last_fetch_diag after full_sweep().
    """
    return _get_oddspro()


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

def _has_numeric_meeting_id(meeting: Any) -> bool:
    """Return True if the meeting's raw payload includes a numeric id or meetingId."""
    raw = meeting.extra.get("raw", {}) if hasattr(meeting, "extra") else {}
    return (
        str(raw.get("id") or "").isdigit()
        or str(raw.get("meetingId") or "").isdigit()
    )


def full_sweep(target_date: str | None = None) -> dict[str, Any]:
    """
    Daily bootstrap via OddsPro GET /api/external/meetings.
    Fetches all meetings for the day, parses races and runners from
    the /meetings response if they are embedded inline.  Only calls
    GET /api/external/meeting/:id when races are NOT present in the
    /meetings response for a given meeting.

    When the /api/external/meetings response returns meetings without
    numeric IDs and without embedded races (e.g. only meetingName/racingCode),
    calls GET /api/meetings (the discovery endpoint) to resolve numeric meeting
    IDs and/or embedded race data before fetching per-meeting detail.
    This follows the documented OddsPro discovery flow:
      1. GET /api/meetings          → all meetings with IDs and race IDs
      2. GET /api/external/meeting/:id → full meeting/race/runner detail

    Writes official data to the database.  FormFav is NOT called here.

    Returns diagnostics:
      meetings_found, meetings_fetched, races_found, runners_found,
      races_stored, runners_stored, races_blocked
    """
    today = target_date or date.today().isoformat()
    conn = _get_oddspro()

    if not conn.is_enabled():
        log.warning("full_sweep skipped: OddsPro not configured")
        return {"ok": False, "reason": "oddspro_not_configured", "date": today}

    try:
        meetings = conn.fetch_meetings(today)
    except _requests_lib.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else None
        reason = f"oddspro_http_{status_code}" if status_code else "oddspro_request_exception"
        log.error(f"full_sweep: fetch_meetings HTTP error {status_code}: {e}")
        return {"ok": False, "reason": reason, "http_status": status_code, "date": today}
    except ValueError as e:
        log.error(f"full_sweep: fetch_meetings parse error: {e}")
        err_dict: dict[str, Any] = {
            "ok": False,
            "error": str(e),
            "reason": getattr(e, "parse_stage", None) or "oddspro_parse_error",
            "detail": str(e),
            "date": today,
        }
        if hasattr(e, "parse_stage"):
            err_dict["parse_stage"] = getattr(e, "parse_stage", "unknown")
            err_dict["response_type"] = getattr(e, "response_type", "")
            err_dict["top_level_keys"] = getattr(e, "response_keys", [])
            err_dict["first_item_keys"] = getattr(e, "first_item_keys", [])
            err_dict["sample_payload"] = getattr(e, "sample_payload", None)
            err_dict["exception_message"] = getattr(e, "exception_message", None) or str(e)
            err_dict["http_status"] = getattr(e, "http_status", None)
            err_dict["content_type"] = getattr(e, "content_type", "")
            err_dict["final_url"] = getattr(e, "final_url", "")
            err_dict["redirected_url"] = getattr(e, "redirected_url", "")
            err_dict["response_length"] = getattr(e, "response_length", None)
            err_dict["response_preview"] = getattr(e, "response_preview", "")
        else:
            err_dict["exception_message"] = str(e)
        return err_dict
    except Exception as e:
        log.error(f"full_sweep: fetch_meetings failed: {e}")
        return {"ok": False, "reason": "oddspro_request_exception", "date": today}

    meetings_found = len(meetings)
    if not meetings_found:
        log.info(f"full_sweep: no meetings returned for {today}")
        return {
            "ok": True,
            "meetings_found": 0, "meetings_fetched": 0,
            "meeting_ids_found": 0, "meeting_ids_missing": 0,
            "meeting_details_attempted": 0, "meeting_details_succeeded": 0, "meeting_details_failed": 0,
            "races_found": 0, "runners_found": 0,
            "races_stored": 0, "runners_stored": 0, "races_blocked": 0,
            # legacy keys kept for callers that rely on them
            "meetings": 0, "races": 0,
            "reason": "no_meetings_scheduled",
            "date": today,
        }

    # Count meetings that already have numeric IDs vs those that don't.
    meeting_ids_found = sum(1 for m in meetings if _has_numeric_meeting_id(m))
    meeting_ids_missing = meetings_found - meeting_ids_found
    if meeting_ids_missing:
        log.info(
            f"full_sweep: {meeting_ids_missing}/{meetings_found} meetings lack numeric IDs "
            f"— will attempt discovery to resolve"
        )

    meetings_fetched = 0
    races_found = 0
    runners_found = 0
    races_stored = 0
    runners_stored = 0
    races_blocked = 0
    meeting_details_attempted = 0
    meeting_details_succeeded = 0
    meeting_details_failed = 0

    # When the /api/external/meetings response contains meeting name-only items
    # (no numeric id and no embedded races), the discovery endpoint /api/meetings
    # is used to resolve numeric meeting IDs and/or obtain embedded race data.
    # This matches the documented discovery flow in the OddsPro API documentation:
    #   1. GET /api/meetings          → all meetings with IDs and race IDs
    #   2. GET /api/external/meeting/:id → full meeting/race/runner detail
    _disc_by_track: dict[str, dict] = {}
    _needs_discovery = bool(meetings) and all(
        not m.extra.get("raw", {}).get("races") and not _has_numeric_meeting_id(m)
        for m in meetings
    )
    _discovery_failed = False
    _discovery_diag: dict = {}
    if _needs_discovery:
        try:
            for dm in conn.fetch_meetings_discovery():
                if not isinstance(dm, dict):
                    continue
                dm_track = conn._clean_track(
                    dm.get("track") or dm.get("meetingName") or ""
                )
                if dm_track:
                    _disc_by_track[dm_track] = dm
            _discovery_diag = dict(getattr(conn, "_last_discovery_diag", {}))
            log.info(
                f"full_sweep: discovery loaded {len(_disc_by_track)} meetings "
                f"(meetings from /external/meetings lacked embedded races and numeric IDs)"
            )
        except Exception as _disc_exc:
            _discovery_failed = True
            _discovery_diag = dict(getattr(conn, "_last_discovery_diag", {}))
            _discovery_diag["error"] = str(_disc_exc)
            log.error(
                f"full_sweep: discovery failed: {_disc_exc} "
                f"— detail fetches will proceed with available identifiers but may fail"
            )

    _first_detail_error: dict = {}

    for meeting in meetings:
        meeting_details_attempted += 1
        try:
            # Prefer races embedded in the /meetings response (extra["raw"]["races"]).
            # Only call /meeting/:id when races are absent from the /meetings payload.
            raw_meeting = meeting.extra.get("raw", {})
            embedded_races = raw_meeting.get("races")

            if embedded_races:
                races, runners = conn.parse_meeting_races_with_runners(meeting, raw_meeting)
                log.debug(
                    f"full_sweep: meeting {meeting.meeting_id} — "
                    f"used embedded races ({len(races)} races)"
                )
            elif _needs_discovery:
                # Use discovery data to resolve numeric meeting ID or embedded races.
                disc_raw = _disc_by_track.get(meeting.track)
                if disc_raw:
                    disc_races = disc_raw.get("races")
                    disc_id = disc_raw.get("id") or disc_raw.get("meetingId")
                    if disc_races:
                        # Races are embedded in the discovery response — parse directly.
                        races, runners = conn.parse_meeting_races_with_runners(
                            meeting, disc_raw
                        )
                        log.debug(
                            f"full_sweep: meeting {meeting.meeting_id} — "
                            f"used discovery embedded races ({len(races)} races)"
                        )
                    elif disc_id:
                        # Use numeric meeting ID from discovery to call /meeting/:id.
                        meeting.meeting_id = str(disc_id)
                        races, runners = conn.fetch_meeting_races_with_runners(meeting)
                        log.debug(
                            f"full_sweep: meeting {meeting.meeting_id} — "
                            f"fetched via discovery ID ({len(races)} races)"
                        )
                    else:
                        log.warning(
                            f"full_sweep: discovery found track {meeting.track!r} but no "
                            f"numeric ID or embedded races — fetching via name identifier "
                            f"{meeting.meeting_id!r} (may return 0 races)"
                        )
                        races, runners = conn.fetch_meeting_races_with_runners(meeting)
                        log.debug(
                            f"full_sweep: meeting {meeting.meeting_id} — "
                            f"fetched via /meeting/:id ({len(races)} races)"
                        )
                else:
                    log.warning(
                        f"full_sweep: discovery did not resolve track {meeting.track!r} "
                        f"(discovery_failed={_discovery_failed}) — fetching via "
                        f"identifier {meeting.meeting_id!r} (may return 0 races)"
                    )
                    races, runners = conn.fetch_meeting_races_with_runners(meeting)
                    log.debug(
                        f"full_sweep: meeting {meeting.meeting_id} — "
                        f"fetched via /meeting/:id ({len(races)} races)"
                    )
            else:
                races, runners = conn.fetch_meeting_races_with_runners(meeting)
                log.debug(
                    f"full_sweep: meeting {meeting.meeting_id} — "
                    f"fetched via /meeting/:id ({len(races)} races)"
                )

            if races:
                meeting_details_succeeded += 1
            else:
                meeting_details_failed += 1
                if not _first_detail_error:
                    _first_detail_error = {
                        "meeting_id": meeting.meeting_id,
                        "stage": "zero_races_returned",
                        "detail_diag": dict(getattr(conn, "_last_detail_diag", {})),
                    }
                log.warning(
                    f"full_sweep: meeting {meeting.meeting_id} detail returned 0 races "
                    f"(meeting_ids_missing={meeting_ids_missing}, "
                    f"discovery_failed={_discovery_failed})"
                )

            meetings_fetched += 1
            races_found += len(races)
            runners_found += len(runners)

            # Build a mapping race_uid → runners for storage after race upsert
            runners_by_race: dict[str, list[Any]] = {}
            for runner in runners:
                runners_by_race.setdefault(runner.race_uid, []).append(runner)

            for race in races:
                stored_ok = _store_with_pipeline(race)
                races_stored += 1
                if not stored_ok:
                    races_blocked += 1

                # Store runners associated with this race
                race_runners = runners_by_race.get(race.race_uid, [])
                if race_runners:
                    stored = _store_runners_for_race(race.race_uid, race_runners)
                    runners_stored += stored

        except Exception as e:
            meeting_details_failed += 1
            if not _first_detail_error:
                _first_detail_error = {
                    "meeting_id": meeting.meeting_id,
                    "stage": "exception",
                    "error": str(e),
                    "detail_diag": dict(getattr(conn, "_last_detail_diag", {})),
                }
            log.error(f"full_sweep: failed for meeting {meeting.meeting_id}: {e}")

    log.info(
        f"full_sweep complete: {meetings_found} meetings found, {meetings_fetched} fetched, "
        f"ids_found={meeting_ids_found} ids_missing={meeting_ids_missing} "
        f"details attempted={meeting_details_attempted} "
        f"succeeded={meeting_details_succeeded} failed={meeting_details_failed} "
        f"{races_stored} races stored ({races_blocked} blocked), "
        f"{runners_stored} runners stored for {today}"
    )
    # Mandatory pipeline validation output
    log.info(
        f"PIPELINE VALIDATION: date={today} "
        f"races_loaded={races_stored} "
        f"runners_inserted={runners_stored} "
        f"runners_skipped=0"
    )
    return {
        "ok": True,
        "date": today,
        "meetings_found": meetings_found,
        "meetings_fetched": meetings_fetched,
        "meeting_ids_found": meeting_ids_found,
        "meeting_ids_missing": meeting_ids_missing,
        "meeting_details_attempted": meeting_details_attempted,
        "meeting_details_succeeded": meeting_details_succeeded,
        "meeting_details_failed": meeting_details_failed,
        "races_found": races_found,
        "runners_found": runners_found,
        "races_stored": races_stored,
        "runners_stored": runners_stored,
        "races_blocked": races_blocked,
        "races_passed": races_stored - races_blocked,
        "discovery_failed": _discovery_failed,
        "discovery_diag": _discovery_diag,
        "first_detail_error": _first_detail_error,
        # legacy keys kept for callers that rely on them
        "meetings": meetings_found,
        "races": races_stored,
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
# NEAR-JUMP REFRESH — OddsPro authoritative + FormFav overlay
# ------------------------------------------------------------

def near_jump_refresh(target_date: str | None = None) -> dict[str, Any]:
    """
    Near-jump engine: refresh races with NTJ < 10 min using OddsPro,
    then apply FormFav provisional overlay for eligible races.

    Called more frequently than rolling_refresh (every ~60s).
    Only processes races identified as near-jump from stored jump_time.

    FormFav overlay is applied ONLY here, never for non-near-jump races.
    Overlay data is stored in the provisional store, NOT in official tables.
    """
    today = target_date or date.today().isoformat()
    conn = _get_oddspro()

    if not conn.is_enabled():
        log.warning("near_jump_refresh skipped: OddsPro not configured")
        return {"ok": False, "reason": "oddspro_not_configured", "date": today}

    races_refreshed = 0
    overlay_count = 0
    near_jump_count = 0

    try:
        from database import get_active_races
        from race_status import compute_ntj, should_trigger_formfav_overlay

        active_races = get_active_races(today)

        for stored_race in active_races:
            # Near-jump gate: only process races with NTJ < 10 min
            ntj = compute_ntj(stored_race.get("jump_time"), stored_race.get("date"))
            if not ntj.get("is_near_jump"):
                continue

            near_jump_count += 1
            oddspro_race_id = stored_race.get("oddspro_race_id") or ""
            if not oddspro_race_id:
                continue

            try:
                # OddsPro authoritative refresh for near-jump race
                fresh_race, _runners = conn.fetch_race_with_runners(oddspro_race_id)
                if fresh_race:
                    integrity_ok = _store_with_pipeline(fresh_race)
                    races_refreshed += 1
                    fresh_dict = _race_to_dict(fresh_race)

                    # Clear stale overlay ONLY after successful OddsPro refresh
                    # AND validation + integrity passed (not blocked)
                    if integrity_ok:
                        clear_provisional_overlay_for_race(fresh_dict.get("race_uid", ""))

                    # FormFav provisional overlay — near-jump eligible only
                    if should_trigger_formfav_overlay(fresh_dict):
                        enriched = formfav_overlay(
                            fresh_dict.get("race_uid", ""), fresh_dict
                        )
                        if enriched.get("has_provisional_overlay"):
                            _store_provisional_overlay(
                                fresh_dict["race_uid"], enriched
                            )
                            overlay_count += 1

            except Exception as e:
                log.error(
                    f"near_jump_refresh: failed for race {oddspro_race_id}: {e}"
                )

    except Exception as e:
        log.error(f"near_jump_refresh: outer error: {e}")
        return {"ok": False, "error": "Data engine error", "date": today}

    log.info(
        f"near_jump_refresh: {near_jump_count} near-jump races identified, "
        f"{races_refreshed} refreshed via OddsPro, "
        f"{overlay_count} FormFav overlays applied"
    )
    return {
        "ok": True,
        "date": today,
        "near_jump_races": near_jump_count,
        "races_refreshed": races_refreshed,
        "formfav_overlays": overlay_count,
        "source": "oddspro+formfav_overlay",
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
# AU RACE DETECTION — FormFav compatibility helper
# ------------------------------------------------------------

# Known Australian state/territory abbreviations used in the `state` field.
# Also includes country-level identifiers ('au', 'aus', 'australia') because
# some API responses populate the state field with the country code instead of
# a state abbreviation, especially for interstate or undifferentiated AU meetings.
_AU_STATES = frozenset({
    "vic", "nsw", "qld", "sa", "wa", "tas", "act", "nt",
    "au", "aus", "australia",
})


def _is_au_race(race: dict[str, Any]) -> bool:
    """
    Return True when a race is an Australian race eligible for FormFav enrichment.
    FormFav only supports AU tracks.

    Detection uses the `state` field (AU state/territory codes) stored in
    today_races.  Races with an unrecognised or empty state are treated as AU
    because OddsPro defaults to Australian data (ODDSPRO_COUNTRY=au).
    """
    state = (race.get("state") or "").strip().lower()
    if not state:
        # No state info — assume AU (OddsPro country default is 'au')
        return True
    # Accept if state matches any known AU code, or if it is not a clearly
    # non-AU identifier (e.g. "usa", "uk", "hk", "fr", "nz" etc.)
    return not state or state in _AU_STATES


# ------------------------------------------------------------
# FORMFAV PERSISTENT ENRICHMENT SYNC
# ------------------------------------------------------------

def formfav_sync(target_date: str | None = None) -> dict[str, Any]:
    """
    Persistent FormFav enrichment sync — fetches full FormFav data for all
    today's races and stores it in formfav_race_enrichment /
    formfav_runner_enrichment tables.

    This is a SECONDARY enrichment source only. OddsPro remains the primary
    source of record. FormFav data is stored separately and never overwrites
    official race/runner records.

    Called by the scheduler every 300s when FormFav API key is configured.
    Returns a summary dict for health tracking.
    """
    ff = _get_formfav()
    if not ff.is_enabled():
        return {"ok": False, "reason": "formfav_not_enabled", "races_enriched": 0, "runners_enriched": 0}

    td = target_date or date.today().isoformat()

    try:
        from database import (
            get_active_races,
            upsert_formfav_race_enrichment,
            upsert_formfav_runner_enrichment,
        )
    except ImportError as e:
        log.error(f"data_engine.formfav_sync: import error: {e}")
        return {"ok": False, "reason": "import_error", "races_enriched": 0, "runners_enriched": 0}

    races = get_active_races(td)
    if not races:
        log.debug(f"data_engine.formfav_sync: no active races for {td}")
        return {"ok": True, "races_enriched": 0, "runners_enriched": 0, "date": td}

    # FormFav only supports AU races — filter to Australian races only.
    # International races remain in the system but are excluded from FormFav processing.
    au_races = [r for r in races if _is_au_race(r)]
    skipped_intl = len(races) - len(au_races)
    if skipped_intl:
        log.info(
            f"data_engine.formfav_sync: skipping {skipped_intl} non-AU races "
            f"(FormFav supports AU only); processing {len(au_races)} AU races"
        )

    races_enriched = 0
    runners_enriched = 0
    errors = 0
    fetched_at = datetime.now(timezone.utc).isoformat()

    for race in au_races:
        race_uid = race.get("race_uid") or ""
        if not race_uid:
            continue

        # Map OddsPro canonical code to FormFav code
        # OddsPro uses GALLOPS; FormFav connector accepts HORSE/HARNESS/GREYHOUND
        raw_code = (race.get("code") or "HORSE").upper()
        ff_code = "HORSE" if raw_code == "GALLOPS" else raw_code

        try:
            ff_race, ff_runners = ff.fetch_race_form_with_predictions(
                target_date=race.get("date") or td,
                track=race.get("track") or "",
                race_num=int(race.get("race_num") or 0),
                code=ff_code,
            )

            # Build race enrichment payload using canonical race_uid from OddsPro
            race_payload = {
                "race_uid":          race_uid,
                "date":              ff_race.date,
                "track":             ff_race.track,
                "race_num":          ff_race.race_num,
                "race_code":         raw_code,
                "race_name":         ff_race.race_name,
                "distance":          ff_race.distance,
                "grade":             ff_race.grade,
                "condition":         ff_race.condition,
                "weather":           ff_race.weather,
                "start_time":        ff_race.start_time,
                "start_time_utc":    ff_race.start_time_utc,
                "timezone":          ff_race.timezone,
                "abandoned":         ff_race.abandoned,
                "number_of_runners": ff_race.number_of_runners,
                "pace_scenario":     ff_race.pace_scenario,
                "raw_response":      ff_race.raw_response,
                "fetched_at":        fetched_at,
            }
            upsert_formfav_race_enrichment(race_payload)
            races_enriched += 1

            # Store each runner's enrichment
            for runner in ff_runners:
                stats = runner.stats_json or {}
                runner_payload = {
                    "race_uid":           race_uid,
                    "runner_name":        runner.name,
                    "number":             runner.number if runner.number is not None else runner.box_num,
                    "barrier":            runner.barrier,
                    "age":                runner.age,
                    "claim":              runner.claim,
                    "scratched":          runner.scratched,
                    "form_string":        runner.form_string,
                    "trainer":            runner.trainer,
                    "jockey":             runner.jockey,
                    "driver":             runner.driver,
                    "weight":             runner.weight,
                    "decorators":         runner.decorators or [],
                    "speed_map":          runner.speed_map,
                    "class_profile":      runner.class_profile,
                    "race_class_fit":     runner.race_class_fit,
                    "stats_overall":      stats.get("overall"),
                    "stats_track":        runner.stats_track,
                    "stats_distance":     runner.stats_distance,
                    "stats_condition":    runner.stats_condition,
                    "stats_track_distance": runner.stats_track_distance,
                    "stats_full":         stats,
                    "win_prob":           runner.win_prob,
                    "place_prob":         runner.place_prob,
                    "model_rank":         runner.model_rank,
                    "confidence":         runner.confidence,
                    "model_version":      runner.model_version,
                    "fetched_at":         fetched_at,
                }
                # Only store runners with a valid number
                if runner_payload["number"] is not None:
                    upsert_formfav_runner_enrichment(runner_payload)
                    runners_enriched += 1

        except Exception as e:
            log.debug(f"data_engine.formfav_sync: failed for {race_uid}: {e}")
            errors += 1
            continue

    log.info(
        f"data_engine.formfav_sync: date={td} "
        f"races_enriched={races_enriched} runners_enriched={runners_enriched} errors={errors} "
        f"skipped_international={skipped_intl}"
    )
    # Mandatory pipeline validation output
    log.info(
        f"PIPELINE VALIDATION: date={td} "
        f"formfav_races_enriched={races_enriched}"
    )
    return {
        "ok": True,
        "date": td,
        "races_enriched": races_enriched,
        "runners_enriched": runners_enriched,
        "errors": errors,
        "skipped_international": skipped_intl,
        "source": "formfav",
    }


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


def _store_with_pipeline(race: Any) -> bool:
    """
    Enforce pipeline order before storing an OddsPro race record:
      1. Normalize  (already done by connector)
      2. Validate   (log warning; OddsPro data is stored regardless)
      3. Integrity  (if blocked, mark before storing)
      4. Store

    Board building (step 5) happens separately in board_builder.py using
    the stored records. Blocked races are stored with status='blocked' so
    they are tracked explicitly rather than silently dropped.

    Returns True if the race passed integrity (not blocked), False otherwise.
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
        return bool(allowed)

    except Exception as e:
        log.error(f"data_engine: _store_with_pipeline failed: {e}")
        return False


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


def _store_runners_for_race(race_uid: str, runners: list[Any]) -> int:
    """
    Store runners for a race that has already been written to today_races.
    Looks up the race's UUID primary key, then calls upsert_runners.
    Returns the count of runners successfully stored (0 on failure).
    """
    if not runners:
        return 0
    try:
        from database import get_race, upsert_runners

        row = get_race(race_uid)
        if not row:
            log.warning(f"data_engine: _store_runners_for_race: race {race_uid} not found in DB")
            return 0

        race_db_id = row.get("id")
        if not race_db_id:
            log.warning(f"data_engine: _store_runners_for_race: race {race_uid} has no DB id")
            return 0

        runner_dicts = [r.__dict__ if hasattr(r, "__dict__") else r for r in runners]
        stored = upsert_runners(race_db_id, runner_dicts)
        log.debug(f"data_engine: stored {stored} runners for race {race_uid}")
        return stored

    except Exception as e:
        log.error(f"data_engine: _store_runners_for_race({race_uid}) failed: {e}")
        return 0


def _apply_formfav_overlay(race: dict[str, Any]) -> None:
    """
    Apply FormFav provisional overlay and store in the in-memory overlay store.
    Does NOT write to official tables — overlay is provisional and ephemeral.
    Cleared when the next OddsPro authoritative sweep covers this race.
    """
    race_uid = race.get("race_uid") or ""
    if not race_uid:
        return
    enriched = formfav_overlay(race_uid, race)
    if enriched.get("has_provisional_overlay"):
        _store_provisional_overlay(race_uid, enriched)
    log.debug(f"data_engine: provisional overlay stored for {race_uid} (not persisted)")


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
