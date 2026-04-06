"""
data_engine.py - DemonPulse Data Engine
=========================================
OddsPro is the PRIMARY and AUTHORITATIVE source of record.
FormFav is a SECONDARY enrichment source — persistent race/runner data
stored in formfav_race_enrichment / formfav_runner_enrichment tables.

Core functions:
  full_sweep()          - OddsPro daily bootstrap via /api/external/meetings
                          (with /api/meetings discovery fallback for meeting ID resolution)
  rolling_refresh()     - OddsPro meeting/race refresh via /api/external/meeting/:id
  near_jump_refresh()   - OddsPro near-jump refresh + FormFav provisional overlay
  check_results()       - OddsPro result sweep via /api/external/results
  formfav_sync()        - FormFav second-stage persistent enrichment (AU/NZ races only)
  formfav_overlay()     - FormFav provisional enrichment (near-jump only)
  get_provisional_overlays() - Return current provisional overlay store

Architecture rules enforced here:
  - OddsPro builds the day (full_sweep)
  - OddsPro builds official board state (rolling_refresh, near_jump_refresh)
  - OddsPro-confirmed data is official truth
  - FormFav enrichment runs AFTER race + runner inserts (second-stage)
  - FormFav only processes AU and NZ races (filter before API call)
  - FormFav persistent data stored in separate enrichment tables
  - FormFav provisional overlays stored in-memory (near-jump only)
  - NTJ calculated from stored jump_time (race_status.compute_ntj)
  - Blocked races tracked explicitly in database
"""
import logging
import threading
from datetime import date, datetime, timezone
from typing import Any

import requests as _requests_lib

log = logging.getLogger(__name__)

try:
    import pipeline_state as _pipeline_state
