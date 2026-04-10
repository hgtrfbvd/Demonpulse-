"""
api/admin_routes.py - DemonPulse Admin API Routes
===================================================
Admin-only endpoints for triggering sweeps, managing blocked races,
and running migrations. Protected by auth in production.
"""
from __future__ import annotations

import logging
import os
from flask import Blueprint, jsonify, request
from auth import require_role

log = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@admin_bp.route("/sweep", methods=["POST"])
@require_role("admin")
def trigger_sweep():
    """Manually trigger a full pipeline sweep for today."""
    try:
        from pipeline import full_sweep
        from datetime import date
        target_date = (request.get_json(silent=True) or {}).get("date")
        result = full_sweep(target_date or date.today().isoformat())
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/sweep failed: {e}")
        return jsonify({"ok": False, "error": "Sweep failed"}), 500


@admin_bp.route("/refresh", methods=["POST"])
@require_role("admin")
def trigger_refresh():
    """Manually trigger a pipeline sweep (alias for sweep)."""
    try:
        from pipeline import full_sweep
        from datetime import date
        target_date = (request.get_json(silent=True) or {}).get("date")
        result = full_sweep(target_date or date.today().isoformat())
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/admin/refresh failed: {e}")
        return jsonify({"ok": False, "error": "Refresh failed"}), 500


@admin_bp.route("/results", methods=["POST"])
@require_role("admin")
def trigger_results():
    """Manually trigger a race state check (result detection from stored times)."""
    try:
        from datetime import date
        from database import get_races_for_date, update_race_status
        from race_status import bulk_update_race_states
        target_date = (request.get_json(silent=True) or {}).get("date") or date.today().isoformat()
        races = get_races_for_date(target_date)
        changes = bulk_update_race_states(races)
        for race_uid, old_status, new_status in changes:
            update_race_status(race_uid, new_status)
        return jsonify({"ok": True, "date": target_date, "status_changes": len(changes)})
    except Exception as e:
        log.error(f"/api/admin/results failed: {e}")
        return jsonify({"ok": False, "error": "Result check failed"}), 500


@admin_bp.route("/block", methods=["POST"])
@require_role("admin")
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


@admin_bp.route("/migrate", methods=["POST"])
@require_role("admin")
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
@require_role("admin")
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
@require_role("admin")
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
@require_role("admin")
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
@require_role("admin")
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
@require_role("admin")
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
@require_role("admin")
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
@require_role("admin")
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
@require_role("admin")
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
@require_role("admin")
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


# ---------------------------------------------------------------------------
# PHASE 4.7 — OPERATIONS / EXECUTION ROUTES (GET aliases for browser testing)
# ---------------------------------------------------------------------------

