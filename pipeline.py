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

import hashlib
import logging
from datetime import date
from typing import Any

from connectors.claude_scraper import ClaudeScraper
from features import compute_greyhound_derived, compute_horse_derived
from database import upsert_race as _db_upsert_race, upsert_runners as _db_upsert_runners

log = logging.getLogger(__name__)


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
    """
    try:
        scraper = ClaudeScraper()
        url = f"https://www.thedogs.com.au/racing/{today}?trial=false"
        raw = scraper._extract(
            url,
            "You fetch Australian greyhound racing schedule pages. "
            "Return ONLY a JSON array of venue objects with 'slug' and 'state' fields. "
            "Example: [{\"slug\": \"townsville\", \"state\": \"QLD\"}]",
            '[{"slug": "townsville", "state": "QLD"}]',
        )
        if isinstance(raw, list):
            return raw
    except Exception as e:
        log.warning(f"pipeline: greyhound venue discovery failed: {e}")
    return []


def _discover_horse_venues() -> list[dict]:
    """
    Discover today's horse venues from racingaustralia.horse via Claude.
    Returns list of {"name": ..., "state": ...} dicts.
    """
    try:
        scraper = ClaudeScraper()
        url = "https://publishingservices.racingaustralia.horse/racebooks/"
        raw = scraper._extract(
            url,
            "You fetch Australian thoroughbred racing schedule pages. "
            "Return ONLY a JSON array of venue objects with 'name' and 'state' fields. "
            "Example: [{\"name\": \"Caulfield\", \"state\": \"VIC\"}]",
            '[{"name": "Caulfield", "state": "VIC"}]',
        )
        if isinstance(raw, list):
            return raw
    except Exception as e:
        log.warning(f"pipeline: horse venue discovery failed: {e}")
    return []


# ---------------------------------------------------------------------------
# PIPELINE FUNCTIONS
# ---------------------------------------------------------------------------

def full_sweep(target_date: str | None = None) -> dict[str, Any]:
    """
    Fetch all venues for today, compute derived features, store to Supabase.

    Args:
        target_date: ISO date string (default: today)

    Returns:
        {"ok": True, "date": ..., "races_stored": N}
    """
    today = target_date or date.today().isoformat()
    scraper = ClaudeScraper()
    stored = 0
    errors = 0

    # Greyhounds
    try:
        venues = get_greyhound_venues(today)
        log.info(f"pipeline: greyhound venues for {today}: {len(venues)}")
        for venue in venues:
            try:
                races = scraper.fetch_greyhound_venue(venue["slug"], today)
                for raw in races:
                    try:
                        raw["derived"] = compute_greyhound_derived(raw)
                        race = _normalise_greyhound_race(raw, today)
                        _store_race(race)
                        stored += 1
                    except Exception as e:
                        log.error(f"pipeline: greyhound race store failed ({venue}): {e}")
                        errors += 1
            except Exception as e:
                log.error(f"pipeline: greyhound venue sweep failed ({venue}): {e}")
                errors += 1
    except Exception as e:
        log.error(f"pipeline: greyhound sweep failed: {e}")
        errors += 1

    # Horses
    try:
        venues = get_horse_venues(today)
        log.info(f"pipeline: horse venues for {today}: {len(venues)}")
        for venue in venues:
            try:
                races = scraper.fetch_horse_venue(venue["name"])
                for raw in races:
                    try:
                        raw["derived"] = compute_horse_derived(raw)
                        race = _normalise_horse_race(raw, today)
                        _store_race(race)
                        stored += 1
                    except Exception as e:
                        log.error(f"pipeline: horse race store failed ({venue}): {e}")
                        errors += 1
            except Exception as e:
                log.error(f"pipeline: horse venue sweep failed ({venue}): {e}")
                errors += 1
    except Exception as e:
        log.error(f"pipeline: horse sweep failed: {e}")
        errors += 1

    log.info(f"pipeline: full_sweep complete — date={today} races_stored={stored} errors={errors}")
    return {"ok": errors == 0, "date": today, "races_stored": stored, "errors": errors}


def venue_sweep(venue: dict, code: str = "GREYHOUND",
                target_date: str | None = None) -> dict[str, Any]:
    """
    Fetch a single venue and store its races. Used for live refresh.

    Args:
        venue: {"slug": ..., "state": ...} for greyhounds or {"name": ..., "state": ...} for horses
        code: "GREYHOUND" or "HORSE"
        target_date: ISO date string (default: today)
    """
    today = target_date or date.today().isoformat()
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

    _db_upsert_race(race)

    if runners_raw:
        norm_runners = [
            _normalise_runner(
                r,
                race_uid=race_uid,
                race_num=race.get("race_num", 0),
                track=race.get("track", ""),
                race_date=race.get("date", date.today().isoformat()),
                code=race.get("code", ""),
            )
            for r in runners_raw
        ]
        _db_upsert_runners(race_uid, norm_runners)
