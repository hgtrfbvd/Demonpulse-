"""
data_engine.py - DemonPulse Data Engine
=========================================
10-step pipeline architecture: both OddsPro and FormFav are fetched as
independent full datasets, merged by canonical race_id, and classified
using hardcoded race-code-specific track whitelists AFTER the merge.

Core functions:
  full_sweep()          - 10-step pipeline: fetch OddsPro + FormFav → normalize
                          → track aliases → canonical race_id → merge → priority
                          → domestic filter (AFTER merge) → store → log
  rolling_refresh()     - OddsPro meeting/race refresh via /api/external/meeting/:id
  near_jump_refresh()   - OddsPro near-jump refresh + FormFav provisional overlay
  check_results()       - OddsPro result sweep via /api/external/results
  formfav_sync()        - FormFav second-stage persistent enrichment (all active races)
  formfav_overlay()     - FormFav provisional enrichment (near-jump only)
  get_provisional_overlays() - Return current provisional overlay store

Architecture rules enforced here:
  - full_sweep fetches ALL races from BOTH sources before any filtering
  - OddsPro = base layer; FormFav = enrichment (Steps 5-6 merge priority)
  - Domestic classification (Step 8) uses classify_track_by_code():
      IF code == HORSE:     check HORSE_AU_TRACKS / HORSE_NZ_TRACKS
      IF code == GREYHOUND: check GREYHOUND_AU_TRACKS / GREYHOUND_NZ_TRACKS
      IF code == HARNESS:   check HARNESS_AU_TRACKS / HARNESS_NZ_TRACKS
      Country is determined SOLELY from set membership — NO API fields.
      Any race not found in the correct set is EXCLUDED.
  - Domestic filter applied ONLY after the full merged dataset is built
  - EXCLUDED races are NEVER written to any database table (no upsert, no touch)
  - _store_with_pipeline() enforces a hard domestic gate before any DB write
  - FormFav called ONLY when classify_track_by_code() returns a country
  - FormFav persistent data stored in separate enrichment tables
  - FormFav provisional overlays stored in-memory (near-jump only)
  - NTJ calculated from stored jump_time (race_status.compute_ntj)
  - Blocked races tracked explicitly in database

Pipeline order (enforced, no exceptions):
  FETCH → NORMALIZE → MERGE → CLASSIFY → FILTER → WRITE
"""
import logging
import threading
from datetime import date, datetime, timezone
from typing import Any

import requests as _requests_lib

from core.domestic_tracks import (
    AU_COUNTRY_CODES, AU_STATE_IDS, AU_TRACKS,
    CODE_GATED_TRACKS, DOMESTIC_COUNTRY_CODES, DOMESTIC_TRACKS,
    NZ_COUNTRY_CODES, NZ_STATE_IDS, NZ_TRACKS,
    normalize_track, apply_track_alias,
    classify_track_by_code,
    resolve_formfav_track,
)

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


# ---------------------------------------------------------------------------
# PIPELINE HELPERS — STEP 2-4: normalisation, aliases, canonical race_id
# ---------------------------------------------------------------------------

def _normalize_race_code(code: str) -> str:
    """
    Normalise a raw race code string to one of HORSE / HARNESS / GREYHOUND.
    Accepts OddsPro codes (GALLOPS, T, H, G, …) and FormFav codes.
    Defaults to HORSE for unrecognised inputs.
    """
    key = (code or "").upper().strip()
    if key in ("GALLOPS", "THOROUGHBRED", "HORSE", "T"):
        return "HORSE"
    if key in ("HARNESS", "TROT", "H"):
        return "HARNESS"
    if key in ("GREYHOUND", "DOGS", "G"):
        return "GREYHOUND"
    # Unrecognised code — default to HORSE (thoroughbred) as the safest fallback
    return "HORSE"


def _build_canonical_race_id(
    race_date: str, code: str, track: str, race_num: int | str
) -> str:
    """
    Build the canonical race_id used by the merge engine.

    Format: DATE_CODE_TRACK_RACENUM
    All components are normalised before concatenation:
      - date  : YYYY-MM-DD (passed as-is; callers must ensure correct format)
      - code  : HORSE | HARNESS | GREYHOUND   (via _normalize_race_code)
      - track : lowercase hyphen slug after alias resolution
      - num   : integer
    """
    norm_code = _normalize_race_code(code)
    norm_track = apply_track_alias(track)
    return f"{race_date}_{norm_code}_{norm_track}_{int(race_num)}"