@admin_bp.route("/bootstrap-day", methods=["GET", "POST"])
@require_role("admin")
def admin_bootstrap_day():
    """
    Full-pipeline diagnostic bootstrap.
    Instruments and exposes every stage from route trigger to board output.

    Returns a structured diagnostic object:
      ok, failing_stage, route, config, request, response, normalization,
      extraction, validation, storage, board, health
    """
    from datetime import date, datetime, timezone

    # ── 1. ROUTE LAYER ───────────────────────────────────────────────────
    route_info = {
        "route_called": "/api/admin/bootstrap-day",
        "function_called": "admin_bootstrap_day",
        "module_called": "api.admin_routes",
    }

    # ── 2. CONFIG / ENV LAYER ────────────────────────────────────────────
    config = {
        "claude_api_key_present": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
        "scheduler_enabled": os.getenv("SCHEDULER_ENABLED", "true").lower() == "true",
        "app_mode": os.getenv("DP_ENV", "LIVE"),
        "data_source": "claude",
    }

    target_date = date.today().isoformat()
    failing_stage: str | None = None

    # Placeholder dicts for each stage (populated below)
    request_diag: dict = {}
    response_diag: dict = {}
    normalization_diag: dict = {}
    extraction_diag: dict = {}
    validation_diag: dict = {}
    storage_diag: dict = {}
    board_diag: dict = {}
    health_diag: dict = {}

    # ── Early-exit: config failure ───────────────────────────────────────
    if not os.getenv("ANTHROPIC_API_KEY"):
        failing_stage = "config"

    # ── 3–8. REQUEST → STORAGE (via full_sweep) ──────────────────────────
    result: dict = {}
    if failing_stage != "config":
        try:
            from pipeline import full_sweep
            result = full_sweep(target_date)
        except Exception as exc:
            log.error(f"/api/admin/bootstrap-day full_sweep raised: {exc}")
            result = {"ok": False, "reason": "unexpected_exception"}

    ok = bool(result.get("ok", False))

    # ── Pull diagnostics ─────────────────────────────────────────────────
    raw_diag = {}

    # 3. REQUEST CONSTRUCTION
    request_diag = {
        "source": "claude_api",
        "model": "claude-haiku-4-5-20251001",
    }

    # 4. RESPONSE
    response_diag = {
        "ok": ok,
        "races_stored": result.get("races_stored", 0),
    }

    # Determine response-layer failure
    reason = result.get("reason") or ""
    if not failing_stage and reason:
        failing_stage = "extraction"

    # 5. NORMALIZATION
    normalization_diag = {
        "json_decode_ok": True,
        "normalized_meetings_count": result.get("races_stored", 0),
        "error": None if ok else (reason or None),
    }

    # 6. EXTRACTION
    races_found = result.get("races_stored", 0)
    runners_found = result.get("runners_stored", 0)
    extraction_diag = {
        "races_found": races_found,
        "runners_found": runners_found,
    }
    if not failing_stage and ok and races_found == 0:
        failing_stage = "extraction"

    # 7. VALIDATION / FILTER
    races_stored = result.get("races_stored", 0)
    races_blocked = result.get("races_blocked", 0)
    races_passed = max(races_stored - races_blocked, 0)
    runners_stored = result.get("runners_stored", 0)
    validation_diag = {
        "races_passed": races_passed,
        "races_blocked": races_blocked,
        "reasons_blocked": [],
        "empty_board_due_to_validation": False,
    }
    if not failing_stage and ok and races_stored > 0 and races_passed == 0:
        failing_stage = "validation"

    # 8. STORAGE
    db_errors: list[str] = []
    target_database = "supabase"
    try:
        from db import get_db
        get_db()
    except Exception as dbe:
        log.error(f"bootstrap-day: DB connectivity check failed: {dbe}")
        db_errors.append("db_connectivity_failed")
        target_database = "unavailable"
    try:
        from env import env as _env
        table_races = _env.table("today_races")
        table_runners = _env.table("today_runners")
    except Exception:
        table_races = "today_races"
        table_runners = "today_runners"
    storage_diag = {
        "races_upsert_attempted": races_stored,
        "races_stored": races_stored,
        "runners_upsert_attempted": runners_found,
        "runners_stored": runners_stored,
        "db_errors": db_errors,
        "target_database": target_database,
        "table_names": [table_races, table_runners],
    }
    if not failing_stage and ok and races_found > 0 and races_stored == 0:
        failing_stage = "storage"
    if db_errors and not failing_stage:
        failing_stage = "storage"

    # ── 9. BOARD BUILD ────────────────────────────────────────────────────
    board_count = 0
    try:
        from board_service import get_board_for_today
        board = get_board_for_today()
        board_count = board.get("count", 0)
        board_diag = {
            "stored_race_count_today": board_count,
            "active_race_count": board_count,
            "blocked_race_count": 0,
            "rejected_count": 0,
            "board_count": board_count,
            "empty_reason": None,
        }
        if not failing_stage and ok and races_passed > 0 and board_count == 0:
            failing_stage = "board"
    except Exception as be:
        log.warning(f"admin/bootstrap-day: board build failed: {be}")
        board_diag = {
            "stored_race_count_today": 0,
            "active_race_count": 0,
            "blocked_race_count": 0,
            "rejected_count": 0,
            "board_count": 0,
            # Hardcoded semantic code — no exception-derived data (py/stack-trace-exposure)
            "empty_reason": "board_build_exception",
        }
        if not failing_stage and ok:
            failing_stage = "board"

    # ── Record results in health service ──────────────────────────────────
    try:
        from services.health_service import record_bootstrap, record_board_rebuild
        record_bootstrap(ok=ok, result=result)
        record_board_rebuild(count=board_count)
    except Exception:
        pass

    # ── 10. HEALTH LAYER ──────────────────────────────────────────────────
    try:
        from services.health_service import get_health
        h = get_health()
        health_diag = {
            "last_bootstrap_at": h.get("last_bootstrap_at"),
            "last_bootstrap_ok": h.get("last_bootstrap_ok"),
            "last_bootstrap_error": h.get("last_bootstrap_error"),
            "last_bootstrap_count": h.get("last_bootstrap_count", 0),
            "board_count": h.get("board_count", board_count),
        }
    except Exception as he:
        log.error(f"bootstrap-day: health service read failed: {he}")
        health_diag = {
            "last_bootstrap_at": None,
            "last_bootstrap_ok": None,
            # Hardcoded semantic code — no exception-derived data (py/stack-trace-exposure)
            "last_bootstrap_error": "health_service_unavailable",
            "last_bootstrap_count": 0,
            "board_count": board_count,
        }

    # ── Determine failing_stage for uncaught failures ─────────────────────
    if not failing_stage and not ok:
        failing_stage = "unknown"

    return jsonify({
        "ok": ok,
        "failing_stage": failing_stage,
        **route_info,
        "date": target_date,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "request": request_diag,
        "response": response_diag,
        "normalization": normalization_diag,
        "extraction": extraction_diag,
        "validation": validation_diag,
        "storage": storage_diag,
        "board": board_diag,
        "health": health_diag,
    })


