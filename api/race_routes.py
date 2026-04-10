"""
api/race_routes.py - DemonPulse Race API Routes
=================================================
Provides API endpoints for race data.
All race data is sourced from the Claude-powered pipeline.
"""
from __future__ import annotations

import logging
from datetime import date
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

race_bp = Blueprint("races", __name__, url_prefix="/api/races")


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
