"""
routes/dashboard_dogs.py
==========================
Flask blueprint for the DOGS dashboard API routes.

Exposes the new browser-based greyhound collection pipeline state
and day board data for the dashboard.

Registered in app.py as: app.register_blueprint(dogs_dashboard_bp)

Endpoints:
  GET /api/dogs/board              — day board for today
  GET /api/dogs/upcoming           — next upcoming race
  GET /api/dogs/race/<race_uid>    — race detail + runners
  GET /api/dogs/health             — pipeline health/status
  POST /api/dogs/collect           — trigger manual board collection
  POST /api/dogs/race/<race_uid>/refresh — refresh a single race

Logging prefix: [DOGS_DASHBOARD]
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
_AEST = ZoneInfo("Australia/Sydney")

dogs_dashboard_bp = Blueprint("dogs_dashboard", __name__, url_prefix="/api/dogs")

# In-memory health state updated by collection runs
_dogs_health: dict = {
    "last_board_collect_at": None,
    "last_board_entries": 0,
    "last_collection_source": "thedogs.com.au",
    "last_error": None,
    "current_race_being_processed": None,
    "parser_last_success": None,
    "parser_last_error": None,
}


def _update_health(**kwargs) -> None:
    _dogs_health.update(kwargs)


@dogs_dashboard_bp.route("/board", methods=["GET"])
def get_dogs_board():
    """
    Get today's greyhound day board from stored races.

    Returns races with collection status, sorted by jump_time.
    """
    try:
        from datetime import date
        from database import get_races_for_date
        from race_status import compute_ntj

        today = request.args.get("date") or datetime.now(_AEST).date().isoformat()
        races = get_races_for_date(today)

        # Filter to GREYHOUND only
        dog_races = [r for r in races if (r.get("code") or "").upper() == "GREYHOUND"]

        items = []
        for race in dog_races:
            ntj = compute_ntj(race.get("jump_time"), race.get("date"))
            source = (race.get("source") or "unknown")
            items.append({
                "race_uid": race.get("race_uid"),
                "track": race.get("track"),
                "race_num": race.get("race_num"),
                "jump_time": race.get("jump_time"),
                "status": race.get("status"),
                "source": source,
                "capture_status": "captured" if source == "thedogs_browser" else "legacy",
                "seconds_to_jump": ntj.get("seconds_to_jump"),
                "ntj_display": ntj.get("ntj_display"),
            })

        items.sort(key=lambda x: x.get("seconds_to_jump") or float("inf"))

        return jsonify({
            "ok": True,
            "date": today,
            "count": len(items),
            "items": items,
            "source": "thedogs.com.au",
        })
    except Exception as e:
        log.error(f"[DOGS_DASHBOARD] /api/dogs/board failed: {e}")
        return jsonify({"ok": False, "items": [], "error": "Board unavailable"}), 500


@dogs_dashboard_bp.route("/upcoming", methods=["GET"])
def get_upcoming_dog_race():
    """
    Return the next upcoming greyhound race and its collection status.
    """
    try:
        from database import get_active_races
        from race_status import compute_ntj

        today = datetime.now(_AEST).date().isoformat()
        races = get_active_races(today)
        dog_races = [r for r in races if (r.get("code") or "").upper() == "GREYHOUND"]

        if not dog_races:
            return jsonify({"ok": True, "race": None, "message": "No upcoming dog races"})

        # Sort by time to jump, pick earliest upcoming
        def _sort(r):
            ntj = compute_ntj(r.get("jump_time"), r.get("date"))
            s = ntj.get("seconds_to_jump")
            return s if s is not None and s > 0 else float("inf")

        dog_races.sort(key=_sort)
        next_race = dog_races[0]
        ntj = compute_ntj(next_race.get("jump_time"), next_race.get("date"))

        return jsonify({
            "ok": True,
            "race": {
                **next_race,
                **ntj,
                "source": next_race.get("source") or "unknown",
                "source_page": (
                    f"https://www.thedogs.com.au/racing/"
                    f"{(next_race.get('track') or '').lower().replace(' ', '-')}/"
                    f"{next_race.get('date')}/{next_race.get('race_num')}/expert-form"
                ),
            },
        })
    except Exception as e:
        log.error(f"[DOGS_DASHBOARD] /api/dogs/upcoming failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve upcoming race"}), 500


@dogs_dashboard_bp.route("/race/<race_uid>", methods=["GET"])
def get_dog_race_detail(race_uid: str):
    """
    Return full race detail + runners + extracted fields + capture metadata.
    """
    try:
        from database import get_race, get_runners_for_race
        from race_status import compute_ntj

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        ntj = compute_ntj(race.get("jump_time"), race.get("date"))
        runners = get_runners_for_race(race_uid)
        raw_json = race.get("raw_json") or {}

        track = race.get("track") or ""
        race_num = race.get("race_num")
        race_date = race.get("date") or ""

        return jsonify({
            "ok": True,
            "race": {**race, **ntj},
            "runners": runners,
            "source": race.get("source") or "unknown",
            "source_page": (
                f"https://www.thedogs.com.au/racing/"
                f"{track.lower().replace(' ', '-')}/"
                f"{race_date}/{race_num}/expert-form"
            ),
            "screenshot_path": raw_json.get("_screenshot_path"),
            "html_path": raw_json.get("_html_path"),
            "capture_timestamp": raw_json.get("_race_capture_ts"),
            "parse_errors": raw_json.get("_parse_errors", []),
            "raw_debug": raw_json if request.args.get("debug") == "1" else None,
        })
    except Exception as e:
        log.error(f"[DOGS_DASHBOARD] /api/dogs/race/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve race"}), 500


@dogs_dashboard_bp.route("/health", methods=["GET"])
def get_dogs_pipeline_health():
    """
    Return dogs pipeline health: last refresh, board size, parser status.
    """
    try:
        from database import get_races_for_date
        today = datetime.now(_AEST).date().isoformat()
        all_races = get_races_for_date(today)
        dog_races = [r for r in all_races if (r.get("code") or "").upper() == "GREYHOUND"]
        browser_sourced = [r for r in dog_races if r.get("source") == "thedogs_browser"]

        return jsonify({
            "ok": True,
            "date": today,
            "last_board_collect_at": _dogs_health.get("last_board_collect_at"),
            "board_size": len(dog_races),
            "browser_sourced_count": len(browser_sourced),
            "last_collection_source": _dogs_health.get("last_collection_source"),
            "current_race_being_processed": _dogs_health.get("current_race_being_processed"),
            "parser_last_success": _dogs_health.get("parser_last_success"),
            "parser_last_error": _dogs_health.get("parser_last_error"),
            "last_error": _dogs_health.get("last_error"),
        })
    except Exception as e:
        log.error(f"[DOGS_DASHBOARD] /api/dogs/health failed: {e}")
        return jsonify({"ok": False, "error": "Health check failed"}), 500


@dogs_dashboard_bp.route("/collect", methods=["POST"])
def trigger_dogs_collection():
    """
    Manually trigger a greyhound board collection for today (or given date).
    """
    try:
        data = request.get_json(silent=True) or {}
        target_date = data.get("date") or datetime.now(_AEST).date().isoformat()

        log.info(f"[DOGS_DASHBOARD] manual collection triggered date={target_date}")

        from services.dogs_board_service import collect_greyhound_board
        from pipeline import _store_race

        _update_health(
            last_board_collect_at=datetime.utcnow().isoformat(),
            last_error=None,
        )

        races = collect_greyhound_board(target_date)
        stored = 0
        errors = 0
        for race in races:
            try:
                _store_race(race)
                stored += 1
            except Exception as exc:
                log.error(f"[DOGS_DASHBOARD] store race failed: {exc}")
                errors += 1

        _update_health(
            last_board_entries=len(races),
            last_board_collect_at=datetime.utcnow().isoformat(),
        )

        return jsonify({
            "ok": True,
            "date": target_date,
            "races_collected": len(races),
            "races_stored": stored,
            "errors": errors,
            "source": "thedogs.com.au",
        })
    except Exception as e:
        log.error(f"[DOGS_DASHBOARD] /api/dogs/collect failed: {e}")
        _update_health(last_error=str(e))
        return jsonify({"ok": False, "error": "Collection failed"}), 500


@dogs_dashboard_bp.route("/race/<race_uid>/refresh", methods=["POST"])
def refresh_dog_race(race_uid: str):
    """
    Refresh a single greyhound race from thedogs.com.au.
    """
    try:
        from database import get_race
        from pipeline import _store_race

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        _update_health(current_race_being_processed=race_uid)

        from services.dogs_capture_service import refresh_race
        fresh = refresh_race(race_uid, race)

        if not fresh:
            _update_health(
                parser_last_error=f"refresh failed for {race_uid}",
                current_race_being_processed=None,
            )
            return jsonify({"ok": False, "error": "Race refresh failed"}), 500

        _store_race(fresh)
        _update_health(
            parser_last_success=datetime.utcnow().isoformat(),
            parser_last_error=None,
            current_race_being_processed=None,
        )

        return jsonify({
            "ok": True,
            "race_uid": race_uid,
            "source": "thedogs_browser",
        })
    except Exception as e:
        log.error(f"[DOGS_DASHBOARD] /api/dogs/race/{race_uid}/refresh failed: {e}")
        _update_health(
            parser_last_error=str(e),
            current_race_being_processed=None,
        )
        return jsonify({"ok": False, "error": "Race refresh failed"}), 500