@admin_bp.route("/run-cycle", methods=["GET", "POST"])
@require_role("admin")
def admin_run_cycle():
    """
    Trigger a pipeline sweep (fetch all venues for today).
    GET is provided for easy browser testing; POST is also accepted.
    """
    try:
        from pipeline import full_sweep
        from datetime import date
        from services.health_service import record_broad_refresh

        target_date = date.today().isoformat()
        result = full_sweep(target_date)
        ok = result.get("ok", False)

        try:
            record_broad_refresh(
                ok=ok,
                races_refreshed=result.get("races_stored", 0),
                error=result.get("error") or result.get("reason") if not ok else None,
            )
        except Exception:
            pass

        board_count = None
        if ok:
            try:
                from board_service import get_board_for_today
                board = get_board_for_today()
                board_count = board.get("count", 0)
                from services.health_service import record_board_rebuild
                record_board_rebuild(count=board_count)
            except Exception as be:
                log.warning(f"admin/run-cycle: board rebuild failed: {be}")

        return jsonify({
            "ok": ok,
            "action": "run-cycle",
            "date": target_date,
            "races_stored": result.get("races_stored", 0),
            "board_count": board_count,
            "error": result.get("error") or result.get("reason") if not ok else None,
        })
    except Exception as e:
        log.error(f"/api/admin/run-cycle failed: {e}")
        return jsonify({"ok": False, "action": "run-cycle", "error": "Cycle refresh failed"}), 500


