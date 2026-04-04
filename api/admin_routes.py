"""
api/admin_routes.py - Admin/trigger endpoints.
"""

import logging
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
admin_bp = Blueprint("admin", __name__)

_INTERNAL_ERROR = {"ok": False, "error": "Internal server error"}


def _safe_result(result: dict) -> dict:
    """Strip raw exception strings from data_engine result dicts before returning to caller."""
    safe = {k: v for k, v in result.items() if k != "errors"}
    error_count = len(result.get("errors", []))
    if error_count:
        safe["error_count"] = error_count
    return safe


@admin_bp.route("/api/admin/bootstrap", methods=["POST"])
def api_bootstrap():
    """Trigger full_sweep() for today."""
    from data_engine import full_sweep
    try:
        result = full_sweep()
        return jsonify(_safe_result(result))
    except Exception as e:
        log.exception(f"/api/admin/bootstrap failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@admin_bp.route("/api/admin/sweep", methods=["POST"])
def api_sweep():
    """Trigger full_sweep()."""
    from data_engine import full_sweep
    data = request.get_json(silent=True) or {}
    date_str = data.get("date")
    try:
        result = full_sweep(date_str)
        return jsonify(_safe_result(result))
    except Exception as e:
        log.exception(f"/api/admin/sweep failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@admin_bp.route("/api/admin/refresh/meeting/<meeting_id>", methods=["POST"])
def api_refresh_meeting(meeting_id):
    """Trigger refresh_meeting(meeting_id)."""
    from data_engine import refresh_meeting
    try:
        result = refresh_meeting(meeting_id)
        return jsonify(_safe_result(result))
    except Exception as e:
        log.exception(f"/api/admin/refresh/meeting/{meeting_id} failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@admin_bp.route("/api/admin/refresh/race/<race_id>", methods=["POST"])
def api_refresh_race(race_id):
    """Trigger refresh_race(race_id)."""
    from data_engine import refresh_race
    try:
        result = refresh_race(race_id)
        return jsonify(_safe_result(result))
    except Exception as e:
        log.exception(f"/api/admin/refresh/race/{race_id} failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@admin_bp.route("/api/admin/results", methods=["POST"])
def api_recheck_results():
    """Trigger check_results()."""
    from data_engine import check_results
    data = request.get_json(silent=True) or {}
    date_str = data.get("date")
    try:
        result = check_results(date_str)
        return jsonify(_safe_result(result))
    except Exception as e:
        log.exception(f"/api/admin/results failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@admin_bp.route("/api/admin/board/rebuild", methods=["POST"])
def api_rebuild_board():
    """Trigger rebuild_board()."""
    from data_engine import rebuild_board
    data = request.get_json(silent=True) or {}
    date_str = data.get("date")
    try:
        result = rebuild_board(date_str)
        return jsonify(_safe_result(result))
    except Exception as e:
        log.exception(f"/api/admin/board/rebuild failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500


@admin_bp.route("/api/admin/formfav/overlay", methods=["POST"])
def api_formfav_overlay():
    """Trigger run_formfav_overlay() (secondary only)."""
    from data_engine import run_formfav_overlay
    data = request.get_json(silent=True) or {}
    date_str = data.get("date")
    try:
        result = run_formfav_overlay(date_str)
        return jsonify(_safe_result(result))
    except Exception as e:
        log.exception(f"/api/admin/formfav/overlay failed: {e}")
        return jsonify(_INTERNAL_ERROR), 500
