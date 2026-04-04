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


# ---------------------------------------------------------------------------
# PHASE 3 — INTELLIGENCE LAYER ADMIN HOOKS
# ---------------------------------------------------------------------------

@admin_bp.route("/predict/race", methods=["POST"])
def admin_predict_race():
    """
    Trigger prediction build for a single race.

    POST body: {"race_uid": "<uid>"}
    """
    try:
        data = request.get_json(silent=True) or {}
        race_uid = (data.get("race_uid") or "").strip()
        if not race_uid:
            return jsonify({"ok": False, "error": "race_uid required"}), 400

        from ai.predictor import predict_race
        result = predict_race(race_uid)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/predict/race failed: {e}")
        return jsonify({"ok": False, "error": "Prediction failed"}), 500


@admin_bp.route("/predict/today", methods=["POST"])
def admin_predict_today():
    """Trigger prediction build for all open/upcoming races today."""
    try:
        from ai.predictor import predict_today
        from services.health_service import record_prediction_run
        result = predict_today()
        if result.get("ok"):
            record_prediction_run(count=result.get("total", 0))
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/predict/today failed: {e}")
        return jsonify({"ok": False, "error": "Today prediction run failed"}), 500


@admin_bp.route("/backtest", methods=["POST"])
def admin_backtest():
    """
    Run a backtest for a date or date range.

    POST body:
      {"date": "YYYY-MM-DD"}                            — single day
      {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}  — range
      Optional: "code_filter", "track_filter"

    No-leakage rule: future dates are rejected.
    """
    try:
        from datetime import date as date_type
        data = request.get_json(silent=True) or {}
        today = date_type.today().isoformat()
        date_single = data.get("date")
        date_from = data.get("date_from") or date_single
        date_to = data.get("date_to") or date_single

        if not date_from or not date_to:
            return jsonify({"ok": False, "error": "date or date_from + date_to required"}), 400

        if date_from > today or date_to > today:
            return jsonify({
                "ok": False,
                "error": "Backtest cannot use future dates (no leakage)",
                "today": today,
            }), 400

        from ai.backtest_engine import backtest_date_range
        from services.health_service import record_backtest_run
        result = backtest_date_range(
            date_from=date_from,
            date_to=date_to,
            code_filter=data.get("code_filter"),
            track_filter=data.get("track_filter"),
        )
        if result.get("ok"):
            record_backtest_run(run_id=result.get("run_id", ""))
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/backtest failed: {e}")
        return jsonify({"ok": False, "error": "Backtest failed"}), 500


@admin_bp.route("/predictions/inspect/<race_uid>", methods=["GET"])
def admin_inspect_prediction(race_uid: str):
    """Inspect the stored prediction for a race."""
    try:
        from ai.learning_store import get_stored_prediction
        result = get_stored_prediction(race_uid)
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/predictions/inspect/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve prediction"}), 500


@admin_bp.route("/predictions/performance", methods=["GET"])
def admin_performance_summary():
    """Inspect model/performance summary across stored evaluations."""
    try:
        from ai.learning_store import get_performance_summary
        model_version = request.args.get("model_version")
        result = get_performance_summary(model_version=model_version)
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/predictions/performance failed: {e}")
        return jsonify({"ok": False, "error": "Performance summary unavailable"}), 500


@admin_bp.route("/phase3-migrate", methods=["POST"])
def run_phase3_migrations():
    """Run Phase 3 database migrations to create intelligence-layer tables."""
    try:
        from migrations import run_phase3_migrations as _run_p3
        results = _run_p3()
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        log.error(f"/api/admin/phase3-migrate failed: {e}")
        return jsonify({"ok": False, "error": "Phase 3 migration failed"}), 500


@admin_bp.route("/phase4-migrate", methods=["POST"])
def run_phase4_migrations():
    """
    Run Phase 4 database migrations:
      - Creates sectional_snapshots and race_shape_snapshots tables
      - Adds new columns to feature_snapshots, prediction_snapshots,
        and backtest_run_items
    Safe to re-run.
    """
    try:
        from migrations import run_phase4_migrations as _run_p4
        results = _run_p4()
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        log.error(f"/api/admin/phase4-migrate failed: {e}")
        return jsonify({"ok": False, "error": "Phase 4 migration failed"}), 500


@admin_bp.route("/migrate-all", methods=["POST"])
def run_all_migrations():
    """
    Run all migration phases in order: column migrations → Phase 3 → Phase 4.
    Safe to re-run. Use this for full schema reconciliation.
    """
    try:
        from migrations import run_all_migrations as _run_all
        results = _run_all()
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        log.error(f"/api/admin/migrate-all failed: {e}")
        return jsonify({"ok": False, "error": "Full migration failed"}), 500