@admin_bp.route("/rebuild-board", methods=["GET", "POST"])
@require_role("admin")
def admin_rebuild_board():
    """
    Rebuild the racing board from stored validated races.
    GET is provided for easy browser testing; POST is also accepted.
    """
    try:
        from board_service import get_board_for_today
        from services.health_service import record_board_rebuild

        result = get_board_for_today()
        ok = result.get("ok", False)
        count = result.get("count", 0)

        try:
            record_board_rebuild(count=count)
        except Exception:
            pass

        return jsonify({
            "ok": ok,
            "action": "rebuild-board",
            "board_count": count,
            "date": result.get("date"),
            "error": result.get("error") if not ok else None,
        })
    except Exception as e:
        log.error(f"/api/admin/rebuild-board failed: {e}")
        return jsonify({"ok": False, "action": "rebuild-board", "error": "Board rebuild failed"}), 500


@admin_bp.route("/near-jump-refresh", methods=["GET", "POST"])
@require_role("admin")
def admin_near_jump_refresh():
    """
    Trigger a pipeline venue sweep for near-jump races.
    GET is provided for easy browser testing; POST is also accepted.
    """
    try:
        from pipeline import full_sweep
        from datetime import date
        from services.health_service import record_near_jump_refresh

        target_date = date.today().isoformat()
        result = full_sweep(target_date)
        ok = result.get("ok", False)

        try:
            record_near_jump_refresh(
                ok=ok,
                races=result.get("races_stored", 0),
                error=result.get("error") or result.get("reason") if not ok else None,
            )
        except Exception:
            pass

        return jsonify({
            "ok": ok,
            "action": "near-jump-refresh",
            "date": target_date,
            "races_stored": result.get("races_stored", 0),
            "error": result.get("error") or result.get("reason") if not ok else None,
        })
    except Exception as e:
        log.error(f"/api/admin/near-jump-refresh failed: {e}")
        return jsonify({"ok": False, "action": "near-jump-refresh", "error": "Near-jump refresh failed"}), 500


@admin_bp.route("/check-results", methods=["GET", "POST"])
@require_role("admin")
def admin_check_results():
    """
    Trigger a race state check (detect jumped races from stored times).
    GET is provided for easy browser testing; POST is also accepted.
    """
    try:
        from datetime import date
        from database import get_races_for_date, update_race_status
        from race_status import bulk_update_race_states
        from services.health_service import record_result_check

        target_date = date.today().isoformat()
        races = get_races_for_date(target_date)
        changes = bulk_update_race_states(races)
        for race_uid, old_status, new_status in changes:
            update_race_status(race_uid, new_status)
        ok = True

        try:
            record_result_check(ok=ok, confirmations=len(changes))
        except Exception:
            pass

        return jsonify({
            "ok": ok,
            "action": "check-results",
            "date": target_date,
            "status_changes": len(changes),
        })
    except Exception as e:
        log.error(f"/api/admin/check-results failed: {e}")
        return jsonify({"ok": False, "action": "check-results", "error": "Result check failed"}), 500


