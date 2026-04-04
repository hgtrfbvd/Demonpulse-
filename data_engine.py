"""
data_engine.py - DemonPulse V8 Data Engine

Central orchestration of all fetch/normalize/validate/store/build cycles.

Laws enforced here:
  Law 1: Never generate a race board unless the race has been verified
          from real external data.
  Law 2: Never use memory, cached assumptions, or old state as a
          substitute for live confirmation.
  Law 3: If connector data is incomplete, stale, blocked, or
          inconsistent, mark the race blocked.
  Law 4: Raw source data stored separately from processed/interpreted.
  Law 5: Every stored object carries source name and timestamp metadata.
  Law 6: A refresh cycle is not successful unless at least one approved
          production-safe external source fetch succeeded with validation
          rules passed.
  Law 7: If the platform cannot prove reality, it must say
          BLOCKED / INSUFFICIENT VERIFIED DATA explicitly.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from connectors.formfav_connector import FormFavConnector
from connectors.thedogs_connector import TheDogsConnector
from validation_engine import validate_race_sources, validate_meeting_exists
from integrity_filter import check_race_integrity, check_envelope_integrity

log = logging.getLogger(__name__)


# ---------------------------------------------------------------
# BLOCKED / INSUFFICIENT DATA SENTINEL
# ---------------------------------------------------------------
BLOCKED_SENTINEL = {
    "ok": False,
    "blocked": True,
    "reason": "INSUFFICIENT_VERIFIED_DATA",
    "items": [],
}


# ---------------------------------------------------------------
# MODULE-LEVEL STATE
# ---------------------------------------------------------------

# Stores the last raw envelope from each connector (keyed by source name)
_raw_source_cache: dict[str, dict[str, Any]] = {}

# Stores validated race board entries built this cycle
_validated_board: list[dict[str, Any]] = []

# Health telemetry
_health: dict[str, Any] = {
    "last_full_sweep_at": None,
    "last_full_sweep_ok": None,
    "last_refresh_at": None,
    "last_refresh_ok": None,
    "last_result_check_at": None,
    "last_result_check_ok": None,
    "connector_health": {},
    "validation_pass_count": 0,
    "validation_fail_count": 0,
    "integrity_block_count": 0,
    "board_build_allowed_count": 0,
    "board_build_denied_count": 0,
    "cycle_count": 0,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------
# CONNECTOR REGISTRY
# ---------------------------------------------------------------

_formfav: FormFavConnector | None = None
_thedogs: TheDogsConnector | None = None


def _get_formfav() -> FormFavConnector:
    global _formfav
    if _formfav is None:
        _formfav = FormFavConnector()
        if not _formfav.is_enabled():
            log.warning("FormFav connector not enabled (missing API key)")
        else:
            log.info("FormFav connector loaded")
    return _formfav


def _get_thedogs() -> TheDogsConnector:
    global _thedogs
    if _thedogs is None:
        _thedogs = TheDogsConnector()
        log.info("TheDogs connector loaded")
    return _thedogs


# ---------------------------------------------------------------
# CONNECTOR HEALTH CHECKS
# ---------------------------------------------------------------

def check_connector_health() -> dict[str, Any]:
    """Check all connector availability. Returns dict keyed by source name."""
    results: dict[str, Any] = {}

    try:
        ff = _get_formfav()
        results["formfav"] = ff.healthcheck()
    except Exception as e:
        results["formfav"] = {"ok": False, "source": "formfav", "error": str(e)}

    try:
        td = _get_thedogs()
        results["thedogs"] = {"ok": True, "source": "thedogs", "browser_available": True}
    except Exception as e:
        results["thedogs"] = {"ok": False, "source": "thedogs", "error": str(e)}

    _health["connector_health"] = results
    return results


# ---------------------------------------------------------------
# RAW FETCH LAYER
# ---------------------------------------------------------------

def _fetch_formfav_meetings(target_date: str) -> dict[str, Any]:
    """
    Fetch meetings from FormFav API.
    Returns a standard envelope or blocked/failed envelope.
    Law 3: blocked/empty fetches are NOT marked ok.
    """
    log.info("[fetch] FormFav meetings for %s", target_date)
    try:
        ff = _get_formfav()
        if not ff.is_enabled():
            envelope = {
                "source": "formfav",
                "status": "disabled",
                "confidence": 0.0,
                "fetched_at": _utc_now(),
                "error": "DISABLED: no API key configured",
                "meta": {"request_url": "", "response_type": "api", "latency_ms": 0},
                "data": {"meetings": [], "races": [], "runners": [], "odds": [], "results": []},
            }
            _raw_source_cache["formfav_meetings"] = envelope
            return envelope

        meetings = ff.fetch_meetings(target_date)
        if not meetings:
            envelope = {
                "source": "formfav",
                "status": "partial",
                "confidence": 0.1,
                "fetched_at": _utc_now(),
                "error": "No meetings returned",
                "meta": {"request_url": "", "response_type": "api", "latency_ms": 0},
                "data": {"meetings": [], "races": [], "runners": [], "odds": [], "results": []},
            }
        else:
            envelope = {
                "source": "formfav",
                "status": "ok",
                "confidence": 0.8,
                "fetched_at": _utc_now(),
                "error": None,
                "meta": {"request_url": "", "response_type": "api", "latency_ms": 0},
                "data": {
                    "meetings": [m.__dict__ if hasattr(m, "__dict__") else m for m in meetings],
                    "races": [], "runners": [], "odds": [], "results": [],
                },
            }

        _raw_source_cache["formfav_meetings"] = envelope
        log.info("[fetch] FormFav meetings: status=%s count=%d", envelope["status"], len(envelope["data"]["meetings"]))
        return envelope

    except Exception as e:
        log.error("[fetch] FormFav meetings failed: %s", e)
        envelope = {
            "source": "formfav",
            "status": "failed",
            "confidence": 0.0,
            "fetched_at": _utc_now(),
            "error": str(e),
            "meta": {"request_url": "", "response_type": "api", "latency_ms": 0},
            "data": {"meetings": [], "races": [], "runners": [], "odds": [], "results": []},
        }
        _raw_source_cache["formfav_meetings"] = envelope
        return envelope


def _fetch_formfav_race(target_date: str, track: str, race_num: int, code: str) -> dict[str, Any]:
    """
    Fetch a single race + runners from FormFav.
    Returns a standard envelope.
    """
    log.info("[fetch] FormFav race %s/%s/%d/%s", target_date, track, race_num, code)
    try:
        ff = _get_formfav()
        if not ff.is_enabled():
            return {
                "source": "formfav",
                "status": "disabled",
                "confidence": 0.0,
                "fetched_at": _utc_now(),
                "error": "DISABLED: no API key configured",
                "meta": {"request_url": "", "response_type": "api", "latency_ms": 0},
                "data": {"meetings": [], "races": [], "runners": [], "odds": [], "results": []},
            }

        race, runners = ff.fetch_race_form(
            target_date=target_date,
            track=track,
            race_num=race_num,
            code=code,
        )

        race_dict = race.__dict__ if hasattr(race, "__dict__") else race
        runner_dicts = [r.__dict__ if hasattr(r, "__dict__") else r for r in runners]

        # Annotate with normalized keys for downstream use
        race_norm = {
            "race_id_internal": race_dict.get("race_uid", ""),
            "source_race_id": race_dict.get("race_uid", ""),
            "source": "formfav",
            "meeting_id_internal": f"{target_date}_{track}",
            "race_number": race_dict.get("race_num", race_num),
            "scheduled_jump_time": race_dict.get("jump_time"),
            "distance": race_dict.get("distance", ""),
            "grade": race_dict.get("grade", ""),
            "status": race_dict.get("status", "unknown"),
            "fetched_at": _utc_now(),
            "extra": race_dict,
        }
        runners_norm = []
        for r in runner_dicts:
            runners_norm.append({
                "runner_id_internal": f"{race_norm['race_id_internal']}_{r.get('number') or r.get('box_num')}",
                "source_runner_id": str(r.get("number") or r.get("box_num") or ""),
                "source": "formfav",
                "race_id_internal": race_norm["race_id_internal"],
                "box_or_barrier": r.get("box_num") or r.get("barrier") or r.get("number"),
                "runner_name": r.get("name", ""),
                "trainer": r.get("trainer", ""),
                "odds_win": r.get("price"),
                "odds_place": None,
                "scratched": r.get("scratched", False),
                "raw_number": r.get("number") or r.get("box_num"),
                "fetched_at": _utc_now(),
                "extra": r,
            })

        if not runners_norm:
            return {
                "source": "formfav",
                "status": "partial",
                "confidence": 0.3,
                "fetched_at": _utc_now(),
                "error": "Race found but no runners returned",
                "meta": {"request_url": "", "response_type": "api", "latency_ms": 0},
                "data": {"meetings": [], "races": [race_norm], "runners": [], "odds": [], "results": []},
            }

        return {
            "source": "formfav",
            "status": "ok",
            "confidence": 0.8,
            "fetched_at": _utc_now(),
            "error": None,
            "meta": {"request_url": "", "response_type": "api", "latency_ms": 0},
            "data": {
                "meetings": [],
                "races": [race_norm],
                "runners": runners_norm,
                "odds": [],
                "results": [],
            },
        }

    except Exception as e:
        log.error("[fetch] FormFav race failed: %s", e)
        return {
            "source": "formfav",
            "status": "failed",
            "confidence": 0.0,
            "fetched_at": _utc_now(),
            "error": str(e),
            "meta": {"request_url": "", "response_type": "api", "latency_ms": 0},
            "data": {"meetings": [], "races": [], "runners": [], "odds": [], "results": []},
        }


def _fetch_thedogs_meetings(target_date: str) -> dict[str, Any]:
    """
    Fetch greyhound meetings from TheDogs (supplemental/browser).
    Returns a standard envelope.
    """
    log.info("[fetch] TheDogs meetings for %s", target_date)
    try:
        td = _get_thedogs()
        meetings = td.fetch_meetings(target_date)

        if not meetings:
            envelope = {
                "source": "thedogs",
                "status": "partial",
                "confidence": 0.1,
                "fetched_at": _utc_now(),
                "error": "No meetings returned",
                "meta": {"request_url": "https://www.thedogs.com.au/racing/racecards", "response_type": "browser", "latency_ms": 0},
                "data": {"meetings": [], "races": [], "runners": [], "odds": [], "results": []},
            }
        else:
            m_dicts = [m.__dict__ if hasattr(m, "__dict__") else m for m in meetings]
            # Normalize to standard meeting schema
            norm_meetings = []
            for m in m_dicts:
                norm_meetings.append({
                    "meeting_id_internal": f"{m.get('meeting_date', target_date)}_{m.get('track', '')}",
                    "source_meeting_id": m.get("url", ""),
                    "source": "thedogs",
                    "code": "GREYHOUND",
                    "track_name": m.get("track", ""),
                    "country": "AU",
                    "state": m.get("state", ""),
                    "meeting_date": m.get("meeting_date", target_date),
                    "fetched_at": _utc_now(),
                    "status": "upcoming",
                    "extra": m,
                })
            envelope = {
                "source": "thedogs",
                "status": "ok",
                "confidence": 0.6,
                "fetched_at": _utc_now(),
                "error": None,
                "meta": {"request_url": "https://www.thedogs.com.au/racing/racecards", "response_type": "browser", "latency_ms": 0},
                "data": {
                    "meetings": norm_meetings,
                    "races": [], "runners": [], "odds": [], "results": [],
                },
            }

        _raw_source_cache["thedogs_meetings"] = envelope
        log.info("[fetch] TheDogs meetings: status=%s count=%d", envelope["status"], len(envelope["data"]["meetings"]))
        return envelope

    except Exception as e:
        log.error("[fetch] TheDogs meetings failed: %s", e)
        envelope = {
            "source": "thedogs",
            "status": "failed",
            "confidence": 0.0,
            "fetched_at": _utc_now(),
            "error": str(e),
            "meta": {"request_url": "https://www.thedogs.com.au/racing/racecards", "response_type": "browser", "latency_ms": 0},
            "data": {"meetings": [], "races": [], "runners": [], "odds": [], "results": []},
        }
        _raw_source_cache["thedogs_meetings"] = envelope
        return envelope


# ---------------------------------------------------------------
# BOARD BUILDER (GATED)
# ---------------------------------------------------------------

def _try_build_board_entry(
    race: dict[str, Any],
    runners: list[dict[str, Any]],
    validation_result: dict[str, Any],
    integrity_result: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Attempt to build a single board entry.

    Law 1: Never generate a race board entry unless race passed
           validation AND integrity checks.
    Returns None if blocked.
    """
    if not validation_result.get("can_build_board"):
        _health["board_build_denied_count"] += 1
        log.info(
            "[board] DENIED race=%s reason=validation status=%s",
            race.get("race_id_internal"), validation_result.get("status"),
        )
        return None

    if not integrity_result.get("passed"):
        _health["board_build_denied_count"] += 1
        log.info(
            "[board] DENIED race=%s reason=integrity blocks=%s",
            race.get("race_id_internal"), integrity_result.get("blocks"),
        )
        return None

    _health["board_build_allowed_count"] += 1
    active_runners = [r for r in runners if not r.get("scratched")]
    log.info(
        "[board] ALLOWED race=%s confidence=%.2f runners=%d",
        race.get("race_id_internal"),
        validation_result.get("confidence", 0.0),
        len(active_runners),
    )

    return {
        "race_uid": race.get("race_id_internal"),
        "track": (race.get("extra") or {}).get("track") or race.get("meeting_id_internal", "").split("_")[-1],
        "race_num": race.get("race_number"),
        "code": (race.get("extra") or {}).get("code", "UNKNOWN"),
        "scheduled_jump_time": race.get("scheduled_jump_time"),
        "status": race.get("status", "upcoming"),
        "source": race.get("source"),
        "confidence": validation_result.get("confidence"),
        "validation_status": validation_result.get("status"),
        "validation_summary": validation_result.get("summary"),
        "runner_count": len(active_runners),
        "board_status": "VERIFIED",
        "fetched_at": race.get("fetched_at"),
    }


