"""
api/admin_routes.py - DemonPulse Admin API Routes
===================================================
Admin-only endpoints for triggering sweeps, managing blocked races,
and running migrations. Protected by auth in production.
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@admin_bp.route("/sweep", methods=["POST"])
def trigger_sweep():
    """Manually trigger a full OddsPro sweep for today."""
    try:
        from data_engine import full_sweep
        from datetime import date
        target_date = (request.get_json(silent=True) or {}).get("date")
        result = full_sweep(target_date or date.today().isoformat())
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/sweep failed: {e}")
        return jsonify({"ok": False, "error": "Sweep failed"}), 500


@admin_bp.route("/refresh", methods=["POST"])
def trigger_refresh():
    """Manually trigger a rolling refresh of active races."""
    try:
        from data_engine import rolling_refresh
        from datetime import date
        target_date = (request.get_json(silent=True) or {}).get("date")
        result = rolling_refresh(target_date or date.today().isoformat())
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/refresh failed: {e}")
        return jsonify({"ok": False, "error": "Refresh failed"}), 500


@admin_bp.route("/results", methods=["POST"])
def trigger_results():
    """Manually trigger a result check sweep."""
    try:
        from data_engine import check_results
        from datetime import date
        target_date = (request.get_json(silent=True) or {}).get("date")
        result = check_results(target_date or date.today().isoformat())
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/results failed: {e}")
        return jsonify({"ok": False, "error": "Result check failed"}), 500


@admin_bp.route("/block", methods=["POST"])
def block_race():
    """Explicitly block a race by race_uid."""
    try:
        data = request.get_json(silent=True) or {}
        race_uid = data.get("race_uid") or ""
        block_code = data.get("block_code") or "ADMIN_BLOCK"

        if not race_uid:
            return jsonify({"ok": False, "error": "race_uid required"}), 400

        from database import mark_race_blocked
        mark_race_blocked(race_uid, block_code)
        return jsonify({"ok": True, "race_uid": race_uid, "block_code": block_code})
    except Exception as e:
        log.error(f"/api/admin/block failed: {e}")
        return jsonify({"ok": False, "error": "Block operation failed"}), 500


@admin_bp.route("/near-jump-refresh", methods=["POST"])
def trigger_near_jump_refresh():
    """Manually trigger a near-jump OddsPro refresh + FormFav overlay cycle."""
    try:
        from data_engine import near_jump_refresh
        from datetime import date
        target_date = (request.get_json(silent=True) or {}).get("date")
        result = near_jump_refresh(target_date or date.today().isoformat())
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/near-jump-refresh failed: {e}")
        return jsonify({"ok": False, "error": "Near-jump refresh failed"}), 500


@admin_bp.route("/migrate", methods=["POST"])
def run_migrations():
    """Run DB schema migrations to add missing columns."""
    try:
        from migrations import run_migrations as _run, ensure_race_uid_index
        results = _run()
        ensure_race_uid_index()
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        log.error(f"/api/admin/migrate failed: {e}")
        return jsonify({"ok": False, "error": "Migration failed"}), 500


@admin_bp.route("/scheduler", methods=["GET"])
def scheduler_status():
    """Get scheduler status."""
    try:
        from scheduler import get_status
        return jsonify({"ok": True, "scheduler": get_status()})
    except Exception as e:
        log.error(f"/api/admin/scheduler failed: {e}")
        return jsonify({"ok": False, "error": "Scheduler status unavailable"}), 500
