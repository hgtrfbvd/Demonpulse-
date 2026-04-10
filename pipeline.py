"""
pipeline.py
===========
Daily data pipeline. Fetches all venues for today, extracts races,
computes derived features, stores to Supabase.

Cycles:
  full_sweep()     - fetch all venues for today (runs on startup + every 10 min)
  venue_sweep(v)   - fetch single venue (for live refresh)
  compute_derived()- calculate derived fields from raw stored data

The only data path is:
  Claude API → ClaudeScraper → pipeline.py → Supabase → board_service.py
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any

_AEST = ZoneInfo("Australia/Sydney")

from connectors.claude_scraper import (
    ClaudeScraper,
    ClaudeRateLimitError,
    GREYHOUND_BATCH_SIZE,
    HORSE_BATCH_SIZE,
    save_venue_cache,
    load_venue_cache,
)
from features import compute_greyhound_derived, compute_horse_derived
from database import upsert_race as _db_upsert_race, upsert_runners as _db_upsert_runners
from db import T

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PIPELINE DB STATE — updated by _store_race(); read by /api/debug/claude-pipeline
# ---------------------------------------------------------------------------
_pipeline_db_state: dict = {
    "last_rows_written_today_races": 0,
    "last_rows_written_today_runners": 0,
    "resolved_table_today_races": None,
    "resolved_table_today_runners": None,
    "last_race_uids_written": [],
}


def get_pipeline_db_state() -> dict:
    """Return a snapshot of the pipeline DB write state for diagnostics."""
    return dict(_pipeline_db_state)


# ---------------------------------------------------------------------------
# SWEEP STATUS — per-sweep lifecycle tracking; read by /api/debug/board-status
# ---------------------------------------------------------------------------
_sweep_status: dict = {
    "last_sweep_id": None,
    "last_started_at": None,
    "last_completed_at": None,
    # success | partial_cached | failed_rate_limited | failed_parse | failed_db_write
    "last_status": None,
    "last_failure_stage": None,
    "last_failure_reason": None,
    "last_venues_count": 0,
    "last_races_written": 0,
    "last_runners_written": 0,
    # live_claude | cached_claude | failed_no_cache | mixed
    "last_data_source": None,
    "greyhound_source": None,
    "horse_source": None,
}


def get_sweep_status() -> dict:
    """Return a snapshot of the last full_sweep lifecycle for diagnostics."""
    return dict(_sweep_status)


# ---------------------------------------------------------------------------
# VENUE LISTS — populated from schedule pages
# Extend these or replace with dynamic discovery via Claude when needed.
# ---------------------------------------------------------------------------

GREYHOUND_VENUES: list[dict] = [
    # Each entry: {"slug": "townsville", "state": "QLD"}
    # Populated dynamically by _discover_greyhound_venues() or configured here.
]

HORSE_VENUES: list[dict] = [
    # Each entry: {"name": "Caulfield", "state": "VIC"}
    # Populated dynamically by _discover_horse_venues() or configured here.
]


# ---------------------------------------------------------------------------
# FIELD NORMALISATION
# ---------------------------------------------------------------------------

def _normalise_track(race: dict) -> str:
    """Return a canonical track name from either schema."""
    return (race.get("track_name") or race.get("track") or "").strip()


def _race_uid(code: str, track: str, race_num: int, race_date: str) -> str:
    """Generate a stable race_uid matching database.py format: DATE_CODE_TRACK_NUM."""
    norm = track.lower().replace(" ", "_")
    return f"{race_date}_{code.upper()}_{norm}_{race_num}"


def _normalise_greyhound_race(raw: dict, today: str) -> dict:
    """
    Map Claude greyhound schema → DB today_races schema.
    Preserves all original fields in raw_json.
    """
    track = _normalise_track(raw)
    race_num = int(raw.get("race_number") or raw.get("race_num") or 0)
    race_date = raw.get("date") or today

    uid = _race_uid("GREYHOUND", track, race_num, race_date)

    return {
        "race_uid": uid,
        "date": race_date,
        "track": track,
        "state": raw.get("state") or "",
        "country": "au",
        "race_num": race_num,
        "code": "GREYHOUND",
        "distance": str(raw.get("distance_m") or ""),
        "grade": raw.get("grade") or "",
        "race_name": raw.get("race_type") or "",
        "jump_time": _build_jump_time(raw.get("race_time"), race_date),
        "prize_money": str(raw.get("prize_money") or ""),
        "condition": raw.get("track_condition") or "",
        "status": "upcoming",
        "source": "claude",
        "runner_count": len([r for r in raw.get("runners", []) if not r.get("scratched")]),
        "derived_json": raw.get("derived"),
        "raw_json": {k: v for k, v in raw.items() if k != "runners"},
        # Pass runners through for upsert_runners_for_race
        "_runners": raw.get("runners", []),
        "_race_uid": uid,
    }


def _normalise_horse_race(raw: dict, today: str) -> dict:
    """
    Map Claude horse schema → DB today_races schema.
    Preserves all original fields in raw_json.
    """
    track = _normalise_track(raw)
    race_num = int(raw.get("race_number") or raw.get("race_num") or 0)
    race_date = raw.get("date") or today

    uid = _race_uid("HORSE", track, race_num, race_date)

    return {
        "race_uid": uid,
        "date": race_date,
        "track": track,
        "state": raw.get("state") or "",
        "country": "au",
        "race_num": race_num,
        "code": "HORSE",
        "distance": str(raw.get("distance_m") or ""),
        "grade": raw.get("race_class") or "",
        "race_name": raw.get("race_type") or "",
        "jump_time": _build_jump_time(raw.get("race_time"), race_date),
        "prize_money": str(raw.get("prize_money") or ""),
        "condition": raw.get("track_condition") or "",
        "status": "upcoming",
        "source": "claude",
        "runner_count": len([r for r in raw.get("runners", []) if not r.get("scratched")]),
        "derived_json": raw.get("derived"),
        "raw_json": {k: v for k, v in raw.items() if k != "runners"},
        "_runners": raw.get("runners", []),
        "_race_uid": uid,
    }


def _build_jump_time(race_time: str | None, race_date: str) -> str | None:
    """Build an ISO jump_time string from HH:MM and date."""
    if not race_time:
        return None
    try:
        return f"{race_date}T{race_time}:00+10:00"
    except Exception:
        return None


def _normalise_runner(runner: dict, race_uid: str, race_num: int,
                      track: str, race_date: str, code: str) -> dict:
    """Normalise a runner dict from either schema to today_runners format."""
    # Box or barrier number
    box_num = runner.get("box") or runner.get("barrier")
    try:
        box_num = int(box_num) if box_num is not None else None
    except (TypeError, ValueError):
        box_num = None

    return {
        "race_uid": race_uid,
        "date": race_date,
        "track": track,
        "race_num": race_num,
        "box_num": box_num,
        "barrier": box_num,
        "name": runner.get("name") or "",
        "trainer": runner.get("trainer") or "",
        "jockey": runner.get("jockey") or "",
        "weight": runner.get("weight"),
        "scratched": bool(runner.get("scratched")),
        "run_style": runner.get("run_style"),
        "early_speed": runner.get("early_speed_rating"),
        "best_time": str(runner.get("best_time_distance_match") or ""),
        # Derived fields stored on runner
        "rating": runner.get("consistency_rating"),
        "career": _career_string(runner),
        "source_confidence": "claude",
    }


def _career_string(runner: dict) -> str | None:
    """Build a compact career string from scraped stats."""
    starts = runner.get("career_starts")
    wins = runner.get("career_wins")
    places = runner.get("career_places")
    if starts is not None:
        return f"{starts}:{wins or 0}-{places or 0}"
    return None


# ---------------------------------------------------------------------------
# VENUE DISCOVERY
# ---------------------------------------------------------------------------

def get_greyhound_venues(today: str) -> list[dict]:
    """Return list of greyhound venue dicts for today. Uses configured list or discovers."""
    if GREYHOUND_VENUES:
        return GREYHOUND_VENUES
    return _discover_greyhound_venues(today)


def get_horse_venues(today: str) -> list[dict]:
    """Return list of horse venue dicts for today. Uses configured list or discovers."""
    if HORSE_VENUES:
        return HORSE_VENUES
    return _discover_horse_venues()


def _discover_greyhound_venues(today: str) -> list[dict]:
    """
    Discover today's greyhound venues from thedogs.com.au schedule via Claude.
    Returns list of {"slug": ..., "state": ...} dicts.

    On HTTP 429 the last cached venue list is used so board building can
    continue. Sets _sweep_status["greyhound_source"] to reflect the source.
    """
    cache_key = f"greyhound_{today}"
    try:
        scraper = ClaudeScraper()
        venues = scraper.discover_greyhound_venues(today)
        if venues:
            log.info(
                f"[VENUES_FETCH_SUCCESS] type=greyhound count={len(venues)} "
                f"date={today} source=live_claude"
            )
            save_venue_cache(cache_key, venues)
            _sweep_status["greyhound_source"] = "live_claude"
        else:
            log.warning(
                f"pipeline: greyhound venue discovery returned 0 venues for {today}"
            )
            _sweep_status["greyhound_source"] = "live_claude"
        return venues
    except ClaudeRateLimitError as exc:
        log.error(
            f"[VENUES_FETCH_429] type=greyhound provider=anthropic "
            f"stage=venue_fetch retry_delay={exc.retry_after:.0f}s "
            f"endpoint={exc.endpoint!r} date={today}"
        )
        cached = load_venue_cache(cache_key)
        if cached:
            log.info(
                f"[VENUES_FETCH_CACHE_USED] type=greyhound source=cached_claude "
                f"count={len(cached)} date={today}"
            )
            _sweep_status["greyhound_source"] = "cached_claude"
            return cached
        log.error(
            f"[VENUES_FETCH_NO_CACHE] type=greyhound source=failed_no_cache "
            f"date={today}"
        )
        _sweep_status["greyhound_source"] = "failed_no_cache"
        return []
    except Exception as exc:
        log.warning(f"pipeline: greyhound venue discovery failed: {exc}")
        _sweep_status["greyhound_source"] = "failed_no_cache"
    return []


def _discover_horse_venues() -> list[dict]:
    """
    Discover today's horse venues from racingaustralia.horse via Claude.
    Returns list of {"name": ..., "state": ...} dicts.

    On HTTP 429 the last cached venue list is used so board building can
    continue. Sets _sweep_status["horse_source"] to reflect the source.
    """
    today = datetime.now(_AEST).date().isoformat()
    cache_key = f"horse_{today}"
    try:
        scraper = ClaudeScraper()
        venues = scraper.discover_horse_venues()
        if venues:
            log.info(
                f"[VENUES_FETCH_SUCCESS] type=horse count={len(venues)} "
                f"date={today} source=live_claude"
            )
            save_venue_cache(cache_key, venues)
            _sweep_status["horse_source"] = "live_claude"
        else:
            log.warning("pipeline: horse venue discovery returned 0 venues")
            _sweep_status["horse_source"] = "live_claude"
        return venues
    except ClaudeRateLimitError as exc:
        log.error(
            f"[VENUES_FETCH_429] type=horse provider=anthropic "
            f"stage=venue_fetch retry_delay={exc.retry_after:.0f}s "
            f"endpoint={exc.endpoint!r} date={today}"
        )
        cached = load_venue_cache(cache_key)
        if cached:
            log.info(
                f"[VENUES_FETCH_CACHE_USED] type=horse source=cached_claude "
                f"count={len(cached)} date={today}"
            )
            _sweep_status["horse_source"] = "cached_claude"
            return cached
        log.error(
            f"[VENUES_FETCH_NO_CACHE] type=horse source=failed_no_cache "
            f"date={today}"
        )
        _sweep_status["horse_source"] = "failed_no_cache"
        return []
    except Exception as exc:
        log.warning(f"pipeline: horse venue discovery failed: {exc}")
        _sweep_status["horse_source"] = "failed_no_cache"
    return []


# ---------------------------------------------------------------------------
# PIPELINE FUNCTIONS
# ---------------------------------------------------------------------------

def _chunks(lst: list, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def full_sweep(target_date: str | None = None) -> dict[str, Any]:
    """
    Fetch all venues for today, compute derived features, store to Supabase.
    Uses batch scraping (4 greyhound venues / 2 horse venues per Claude call)
    to maximise context window usage and minimise API calls.

    On HTTP 429, venue discovery falls back to cached venue lists so the
    board can still be built with slightly stale venue data.

    Args:
        target_date: ISO date string (default: today)

    Returns:
        {"ok": True, "date": ..., "races_stored": N, "status": "success"|"partial_cached"|...}
    """
    # Always use AEST date so stored races match board_service queries (which
    # also use AEST).  On a UTC server date.today() can differ from the AEST
    # calendar date by up to 10–11 hours, causing an empty board.
    today = target_date or datetime.now(_AEST).date().isoformat()
    sweep_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    # --- sweep start ---
    _sweep_status.update({
        "last_sweep_id": sweep_id,
        "last_started_at": datetime.utcnow().isoformat(),
        "last_completed_at": None,
        "last_status": "running",
        "last_failure_stage": None,
        "last_failure_reason": None,
        "greyhound_source": None,
        "horse_source": None,
    })
    log.info(f"[BOARD_BUILD_START] sweep_id={sweep_id} date={today}")

    # Snapshot DB counters at sweep start so we can compute delta at the end
    _start_races = _pipeline_db_state.get("last_rows_written_today_races", 0)
    _start_runners = _pipeline_db_state.get("last_rows_written_today_runners", 0)

    scraper = ClaudeScraper()
    stored = 0
    errors = 0
    races_prepared = 0

    # Greyhounds — batch 4 venues per call
    try:
        venues = get_greyhound_venues(today)
        log.info(f"pipeline: greyhound venues for {today}: {len(venues)}")
        for batch in _chunks(venues, GREYHOUND_BATCH_SIZE):
            try:
                results = scraper.fetch_greyhound_batch(batch, today)
                for slug, races in results.items():
                    races_prepared += len(races)
                    for raw in races:
                        try:
                            raw["derived"] = compute_greyhound_derived(raw)
                            race = _normalise_greyhound_race(raw, today)
                            _store_race(race)
                            stored += 1
                        except Exception as e:
                            log.error(f"pipeline: greyhound race store failed ({slug}): {e}")
                            errors += 1
            except Exception as e:
                slugs = [v["slug"] for v in batch]
                log.error(f"pipeline: greyhound batch failed ({slugs}): {e}")
                errors += 1
    except Exception as e:
        log.error(f"pipeline: greyhound sweep failed: {e}")
        errors += 1

    # Horses — batch 2 venues per call
    try:
        venues = get_horse_venues(today)
        log.info(f"pipeline: horse venues for {today}: {len(venues)}")
        for batch in _chunks(venues, HORSE_BATCH_SIZE):
            try:
                results = scraper.fetch_horse_batch(batch)
                for venue_name, races in results.items():
                    races_prepared += len(races)
                    for raw in races:
                        try:
                            raw["derived"] = compute_horse_derived(raw)
                            race = _normalise_horse_race(raw, today)
                            _store_race(race)
                            stored += 1
                        except Exception as e:
                            log.error(f"pipeline: horse race store failed ({venue_name}): {e}")
                            errors += 1
            except Exception as e:
                names = [v["name"] for v in batch]
                log.error(f"pipeline: horse batch failed ({names}): {e}")
                errors += 1
    except Exception as e:
        log.error(f"pipeline: horse sweep failed: {e}")
        errors += 1

    # --- compute sweep metrics ---
    races_written = _pipeline_db_state.get("last_rows_written_today_races", 0) - _start_races
    runners_written = _pipeline_db_state.get("last_rows_written_today_runners", 0) - _start_runners

    # Determine data source based on what venue discovery reported
    gdog_src = _sweep_status.get("greyhound_source") or "live_claude"
    horse_src = _sweep_status.get("horse_source") or "live_claude"
    any_cached = "cached_claude" in (gdog_src, horse_src)
    any_no_cache = "failed_no_cache" in (gdog_src, horse_src)

    if any_no_cache and any_cached:
        data_source = "mixed"
    elif any_no_cache:
        data_source = "failed_no_cache"
    elif any_cached:
        data_source = "cached_claude"
    else:
        data_source = "live_claude"

    # Determine sweep status
    if stored == 0 and any_no_cache:
        sweep_status_val = "failed_rate_limited"
    elif stored == 0 and errors > 0:
        sweep_status_val = "failed_parse"
    elif any_cached and stored > 0:
        sweep_status_val = "partial_cached"
    elif stored > 0 and errors == 0:
        sweep_status_val = "success"
    elif stored > 0:
        sweep_status_val = "partial_cached"
    else:
        sweep_status_val = "failed_rate_limited"

    if stored == 0 and errors == 0:
        log.warning(
            f"pipeline: full_sweep stored 0 races with 0 errors for {today} — "
            "venue discovery may have returned an empty list. "
            "Check ANTHROPIC_API_KEY and ClaudeScraper logs."
        )

    # Structured board-build logs
    log.info(f"[RACES_PREPARED] sweep_id={sweep_id} count={races_prepared}")
    log.info(f"[TODAY_RACES_UPSERTED] sweep_id={sweep_id} count={races_written}")
    log.info(f"[TODAY_RUNNERS_UPSERTED] sweep_id={sweep_id} count={runners_written}")
    log.info(
        f"[BOARD_BUILD_DONE] sweep_id={sweep_id} status={sweep_status_val} "
        f"races_stored={stored} errors={errors} data_source={data_source}"
    )
    log.info(
        f"pipeline: full_sweep complete — date={today} races_stored={stored} "
        f"errors={errors} status={sweep_status_val} source={data_source}"
    )

    now_iso = datetime.utcnow().isoformat()
    _sweep_status.update({
        "last_completed_at": now_iso,
        "last_status": sweep_status_val,
        "last_venues_count": 0,  # legacy; per-type counts are in greyhound/horse logs
        "last_races_written": races_written,
        "last_runners_written": runners_written,
        "last_data_source": data_source,
    })

    # ok=True for success or partial_cached so the scheduler rebuilds the board
    ok = stored > 0 or sweep_status_val == "partial_cached"
    return {
        "ok": ok,
        "date": today,
        "races_stored": stored,
        "errors": errors,
        "status": sweep_status_val,
        "data_source": data_source,
        "races_written": races_written,
        "runners_written": runners_written,
    }


def venue_sweep(venue: dict, code: str = "GREYHOUND",
                target_date: str | None = None) -> dict[str, Any]:
    """
    Fetch a single venue and store its races. Used for live refresh.

    Args:
        venue: {"slug": ..., "state": ...} for greyhounds or {"name": ..., "state": ...} for horses
        code: "GREYHOUND" or "HORSE"
        target_date: ISO date string (default: today)
    """
    today = target_date or datetime.now(_AEST).date().isoformat()
    scraper = ClaudeScraper()
    stored = 0

    try:
        if code == "GREYHOUND":
            races = scraper.fetch_greyhound_venue(venue["slug"], today)
            normalise = _normalise_greyhound_race
            derive = compute_greyhound_derived
        else:
            races = scraper.fetch_horse_venue(venue["name"])
            normalise = _normalise_horse_race
            derive = compute_horse_derived

        for raw in races:
            raw["derived"] = derive(raw)
            race = normalise(raw, today)
            _store_race(race)
            stored += 1

        return {"ok": True, "venue": venue, "races_stored": stored}
    except Exception as e:
        log.error(f"pipeline: venue_sweep failed ({venue}): {e}")
        return {"ok": False, "venue": venue, "error": str(e)}


def _store_race(race: dict) -> None:
    """Persist a normalised race dict and its runners to Supabase."""
    runners_raw = race.pop("_runners", [])
    race_uid = race.pop("_race_uid", race.get("race_uid", ""))

    # Resolve canonical table names via T() so TEST/LIVE are always correct.
    table_races = T("today_races")
    table_runners = T("today_runners")
    _pipeline_db_state["resolved_table_today_races"] = table_races
    _pipeline_db_state["resolved_table_today_runners"] = table_runners

    log.info(
        f"[PIPELINE DB] upsert race race_uid={race_uid!r} "
        f"table={table_races!r} runners_prepared={len(runners_raw)}"
    )

    upsert_result = _db_upsert_race(race)
    if upsert_result:
        _pipeline_db_state["last_rows_written_today_races"] += 1
        uids = _pipeline_db_state.setdefault("last_race_uids_written", [])
        if race_uid and race_uid not in uids:
            uids.append(race_uid)
        log.info(
            f"[PIPELINE DB] race upserted race_uid={race_uid!r} table={table_races!r}"
        )
    else:
        log.warning(
            f"[PIPELINE DB] race upsert returned no data race_uid={race_uid!r} "
            f"table={table_races!r} — check DB connection and payload"
        )

    # Resolve the UUID primary key of the newly upserted race so that
    # today_runners.race_id (a UUID FK) can be set to a valid value.
    # If the upsert didn't return a row (e.g. Supabase returned nothing),
    # pass None — the column is nullable and the conflict key is (race_uid,
    # box_num), so runners will still be stored correctly.
    race_id_uuid: str | None = (
        upsert_result.get("id") if isinstance(upsert_result, dict) else None
    )

    if runners_raw:
        norm_runners = [
            _normalise_runner(
                r,
                race_uid=race_uid,
                race_num=race.get("race_num", 0),
                track=race.get("track", ""),
                race_date=race.get("date", datetime.now(_AEST).date().isoformat()),
                code=race.get("code", ""),
            )
            for r in runners_raw
        ]
        log.info(
            f"[PIPELINE DB] upsert {len(norm_runners)} runners race_uid={race_uid!r} "
            f"table={table_runners!r}"
        )
        count = _db_upsert_runners(race_id_uuid, norm_runners)
        _pipeline_db_state["last_rows_written_today_runners"] += count
        log.info(
            f"[PIPELINE DB] runners upserted count={count} race_uid={race_uid!r} "
            f"table={table_runners!r}"
        )