# ---------------------------------------------------------------------------
# STEP 1b — FETCH FORMFAV DATASET (all races, no filtering)
# ---------------------------------------------------------------------------

def _fetch_formfav_dataset(target_date: str) -> list[dict[str, Any]]:
    """
    Fetch ALL FormFav races for *target_date* across all supported race codes.

    Returns a list of race dicts each containing:
      - All RaceRecord fields (as dict)
      - '_formfav_runners': list of runner dicts for this race
      - '_canonical_race_id': canonical race_id used by the merge engine
      - 'source': 'formfav'

    No country / domestic filtering is applied here — the merge engine and
    Step 8 domestic filter handle classification after the full dataset is built.

    Returns empty list when FormFav connector is not enabled or all API calls fail.
    """
    ff = _get_formfav()
    if not ff.is_enabled():
        log.info("[FETCH] FORMFAV disabled — connector not configured, skipping FormFav fetch")
        return []

    try:
        all_races = ff.fetch_all_races_for_date(target_date)
    except Exception as exc:
        log.warning(f"[FETCH] FORMFAV fetch_all_races_for_date failed: {exc}")
        return []

    result: list[dict[str, Any]] = []
    for race, runners in all_races:
        d = race.__dict__.copy() if hasattr(race, "__dict__") else dict(race)
        d["source"] = "formfav"
        d["_formfav_runners"] = [
            r.__dict__.copy() if hasattr(r, "__dict__") else dict(r) for r in runners
        ]
        d["_canonical_race_id"] = _build_canonical_race_id(
            d.get("date") or target_date,
            d.get("code") or "HORSE",
            d.get("track") or "",
            int(d.get("race_num") or 0),
        )
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# STEP 5-6 — MERGE ENGINE
# ---------------------------------------------------------------------------