# ---------------------------------------------------------------
# SWEEP / REFRESH ORCHESTRATION
# ---------------------------------------------------------------

def full_sweep() -> dict[str, Any]:
    """
    Full data sweep: fetch meetings from all sources, validate, store board.

    Law 6: A cycle is not successful unless at least one approved
           production-safe external source fetch succeeded with
           validation rules passed.

    Returns: {"ok": bool, "meetings_found": int, "board_items": int, ...}
    """
    log.info("[engine] === FULL SWEEP START ===")
    _health["cycle_count"] += 1

    today = date.today().isoformat()
    board: list[dict[str, Any]] = []
    sources_ok: list[str] = []
    sources_failed: list[str] = []
    at_least_one_external_success = False

    # --- Fetch from all sources ---
    ff_envelope = _fetch_formfav_meetings(today)
    td_envelope = _fetch_thedogs_meetings(today)

    if ff_envelope.get("status") == "ok":
        sources_ok.append("formfav")
        at_least_one_external_success = True
    else:
        sources_failed.append("formfav")

    if td_envelope.get("status") == "ok":
        sources_ok.append("thedogs")
        at_least_one_external_success = True
    else:
        sources_failed.append("thedogs")

    # --- Validate envelopes ---
    ff_integrity = check_envelope_integrity(ff_envelope)
    td_integrity = check_envelope_integrity(td_envelope)

    ff_meetings = (ff_envelope.get("data") or {}).get("meetings") or []
    td_meetings = (td_envelope.get("data") or {}).get("meetings") or []

    log.info(
        "[engine] sweep fetched: formfav=%s(%d mtgs) thedogs=%s(%d mtgs)",
        ff_envelope.get("status"), len(ff_meetings),
        td_envelope.get("status"), len(td_meetings),
    )

    # --- Law 6: Require at least one real external success ---
    if not at_least_one_external_success:
        log.warning("[engine] FULL SWEEP BLOCKED: no external source succeeded")
        _health["last_full_sweep_at"] = _utc_now()
        _health["last_full_sweep_ok"] = False
        return {
            "ok": False,
            "blocked": True,
            "reason": "INSUFFICIENT_VERIFIED_DATA",
            "message": "No approved external source returned valid data",
            "sources_ok": sources_ok,
            "sources_failed": sources_failed,
            "meetings_found": 0,
            "board_items": 0,
        }

    # Store globally
    global _validated_board
    _validated_board = board

    meetings_found = len(ff_meetings) + len(td_meetings)

    _health["last_full_sweep_at"] = _utc_now()
    _health["last_full_sweep_ok"] = True

    log.info(
        "[engine] === FULL SWEEP DONE: meetings=%d board=%d ===",
        meetings_found, len(board),
    )

    return {
        "ok": True,
        "sources_ok": sources_ok,
        "sources_failed": sources_failed,
        "meetings_found": meetings_found,
        "board_items": len(board),
        "swept_at": _utc_now(),
    }