except ImportError:
    _pipeline_state = None  # type: ignore[assignment]

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

    # Reset pipeline state at the start of each full sweep so counters reflect
    # the current day's run rather than accumulating across multiple days.
    if _pipeline_state is not None:
        _pipeline_state.reset()

    log.info(
        f"full_sweep: fetching domestic-only meetings from OddsPro "
        f"(location=domestic, date={today})"
    )
    try:
        meetings = conn.fetch_meetings(today, location="domestic")
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
            log.info(
                f"[ODDSPRO] DISCOVERY start: /api/meetings location=domestic"
                f" — resolving numeric meeting IDs for {meetings_found} meetings"
            )
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
                f"[ODDSPRO] DISCOVERY complete: loaded {len(_disc_by_track)} meetings"
                f" from /api/meetings"
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
                _race_country = getattr(race, "country", "au") or "au"
                log.info(
                    f"[ODDSPRO] DISCOVERED race_uid={race.race_uid}"
                    f" track={race.track!r} country={_race_country!r}"
                )
                if _pipeline_state is not None:
                    _pipeline_state.record_race_discovered(
                        race.race_uid, race.track, _race_country
                    )
                stored_ok = _store_with_pipeline(race)
                races_stored += 1
                if not stored_ok:
                    races_blocked += 1
                else:
                    log.debug(
                        f"[ODDSPRO] RACE_INSERTED race_uid={race.race_uid}"
                        f" track={race.track!r} race_num={race.race_num}"
                        f" code={race.code!r} country={_race_country!r}"
                    )

                # Store runners associated with this race
                race_runners = runners_by_race.get(race.race_uid, [])
                if race_runners:
                    stored = _store_runners_for_race(race.race_uid, race_runners)
                    runners_stored += stored
                    log.debug(
                        f"[ODDSPRO] RUNNERS_INSERTED race_uid={race.race_uid}"
                        f" runners_stored={stored}"
                    )

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

    # Tally domestic vs international races from the parsed meetings to verify
    # the domestic-only filter is working end-to-end.  OddsPro applies the
    # location=domestic filter at the API level so international_found should
    # always be 0, but we count explicitly here so any leakage is visible in
    # the logs rather than hidden behind "international excluded: 0 (API filter)".
    _au_country_codes = {"au", "aus", "australia"}
    _nz_country_codes = {"nz", "nzl", "new zealand", "new-zealand"}
    _domestic_races = 0
    _international_races = 0
    for m in meetings:
        country = (m.country or "").strip().lower()
        state = (m.state or "").strip().lower()
        if country in _au_country_codes or country in _nz_country_codes:
            _domestic_races += 1
        elif not country:
            # No country on the meeting record — check state for AU codes
            _au_states = {
                "vic", "nsw", "qld", "sa", "wa", "tas", "act", "nt",
                "au", "aus", "australia",
            }
            _nz_codes = {"nz", "nzl", "new zealand", "new-zealand"}
            if state in _au_states or state in _nz_codes:
                _domestic_races += 1
            else:
                # Unknown — assume domestic since location=domestic was requested
                _domestic_races += 1
        else:
            _international_races += 1
            log.warning(
                f"full_sweep: international meeting detected despite location=domestic "
                f"filter — track={m.track!r} country={m.country!r} state={m.state!r}. "
                f"Review OddsPro domestic filter or ODDSPRO_COUNTRY env var."
            )

    log.info(
        f"full_sweep complete: {meetings_found} meetings found, {meetings_fetched} fetched, "
        f"ids_found={meeting_ids_found} ids_missing={meeting_ids_missing} "
        f"details attempted={meeting_details_attempted} "
        f"succeeded={meeting_details_succeeded} failed={meeting_details_failed} "
        f"{races_stored} races stored ({races_blocked} blocked), "
        f"{runners_stored} runners stored for {today}"
    )
    log.info(
        f"LOCATION FILTER: domestic-only feed applied at OddsPro source "
        f"(location=domestic) — meetings_domestic={_domestic_races} "
        f"meetings_international_detected={_international_races} "
        f"races_discovered={races_found} "
        f"races_stored={races_stored} international_excluded={_international_races}"
    )
    # Mandatory pipeline validation output
    log.info(
        f"PIPELINE VALIDATION: date={today} "
        f"races_loaded={races_stored} "
        f"races_domestic={_domestic_races} "
        f"races_international_excluded={_international_races} "
        f"runners_inserted={runners_stored} "
        f"runners_skipped=0"
    )
    return {
        "ok": True,
        "date": today,
        "location_filter": "domestic",
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
        # International meetings detected in the domestic-only pipeline.
        # Should always be 0; non-zero means the OddsPro location=domestic
        # filter is not being applied correctly or ODDSPRO_COUNTRY is wrong.
        "international_excluded": _international_races,
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
        results = conn.fetch_results(today, location="domestic")
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

        # Map GALLOPS→HORSE for FormFav compatibility.
        # FormFavConnector.RACE_CODE_MAP accepts HORSE/HARNESS/GREYHOUND;
        # GALLOPS is a legacy OddsPro code. The connector's fallback default
        # handles it, but explicit mapping here keeps the path consistent
        # with formfav_sync which already applies the same remapping.
        raw_code = (race.get("code") or "HORSE").upper()
        ff_code = "HORSE" if raw_code == "GALLOPS" else raw_code

        ff_race, ff_runners = ff.fetch_race_form(
            target_date=race.get("date") or date.today().isoformat(),
            track=race.get("track") or "",
            race_num=int(race.get("race_num") or 0),
            code=ff_code,
            country=_get_formfav_country(race),
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
# AU/NZ RACE DETECTION — FormFav compatibility helper
# ------------------------------------------------------------

# Known Australian state/territory abbreviations used in the `state` field.
# Also includes country-level identifiers ('au', 'aus', 'australia') because
# some API responses populate the state field with the country code instead of
# a state abbreviation, especially for interstate or undifferentiated AU meetings.
_AU_STATES = frozenset({
    "vic", "nsw", "qld", "sa", "wa", "tas", "act", "nt",
    "au", "aus", "australia",
})

# Known New Zealand identifiers that may appear in the `state` field.
_NZ_IDENTIFIERS = frozenset({
    "nz", "nzl", "new zealand", "new-zealand",
    "auckland", "wellington", "christchurch", "otago", "canterbury",
    "hawkes bay", "hawkes-bay", "waikato", "manawatu", "northland",
    "taranaki", "southland", "nelson", "marlborough",
})

# Country codes that map to Australia / New Zealand in the `country` field.
# These are lowercased ISO-style codes that OddsPro may return.
_AU_COUNTRY_CODES = frozenset({"au", "aus", "australia"})
_NZ_COUNTRY_CODES = frozenset({"nz", "nzl", "new zealand", "new-zealand"})

# Maps OddsPro canonical race codes to FormFav race_code labels for logging.
_FF_CODE_DISPLAY: dict[str, str] = {
    "HORSE":     "gallops",
    "HARNESS":   "harness",
    "GREYHOUND": "greyhounds",
}

# Valid OddsPro race codes after normalisation (GALLOPS is remapped to HORSE first).
_FF_VALID_CODES = frozenset({"HORSE", "HARNESS", "GREYHOUND"})

# FormFav API base URL — shared with the connector, used in log messages.
_FF_BASE_URL = "https://api.formfav.com"


def _is_au_nz_race(race: dict[str, Any]) -> bool:
    """
    Return True when a race is an Australian or New Zealand race eligible
    for FormFav enrichment.  FormFav supports AU and NZ tracks only.

    Detection strategy (in priority order):
    1. `country` field — added to today_races in the FormFav integration phase.
       If present and non-empty, use it as the authoritative filter.
       International races (e.g. Bath=GB, Hanshin=JP) will have a non-AU/NZ
       country and are correctly excluded.
    2. `state` field — fallback for records that pre-date the country column.
       Known AU state abbreviations and NZ region names are recognised.
    3. Empty both fields — assume AU only if both country AND state are empty,
       since OddsPro's default country is 'au'.
    """
    # Primary: explicit country field (stored since FormFav integration update)
    country = (race.get("country") or "").strip().lower()
    if country:
        return country in _AU_COUNTRY_CODES or country in _NZ_COUNTRY_CODES

    # Fallback: state-based detection for older / migration records
    state = (race.get("state") or "").strip().lower()
    if state:
        return state in _AU_STATES or state in _NZ_IDENTIFIERS

    # Both empty: assume AU (OddsPro default country is 'au')
    return True


def _get_formfav_country(race: dict[str, Any]) -> str:
    """
    Return the FormFav country code ('au' or 'nz') for a race.
    Uses the `country` field first, then falls back to the `state` field.
    Defaults to 'au' if the race cannot be identified as NZ.
    """
    country = (race.get("country") or "").strip().lower()
    if country in _NZ_COUNTRY_CODES:
        return "nz"
    state = (race.get("state") or "").strip().lower()
    if state in _NZ_IDENTIFIERS:
        return "nz"
    return "au"


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
        log.info(f"[FORMFAV] SKIPPED all: no active races for {td}")
        return {"ok": True, "races_enriched": 0, "runners_enriched": 0, "date": td}

    log.info(
        f"[FORMFAV] SYNC START date={td} total_active_races={len(races)}"
        f" — processing eligibility filters (AU/NZ, race_code, track/race_num)"
    )

    races_enriched = 0
    runners_enriched = 0
    requests_made = 0
    errors = 0
    # Separate counters per skip reason so the summary is unambiguous.
    skipped_international = 0   # country is not AU or NZ
    skipped_missing_fields = 0  # race_uid / track / race_num absent
    skipped_invalid_code = 0    # code is not HORSE, HARNESS, GREYHOUND
    fetched_at = datetime.now(timezone.utc).isoformat()

    for race in races:
        race_uid = race.get("race_uid") or ""
        # Build a consistent identifier for log messages (race_uid if available,
        # else track/race_num which are always present).
        _log_id = race_uid or f"{race.get('track','?')}/{race.get('race_num','?')}"

        # --- AU/NZ filter (BEFORE FormFav call) ---
        if not _is_au_nz_race(race):
            country_val = race.get("country") or race.get("state") or "unknown"
            log.info(
                f"[FORMFAV] SKIPPED race_uid={_log_id}"
                f" reason=international_excluded country={country_val!r}"
            )
            skipped_international += 1
            if _pipeline_state is not None and race_uid:
                _pipeline_state.record_formfav_skipped(race_uid, "international_excluded")
            continue

        # --- Validate required fields ---
        if not race_uid:
            log.info(
                f"[FORMFAV] SKIPPED race_uid={_log_id} reason=missing_race_uid"
            )
            skipped_missing_fields += 1
            continue

        # Map OddsPro canonical code to FormFav code.
        # OddsPro normalises to HORSE/HARNESS/GREYHOUND; the FormFavConnector's
        # RACE_CODE_MAP converts these to gallops/harness/greyhounds respectively.
        # If the code is still stored as GALLOPS (from earlier OddsPro builds),
        # remap it to HORSE so the connector handles it correctly.
        raw_code = (race.get("code") or "HORSE").upper()
        ff_code = "HORSE" if raw_code == "GALLOPS" else raw_code

        if ff_code not in _FF_VALID_CODES:
            log.info(
                f"[FORMFAV] SKIPPED race_uid={race_uid}"
                f" reason=invalid_code code={raw_code!r}"
                f" (expected HORSE, HARNESS or GREYHOUND)"
            )
            skipped_invalid_code += 1
            if _pipeline_state is not None:
                _pipeline_state.record_formfav_skipped(race_uid, "invalid_code")
            continue

        track = race.get("track") or ""
        race_num = int(race.get("race_num") or 0)
        race_date = race.get("date") or td

        if not track or not race_num:
            log.info(
                f"[FORMFAV] SKIPPED race_uid={race_uid}"
                f" reason=missing_track_or_race_num track={track!r} race_num={race_num}"
            )
            skipped_missing_fields += 1
            if _pipeline_state is not None:
                _pipeline_state.record_formfav_skipped(race_uid, "missing_track_or_race_num")
            continue

        # Determine the correct country to send to FormFav (au or nz).
        ff_country = _get_formfav_country(race)
        mapped_race_code = _FF_CODE_DISPLAY.get(ff_code, ff_code.lower())

        # --- Issue FormFav API call ---
        log.info(
            f"[FORMFAV] ELIGIBLE race_uid={race_uid}"
            f" track={track!r} race_num={race_num} code={ff_code!r}"
            f" ff_code={mapped_race_code!r} country={ff_country!r}"
        )
        if _pipeline_state is not None:
            _pipeline_state.record_formfav_eligible(race_uid)
        log.info(
            f"[FORMFAV] CALL"
            f" url={_FF_BASE_URL}/v1/form"
            f" params=date={race_date}&track={track}&race={race_num}"
            f"&race_code={mapped_race_code}&country={ff_country}"
            f" race_uid={race_uid}"
        )
        if _pipeline_state is not None:
            _pipeline_state.record_formfav_called(race_uid)

        try:
            requests_made += 1
            ff_race, ff_runners = ff.fetch_race_form_with_predictions(
                target_date=race_date,
                track=track,
                race_num=race_num,
                code=ff_code,
                country=ff_country,
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
            log.debug(
                f"[FORMFAV] DB_WRITE race_enrichment race_uid={race_uid}"
                f" track={track!r} race_num={race_num}"
            )
            races_enriched += 1

            # Store each runner's enrichment
            race_runners_enriched = 0
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
                    race_runners_enriched += 1

            log.info(
                f"[FORMFAV] SUCCESS race_uid={race_uid}"
                f" track={track!r} race_num={race_num}"
                f" runners_enriched={race_runners_enriched}"
            )
            if _pipeline_state is not None:
                _pipeline_state.record_formfav_success(race_uid)

        except Exception as e:
            import requests as _req_lib
            status_code: int | None = None
            resp_body: str = ""
            if isinstance(e, _req_lib.HTTPError) and e.response is not None:
                status_code = e.response.status_code
                try:
                    resp_body = e.response.text[:300]
                except Exception:
                    pass
            log.warning(
                f"[FORMFAV] FAILED race_uid={race_uid}"
                f" track={track!r} race_num={race_num} code={ff_code!r}"
                f" country={ff_country!r} status={status_code}"
                f" body={resp_body!r} error={e}"
            )
            if _pipeline_state is not None:
                _pipeline_state.record_formfav_failed(race_uid)
            errors += 1
            continue

    _total_skipped = skipped_international + skipped_missing_fields + skipped_invalid_code
    _au_nz_eligible = len(races) - skipped_international

    log.info(
        f"[FORMFAV] SYNC COMPLETE date={td}"
        f" total_races={len(races)}"
        f" au_nz_eligible={_au_nz_eligible}"
        f" international_excluded={skipped_international}"
        f" requests_made={requests_made}"
        f" races_enriched={races_enriched} runners_enriched={runners_enriched}"
        f" errors={errors}"
        f" skipped_missing_fields={skipped_missing_fields}"
        f" skipped_invalid_code={skipped_invalid_code}"
    )
    if races_enriched > 0:
        log.info(
            f"[FORMFAV] READBACK enriched_races_today={races_enriched}"
            f" — stored in formfav_race_enrichment table,"
            f" readable via GET /api/formfav/status"
            f" and attached to race items via GET /api/board"
        )
    # Mandatory pipeline validation output
    log.info(
        f"PIPELINE VALIDATION: date={td} "
        f"formfav_total_active={len(races)} "
        f"formfav_au_nz_eligible={_au_nz_eligible} "
        f"formfav_international_excluded={skipped_international} "
        f"formfav_requests_made={requests_made} "
        f"formfav_races_enriched={races_enriched} "
        f"formfav_runners_enriched={runners_enriched} "
        f"formfav_skipped_missing_fields={skipped_missing_fields} "
        f"formfav_skipped_invalid_code={skipped_invalid_code}"
    )
    return {
        "ok": True,
        "date": td,
        "au_nz_eligible": _au_nz_eligible,
        "requests_made": requests_made,
        "races_enriched": races_enriched,
        "runners_enriched": runners_enriched,
        "errors": errors,
        "skipped_international": skipped_international,
        "skipped_missing_fields": skipped_missing_fields,
        "skipped_invalid_code": skipped_invalid_code,
        "skipped": _total_skipped,
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
      0. Domestic failsafe (hard gate: only AU/NZ races reach the DB)
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

        # Step 0 — Domestic failsafe (HARD GATE — no international race enters the DB)
        _country = (race_dict.get("country") or "au").strip().lower()
        if _country not in _AU_COUNTRY_CODES and _country not in _NZ_COUNTRY_CODES:
            log.warning(
                f"[ODDSPRO] EXCLUDED race_uid={race_uid}"
                f" reason=non_domestic_guard country={_country!r}"
            )
            if _pipeline_state is not None:
                _pipeline_state.record_race_excluded(race_uid, "non_domestic_guard")
            return False

        log.info(
            f"[ODDSPRO] INCLUDED race_uid={race_uid}"
            f" reason=domestic_source country={_country!r}"
        )
        if _pipeline_state is not None:
            _pipeline_state.record_race_included(race_uid)

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
