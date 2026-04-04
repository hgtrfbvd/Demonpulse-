"""
api/race_routes.py - Race and meeting data endpoints.
"""

import logging
from datetime import date as _date
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
race_bp = Blueprint("races", __name__)

_INTERNAL_ERROR = {"ok": False, "error": "Internal server error"}


@race_bp.route("/api/races")
def api_races():
    """Query params: date (default today), status, code, meeting_id."""
    import database
    from board_builder import _race_to_dict, _runner_to_dict

    date_str = request.args.get("date", _date.today().isoformat())
    status_filter = request.args.get("status")
    code_filter = request.args.get("code", "").upper()
    meeting_id_filter = request.args.get("meeting_id")

    try:
        races = database.get_active_races(date_str)
        result = []
        for race in races:
            if status_filter and race.status != status_filter:
                continue
            if code_filter and race.code != code_filter:
                continue
            if meeting_id_filter and race.meeting_id != meeting_id_filter:
                continue

            runners = database.get_runners_for_race(race.race_id)
            rd = _race_to_dict(race)
            rd["runners"] = [_runner_to_dict(r) for r in runners]
            result.append(rd)

        return jsonify({"ok": True, "count": len(result), "races": result})
    except Exception as e:
        log.exception(f"/api/races failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@race_bp.route("/api/races/<race_id>")
def api_race_detail(race_id):
    """Single race with runners + provisional odds."""
    import database
    from board_builder import _race_to_dict, _runner_to_dict

    try:
        race = database.get_race(race_id)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        runners = database.get_runners_for_race(race_id)
        provisional_odds = database.get_provisional_odds(race_id)

        rd = _race_to_dict(race)
        rd["runners"] = [_runner_to_dict(r) for r in runners]
        rd["provisional_odds"] = provisional_odds

        return jsonify({"ok": True, "race": rd})
    except Exception as e:
        log.exception(f"/api/races/{race_id} failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@race_bp.route("/api/races/<race_id>/runners")
def api_race_runners(race_id):
    """Runners for a race."""
    import database
    from board_builder import _runner_to_dict

    try:
        race = database.get_race(race_id)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        runners = database.get_runners_for_race(race_id)
        return jsonify({
            "ok": True,
            "race_id": race_id,
            "count": len(runners),
            "runners": [_runner_to_dict(r) for r in runners],
        })
    except Exception as e:
        log.exception(f"/api/races/{race_id}/runners failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@race_bp.route("/api/meetings")
def api_meetings():
    """All meetings for today (or ?date=)."""
    import database

    date_str = request.args.get("date", _date.today().isoformat())
    try:
        meetings = database.get_all_meetings(date_str)
        return jsonify({"ok": True, "count": len(meetings), "meetings": meetings})
    except Exception as e:
        log.exception(f"/api/meetings failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@race_bp.route("/api/meetings/<meeting_id>/races")
def api_meeting_races(meeting_id):
    """All races for a meeting."""
    import database
    from board_builder import _race_to_dict, _runner_to_dict

    try:
        races = database.get_races_for_meeting(meeting_id)
        result = []
        for race in races:
            runners = database.get_runners_for_race(race.race_id)
            rd = _race_to_dict(race)
            rd["runners"] = [_runner_to_dict(r) for r in runners]
            result.append(rd)
        return jsonify({"ok": True, "meeting_id": meeting_id,
                        "count": len(result), "races": result})
    except Exception as e:
        log.exception(f"/api/meetings/{meeting_id}/races failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500