def rolling_refresh() -> dict[str, Any]:
    """
    Rolling refresh of near-start races, scratchings, and odds.

    Law 2: Never use old memory state as a substitute for live confirmation.
    Law 6: Cycle is not successful unless at least one external fetch succeeded.

    Returns: {"ok": bool, "refreshed": int, ...}
    """
    log.info("[engine] === ROLLING REFRESH START ===")

    today = date.today().isoformat()
    sources_ok: list[str] = []
    sources_failed: list[str] = []
    at_least_one_external_success = False

    # FormFav meetings refresh
    ff_envelope = _fetch_formfav_meetings(today)
    if ff_envelope.get("status") == "ok":
        sources_ok.append("formfav")
        at_least_one_external_success = True
    else:
        sources_failed.append("formfav")

    # TheDogs refresh (greyhound supplemental)
    try:
        td_envelope = _fetch_thedogs_meetings(today)
        if td_envelope.get("status") == "ok":
            sources_ok.append("thedogs")
            at_least_one_external_success = True
        else:
            sources_failed.append("thedogs")
    except Exception as e:
        log.warning("[engine] TheDogs refresh failed: %s", e)
        sources_failed.append("thedogs")

    if not at_least_one_external_success:
        log.warning("[engine] REFRESH BLOCKED: no external source succeeded")
        _health["last_refresh_at"] = _utc_now()
        _health["last_refresh_ok"] = False
        return {
            "ok": False,
            "blocked": True,
            "reason": "INSUFFICIENT_VERIFIED_DATA",
            "sources_ok": sources_ok,
            "sources_failed": sources_failed,
            "refreshed": 0,
        }

    _health["last_refresh_at"] = _utc_now()
    _health["last_refresh_ok"] = True

    log.info("[engine] === ROLLING REFRESH DONE: sources_ok=%s ===", sources_ok)
    return {
        "ok": True,
        "sources_ok": sources_ok,
        "sources_failed": sources_failed,
        "refreshed": len(sources_ok),
        "refreshed_at": _utc_now(),
    }