@admin_bp.route("/engine-status", methods=["GET"])
@require_role("admin")
def admin_engine_status():
    """
    Comprehensive live engine status including health metrics, scheduler state,
    connector configuration, and board/race counts.
    Calls: services.health_service.get_health(), scheduler.get_status()
    """
    try:
        from datetime import date
        from env import env

        health: dict = {}
        scheduler_status: dict = {}

        try:
            from services.health_service import get_health, is_engine_healthy
            health = get_health()
            engine_ok = is_engine_healthy()
        except Exception as he:
            log.error(f"engine-status: health_service unavailable: {he}")
            engine_ok = False
            health = {}

        try:
            from scheduler import get_status
            scheduler_status = get_status()
        except Exception as se:
            log.error(f"engine-status: scheduler unavailable: {se}")
            scheduler_status = {}

        # Board count from health state or live query
        board_count = health.get("board_count", 0)
        stored_today = health.get("stored_race_count_today", 0)
        blocked_count = health.get("blocked_race_count", 0)
        stale_count = health.get("stale_race_count", 0)

        # Try to get live counts from DB
        try:
            today = date.today().isoformat()
            from database import get_races_for_date, get_blocked_races
            all_races = get_races_for_date(today)
            blocked_races = get_blocked_races(today)
            stored_today = len(all_races)
            blocked_count = len(blocked_races)
        except Exception:
            pass

        return jsonify({
            "ok": engine_ok,
            "action": "engine-status",
            "app_mode": env.mode,
            "scheduler_enabled": os.getenv("SCHEDULER_ENABLED", "true") == "true",
            "scheduler": {
                "running": scheduler_status.get("running", False),
                "thread_alive": scheduler_status.get("thread_alive", False),
                "started_at": scheduler_status.get("started_at"),
                "last_loop_at": scheduler_status.get("last_loop_at"),
                "last_full_sweep_at": scheduler_status.get("last_full_sweep_at"),
                "last_error": scheduler_status.get("last_error"),
            },
            "primary_source": "oddspro",
            "overlay_source": "formfav (provisional only)",
            # Bootstrap
            "last_bootstrap_at": health.get("last_bootstrap_at"),
            "last_bootstrap_ok": health.get("last_bootstrap_ok"),
            "last_bootstrap_error": health.get("last_bootstrap_error"),
            "last_bootstrap_count": health.get("last_bootstrap_count", 0),
            # Broad refresh
            "last_broad_refresh_at": health.get("last_broad_refresh_at"),
            "last_broad_refresh_ok": health.get("last_broad_refresh_ok"),
            "last_broad_refresh_races": health.get("last_broad_refresh_races", 0),
            "last_broad_refresh_error": health.get("last_broad_refresh_error"),
            # Near-jump refresh
            "last_near_jump_refresh_at": health.get("last_near_jump_refresh_at"),
            "last_near_jump_refresh_ok": health.get("last_near_jump_refresh_ok"),
            "last_near_jump_refresh_races": health.get("last_near_jump_refresh_races", 0),
            "last_near_jump_refresh_error": health.get("last_near_jump_refresh_error"),
            # Result check
            "last_result_check_at": health.get("last_result_check_at"),
            "last_result_check_ok": health.get("last_result_check_ok"),
            "last_result_check_error": health.get("last_result_check_error"),
            "result_confirmation_count": health.get("result_confirmation_count", 0),
            # Counts
            "board_count": board_count,
            "stored_race_count_today": stored_today,
            "blocked_race_count": blocked_count,
            "stale_race_count": stale_count,
        })
    except Exception as e:
        log.error(f"/api/admin/engine-status failed: {e}")
        return jsonify({"ok": False, "action": "engine-status", "error": "Engine status unavailable"}), 500


@admin_bp.route("/routes", methods=["GET"])
@require_role("admin")
def admin_routes_list():
    """
    List important operational endpoints for fast browser testing and debugging.
    """
    return jsonify({
        "ok": True,
        "routes": [
            {"method": "GET", "path": "/api/health", "description": "Basic liveness probe"},
            {"method": "GET", "path": "/api/health/live", "description": "Live engine health metrics"},
            {"method": "GET", "path": "/api/health/connectors", "description": "Connector health check"},
            {"method": "GET", "path": "/api/health/scheduler", "description": "Scheduler status"},
            {"method": "GET", "path": "/api/health/intelligence", "description": "Intelligence layer health"},
            {"method": "GET", "path": "/api/board", "description": "Live racing board"},
            {"method": "GET", "path": "/api/board/blocked", "description": "Blocked races today"},
            {"method": "GET", "path": "/api/board/ntj", "description": "Next-to-jump races"},
            {"method": "GET", "path": "/api/admin/bootstrap-day", "description": "Trigger full OddsPro daily bootstrap"},
            {"method": "GET", "path": "/api/admin/run-cycle", "description": "Trigger broad OddsPro refresh"},
            {"method": "GET", "path": "/api/admin/rebuild-board", "description": "Rebuild board from stored races"},
            {"method": "GET", "path": "/api/admin/near-jump-refresh", "description": "Trigger near-jump refresh + FormFav overlay"},
            {"method": "GET", "path": "/api/admin/check-results", "description": "Trigger OddsPro result sweep"},
            {"method": "GET", "path": "/api/admin/engine-status", "description": "Comprehensive engine status"},
            {"method": "GET", "path": "/api/admin/routes", "description": "This route list"},
            {"method": "GET", "path": "/api/admin/scheduler", "description": "Scheduler status detail"},
            {"method": "GET", "path": "/api/predictions/today", "description": "Today's predictions"},
            {"method": "GET", "path": "/api/predictions/model-performance", "description": "Model performance metrics"},
            {"method": "POST", "path": "/api/admin/sweep", "description": "POST: full OddsPro sweep (legacy)"},
            {"method": "POST", "path": "/api/admin/refresh", "description": "POST: rolling refresh (legacy)"},
            {"method": "POST", "path": "/api/admin/results", "description": "POST: result check (legacy)"},
            {"method": "POST", "path": "/api/admin/migrate-all", "description": "Run all DB migrations"},
        ],
    })


