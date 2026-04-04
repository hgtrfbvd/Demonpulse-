"""
api/external_routes.py - DemonPulse External Discovery API Routes
==================================================================
Exposes OddsPro race and meeting discovery endpoints as DemonPulse API routes.

Discovery flow:
  1. GET /api/meetings                       - all meetings with race IDs
  2. GET /api/external/race/<raceId>         - specific race details + runners
  3. GET /api/external/meeting/<meetingId>   - all races in a meeting

Additional endpoints:
  GET /api/external/meetings   - upcoming meetings with type/location filters
  GET /api/external/results    - race results with date/type/location filters
  GET /api/external/tracks     - track list with code/location filters

All data is sourced from OddsPro (authoritative).
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

external_bp = Blueprint("external", __name__)


def _get_connector():
    from connectors.oddspro_connector import OddsProConnector
    return OddsProConnector()


@external_bp.route("/api/meetings", methods=["GET"])
def get_meetings_discovery():
    """
    GET /api/meetings
    Discovery endpoint — returns all meetings with their race IDs.
    This is the entry point for the race/meeting discovery flow.

    Returns the raw list of meeting dicts from OddsPro /api/meetings.
    """
    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503
        data = conn.fetch_meetings_discovery()
        return jsonify({"ok": True, "data": data, "count": len(data)})
    except Exception as e:
        log.error(f"/api/meetings failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve meetings"}), 500


@external_bp.route("/api/external/meetings", methods=["GET"])
def get_external_meetings():
    """
    GET /api/external/meetings
    Get all upcoming meetings with race information.

    Query parameters (all optional):
      type      - race type: T (thoroughbred), H (harness), G (greyhound)
      location  - domestic, international, all
      date      - YYYY-MM-DD (defaults to today)
    """
    type_ = request.args.get("type")
    location = request.args.get("location")
    date = request.args.get("date")

    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503
        meetings = conn.fetch_meetings(
            target_date=date, type_=type_, location=location
        )
        data = [
            {
                "meeting_id": m.meeting_id,
                "track": m.track,
                "code": m.code,
                "date": m.meeting_date,
                "state": m.state,
                "country": m.country,
                "source": m.source,
            }
            for m in meetings
        ]
        return jsonify({"ok": True, "data": data, "count": len(data)})
    except Exception as e:
        log.error(f"/api/external/meetings failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve meetings"}), 500


@external_bp.route("/api/external/race/<race_id>", methods=["GET"])
def get_external_race(race_id: str):
    """
    GET /api/external/race/<raceId>
    Get detailed information for a specific race including all runners
    and their top 3 odds.

    Path parameter:
      raceId - the OddsPro race ID (e.g. 123456)
    """
    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503

        race, runners = conn.fetch_race_with_runners(race_id)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        race_data = {
            "race_uid": race.race_uid,
            "oddspro_race_id": race.oddspro_race_id,
            "date": race.date,
            "track": race.track,
            "race_num": race.race_num,
            "race_name": race.race_name,
            "code": race.code,
            "distance": race.distance,
            "grade": race.grade,
            "jump_time": race.jump_time,
            "status": race.status,
            "condition": race.condition,
            "prize_money": race.prize_money,
            "source": race.source,
        }
        runners_data = [
            {
                "name": r.name,
                "number": r.number,
                "box_num": r.box_num,
                "barrier": r.barrier,
                "trainer": r.trainer,
                "jockey": r.jockey,
                "driver": r.driver,
                "weight": r.weight,
                "price": r.price,
                "scratched": r.scratched,
                "run_style": r.run_style,
                "early_speed": r.early_speed,
                "best_time": r.best_time,
                "career": r.career,
                "rating": r.rating,
            }
            for r in runners
        ]
        return jsonify({
            "ok": True,
            "race": race_data,
            "runners": runners_data,
            "runner_count": len(runners_data),
        })
    except Exception as e:
        log.error(f"/api/external/race/{race_id} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve race"}), 500


@external_bp.route("/api/external/meeting/<meeting_id>", methods=["GET"])
def get_external_meeting(meeting_id: str):
    """
    GET /api/external/meeting/<meetingId>
    Get all races for a specific meeting with detailed runner information
    and odds.

    Path parameter:
      meetingId - the OddsPro meeting ID (e.g. 789)

    Query parameters (optional):
      date - YYYY-MM-DD meeting date
    """
    meeting_date = request.args.get("date", "")

    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503

        # Fetch the actual meeting record first to get accurate code/track/date
        meeting = conn.fetch_meeting(meeting_id, meeting_date=meeting_date)
        if not meeting:
            from connectors.oddspro_connector import MeetingRecord
            meeting = MeetingRecord(
                meeting_id=meeting_id,
                code="",
                source="oddspro",
                track="",
                meeting_date=meeting_date,
            )

        races, runners = conn.fetch_meeting_races_with_runners(meeting)
        if not races:
            return jsonify({"ok": False, "error": "Meeting not found or no races"}), 404

        races_data = []
        for race in races:
            race_runners = [r for r in runners if r.race_uid == race.race_uid]
            races_data.append({
                "race_uid": race.race_uid,
                "oddspro_race_id": race.oddspro_race_id,
                "race_num": race.race_num,
                "race_name": race.race_name,
                "distance": race.distance,
                "grade": race.grade,
                "jump_time": race.jump_time,
                "status": race.status,
                "condition": race.condition,
                "prize_money": race.prize_money,
                "runner_count": len(race_runners),
                "runners": [
                    {
                        "name": r.name,
                        "number": r.number,
                        "box_num": r.box_num,
                        "barrier": r.barrier,
                        "trainer": r.trainer,
                        "jockey": r.jockey,
                        "driver": r.driver,
                        "weight": r.weight,
                        "price": r.price,
                        "scratched": r.scratched,
                    }
                    for r in race_runners
                ],
            })

        return jsonify({
            "ok": True,
            "meeting_id": meeting_id,
            "race_count": len(races_data),
            "races": races_data,
        })
    except Exception as e:
        log.error(f"/api/external/meeting/{meeting_id} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve meeting"}), 500


@external_bp.route("/api/external/results", methods=["GET"])
def get_external_results():
    """
    GET /api/external/results
    Get all race results for a specific day with filtering options.

    Query parameters (all optional):
      date      - YYYY-MM-DD (defaults to today)
      type      - race type: T (thoroughbred), H (harness), G (greyhound)
      location  - domestic, international, all
    """
    date = request.args.get("date")
    type_ = request.args.get("type")
    location = request.args.get("location")

    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503

        results = conn.fetch_results(target_date=date, type_=type_, location=location)
        data = [
            {
                "race_uid": r.race_uid,
                "oddspro_race_id": r.oddspro_race_id,
                "date": r.date,
                "track": r.track,
                "race_num": r.race_num,
                "code": r.code,
                "winner": r.winner,
                "winner_number": r.winner_number,
                "win_price": r.win_price,
                "place_2": r.place_2,
                "place_3": r.place_3,
                "margin": r.margin,
                "winning_time": r.winning_time,
            }
            for r in results
        ]
        return jsonify({"ok": True, "data": data, "count": len(data)})
    except Exception as e:
        log.error(f"/api/external/results failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve results"}), 500


@external_bp.route("/api/external/tracks", methods=["GET"])
def get_external_tracks():
    """
    GET /api/external/tracks
    Get simple list of track names with location filtering.

    Query parameters (all optional):
      code      - race type code: T (thoroughbred), H (harness), G (greyhound)
      location  - location filter (e.g. AUS, domestic)
    """
    code = request.args.get("code")
    location = request.args.get("location")

    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503

        data = conn.fetch_tracks(code=code, location=location)
        return jsonify({"ok": True, "data": data, "count": len(data)})
    except Exception as e:
        log.error(f"/api/external/tracks failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve tracks"}), 500
