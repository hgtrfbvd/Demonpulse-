"""
database.py - DemonPulse Database Abstraction Layer
=====================================================
Thin compatibility shim over db.py for architecture compliance.
Provides typed helpers for race/runner/result CRUD operations
against the Supabase backend (today_races, today_runners, results_log tables).

All writes go through OddsPro-confirmed data only.
FormFav provisional data is never written to official tables directly.
"""
from __future__ import annotations

import logging
from datetime import datetime, date, timezone
from typing import Any
import hashlib
import json

from db import get_db, safe_query, T

log = logging.getLogger(__name__)

# Range of synthetic box numbers assigned to runners whose position cannot be
# determined from the source data.  Must not collide with real barrier numbers
# (which are at most a few dozen for any race format).
_FALLBACK_BOX_BASE = 9000
_FALLBACK_BOX_RANGE = 1000  # fallback box_nums span 9000-9999


def _as_json(val: Any) -> Any:
    """Serialise dict/list values to JSON strings for JSONB columns."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return val


# ---------------------------------------------------------------------------
# MEETING UPSERT / QUERY
# ---------------------------------------------------------------------------

def upsert_meeting(meeting: dict[str, Any]) -> dict[str, Any] | None:
    """
    Insert or update a meeting record in meetings.
    Conflict key: (date, track, code) — canonical per 001_canonical_schema.sql.
    Only OddsPro-sourced records should be written here as authoritative truth.
    """
    track = meeting.get("track") or ""
    code = meeting.get("code") or "GREYHOUND"
    if not track:
        log.warning("database.upsert_meeting: skipping row missing track")
        return None

    payload = {
        "date":        meeting.get("date") or date.today().isoformat(),
        "track":       track,
        "code":        code,
        "state":       meeting.get("state") or "",
        "country":     meeting.get("country") or "AUS",
        "weather":     meeting.get("weather") or "",
        "rail":        meeting.get("rail") or "",
        "track_cond":  meeting.get("track_cond") or "",
        "race_count":  int(meeting.get("race_count") or 0),
        "source":      meeting.get("source") or "oddspro",
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }

    result = safe_query(
        lambda: get_db()
        .table(T("meetings"))
        .upsert(payload, on_conflict="date,track,code")
        .execute()
        .data
    )
    if result:
        log.debug(f"database: upserted meeting {track}/{code}")
        return result[0] if isinstance(result, list) else result
    return None


def get_meeting(meeting_date: str, track: str, code: str) -> dict[str, Any] | None:
    """Fetch a meeting record by (date, track, code)."""
    result = safe_query(
        lambda: get_db()
        .table(T("meetings"))
        .select("*")
        .eq("date", meeting_date)
        .eq("track", track)
        .eq("code", code)
        .limit(1)
        .execute()
        .data
    )
    return (result or [None])[0]


# ---------------------------------------------------------------------------
# SOURCE LOG
# ---------------------------------------------------------------------------

def write_source_log(entry: dict[str, Any]) -> dict[str, Any] | None:
    """
    Write a data-source call record to source_log.
    Called by ingestion paths (data_engine, connectors) to log every
    outbound HTTP request made to OddsPro or other data sources.
    """
    payload = {
        "date":          entry.get("date") or date.today().isoformat(),
        "call_num":      entry.get("call_num"),
        "url":           entry.get("url") or "",
        "method":        entry.get("method") or "GET",
        "status":        entry.get("status") or "",
        "rows_returned": entry.get("rows_returned"),
        "created_at":    datetime.now(timezone.utc).isoformat(),
    }
    result = safe_query(
        lambda: get_db()
        .table(T("source_log"))
        .insert(payload)
        .execute()
        .data
    )
    if result:
        return result[0] if isinstance(result, list) else result
    return None


# ---------------------------------------------------------------------------
# RACE UPSERT / QUERY
# ---------------------------------------------------------------------------

def upsert_race(race: dict[str, Any]) -> dict[str, Any] | None:
    """
    Insert or update a race record in today_races.
    Only OddsPro-sourced records should be written here as authoritative truth.
    """
    payload = _build_race_payload(race)
    if not payload:
        return None

    result = safe_query(
        lambda: get_db()
        .table(T("today_races"))
        .upsert(payload, on_conflict="date,track,race_num,code")
        .execute()
        .data
    )
    if result:
        log.debug(f"database: upserted race {race.get('race_uid')}")
        return result[0] if isinstance(result, list) else result
    return None


def get_race(race_uid: str) -> dict[str, Any] | None:
    """Fetch a race record by race_uid."""
    result = safe_query(
        lambda: get_db()
        .table(T("today_races"))
        .select("*")
        .eq("race_uid", race_uid)
        .limit(1)
        .execute()
        .data
    )
    return (result or [None])[0]


def get_races_for_date(target_date: str) -> list[dict[str, Any]]:
    """Fetch all races for a given date."""
    return safe_query(
        lambda: get_db()
        .table(T("today_races"))
        .select("*")
        .eq("date", target_date)
        .order("jump_time")
        .execute()
        .data,
        [],
    ) or []


def get_active_races(target_date: str) -> list[dict[str, Any]]:
    """
    Fetch all live (non-final) races for today.

    Matches race_status.LIVE_STATUSES: upcoming, open, interim, near_jump,
    jumped_estimated, awaiting_result.  Races that have transitioned beyond
    "upcoming"/"open"/"interim" (e.g. via the race state machine) are still
    live and must be included so rolling_refresh, near_jump_refresh and
    formfav_sync all operate on the full live race pool rather than a stale
    subset.
    """
    return safe_query(
        lambda: get_db()
        .table(T("today_races"))
        .select("*")
        .eq("date", target_date)
        .in_(
            "status",
            [
                "upcoming",
                "open",
                "interim",
                "near_jump",
                "jumped_estimated",
                "awaiting_result",
            ],
        )
        .order("jump_time")
        .execute()
        .data,
        [],
    ) or []


def mark_race_blocked(race_uid: str, block_code: str) -> None:
    """Explicitly mark a race as blocked. Blocked races never reach the board."""
    safe_query(
        lambda: get_db()
        .table(T("today_races"))
        .update({
            "status": "blocked",
            "block_code": block_code,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("race_uid", race_uid)
        .execute()
    )
    log.info(f"database: race {race_uid} blocked [{block_code}]")


def update_race_status(race_uid: str, status: str) -> None:
    """Update the status of a race record."""
    safe_query(
        lambda: get_db()
        .table(T("today_races"))
        .update({
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("race_uid", race_uid)
        .execute()
    )


# ---------------------------------------------------------------------------
# RUNNER UPSERT / QUERY
# ---------------------------------------------------------------------------

def upsert_runners(race_id_uuid: str, runners: list[dict[str, Any]]) -> int:
    """
    Insert or replace runners for a race.
    race_id_uuid is the UUID primary key of the today_races row.
    Returns count of upserted runners.

    Conflict key: (race_uid, box_num) — canonical identity per supabase_config.UPSERT_KEYS.
    race_id (UUID FK) is still populated for relational integrity.
    """
    if not runners:
        return 0

    rows = []
    for idx, r in enumerate(runners):
        race_uid = r.get("race_uid") or ""
        if not race_uid:
            log.warning(
                f"database.upsert_runners: skipping runner missing race_uid "
                f"(name={r.get('name')!r})"
            )
            continue

        # Resolve box_num: use stored value first, then fall back to barrier, then number.
        box_num = r.get("box_num")
        if box_num is None:
            barrier = r.get("barrier")
            try:
                box_num = int(barrier) if barrier is not None else None
            except (TypeError, ValueError):
                box_num = None
        if box_num is None:
            number_val = r.get("number")
            try:
                box_num = int(number_val) if number_val is not None else None
            except (TypeError, ValueError):
                box_num = None

        if box_num is None:
            # Generate a stable fallback box_num so no runner is ever skipped,
            # and the same runner always gets the same fallback across upsert calls.
            # Include the runner name and list position as discriminators so runners
            # with empty or identical names still get distinct box numbers.
            # Use SHA-256 of (race_uid:name:idx) for a deterministic, stable identifier.
            _hash_key = f"{race_uid}:{r.get('name', '')}:{idx}"
            _hash_val = int(hashlib.sha256(_hash_key.encode()).hexdigest(), 16)
            box_num = _FALLBACK_BOX_BASE + (_hash_val % _FALLBACK_BOX_RANGE)
            log.info(
                f"database.upsert_runners: runner missing box_num/barrier/number "
                f"(race_uid={race_uid!r}, name={r.get('name')!r}) "
                f"— assigned stable fallback box_num={box_num}"
            )
        rows.append({
            "race_id": race_id_uuid,
            "race_uid": race_uid,
            "date": r.get("date") or date.today().isoformat(),
            "track": r.get("track") or "",
            "race_num": r.get("race_num"),
            "box_num": box_num,
            "name": r.get("name") or "",
            "number": r.get("number"),
            "barrier": r.get("barrier"),
            "trainer": r.get("trainer") or "",
            "jockey": r.get("jockey") or "",
            "driver": r.get("driver") or "",
            "owner": r.get("owner") or "",
            "weight": r.get("weight"),
            "run_style": r.get("run_style"),
            "early_speed": r.get("early_speed"),
            "best_time": r.get("best_time"),
            "career": r.get("career"),
            "price": r.get("price"),
            "rating": r.get("rating"),
            "scratched": bool(r.get("scratched")),
            # CF-06: normalise scratch field — connectors may provide scratch_timing
            "scratch_reason": r.get("scratch_reason") or r.get("scratch_timing"),
            "source_confidence": r.get("source_confidence") or "official",
        })

    result = safe_query(
        lambda: get_db()
        .table(T("today_runners"))
        .upsert(rows, on_conflict="race_uid,box_num")
        .execute()
        .data
    )
    return len(result) if result else 0


def get_runners_for_race(race_uid: str) -> list[dict[str, Any]]:
    """Fetch all runners for a race by race_uid."""
    return safe_query(
        lambda: get_db()
        .table(T("today_runners"))
        .select("*")
        .eq("race_uid", race_uid)
        .order("box_num")
        .execute()
        .data,
        [],
    ) or []


# ---------------------------------------------------------------------------
# RESULTS
# ---------------------------------------------------------------------------

def upsert_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """
    Write a race result to results_log.
    Only called after OddsPro result confirmation.
    """
    payload = {
        "date": result.get("date") or date.today().isoformat(),
        "track": result.get("track") or "",
        "race_num": result.get("race_num"),
        "code": result.get("code") or "GREYHOUND",
        "winner": result.get("winner") or "",
        "winner_box": result.get("winner_number"),
        "win_price": result.get("win_price"),
        "place_2": result.get("place_2") or "",
        "place_3": result.get("place_3") or "",
        "margin": result.get("margin"),
        "winning_time": result.get("winning_time"),
        "source": result.get("source") or "oddspro",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    res = safe_query(
        lambda: get_db()
        .table(T("results_log"))
        .upsert(payload, on_conflict="date,track,race_num,code")
        .execute()
        .data
    )
    if res:
        log.debug(f"database: result recorded for {result.get('race_uid')}")
        return res[0] if isinstance(res, list) else res
    return None


def get_result(race_uid: str) -> dict[str, Any] | None:
    """Fetch a stored result by race_uid (parsed from date/track/race_num/code)."""
    parts = (race_uid or "").split("_")
    if len(parts) < 4:
        return None
    # Format: DATE_CODE_TRACK_RACENUM
    # DATE (index 0) and CODE (index 1) are fixed; RACENUM is the last component;
    # TRACK is everything between CODE and RACENUM (handles underscores in track names).
    race_date = parts[0]
    code = parts[1]
    race_num_str = parts[-1]
    # parts[2:-1] works for both len==4 (single-component track) and
    # len>4 (track name contains underscores); join always produces the correct string.
    track = "_".join(parts[2:-1])
    try:
        race_num = int(race_num_str)
    except ValueError:
        return None

    result = safe_query(
        lambda: get_db()
        .table(T("results_log"))
        .select("*")
        .eq("date", race_date)
        .eq("track", track)
        .eq("race_num", race_num)
        .eq("code", code)
        .limit(1)
        .execute()
        .data
    )
    return (result or [None])[0]


# ---------------------------------------------------------------------------
# BLOCKED RACES TRACKING
# ---------------------------------------------------------------------------

def get_blocked_races(target_date: str) -> list[dict[str, Any]]:
    """Return all explicitly blocked races for a date."""
    return safe_query(
        lambda: get_db()
        .table(T("today_races"))
        .select("race_uid,block_code,track,race_num,code")
        .eq("date", target_date)
        .eq("status", "blocked")
        .execute()
        .data,
        [],
    ) or []


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _build_race_payload(race: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a race dict to a DB-compatible payload for today_races."""
    if not race.get("track") or not race.get("race_num"):
        return None

    return {
        "race_uid": race.get("race_uid") or "",
        "oddspro_race_id": race.get("oddspro_race_id") or "",
        "date": race.get("date") or date.today().isoformat(),
        "track": race.get("track") or "",
        "state": race.get("state") or "",
        "country": (race.get("country") or "au").strip().lower(),
        "race_num": int(race.get("race_num") or 0),
        "code": race.get("code") or "GREYHOUND",
        "distance": str(race.get("distance") or ""),
        "grade": str(race.get("grade") or ""),
        "jump_time": race.get("jump_time"),
        "prize_money": str(race.get("prize_money") or ""),
        "status": race.get("status") or "upcoming",
        "block_code": race.get("block_code") or "",
        "source": race.get("source") or "oddspro",
        "source_url": race.get("source_url") or "",
        "time_status": race.get("time_status") or "PARTIAL",
        "condition": race.get("condition") or "",
        "race_name": race.get("race_name") or "",
        # runner_count is set by full_sweep before calling upsert_race so the
        # integrity_filter NO_RUNNERS check can use it.  Only included when
        # explicitly set; absence means unknown (not zero).
        "runner_count": int(race["runner_count"]) if race.get("runner_count") is not None else 0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# FORMFAV ENRICHMENT — PERSISTENT SECONDARY SOURCE
# ---------------------------------------------------------------------------

def upsert_formfav_race_enrichment(data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Insert or update a FormFav race enrichment record.
    Conflict key: race_uid (one enrichment row per race).
    Never overwrites primary OddsPro records.
    """
    race_uid = data.get("race_uid") or ""
    if not race_uid:
        log.warning("database.upsert_formfav_race_enrichment: skipping row missing race_uid")
        return None

    payload = {
        "race_uid":          race_uid,
        "date":              data.get("date") or date.today().isoformat(),
        "track":             data.get("track") or "",
        "race_num":          int(data.get("race_num") or 0),
        "race_code":         data.get("race_code") or data.get("code") or "",
        "race_name":         data.get("race_name") or "",
        "distance":          data.get("distance") or "",
        "grade":             data.get("grade") or "",
        "condition":         data.get("condition") or "",
        "weather":           data.get("weather") or "",
        "start_time":        data.get("start_time") or "",
        "start_time_utc":    data.get("start_time_utc") or "",
        "timezone":          data.get("timezone") or "",
        "abandoned":         bool(data.get("abandoned", False)),
        "number_of_runners": int(data.get("number_of_runners") or 0),
        "pace_scenario":     data.get("pace_scenario") or "",
        "prize_money":       data.get("prize_money") or "",
        "raw_response":      _as_json(data.get("raw_response")),
        "fetched_at":        data.get("fetched_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at":        datetime.now(timezone.utc).isoformat(),
    }

    result = safe_query(
        lambda: get_db()
        .table(T("formfav_race_enrichment"))
        .upsert(payload, on_conflict="race_uid")
        .execute()
        .data
    )
    if result:
        log.debug(f"database: upserted formfav_race_enrichment {race_uid}")
        return result[0] if isinstance(result, list) else result
    return None


def upsert_formfav_runner_enrichment(data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Insert or update a FormFav runner enrichment record.
    Conflict key: (race_uid, number).
    """
    race_uid = data.get("race_uid") or ""
    number = data.get("number")
    if not race_uid or number is None:
        log.warning("database.upsert_formfav_runner_enrichment: skipping row missing race_uid or number")
        return None

    payload = {
        "race_uid":           race_uid,
        "runner_name":        data.get("runner_name") or data.get("name") or "",
        "number":             int(number),
        "barrier":            int(data["barrier"]) if data.get("barrier") is not None else None,
        "age":                data.get("age") or "",
        "claim":              data.get("claim") or "",
        "scratched":          bool(data.get("scratched", False)),
        "form_string":        data.get("form_string") or "",
        "trainer":            data.get("trainer") or "",
        "jockey":             data.get("jockey") or "",
        "driver":             data.get("driver") or "",
        "weight":             data.get("weight"),
        "decorators":         _as_json(data.get("decorators") or []),
        "speed_map":          _as_json(data.get("speed_map")),
        "class_profile":      _as_json(data.get("class_profile")),
        "race_class_fit":     _as_json(data.get("race_class_fit")),
        "stats_overall":      _as_json(data.get("stats_overall")),
        "stats_track":        _as_json(data.get("stats_track")),
        "stats_distance":     _as_json(data.get("stats_distance")),
        "stats_condition":    _as_json(data.get("stats_condition")),
        "stats_track_distance": _as_json(data.get("stats_track_distance")),
        "stats_full":         _as_json(data.get("stats_full") or data.get("stats_json")),
        "win_prob":           data.get("win_prob"),
        "place_prob":         data.get("place_prob"),
        "model_rank":         int(data["model_rank"]) if data.get("model_rank") is not None else None,
        "confidence":         data.get("confidence") or "",
        "model_version":      data.get("model_version") or "",
        "last20_starts":      data.get("last20_starts") or "",
        "racing_colours":     data.get("racing_colours") or "",
        "gear_change":        data.get("gear_change") or "",
        "stats_first_up":     _as_json(data.get("stats_first_up")),
        "stats_second_up":    _as_json(data.get("stats_second_up")),
        "stats_overall_starts":     data.get("stats_overall_starts"),
        "stats_overall_wins":       data.get("stats_overall_wins"),
        "stats_overall_places":     data.get("stats_overall_places"),
        "stats_overall_win_pct":    data.get("stats_overall_win_pct"),
        "stats_overall_place_pct":  data.get("stats_overall_place_pct"),
        "date":               data.get("date") or date.today().isoformat(),
        "fetched_at":         data.get("fetched_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at":         datetime.now(timezone.utc).isoformat(),
    }

    result = safe_query(
        lambda: get_db()
        .table(T("formfav_runner_enrichment"))
        .upsert(payload, on_conflict="race_uid,number")
        .execute()
        .data
    )
    if result:
        log.debug(f"database: upserted formfav_runner_enrichment {race_uid}/{number}")
        return result[0] if isinstance(result, list) else result
    return None


def get_formfav_race_enrichment(race_uid: str) -> dict[str, Any] | None:
    """Fetch stored FormFav race enrichment for a given race_uid."""
    result = safe_query(
        lambda: get_db()
        .table(T("formfav_race_enrichment"))
        .select("*")
        .eq("race_uid", race_uid)
        .limit(1)
        .execute()
        .data
    )
    return (result or [None])[0]


def get_formfav_runner_enrichments(race_uid: str) -> list[dict[str, Any]]:
    """Fetch all stored FormFav runner enrichments for a given race_uid."""
    return safe_query(
        lambda: get_db()
        .table(T("formfav_runner_enrichment"))
        .select("*")
        .eq("race_uid", race_uid)
        .order("number")
        .execute()
        .data,
        [],
    ) or []


def get_formfav_enrichments_for_date(target_date: str) -> list[dict[str, Any]]:
    """Fetch all FormFav race enrichment rows for a given date."""
    return safe_query(
        lambda: get_db()
        .table(T("formfav_race_enrichment"))
        .select("*")
        .eq("date", target_date)
        .order("race_num")
        .execute()
        .data,
        [],
    ) or []


def get_formfav_runner_enrichments_for_races(race_uids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """
    Fetch all FormFav runner enrichments for a list of race UIDs.
    Queries in chunks of 30 to avoid Supabase URL-length limits.
    Returns a dict mapping race_uid → list of runner enrichment rows (sorted by number).
    """
    if not race_uids:
        return {}
    CHUNK_SIZE = 30
    result: dict[str, list[dict[str, Any]]] = {}
    for i in range(0, len(race_uids), CHUNK_SIZE):
        chunk = race_uids[i:i + CHUNK_SIZE]
        rows = safe_query(
            lambda c=chunk: get_db()
            .table(T("formfav_runner_enrichment"))
            .select("*")
            .in_("race_uid", c)
            .order("number")
            .execute()
            .data,
            [],
        ) or []
        for row in rows:
            uid = row.get("race_uid") or ""
            if uid:
                result.setdefault(uid, []).append(row)
    return result


def upsert_formfav_debug_stats(counters: dict[str, Any]) -> None:
    """
    Insert a pipeline counter snapshot into formfav_debug_stats.
    Called after each formfav_sync() and full_sweep() so the debug endpoint
    always reflects the real execution state (survives restarts).
    """
    payload = {
        "recorded_at":               datetime.now(timezone.utc).isoformat(),
        "total_races_discovered":    int(counters.get("total_races_discovered", 0)),
        "total_domestic_races":      int(counters.get("total_domestic_races", 0)),
        "total_international_filtered": int(counters.get("total_international_filtered", 0)),
        # Merge-stage counters
        "formfav_merge_called":      int(counters.get("formfav_merge_called", 0)),
        "formfav_merge_matched":     int(counters.get("formfav_merge_matched", 0)),
        "formfav_merge_failed":      int(counters.get("formfav_merge_failed", 0)),
        # Sync-stage counters
        "total_formfav_eligible":    int(counters.get("total_formfav_eligible", 0)),
        "total_formfav_called":      int(counters.get("total_formfav_called", 0)),
        "total_formfav_success":     int(counters.get("total_formfav_success", 0)),
        "total_formfav_failed":      int(counters.get("total_formfav_failed", 0)),
    }
    safe_query(
        lambda: get_db()
        .table(T("formfav_debug_stats"))
        .insert(payload)
        .execute()
        .data
    )
    log.debug(f"database: inserted formfav_debug_stats snapshot recorded_at={payload['recorded_at']}")


def get_latest_formfav_debug_stats() -> dict[str, Any] | None:
    """
    Return the most recent formfav_debug_stats row, or None if none exist.
    Used by GET /api/debug/formfav to expose the real pipeline state.
    """
    rows = safe_query(
        lambda: get_db()
        .table(T("formfav_debug_stats"))
        .select("*")
        .order("recorded_at", desc=True)
        .limit(1)
        .execute()
        .data,
        [],
    ) or []
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# RUNNER CONNECTION STATS (jockey/trainer stats anchored to race+runner)
# ---------------------------------------------------------------------------

def upsert_runner_connection_stats(data: dict[str, Any]) -> None:
    """
    Store jockey or trainer stats in the context of a specific race+runner.
    Conflict key: (race_uid, runner_number, person_type).
    """
    race_uid = data.get("race_uid") or ""
    runner_number = data.get("runner_number")
    person_type = data.get("person_type") or ""
    if not race_uid or runner_number is None or not person_type:
        return
    payload = {
        "race_uid":          race_uid,
        "date":              data.get("date") or date.today().isoformat(),
        "track":             data.get("track") or "",
        "race_num":          int(data.get("race_num") or 0),
        "runner_name":       data.get("runner_name") or "",
        "runner_number":     int(runner_number),
        "person_type":       person_type,
        "person_name":       data.get("person_name") or "",
        "race_code":         data.get("race_code") or "gallops",
        "total_starts":      data.get("total_starts"),
        "total_wins":        data.get("total_wins"),
        "overall_win_rate":  data.get("overall_win_rate"),
        "overall_place_rate":data.get("overall_place_rate"),
        "recent_win_rate":   data.get("recent_win_rate"),
        "track_win_rate":    data.get("track_win_rate"),
        "track_starts":      data.get("track_starts"),
        "raw_response":      _as_json(data.get("raw_response")),
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
    }
    safe_query(
        lambda: get_db()
        .table(T("runner_connection_stats"))
        .upsert(payload, on_conflict="race_uid,runner_number,person_type")
        .execute()
    )


def get_runner_connection_stats_for_race(race_uid: str) -> list[dict[str, Any]]:
    """Fetch all jockey/trainer connection stats rows for a given race_uid."""
    return safe_query(
        lambda: get_db()
        .table(T("runner_connection_stats"))
        .select("*")
        .eq("race_uid", race_uid)
        .execute()
        .data,
        [],
    ) or []


# ---------------------------------------------------------------------------
# TRACK BIAS
# ---------------------------------------------------------------------------

def upsert_track_bias(data: dict[str, Any]) -> None:
    """Store FormFav track bias data into track_profiles table."""
    track = (data.get("venue") or "").lower().replace(" ", "-")
    if not track:
        return
    race_type = data.get("raceType") or "R"
    code = {"R": "HORSE", "H": "HARNESS", "G": "GREYHOUND"}.get(race_type, "HORSE")
    barrier_stats = data.get("barrierStats") or []
    inside = next((b for b in barrier_stats if b.get("barrierNumber") == 1), {})
    payload = {
        "track_name":       track,
        "code":             code,
        "inside_bias":      inside.get("advantage"),
        "leader_win_pct":   inside.get("winRate"),
        "updated_at":       datetime.now(timezone.utc).isoformat(),
    }
    safe_query(lambda: get_db().table(T("track_profiles"))
        .upsert(payload, on_conflict="track_name,code").execute())


# ---------------------------------------------------------------------------
# MARKET SNAPSHOTS
# ---------------------------------------------------------------------------

def upsert_market_snapshot(data: dict[str, Any]) -> None:
    """Store OddsPro movers/drifters/top-favs data into market_snapshots."""
    payload = {
        "race_uid":       data.get("race_uid") or "",
        "date":           data.get("date") or date.today().isoformat(),
        "track":          (data.get("track") or "").lower().replace(" ", "-"),
        "race_num":       data.get("raceNumber") or data.get("race_num"),
        "runner_name":    data.get("runnerName") or data.get("runner_name") or "",
        "box_num":        data.get("runnerNumber") or data.get("box_num"),
        "opening_price":  data.get("firstPrice") or data.get("opening_price"),
        "analysis_price": data.get("currentBestOdds") or data.get("analysis_price"),
        "price_movement": str(data.get("movementPercentage") or ""),
        "steam_flag":     bool(data.get("is_mover", False)),
        "drift_flag":     bool(data.get("is_drifter", False)),
        "snapshot_time":  datetime.now(timezone.utc).isoformat(),
    }
    safe_query(lambda: get_db().table(T("market_snapshots")).insert(payload).execute())