def _merge_race_datasets(
    oddspro_races: list[dict[str, Any]],
    formfav_races: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Combine OddsPro and FormFav datasets using canonical race_id.

    Rules (Step 5):
      - Both sources  → merge  (OddsPro base + FormFav enrichment)
      - OddsPro only  → include as-is
      - FormFav only  → include as-is

    Priority rules (Step 6):
      - OddsPro = base layer (race existence, runners, structure)
      - FormFav = enrichment (form, stats, insights)
      - Never overwrite a valid (non-empty / non-None) OddsPro value
        with an empty / None FormFav value.

    No race is discarded at this stage.

    Emits [MERGE] log lines for each race:
      CREATED      — race_id built from source data
      MERGED       — present in both sources (sources=both)
      ODDS_ONLY    — present in OddsPro only
      FORMFAV_ONLY — present in FormFav only
    """
    merged: dict[str, dict[str, Any]] = {}

    # Index OddsPro races by canonical race_id (Step 4 applied)
    for race in oddspro_races:
        cid = race.get("_canonical_race_id") or race.get("race_uid") or ""
        if not cid:
            continue
        race = dict(race)
        race["_merge_sources"] = ["oddspro"]
        merged[cid] = race
        log.info(f"[MERGE] CREATED race_id={cid}")

    # Merge or add FormFav races
    for ff_race in formfav_races:
        cid = ff_race.get("_canonical_race_id") or ff_race.get("race_uid") or ""
        if not cid:
            continue

        if cid in merged:
            # Present in both sources — enrich OddsPro base with FormFav data
            existing = merged[cid]
            existing["_merge_sources"] = ["oddspro", "formfav"]
            for k, v in ff_race.items():
                if k.startswith("_"):
                    continue
                existing_val = existing.get(k)
                # OddsPro priority: only fill in FormFav value when OddsPro value is
                # absent (None) or empty-string ("").  Both represent "no data" for
                # structured API fields — empty string is treated the same as None
                # because OddsPro omits a field by leaving it blank, not by setting it
                # to a non-empty string.  This matches the Step 6 rule: "Never
                # overwrite valid data with empty/null".
                if (existing_val is None or existing_val == "") and (v is not None and v != ""):
                    existing[k] = v
            # Always capture FormFav runners for enrichment storage
            if ff_race.get("_formfav_runners"):
                existing["_formfav_runners"] = ff_race["_formfav_runners"]
            log.info(f"[MERGE] MERGED race_id={cid} sources=both")
        else:
            # FormFav only — include with FormFav as source
            ff_race = dict(ff_race)
            ff_race["_merge_sources"] = ["formfav"]
            ff_race.setdefault("oddspro_race_id", "")
            merged[cid] = ff_race
            log.info(f"[MERGE] FORMFAV_ONLY race_id={cid}")

    # Log OddsPro-only races (those whose sources list was never upgraded)
    for cid, race in merged.items():
        if race.get("_merge_sources") == ["oddspro"]:
            log.info(f"[MERGE] ODDS_ONLY race_id={cid}")

    return list(merged.values())


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
    10-step pipeline rebuild — fetches BOTH OddsPro and FormFav datasets,
    merges them by canonical race_id, then applies the domestic filter AFTER
    the full dataset is assembled.

    STEP 1  — Fetch ALL races from OddsPro (no location/country pre-filter)
              Fetch ALL races from FormFav (independently, no filter)
    STEP 2  — Normalize: track → slug, race_code → HORSE/HARNESS/GREYHOUND,
              date → AEST YYYY-MM-DD, race_number → int
    STEP 3  — Apply TRACK_ALIASES to resolve variant track names
    STEP 4  — Build canonical race_id = DATE_CODE_TRACK_RACENUM (both datasets)
    STEP 5  — Merge: both → merge; OddsPro only → include; FormFav only → include
    STEP 6  — Priority: OddsPro = base; FormFav = enrichment; no null overwrites
    STEP 7  — Build ONE unified dataset (no duplicates, consistent race_id)
    STEP 8  — Apply domestic filter AFTER merge (track name vs DOMESTIC_TRACKS)
              Domestic  → include in betting pipeline
              International → store but exclude from betting pipeline
    STEP 9  — Storage: raw OddsPro (via pipeline_state), raw FormFav (via
              pipeline_state), merged canonical races (today_races DB table)
    STEP 10 — Logging: [FETCH], [MERGE], [FILTER] format

    Returns diagnostics dict compatible with callers of the previous full_sweep.
    """
    today = target_date or date.today().isoformat()
    conn = _get_oddspro()

    if not conn.is_enabled():
        log.warning("full_sweep skipped: OddsPro not configured")
        return {"ok": False, "reason": "oddspro_not_configured", "date": today}

    # Reset pipeline state counters for this sweep.
    if _pipeline_state is not None:
        _pipeline_state.reset()

    # ----------------------------------------------------------------
    # STEP 1a — FETCH ODDSPRO (all races, NO location/domestic filter)
    # ----------------------------------------------------------------
    log.info(f"[FETCH] ODDSPRO start date={today} (no location pre-filter)")
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
    log.info(f"[FETCH] ODDSPRO meetings_found={meetings_found}")

    if not meetings_found:
        log.info(f"full_sweep: no meetings returned for {today}")
        return {
            "ok": True,
            "meetings_found": 0, "meetings_fetched": 0,
            "meeting_ids_found": 0, "meeting_ids_missing": 0,
            "meeting_details_attempted": 0, "meeting_details_succeeded": 0, "meeting_details_failed": 0,
            "races_found": 0, "runners_found": 0,
            "races_stored": 0, "runners_stored": 0, "races_blocked": 0,
            "meetings": 0, "races": 0,
            "reason": "no_meetings_scheduled",
            "date": today,
        }

    # Count meetings with / without numeric IDs.
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
    meeting_details_attempted = 0
    meeting_details_succeeded = 0
    meeting_details_failed = 0

    # Discovery flow — resolve numeric meeting IDs when absent.
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
                f"[ODDSPRO] DISCOVERY start: /api/meetings"
                f" — resolving numeric meeting IDs for {meetings_found} meetings"
            )
            for dm in conn.fetch_meetings_discovery(location=None):
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

    # Collect all OddsPro races and runners from meetings.
    # Normalisation (Steps 2-4) happens inline in the connector (_parse_race).
    _oddspro_race_objects: list[Any] = []
    _oddspro_runners_by_race: dict[str, list[Any]] = {}

    for meeting in meetings:
        meeting_details_attempted += 1
        try:
            raw_meeting = meeting.extra.get("raw", {})
            embedded_races = raw_meeting.get("races")

            if embedded_races:
                races, runners = conn.parse_meeting_races_with_runners(meeting, raw_meeting)
                log.debug(
                    f"full_sweep: meeting {meeting.meeting_id} — "
                    f"used embedded races ({len(races)} races)"
                )
            elif _needs_discovery:
                disc_raw = _disc_by_track.get(meeting.track)
                if disc_raw:
                    disc_races = disc_raw.get("races")
                    disc_id = disc_raw.get("id") or disc_raw.get("meetingId")
                    if disc_races:
                        races, runners = conn.parse_meeting_races_with_runners(
                            meeting, disc_raw
                        )
                        log.debug(
                            f"full_sweep: meeting {meeting.meeting_id} — "
                            f"used discovery embedded races ({len(races)} races)"
                        )
                    elif disc_id:
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
                else:
                    log.warning(
                        f"full_sweep: discovery did not resolve track {meeting.track!r} "
                        f"(discovery_failed={_discovery_failed}) — fetching via "
                        f"identifier {meeting.meeting_id!r} (may return 0 races)"
                    )
                    races, runners = conn.fetch_meeting_races_with_runners(meeting)
            else:
                races, runners = conn.fetch_meeting_races_with_runners(meeting)

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

            for runner in runners:
                _oddspro_runners_by_race.setdefault(runner.race_uid, []).append(runner)

            _oddspro_race_objects.extend(races)

            for race in races:
                _race_country = getattr(race, "country", "") or ""
                log.info(
                    f"[ODDSPRO] DISCOVERED race_uid={race.race_uid}"
                    f" track={race.track!r} country={_race_country!r}"
                )
                if _pipeline_state is not None:
                    _pipeline_state.record_race_discovered(
                        race.race_uid, race.track, _race_country
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

    log.info(f"[FETCH] ODDSPRO count={len(_oddspro_race_objects)}")

    # ----------------------------------------------------------------
    # STEP 1b — FETCH FORMFAV (all races, no filtering)
    # ----------------------------------------------------------------
    log.info(f"[FETCH] FORMFAV start date={today}")
    _formfav_raw = _fetch_formfav_dataset(today)
    log.info(f"[FETCH] FORMFAV count={len(_formfav_raw)}")

    # ----------------------------------------------------------------
    # STEPS 2-4 — Normalize OddsPro races and build canonical race_ids
    # (FormFav races are already normalized by _fetch_formfav_dataset)
    # ----------------------------------------------------------------
    _oddspro_dicts: list[dict[str, Any]] = []
    for race in _oddspro_race_objects:
        d = _race_to_dict(race)
        cid = _build_canonical_race_id(
            d.get("date") or today,
            d.get("code") or "HORSE",
            d.get("track") or "",
            int(d.get("race_num") or 0),
        )
        d["_canonical_race_id"] = cid
        d["_formfav_runners"] = []
        _oddspro_dicts.append(d)

    # ----------------------------------------------------------------
    # STEPS 5-6 — MERGE ENGINE
    # ----------------------------------------------------------------
    merged_races = _merge_race_datasets(_oddspro_dicts, _formfav_raw)

    # MERGE-STAGE FormFav tracking: record counters for all FormFav races
    # fetched and for those that matched an OddsPro race.
    if _pipeline_state is not None:
        for _ff_race in _formfav_raw:
            _ff_track = (_ff_race.get("track") or "").strip()
            _pipeline_state.record_formfav_merge_called(_ff_track)
        for _mr in merged_races:
            if "formfav" in (_mr.get("_merge_sources") or []) and "oddspro" in (_mr.get("_merge_sources") or []):
                _pipeline_state.record_formfav_merge_matched(
                    _mr.get("race_uid") or _mr.get("_canonical_race_id") or ""
                )

    # ----------------------------------------------------------------
    # STEPS 7-8 — BUILD FINAL BOARD + DOMESTIC FILTER (AFTER MERGE)
    # ----------------------------------------------------------------
    races_stored = 0
    runners_stored = 0
    races_blocked = 0
    domestic_count = 0
    international_count = 0

    for race_dict in merged_races:
        _track = (race_dict.get("track") or "").strip()
        track_key = apply_track_alias(_track)

        # Step 8 — DOMESTIC CLASSIFICATION
        # Country is determined SOLELY from classify_track_by_code():
        # checks track membership in the appropriate code-specific hardcoded
        # set (HORSE_AU/NZ, GREYHOUND_AU/NZ, HARNESS_AU/NZ).
        # No API country or state fields are consulted.
        race_code = (race_dict.get("code") or "").upper()
        # GALLOPS is a legacy alias for HORSE in some older OddsPro builds
        if race_code == "GALLOPS":
            race_code = "HORSE"

        effective_country = classify_track_by_code(_track, race_code) or ""
        is_domestic = bool(effective_country)
        country_source = "track_code_set" if is_domestic else "track_code_set_miss"

        race_uid_for_log = race_dict.get("race_uid") or ""
        log.info(
            f"[COUNTRY_RESOLVED] race_uid={race_uid_for_log}"
            f" track={_track!r} code={race_code!r} source={country_source!r}"
            f" effective_country={effective_country!r}"
            f" is_domestic={is_domestic}"
        )

        if is_domestic:
            race_dict["country"] = effective_country
            domestic_count += 1
            log.info(f"[FILTER] INCLUDED track={_track!r} country={effective_country!r}")
        else:
            international_count += 1
            log.info(f"[FILTER] EXCLUDED track={_track!r} code={race_code!r} source={country_source!r}")

        # Step 9 — Storage
        if is_domestic:
            # Store via pipeline (validate + integrity + upsert)
            if _pipeline_state is not None:
                _pipeline_state.record_race_included(race_dict.get("race_uid") or "")
            stored_ok = _store_with_pipeline(race_dict)
            races_stored += 1
            if not stored_ok:
                races_blocked += 1

            # Store runners (OddsPro runners take priority)
            race_uid = race_dict.get("race_uid") or ""
            op_runners = _oddspro_runners_by_race.get(race_uid) or []
            if op_runners:
                stored = _store_runners_for_race(race_uid, op_runners)
                runners_stored += stored
                log.debug(
                    f"[ODDSPRO] RUNNERS_INSERTED race_uid={race_uid}"
                    f" runners_stored={stored}"
                )

            # Store FormFav enrichment if present in merged record
            _ff_runners = race_dict.get("_formfav_runners") or []
            if _ff_runners and race_uid:
                try:
                    from database import upsert_formfav_runner_enrichment
                    from datetime import datetime as _dt, timezone as _tz
                    _fetched_at = _dt.now(_tz.utc).isoformat()
                    for rr in _ff_runners:
                        number = rr.get("number")
                        if number is None:
                            number = rr.get("box_num")
                            if number is not None:
                                log.debug(
                                    f"full_sweep: FF runner {rr.get('name')!r} in race"
                                    f" {race_uid} — using box_num={number} (number field absent)"
                                )
                        if number is not None:
                            upsert_formfav_runner_enrichment({
                                "race_uid": race_uid,
                                "runner_name": rr.get("name") or "",
                                "number": number,
                                "barrier": rr.get("barrier"),
                                "age": str(rr.get("age") or ""),
                                "claim": str(rr.get("claim") or ""),
                                "scratched": bool(rr.get("scratched", False)),
                                "form_string": rr.get("form_string") or "",
                                "trainer": rr.get("trainer") or "",
                                "jockey": rr.get("jockey") or "",
                                "driver": rr.get("driver") or "",
                                "weight": rr.get("weight"),
                                "decorators": rr.get("decorators") or [],
                                "speed_map": rr.get("speed_map"),
                                "class_profile": rr.get("class_profile"),
                                "race_class_fit": rr.get("race_class_fit"),
                                "stats_overall": (rr.get("stats_json") or {}).get("overall"),
                                "stats_track": rr.get("stats_track"),
                                "stats_distance": rr.get("stats_distance"),
                                "stats_condition": rr.get("stats_condition"),
                                "stats_track_distance": rr.get("stats_track_distance"),
                                "stats_full": rr.get("stats_json") or {},
                                "win_prob": rr.get("win_prob"),
                                "place_prob": rr.get("place_prob"),
                                "model_rank": rr.get("model_rank"),
                                "confidence": rr.get("confidence") or "",
                                "model_version": rr.get("model_version") or "",
                                "fetched_at": _fetched_at,
                            })
                except Exception as _ff_exc:
                    log.debug(f"full_sweep: FF runner enrichment write failed: {_ff_exc}")

        else:
            # International race — NEVER write to DB.
            # [FILTER] EXCLUDED was already logged above.
            # No upsert, no table touch.
            if _pipeline_state is not None:
                _pipeline_state.record_race_excluded(
                    race_dict.get("race_uid") or "", "non_domestic_track"
                )

    # Step 10 — Final logging
    log.info(
        f"full_sweep complete: meetings_found={meetings_found} meetings_fetched={meetings_fetched} "
        f"ids_found={meeting_ids_found} ids_missing={meeting_ids_missing} "
        f"details attempted={meeting_details_attempted} "
        f"succeeded={meeting_details_succeeded} failed={meeting_details_failed} "
        f"oddspro_races={len(_oddspro_dicts)} formfav_races={len(_formfav_raw)} "
        f"merged_total={len(merged_races)} "
        f"domestic={domestic_count} international={international_count} "
        f"races_stored={races_stored} races_blocked={races_blocked} "
        f"runners_stored={runners_stored} for {today}"
    )
    log.info(
        f"PIPELINE VALIDATION: date={today} "
        f"races_loaded={races_stored} "
        f"races_domestic={domestic_count} "
        f"races_international_excluded={international_count} "
        f"runners_inserted={runners_stored} "
        f"runners_skipped=0"
    )

    if _pipeline_state is not None:
        _pipeline_state.persist_snapshot()

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
        "oddspro_races": len(_oddspro_dicts),
        "formfav_races": len(_formfav_raw),
        "merged_total": len(merged_races),
        "races_stored": races_stored,
        "runners_stored": runners_stored,
        "races_blocked": races_blocked,
        "races_passed": races_stored - races_blocked,
        "domestic_count": domestic_count,
        "international_excluded": international_count,
        "discovery_failed": _discovery_failed,
        "discovery_diag": _discovery_diag,
        "first_detail_error": _first_detail_error,
        # legacy keys kept for callers that rely on them
        "meetings": meetings_found,
        "races": races_stored,
        "source": "oddspro+formfav",
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

        ff_country = _get_formfav_country(race)
        ff_track = resolve_formfav_track(race.get("track") or "", ff_country)
        if ff_track is None:
            log.debug(
                f"data_engine: FormFav overlay skipped for {race_uid}"
                f" — unsupported track/country"
                f" track={race.get('track')!r} country={ff_country!r}"
            )
            return race

        ff_race, ff_runners = ff.fetch_race_form(
            target_date=race.get("date") or date.today().isoformat(),
            track=ff_track,
            race_num=int(race.get("race_num") or 0),
            code=ff_code,
            country=ff_country,
        )

        provisional = ff_race.__dict__ if hasattr(ff_race, "__dict__") else {}

        # Guard: FormFav cannot overwrite OddsPro authoritative fields
        enriched = guard_formfav_overwrite(race, provisional)
        log.debug(f"data_engine: FormFav overlay applied to {race_uid}")
        return enriched

    except Exception as e:
        log.debug(f"data_engine: FormFav overlay failed for {race_uid}: {e}")
        return race


# ---------------------------------------------------------------------------
# AU/NZ RACE DETECTION — FormFav compatibility helper
# ---------------------------------------------------------------------------
# Uses classify_track_by_code() — track membership in hardcoded race-code-
# specific sets.  No API country or state fields are consulted.


def _is_au_nz_race(race: dict[str, Any]) -> bool:
    """
    Return True when a race is an Australian or New Zealand race eligible
    for FormFav enrichment.

    Uses classify_track_by_code(): checks track membership in the hardcoded
    race-code-specific sets (HORSE/GREYHOUND/HARNESS × AU/NZ).
    No API country or state fields are consulted.
    """
    raw_code = (race.get("code") or "HORSE").upper()
    ff_code = "HORSE" if raw_code == "GALLOPS" else raw_code
    track = race.get("track") or ""
    return classify_track_by_code(track, ff_code) is not None


def _get_formfav_country(race: dict[str, Any]) -> str:
    """
    Return the FormFav country code ('au' or 'nz') for a race.

    Uses classify_track_by_code() to determine country from track membership
    in the hardcoded race-code-specific sets.  Returns 'au' as the default
    for any domestic race where the lookup unexpectedly returns None.

    NOTE: This function should only be called after _is_au_nz_race() returns True.
    """
    raw_code = (race.get("code") or "HORSE").upper()
    ff_code = "HORSE" if raw_code == "GALLOPS" else raw_code
    track = race.get("track") or ""
    country = classify_track_by_code(track, ff_code)
    return country if country else "au"


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


# ------------------------------------------------------------
# FORMFAV PERSISTENT ENRICHMENT SYNC
# ------------------------------------------------------------

def formfav_sync(target_date: str | None = None) -> dict[str, Any]:
    """
    Persistent FormFav enrichment sync — fetches full FormFav data for ALL
    active races today and stores it in formfav_race_enrichment /
    formfav_runner_enrichment tables.

    The AU/NZ pre-filter that previously blocked international races BEFORE the
    FormFav call has been removed.  Domestic classification now happens in
    full_sweep() AFTER both datasets are merged (Step 8 of the 10-step pipeline).
    formfav_sync() operates on whatever races are already in the DB as active
    (status in LIVE_STATUSES), which will be domestic races set by full_sweep().

    This is a SECONDARY enrichment source only. OddsPro remains the primary
    source of record. FormFav data is stored separately and never overwrites
    official race/runner records.

    Called by the scheduler every 300s when FormFav API key is configured.
    Returns a summary dict for health tracking.
    """
    ff = _get_formfav()
    if not ff.is_enabled():
        log.info(
            "[FORMFAV] SKIPPED all: connector not enabled (FORMFAV_API_KEY not configured)"
            " — no FormFav API calls will be made for this cycle"
        )
        # Persist snapshot so the debug endpoint reflects real state (counters from
        # the current full_sweep run) even when FormFav is disabled.
        if _pipeline_state is not None:
            _pipeline_state.persist_snapshot()
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
        if _pipeline_state is not None:
            _pipeline_state.persist_snapshot()
        return {"ok": True, "races_enriched": 0, "runners_enriched": 0, "date": td}

    log.info(
        f"[FORMFAV] SYNC START date={td} total_active_races={len(races)}"
        f" — enriching all active domestic races (no AU/NZ pre-filter)"
    )

    races_enriched = 0
    runners_enriched = 0
    requests_made = 0
    errors = 0
    skipped_missing_fields = 0       # race_uid / track / race_num absent
    skipped_invalid_code = 0         # code is not HORSE, HARNESS, GREYHOUND
    skipped_unsupported_track = 0    # track/country not in FormFav-supported list
    fetched_at = datetime.now(timezone.utc).isoformat()

    for race in races:
        race_uid = race.get("race_uid") or ""
        _log_id = race_uid or f"{race.get('track','?')}/{race.get('race_num','?')}"

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

        # --- FormFav track/country gate ---
        # Validate using classify_track_by_code(): only call FormFav when the
        # track exists in the correct code-specific hardcoded AU/NZ set.
        # This prevents wrong FormFav calls (e.g. Mohawk, international tracks).
        ff_country = classify_track_by_code(track, ff_code)
        if ff_country is None:
            log.info(
                f"[FORMFAV] SKIPPED race_uid={race_uid}"
                f" reason=not_supported_track"
                f" track={track!r} code={ff_code!r}"
            )
            skipped_unsupported_track += 1
            if _pipeline_state is not None:
                _pipeline_state.record_formfav_skipped(race_uid, "not_supported_track")
            continue

        ff_track = apply_track_alias(track)
        mapped_race_code = _FF_CODE_DISPLAY.get(ff_code, ff_code.lower())

        # --- Issue FormFav API call ---
        log.info(
            f"[FORMFAV] ELIGIBLE race_uid={race_uid}"
            f" track={ff_track!r} race_num={race_num} code={ff_code!r}"
            f" ff_code={mapped_race_code!r} country={ff_country!r}"
        )
        if _pipeline_state is not None:
            _pipeline_state.record_formfav_eligible(race_uid)
        log.info(
            f"[FORMFAV] CALL"
            f" url={_FF_BASE_URL}/v1/form"
            f" params=date={race_date}&track={ff_track}&race={race_num}"
            f"&race_code={mapped_race_code}&country={ff_country}"
            f" race_uid={race_uid}"
        )
        log.info(f"[FORMFAV][SYNC] CALLED race_uid={race_uid}")
        if _pipeline_state is not None:
            _pipeline_state.record_formfav_called(race_uid)

        try:
            requests_made += 1
            ff_race, ff_runners = ff.fetch_race_form_with_predictions(
                target_date=race_date,
                track=ff_track,
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
            log.info(f"[FORMFAV][SYNC] UPDATED race_uid={race_uid}")
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

    _total_skipped = skipped_missing_fields + skipped_invalid_code + skipped_unsupported_track

    log.info(
        f"[FORMFAV] SYNC COMPLETE date={td}"
        f" total_races={len(races)}"
        f" requests_made={requests_made}"
        f" races_enriched={races_enriched} runners_enriched={runners_enriched}"
        f" errors={errors}"
        f" skipped_missing_fields={skipped_missing_fields}"
        f" skipped_invalid_code={skipped_invalid_code}"
        f" skipped_unsupported_track={skipped_unsupported_track}"
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
        f"formfav_requests_made={requests_made} "
        f"formfav_races_enriched={races_enriched} "
        f"formfav_runners_enriched={runners_enriched} "
        f"formfav_skipped_missing_fields={skipped_missing_fields} "
        f"formfav_skipped_invalid_code={skipped_invalid_code} "
        f"formfav_skipped_unsupported_track={skipped_unsupported_track}"
    )
    if _pipeline_state is not None:
        _pipeline_state.persist_snapshot()
    return {
        "ok": True,
        "date": td,
        "requests_made": requests_made,
        "races_enriched": races_enriched,
        "runners_enriched": runners_enriched,
        "errors": errors,
        "skipped_missing_fields": skipped_missing_fields,
        "skipped_invalid_code": skipped_invalid_code,
        "skipped_unsupported_track": skipped_unsupported_track,
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
    Enforce pipeline order before storing a race record:
      1. Domestic gate (hard block — excluded races NEVER reach the DB)
         Uses classify_track_by_code(): track must exist in the correct
         code-specific hardcoded AU/NZ set for the race type.
      2. Normalize  (already done by connector / merge engine)
      3. Validate   (log warning; data is stored regardless)
      4. Integrity  (if blocked, mark before storing)
      5. Store

    The domestic gate is the first check. full_sweep() is the primary
    call-site and applies the filter before calling this function.
    This gate acts as a belt-and-suspenders safety check for rolling_refresh /
    near_jump_refresh callers that receive raw OddsPro data.

    Board building happens separately in board_builder.py.  Blocked races are
    stored with status='blocked' so they are tracked explicitly rather than
    silently dropped.

    Returns True if the race passed the domestic gate and integrity check,
    False otherwise.
    """
    try:
        from database import upsert_race
        from validation_engine import validate_race
        from integrity_filter import filter_race

        race_dict = _race_to_dict(race)
        race_uid = race_dict.get("race_uid") or "(no uid)"

        # ----------------------------------------------------------------
        # Step 1 — HARD DOMESTIC GATE
        # Excluded races must NEVER be written to any DB table.
        # Classification uses classify_track_by_code(): country is determined
        # SOLELY from track membership in the correct code-specific set.
        # No API country or state fields are consulted.
        # ----------------------------------------------------------------
        _track = (race_dict.get("track") or "").strip()
        raw_code = (race_dict.get("code") or "").upper()
        if raw_code == "GALLOPS":
            raw_code = "HORSE"

        gate_country = classify_track_by_code(_track, raw_code)
        if gate_country is None:
            log.info(
                f"[FILTER] EXCLUDED track={_track!r} code={raw_code!r}"
                f" race_uid={race_uid} (not in domestic track set; hard gate in _store_with_pipeline — no DB write)"
            )
            return False

        # Ensure canonical country is set (full_sweep sets this before calling us;
        # rolling_refresh / near_jump_refresh pass raw OddsPro objects so we set it here).
        if not race_dict.get("country"):
            race_dict["country"] = gate_country

        log.debug(
            f"[PIPELINE] STORING race_uid={race_uid}"
            f" track={race_dict.get('track')!r} country={race_dict.get('country')!r}"
        )

        # Step 2 — Validate (informational; data is always stored)
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

        # Step 4 — Store (only reached for domestic races that passed Step 1)
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
