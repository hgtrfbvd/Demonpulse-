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
import json

from db import get_db, safe_query, T

log = logging.getLogger(__name__)


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
    """Fetch all upcoming/open races for today."""
    return safe_query(
        lambda: get_db()
        .table(T("today_races"))
        .select("*")
        .eq("date", target_date)
        .in_("status", ["upcoming", "open", "interim"])
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
    for r in runners:
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
            log.warning(
                f"database.upsert_runners: skipping runner missing box_num/barrier/number "
                f"(race_uid={race_uid!r}, name={r.get('name')!r})"
            )
            continue
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
    race_date, code, *track_parts_and_num = parts
    if not track_parts_and_num:
        return None
    race_num_str = track_parts_and_num[-1]
    track = "-".join(track_parts_and_num[:-1])
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