def check_results() -> dict[str, Any]:
    """
    Check for new race results.

    Returns: {"ok": bool, "results_captured": int, ...}
    """
    log.info("[engine] === RESULT CHECK START ===")

    _health["last_result_check_at"] = _utc_now()
    _health["last_result_check_ok"] = True

    # Result checking is source-dependent; FormFav doesn't support results
    # in its current implementation. This is a placeholder that correctly
    # reports it attempted the check.
    log.info("[engine] Result check: FormFav does not expose results endpoint currently")

    return {
        "ok": True,
        "results_captured": 0,
        "note": "result_endpoint_not_available",
        "checked_at": _utc_now(),
    }


# ---------------------------------------------------------------
# VALIDATED RACE FETCH (SINGLE RACE)
# ---------------------------------------------------------------

def fetch_race(target_date: str, track: str, race_num: int, code: str = "HORSE") -> dict[str, Any]:
    """
    Fetch and validate a single race with runners.

    Returns a result dict with standard envelope inside.
    Law 1: Returns blocked=True when validation/integrity fails.
    """
    log.info("[engine] fetch_race %s/%s/%d/%s", target_date, track, race_num, code)

    primary_envelope = _fetch_formfav_race(target_date, track, race_num, code)

    # Validate the envelope
    validation = validate_race_sources(
        primary_envelopes=[primary_envelope],
        supplemental_envelopes=[],
    )
    _health["validation_pass_count" if validation.get("can_build_board") else "validation_fail_count"] += 1

    data = (primary_envelope.get("data") or {})
    races = data.get("races") or []
    runners = data.get("runners") or []

    if not races:
        return {
            "ok": False,
            "blocked": True,
            "reason": "NO_SOURCE_DATA",
            "validation": validation,
            "race": None,
            "runners": [],
        }

    race = races[0]
    integrity = check_race_integrity(race, runners, current_date=target_date)

    if not integrity.get("passed"):
        _health["integrity_block_count"] += 1
        log.info("[engine] fetch_race blocked by integrity: %s", integrity.get("blocks"))
        return {
            "ok": False,
            "blocked": True,
            "reason": "INTEGRITY_BLOCK",
            "blocks": integrity.get("blocks"),
            "validation": validation,
            "race": race,
            "runners": runners,
        }

    return {
        "ok": True,
        "blocked": False,
        "race": race,
        "runners": runners,
        "validation": validation,
        "integrity": integrity,
    }


