"""
data_engine.py — OddsPro-first data engine

Primary source: OddsPro API
  full_sweep()      → discover today's meetings → fetch each meeting + races → persist
  rolling_refresh() → refresh upcoming/open races → update DB rows
  check_results()   → fetch settled results → persist

Optional fallbacks (FormFav, TheDogs, Racenet) are NOT imported here.
They remain in connectors/ for future optional enrichment only.
"""
import logging
from datetime import date, datetime, timezone

from connectors.oddspro_connector import OddsProConnector

log = logging.getLogger(__name__)

_connector: OddsProConnector | None = None


# ─────────────────────────────────────────────────────────────────
# CONNECTOR
# ─────────────────────────────────────────────────────────────────
def get_connector() -> OddsProConnector:
    global _connector
    if _connector is None:
        _connector = OddsProConnector()
        if _connector.is_enabled():
            log.info("OddsPro connector ready")
        else:
            log.warning("OddsPro connector not enabled — set ODDSPRO_BASE_URL + ODDSPRO_API_KEY")
    return _connector


# ─────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────
def _upsert_race(race) -> None:
    try:
        from db import get_db, T
        row = {
            "race_uid":  race.race_uid,
            "race_id":   race.race_id,
            "meeting_id": race.meeting_id,
            "date":      race.date,
            "track":     race.track,
            "race_num":  race.race_num,
            "code":      race.code,
            "race_name": race.race_name,
            "distance":  race.distance,
            "grade":     race.grade,
            "condition": race.condition,
            "status":    race.status,
            "jump_time": race.jump_time,
            "source":    race.source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        get_db().table(T("today_races")).upsert(row, on_conflict="race_uid").execute()
    except Exception as e:
        log.warning(f"_upsert_race failed for {race.race_uid}: {e}")


def _upsert_runners(runners: list) -> None:
    if not runners:
        return
    try:
        from db import get_db, T
        rows = []
        for rn in runners:
            rows.append({
                "race_uid":   rn.race_uid,
                "race_id":    rn.race_id,
                "runner_id":  rn.runner_id,
                "name":       rn.name,
                "number":     rn.number,
                "box_num":    rn.box_num,
                "barrier":    rn.barrier,
                "trainer":    rn.trainer,
                "jockey":     rn.jockey,
                "driver":     rn.driver,
                "weight":     rn.weight,
                "price":      rn.price,
                "scratched":  rn.scratched,
                "scratch_timing": rn.scratch_timing,
                "stats_json": rn.stats_json,
                "source":     rn.source,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        get_db().table(T("today_runners")).upsert(rows, on_conflict="race_uid,runner_id").execute()
    except Exception as e:
        log.warning(f"_upsert_runners failed: {e}")


def _upsert_result(result) -> None:
    try:
        from db import get_db, T
        row = {
            "race_id":   result.race_id,
            "race_uid":  result.race_uid,
            "positions": result.positions,
            "dividends": result.dividends,
            "source":    result.source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        get_db().table(T("today_races")).update({"status": "resulted"}).eq("race_id", result.race_id).execute()
        get_db().table(T("race_results")).upsert(row, on_conflict="race_id").execute()
    except Exception as e:
        log.warning(f"_upsert_result failed for {result.race_id}: {e}")


def _get_active_race_ids() -> list[str]:
    """Return race_ids for today's races that are not yet resulted."""
    try:
        from db import get_db, T, safe_query
        rows = safe_query(
            lambda: get_db().table(T("today_races"))
            .select("race_id")
            .eq("date", date.today().isoformat())
            .in_("status", ["upcoming", "open", "pending"])
            .execute()
            .data,
            [],
        ) or []
        return [r["race_id"] for r in rows if r.get("race_id")]
    except Exception as e:
        log.warning(f"_get_active_race_ids failed: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
# NTJ HELPER
# ─────────────────────────────────────────────────────────────────
def _ntj_from_start_time(jump_time: str | None) -> int | None:
    """
    Return seconds until jump from jump_time ISO string.
    Returns None if jump_time is missing or unparseable.
    """
    if not jump_time:
        return None
    try:
        dt = datetime.fromisoformat(jump_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        secs = int((dt - now).total_seconds())
        return max(secs, 0)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────
# FULL SWEEP (STARTUP / DAILY BOOTSTRAP)
# ─────────────────────────────────────────────────────────────────
def full_sweep(target_date: str | None = None) -> dict:
    """
    Discover all of today's meetings via OddsPro, then fetch and persist
    each meeting's race list. Called once on startup and optionally at midnight.
    """
    conn = get_connector()
    if not conn.is_enabled():
        return {"ok": False, "error": "OddsPro connector not enabled", "meetings": 0, "races": 0}

    today = target_date or date.today().isoformat()
    log.info(f"full_sweep: starting for {today}")

    try:
        meetings = conn.fetch_meetings(today)
    except Exception as e:
        log.error(f"full_sweep: fetch_meetings failed: {e}")
        return {"ok": False, "error": str(e), "meetings": 0, "races": 0}

    if not meetings:
        log.warning("full_sweep: no meetings returned from OddsPro")
        return {"ok": True, "meetings": 0, "races": 0, "note": "no meetings"}

    total_races = 0
    for meeting in meetings:
        try:
            _, races = conn.fetch_meeting(meeting.meeting_id)
            for race in races:
                _upsert_race(race)
                total_races += 1
        except Exception as e:
            log.warning(f"full_sweep: meeting {meeting.meeting_id} failed: {e}")

    log.info(f"full_sweep complete: {len(meetings)} meetings, {total_races} races")
    return {"ok": True, "meetings": len(meetings), "races": total_races}


# ─────────────────────────────────────────────────────────────────
# ROLLING REFRESH (PERIODIC)
# ─────────────────────────────────────────────────────────────────
def rolling_refresh() -> dict:
    """
    Refresh all active (upcoming/open) races via OddsPro race endpoint.
    Updates odds, scratchings, status, and jump_time from live API truth.
    No board is built from stale or missing data.
    """
    conn = get_connector()
    if not conn.is_enabled():
        return {"ok": False, "error": "OddsPro connector not enabled", "refreshed": 0}

    race_ids = _get_active_race_ids()
    if not race_ids:
        log.info("rolling_refresh: no active races to refresh")
        return {"ok": True, "refreshed": 0, "note": "no active races"}

    refreshed = 0
    errors = 0
    for race_id in race_ids:
        try:
            race, runners = conn.fetch_race(race_id)
            if race:
                _upsert_race(race)
                _upsert_runners(runners)
                refreshed += 1
        except Exception as e:
            log.warning(f"rolling_refresh: race {race_id} failed: {e}")
            errors += 1

    log.info(f"rolling_refresh complete: {refreshed} refreshed, {errors} errors")
    return {"ok": True, "refreshed": refreshed, "errors": errors}


# ─────────────────────────────────────────────────────────────────
# RESULT CHECK
# ─────────────────────────────────────────────────────────────────
def check_results(target_date: str | None = None) -> dict:
    """
    Fetch settled results from OddsPro and persist them.
    """
    conn = get_connector()
    if not conn.is_enabled():
        return {"ok": False, "error": "OddsPro connector not enabled", "results": 0}

    today = target_date or date.today().isoformat()
    try:
        results = conn.fetch_results(today)
    except Exception as e:
        log.error(f"check_results: fetch_results failed: {e}")
        return {"ok": False, "error": str(e), "results": 0}

    for result in results:
        _upsert_result(result)

    log.info(f"check_results: {len(results)} results persisted")
    return {"ok": True, "results": len(results)}


# ─────────────────────────────────────────────────────────────────
# BOARD BUILDER (read from DB — no stale data allowed)
# ─────────────────────────────────────────────────────────────────
def get_board() -> dict:
    """
    Return today's race board from DB (which is only populated via OddsPro).
    Returns empty board if DB has no data — never fabricates entries.
    """
    try:
        from db import get_db, T, safe_query
        rows = safe_query(
            lambda: get_db().table(T("today_races")).select("*")
            .eq("date", date.today().isoformat())
            .in_("status", ["upcoming", "open", "pending"])
            .order("jump_time")
            .limit(100)
            .execute()
            .data,
            [],
        ) or []

        items = []
        for race in rows:
            ntj = _ntj_from_start_time(race.get("jump_time"))
            items.append({
                "race_uid":  race.get("race_uid"),
                "race_id":   race.get("race_id"),
                "code":      race.get("code", "GREYHOUND"),
                "track":     race.get("track"),
                "race_num":  race.get("race_num"),
                "race_name": race.get("race_name"),
                "jump_time": race.get("jump_time"),
                "ntj":       ntj,
                "status":    race.get("status", "upcoming"),
                "distance":  race.get("distance"),
                "condition": race.get("condition"),
                "source":    race.get("source"),
            })

        return {"ok": True, "items": items}
    except Exception as e:
        log.error(f"get_board failed: {e}")
        return {"ok": False, "items": [], "error": "Board unavailable"}