# ---------------------------------------------------------------
# USER MANAGEMENT ROUTES
# ---------------------------------------------------------------

import logging as _log
_admin_log = _log.getLogger(__name__)


@admin_bp.route("/users", methods=["GET"])
@require_role("admin")
def list_users():
    from users import get_all_users
    try:
        return jsonify({"ok": True, "users": get_all_users()})
    except Exception as e:
        _admin_log.error(f"/api/admin/users GET failed: {e}")
        return jsonify({"ok": False, "error": "Failed to list users"}), 500


@admin_bp.route("/users/create", methods=["POST"])
@require_role("admin")
def create_user_route():
    from users import create_user_full
    from auth import get_current_user
    data = request.get_json(silent=True) or {}
    actor = get_current_user()
    try:
        user = create_user_full(
            username=data.get("username", "").strip(),
            password=data.get("password", ""),
            role=data.get("role", "operator"),
            starting_bankroll=float(data.get("starting_bankroll", 1000)),
            creator_username=actor.get("username", "admin") if actor else "admin",
        )
        return jsonify({"ok": True, "user": user})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        _admin_log.error(f"/api/admin/users/create failed: {e}")
        return jsonify({"ok": False, "error": "Failed to create user"}), 500


@admin_bp.route("/users/<user_id>", methods=["PATCH"])
@require_role("admin")
def update_user_route(user_id):
    from users import update_user_profile
    from auth import get_current_user
    data = request.get_json(silent=True) or {}
    actor = get_current_user()
    try:
        update_user_profile(user_id, actor.get("username", "admin") if actor else "admin", **data)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        _admin_log.error(f"/api/admin/users/{user_id} PATCH failed: {e}")
        return jsonify({"ok": False, "error": "Failed to update user"}), 500


@admin_bp.route("/users/<user_id>", methods=["DELETE"])
@require_role("admin")
def delete_user_route(user_id):
    from users import delete_user
    from auth import get_current_user
    actor = get_current_user()
    try:
        delete_user(user_id, actor.get("username", "admin") if actor else "admin")
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        _admin_log.error(f"/api/admin/users/{user_id} DELETE failed: {e}")
        return jsonify({"ok": False, "error": "Failed to delete user"}), 500


@admin_bp.route("/users/<user_id>/reset-password", methods=["POST"])
@require_role("admin")
def reset_user_password(user_id):
    from users import reset_password
    from auth import get_current_user
    data = request.get_json(silent=True) or {}
    actor = get_current_user()
    try:
        reset_password(user_id, data.get("new_password", ""), actor.get("username", "admin") if actor else "admin")
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        _admin_log.error(f"/api/admin/users/{user_id}/reset-password failed: {e}")
        return jsonify({"ok": False, "error": "Failed to reset password"}), 500