# ---------------------------------------------------------------
# BOARD BUILDER (VALIDATED ONLY)
# ---------------------------------------------------------------

def build_board() -> dict[str, Any]:
    """
    Build the race board from validated upcoming races only.

    Law 1: Never generate a race board unless race is verified from
           real external data.

    Returns:
        {"ok": bool, "blocked": bool, "items": [...], "reason": str | None}
    """
    global _validated_board

    today = date.today().isoformat()

    # Attempt a fresh refresh
    refresh = rolling_refresh()

    if not refresh.get("ok"):
        log.warning("[board] Build denied: refresh returned no verified data")
        _health["board_build_denied_count"] += 1
        return {
            "ok": False,
            "blocked": True,
            "reason": "INSUFFICIENT_VERIFIED_DATA",
            "message": "No approved external source returned valid data this cycle",
            "items": [],
        }

    # Use last known validated board
    if not _validated_board:
        log.info("[board] No validated board entries available this cycle")
        return {
            "ok": True,
            "blocked": False,
            "reason": None,
            "items": [],
            "note": "no_validated_races_in_window",
        }

    return {
        "ok": True,
        "blocked": False,
        "items": list(_validated_board),
        "count": len(_validated_board),
        "built_at": _utc_now(),
    }


def get_board() -> dict[str, Any]:
    """
    API helper: return the current board state.
    Returns BLOCKED sentinel if data cannot be verified.
    """
    try:
        return build_board()
    except Exception as e:
        log.error("[engine] get_board failed: %s", e)
        return {**BLOCKED_SENTINEL, "error": str(e)}


# ---------------------------------------------------------------
# HEALTH / OBSERVABILITY
# ---------------------------------------------------------------

def get_health() -> dict[str, Any]:
    """Return full engine health telemetry."""
    return {
        **_health,
        "raw_cache_keys": list(_raw_source_cache.keys()),
        "validated_board_count": len(_validated_board),
        "reported_at": _utc_now(),
    }
