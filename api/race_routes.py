"""
api/race_routes.py - DemonPulse Race API Routes
=================================================
Provides API endpoints for race data.
All race data is sourced from the Claude-powered pipeline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from datetime import date
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

race_bp = Blueprint("races", __name__, url_prefix="/api/races")

# On-demand refresh: refresh race data if older than this many seconds
LIVE_STALE_SECONDS = 90


def _seconds_since(fetched_at: str | None) -> float:
    """Return seconds elapsed since fetched_at (ISO string). Returns inf if unparseable."""
    if not fetched_at:
        return float("inf")
    try:
        dt = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return float("inf")


@race_bp.route("", methods=["GET"])
def list_races():
    """List races for today (or a given date)."""
    target_date = request.args.get("date") or date.today().isoformat()
    status_filter = request.args.get("status")  # optional filter

    try:
        from database import get_races_for_date, get_active_races
        from race_status import compute_ntj

        if status_filter == "active":
            races = get_active_races(target_date)
        else:
            races = get_races_for_date(target_date)

        items = []
        for race in races:
            ntj = compute_ntj(race.get("jump_time"), race.get("date"))
            items.append({**race, **ntj})

        return jsonify({"ok": True, "items": items, "count": len(items), "date": target_date})
    except Exception as e:
        log.error(f"/api/races failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve races"}), 500


@race_bp.route("/upcoming", methods=["GET"])
def get_upcoming_races():
    """Get upcoming races from the live board (alias for board)."""
    try:
        from board_service import get_board_for_today
        result = get_board_for_today()
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/races/upcoming failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve upcoming races"}), 500


@race_bp.route("/<race_uid>", methods=["GET"])
def get_race(race_uid: str):
    """Get a single race by race_uid."""
    try:
        from database import get_race, get_runners_for_race
        from race_status import compute_ntj

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        ntj = compute_ntj(race.get("jump_time"), race.get("date"))
        runners = get_runners_for_race(race_uid)
        return jsonify({"ok": True, "race": {**race, **ntj, "runners": runners}})
    except Exception as e:
        log.error(f"/api/races/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve race"}), 500


@race_bp.route("/<race_uid>/analysis", methods=["GET"])
def get_race_analysis(race_uid: str):
    """Get race data and runners for analysis (Claude pipeline)."""
    try:
        from database import get_race, get_runners_for_race
        from race_status import compute_ntj

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        ntj = compute_ntj(race.get("jump_time"), race.get("date"))
        runners = get_runners_for_race(race_uid)
        return jsonify({"ok": True, "race": {**race, **ntj, "runners": runners}})
    except Exception as e:
        log.error(f"/api/races/{race_uid}/analysis failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve race analysis"}), 500


@race_bp.route("/<race_uid>/result", methods=["GET"])
def get_race_result(race_uid: str):
    """Get the settled result for a race."""
    try:
        from database import get_result
        result = get_result(race_uid)
        if not result:
            return jsonify({"ok": False, "error": "Result not found"}), 404
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        log.error(f"/api/races/{race_uid}/result failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve result"}), 500


@race_bp.route("/<race_uid>/results", methods=["GET"])
def get_race_results(race_uid: str):
    """Get stored result for a race."""
    try:
        from database import get_result

        stored = get_result(race_uid)
        if stored:
            return jsonify({"ok": True, **stored})

        return jsonify({"ok": False, "error": "Result not found"}), 404
    except Exception as e:
        log.error(f"/api/races/{race_uid}/results failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve results"}), 500


@race_bp.route("/<race_uid>/refresh", methods=["POST"])
def refresh_race(race_uid: str):
    """
    Trigger a venue sweep to refresh a single race.
    Identifies venue from stored race record and re-fetches.
    """
    try:
        from database import get_race
        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        track = race.get("track") or ""
        code = (race.get("code") or "GREYHOUND").upper()
        race_date = race.get("date") or ""

        from pipeline import venue_sweep
        venue = {"slug": track.lower().replace(" ", "-"), "name": track}
        result = venue_sweep(venue, code=code, target_date=race_date)
        return jsonify({"ok": result.get("ok", False), "race_uid": race_uid, "source": "claude"})

    except Exception as e:
        log.error(f"/api/races/{race_uid}/refresh failed: {e}")
        return jsonify({"ok": False, "error": "Race refresh failed"}), 500


@race_bp.route("/<race_uid>/note", methods=["POST"])
def save_race_note(race_uid: str):
    """Save a user note for a race."""
    try:
        from database import save_race_note as db_save_note, get_race
        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404
        body = request.get_json(silent=True) or {}
        note = str(body.get("note", ""))
        db_save_note(race_uid, note)
        return jsonify({"ok": True, "race_uid": race_uid})
    except Exception as e:
        log.error(f"/api/races/{race_uid}/note failed: {e}")
        return jsonify({"ok": False, "error": "Could not save note"}), 500


@race_bp.route("/<race_uid>/live", methods=["GET"])
def live_race(race_uid: str):
    """
    On-demand race refresh endpoint.
    Returns cached data immediately if fresh (< LIVE_STALE_SECONDS old).
    Triggers a targeted single-race scrape via ClaudeScraper if data is stale.
    Never errors to the client — always returns stale data on failure.
    """
    from database import get_race, get_runners_for_race
    from database import upsert_race as _db_upsert_race, upsert_runners as _db_upsert_runners

    race = get_race(race_uid)
    if not race:
        return jsonify({"ok": False, "error": "not found"}), 404

    age = _seconds_since(race.get("fetched_at"))
    refreshed = False

    if age > LIVE_STALE_SECONDS:
        try:
            from connectors.claude_scraper import ClaudeScraper
            from pipeline import _normalise_greyhound_race, _normalise_horse_race
            from pipeline import _store_race
            from features import compute_greyhound_derived, compute_horse_derived

            scraper = ClaudeScraper()
            code = (race.get("code") or "GREYHOUND").upper()
            track = race.get("track") or ""
            venue_slug = track.lower().replace(" ", "-")
            race_date = race.get("date") or date.today().isoformat()
            race_num = int(race.get("race_num") or 0)

            fresh_raw = scraper.fetch_single_race(
                code=code,
                venue_slug=venue_slug,
                date_slug=race_date,
                race_num=race_num,
            )
            if fresh_raw:
                if code == "GREYHOUND":
                    fresh_raw["derived"] = compute_greyhound_derived(fresh_raw)
                    fresh = _normalise_greyhound_race(fresh_raw, race_date)
                else:
                    fresh_raw["derived"] = compute_horse_derived(fresh_raw)
                    fresh = _normalise_horse_race(fresh_raw, race_date)
                _store_race(fresh)
                race = get_race(race_uid) or race
                refreshed = True
        except Exception as e:
            log.warning(f"live_race refresh failed {race_uid}: {e}")
            # Fall through — return stale data, never error

    runners = get_runners_for_race(race_uid)
    return jsonify({"ok": True, "race": race, "runners": runners, "refreshed": refreshed})


@race_bp.route("/<race_uid>/blocked", methods=["GET"])
def get_blocked_status(race_uid: str):
    """Check if a race is explicitly blocked."""
    try:
        from database import get_race
        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404
        return jsonify({
            "ok": True,
            "race_uid": race_uid,
            "blocked": race.get("status") == "blocked",
            "block_code": race.get("block_code") or "",
        })
    except Exception as e:
        log.error(f"/api/races/{race_uid}/blocked failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve blocked status"}), 500
