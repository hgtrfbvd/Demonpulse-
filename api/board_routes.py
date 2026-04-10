"""
api/board_routes.py - DemonPulse Board API Routes
===================================================
Provides the live racing board API.
Board is built from stored race data (GREYHOUND: browser pipeline, HORSE: Claude pipeline).
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

board_bp = Blueprint("board", __name__, url_prefix="/api/board")


@board_bp.route("", methods=["GET"])
@board_bp.route("/", methods=["GET"])
def get_board():
    """Get the live racing board."""
    try:
        from board_service import get_board_for_today
        result = get_board_for_today()
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/board failed: {e}")
        return jsonify({"ok": False, "items": [], "error": "Board unavailable"}), 500


@board_bp.route("/blocked", methods=["GET"])
def get_blocked():
    """List explicitly blocked races for today."""
    try:
        from datetime import date
        from database import get_blocked_races
        today = request.args.get("date") or date.today().isoformat()
        blocked = get_blocked_races(today)
        return jsonify({"ok": True, "items": blocked, "count": len(blocked), "date": today})
    except Exception as e:
        log.error(f"/api/board/blocked failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve blocked races"}), 500


@board_bp.route("/ntj", methods=["GET"])
def get_ntj():
    """
    Get Next-to-Jump races (calculated from stored jump_time — no scraping).
    Returns races sorted by seconds_to_jump ascending.
    """
    try:
        from datetime import date
        from database import get_active_races
        from race_status import compute_ntj, should_trigger_formfav_overlay
        from integrity_filter import filter_race

        today = request.args.get("date") or date.today().isoformat()
        races = get_active_races(today)

        ntj_races = []
        for race in races:
            allowed, block_code = filter_race(race)
            if not allowed:
                continue

            ntj = compute_ntj(race.get("jump_time"), race.get("date"))
            secs = ntj.get("seconds_to_jump")
            if secs is None or secs < 0:
                continue

            ntj_races.append({
                "race_uid": race.get("race_uid"),
                "track": race.get("track"),
                "race_num": race.get("race_num"),
                "code": race.get("code"),
                "jump_time": race.get("jump_time"),
                "status": race.get("status"),
                "seconds_to_jump": secs,
                "ntj_label": ntj.get("ntj_label"),
                "is_near_jump": ntj.get("is_near_jump"),
                "formfav_overlay_ready": should_trigger_formfav_overlay(race),
            })

        ntj_races.sort(key=lambda x: x["seconds_to_jump"])
        return jsonify({"ok": True, "items": ntj_races, "count": len(ntj_races)})

    except Exception as e:
        log.error(f"/api/board/ntj failed: {e}")
        return jsonify({"ok": False, "error": "NTJ data unavailable"}), 500
