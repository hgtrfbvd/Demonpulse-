"""
api/race_routes.py - DemonPulse Race API Routes
=================================================
Provides API endpoints for race data.
All race data is sourced from OddsPro (authoritative).
FormFav provisional overlays are applied near-jump only.
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


@race_bp.route("/<race_uid>", methods=["GET"])
def get_race(race_uid: str):
    """Get a single race by race_uid."""
    try:
        from database import get_race
        from race_status import compute_ntj

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        ntj = compute_ntj(race.get("jump_time"), race.get("date"))
        return jsonify({"ok": True, "race": {**race, **ntj}})
    except Exception as e:
        log.error(f"/api/races/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve race"}), 500


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
    """
    Get official race results for a single race from OddsPro.
    Falls back to local DB result if OddsPro is not configured.
    """
    try:
        from connectors.oddspro_connector import OddsProConnector
        from database import get_race, get_result

        conn = OddsProConnector()
        if conn.is_enabled():
            race = get_race(race_uid)
            oddspro_id = (race or {}).get("oddspro_race_id") or race_uid
            result = conn.fetch_race_result(oddspro_id)
            if result:
                return jsonify({
                    "ok": True,
                    "race_uid": race_uid,
                    "oddspro_race_id": result.oddspro_race_id,
                    "date": result.date,
                    "track": result.track,
                    "race_num": result.race_num,
                    "code": result.code,
                    "winner": result.winner,
                    "winner_number": result.winner_number,
                    "win_price": result.win_price,
                    "place_2": result.place_2,
                    "place_3": result.place_3,
                    "margin": result.margin,
                    "winning_time": result.winning_time,
                    "source": result.source,
                })

        # Fallback to local DB
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
    Manually trigger an OddsPro refresh for a single race.
    Requires the oddspro_race_id to be stored in the race record.
    """
    try:
        from database import get_race
        from connectors.oddspro_connector import OddsProConnector
        from data_engine import _write_race

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        oddspro_id = race.get("oddspro_race_id") or ""
        if not oddspro_id:
            return jsonify({"ok": False, "error": "No OddsPro race ID stored"}), 400

        conn = OddsProConnector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro connector not configured"}), 503

        fresh_race, _ = conn.fetch_race_with_runners(oddspro_id)
        if not fresh_race:
            return jsonify({"ok": False, "error": "OddsPro returned no data"}), 502

        _write_race(fresh_race)
        return jsonify({"ok": True, "race_uid": race_uid, "source": "oddspro"})

    except Exception as e:
        log.error(f"/api/races/{race_uid}/refresh failed: {e}")
        return jsonify({"ok": False, "error": "Race refresh failed"}), 500


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
