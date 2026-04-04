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

log = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _safe_exc(exc: Exception) -> str:
    """
    Return an error type description safe for inclusion in API responses.
    Only the exception class name is returned — full message and traceback
    details are written to server logs only, avoiding py/stack-trace-exposure.
    """
    return type(exc).__name__


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


# ---------------------------------------------------------------------------
# PHASE 4.7 — OPERATIONS / EXECUTION ROUTES (GET aliases for browser testing)
# ---------------------------------------------------------------------------

@admin_bp.route("/bootstrap-day", methods=["GET", "POST"])
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
        "ODDSPRO_BASE_URL": os.getenv("ODDSPRO_BASE_URL") or None,
        "api_key_present": bool(os.getenv("ODDSPRO_API_KEY", "").strip()),
        "public_mode": (
            bool(os.getenv("ODDSPRO_BASE_URL", "").strip())
            and not bool(os.getenv("ODDSPRO_API_KEY", "").strip())
        ),
        "scheduler_enabled": os.getenv("SCHEDULER_ENABLED", "true").lower() == "true",
        "app_mode": os.getenv("DP_ENV", "LIVE"),
        "oddspro_timeout": int(os.getenv("ODDSPRO_TIMEOUT", "30")),
        "oddspro_country": os.getenv("ODDSPRO_COUNTRY", "au"),
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
    if not config["ODDSPRO_BASE_URL"]:
        failing_stage = "config"

    # ── 3–8. REQUEST → STORAGE (via full_sweep) ──────────────────────────
    result: dict = {}
    if failing_stage != "config":
        try:
            from data_engine import full_sweep
            result = full_sweep(target_date)
        except Exception as exc:
            log.error(f"/api/admin/bootstrap-day full_sweep raised: {exc}")
            result = {"ok": False, "reason": "unexpected_exception", "error": str(exc)}

    ok = bool(result.get("ok", False))

    # ── Pull HTTP diagnostics from the connector singleton ───────────────
    try:
        from data_engine import get_oddspro_connector
        conn = get_oddspro_connector()
        raw_diag = getattr(conn, "_last_fetch_diag", {}) or {}
    except Exception as _ce:
        log.debug(f"bootstrap-day: connector diag unavailable: {_ce}")
        raw_diag = {}

    # 3. REQUEST CONSTRUCTION
    request_diag = {
        "final_url": raw_diag.get("final_url") or (
            f"{config['ODDSPRO_BASE_URL']}/api/external/meetings"
            if config["ODDSPRO_BASE_URL"] else None
        ),
        "params": raw_diag.get("params") or {
            "country": config["oddspro_country"],
            "date": target_date,
        },
        "headers_sent": raw_diag.get("headers_sent") or {},
        "timeout": raw_diag.get("timeout") or config["oddspro_timeout"],
    }

    # 4. RESPONSE
    response_diag = {
        "http_status": (
            raw_diag.get("http_status")
            or result.get("http_status")
        ),
        "content_type": (
            raw_diag.get("content_type")
            or result.get("content_type")
            or ""
        ),
        "response_length": (
            raw_diag.get("response_length")
            or result.get("response_length")
        ),
        "response_preview": (
            raw_diag.get("response_preview")
            or result.get("response_preview")
            or ""
        ),
        "redirected_url": (
            raw_diag.get("redirected_url")
            or result.get("redirected_url")
            or ""
        ),
    }

    # Determine response-layer failure
    reason = result.get("reason") or ""
    if not failing_stage:
        if reason.startswith("oddspro_http_"):
            failing_stage = "response"
        elif reason == "oddspro_request_exception":
            failing_stage = "request"

    # 5. JSON / NORMALIZATION
    parse_stage = result.get("parse_stage")
    if parse_stage:
        normalization_diag = {
            "json_decode_ok": parse_stage not in (
                "root", "oddspro_empty_payload", "oddspro_html_page"
            ),
            "parsed_type": result.get("response_type") or "",
            "top_level_keys": result.get("top_level_keys") or [],
            "normalized_meetings_count": 0,
            "error": (
                result.get("exception_message")
                or result.get("error")
                or parse_stage
            ),
            "parse_stage": parse_stage,
            "sample_payload": result.get("sample_payload"),
        }
        if not failing_stage:
            failing_stage = "normalization"
    else:
        meetings_found_norm = result.get("meetings_found", result.get("meetings", 0))
        normalization_diag = {
            "json_decode_ok": True,
            "parsed_type": "dict" if ok or not reason else (result.get("response_type") or ""),
            "top_level_keys": [],
            "normalized_meetings_count": meetings_found_norm,
            "error": None if ok else (result.get("error") or reason or None),
            "parse_stage": None,
            "sample_payload": None,
        }

    # 6. EXTRACTION
    meetings_found = result.get("meetings_found", result.get("meetings", 0))
    races_found = result.get("races_found", 0)
    runners_found = result.get("runners_found", 0)
    extraction_diag = {
        "meetings_found": meetings_found,
        "races_found": races_found,
        "runners_found": runners_found,
        "parse_stage_failed": result.get("parse_stage") if not ok else None,
    }
    if not failing_stage and ok and meetings_found > 0 and races_found == 0:
        failing_stage = "extraction"

    # 7. VALIDATION / FILTER
    # In full_sweep(), races_stored counts every race processed (including blocked ones).
    # races_blocked counts those that failed the integrity filter.
    # races_passed = races_stored - races_blocked (already computed by full_sweep).
    races_stored = result.get("races_stored", result.get("races", 0))
    races_blocked = result.get("races_blocked", 0)
    races_passed = result.get("races_passed", max(races_stored - races_blocked, 0))
    runners_stored = result.get("runners_stored", 0)
    validation_diag = {
        "races_passed": races_passed,
        "races_blocked": races_blocked,
        # Per-race block reasons are not surfaced at this level (see integrity_filter logs)
        "reasons_blocked": [],
        "empty_board_due_to_validation": bool(races_stored > 0 and races_passed == 0),
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
        # Log full detail server-side; return sanitized message to avoid path exposure
        log.error(f"bootstrap-day: DB connectivity check failed: {dbe}")
        db_errors.append(_safe_exc(dbe))
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
        # runners_found = total extracted runners (all attempted for storage).
        # Fall back to runners_stored when extraction count is unavailable (0).
        "runners_upsert_attempted": runners_found if runners_found > 0 else runners_stored,
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
        from board_builder import get_board_for_today
        board = get_board_for_today()
        board_count = board.get("count", 0)
        bdiag = board.get("diagnostics", {})
        board_diag = {
            "stored_race_count_today": bdiag.get("stored_race_count_today", 0),
            "active_race_count": bdiag.get("active_race_count", 0),
            "blocked_race_count": bdiag.get("blocked_race_count", 0),
            # rejected_count: active races that didn't reach the board and weren't
            # pre-stored as blocked (i.e., failed validation or integrity during build)
            "rejected_count": bdiag.get("rejected_count", 0),
            "board_count": board_count,
            "empty_reason": bdiag.get("empty_reason"),
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
            # Sanitize exception to avoid exposing internal paths (CodeQL py/stack-trace-exposure)
            "empty_reason": f"board_build_exception: {_safe_exc(be)}",
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
            # Sanitize exception to avoid exposing internal paths (CodeQL py/stack-trace-exposure)
            "last_bootstrap_error": _safe_exc(he),
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
def admin_run_cycle():
    """
    Trigger a broad OddsPro rolling refresh cycle.
    GET is provided for easy browser testing; POST is also accepted.
    Calls: data_engine.rolling_refresh()
    """
    try:
        from data_engine import rolling_refresh
        from datetime import date
        from services.health_service import record_broad_refresh

        target_date = date.today().isoformat()
        result = rolling_refresh(target_date)
        ok = result.get("ok", False)

        try:
            record_broad_refresh(
                ok=ok,
                races_refreshed=result.get("races_refreshed", 0),
                error=result.get("error") or result.get("reason") if not ok else None,
            )
        except Exception:
            pass

        # Rebuild board after refresh
        board_count = None
        if ok and result.get("races_refreshed", 0) > 0:
            try:
                from board_builder import get_board_for_today
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
            "races_refreshed": result.get("races_refreshed", 0),
            "formfav_overlays": result.get("formfav_overlays", 0),
            "board_count": board_count,
            "error": result.get("error") or result.get("reason") if not ok else None,
            "timestamp": result.get("timestamp"),
        })
    except Exception as e:
        log.error(f"/api/admin/run-cycle failed: {e}")
        return jsonify({"ok": False, "action": "run-cycle", "error": "Cycle refresh failed"}), 500


@admin_bp.route("/rebuild-board", methods=["GET", "POST"])
def admin_rebuild_board():
    """
    Rebuild the racing board from stored validated races.
    GET is provided for easy browser testing; POST is also accepted.
    Calls: board_builder.get_board_for_today()
    """
    try:
        from board_builder import get_board_for_today
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
            "diagnostics": result.get("diagnostics", {}),
            "error": result.get("error") if not ok else None,
        })
    except Exception as e:
        log.error(f"/api/admin/rebuild-board failed: {e}")
        return jsonify({"ok": False, "action": "rebuild-board", "error": "Board rebuild failed"}), 500


@admin_bp.route("/near-jump-refresh", methods=["GET", "POST"])
def admin_near_jump_refresh():
    """
    Trigger a near-jump OddsPro refresh + FormFav provisional overlay cycle.
    GET is provided for easy browser testing; POST is also accepted.
    Calls: data_engine.near_jump_refresh()
    """
    try:
        from data_engine import near_jump_refresh
        from datetime import date
        from services.health_service import record_near_jump_refresh, record_formfav_overlay

        target_date = date.today().isoformat()
        result = near_jump_refresh(target_date)
        ok = result.get("ok", False)

        try:
            record_near_jump_refresh(
                ok=ok,
                races=result.get("near_jump_races", 0),
                error=result.get("error") or result.get("reason") if not ok else None,
            )
            if result.get("formfav_overlays", 0) > 0:
                record_formfav_overlay(ok=True)
        except Exception:
            pass

        return jsonify({
            "ok": ok,
            "action": "near-jump-refresh",
            "date": target_date,
            "near_jump_races": result.get("near_jump_races", 0),
            "races_refreshed": result.get("races_refreshed", 0),
            "formfav_overlays": result.get("formfav_overlays", 0),
            "error": result.get("error") or result.get("reason") if not ok else None,
            "timestamp": result.get("timestamp"),
        })
    except Exception as e:
        log.error(f"/api/admin/near-jump-refresh failed: {e}")
        return jsonify({"ok": False, "action": "near-jump-refresh", "error": "Near-jump refresh failed"}), 500


@admin_bp.route("/check-results", methods=["GET", "POST"])
def admin_check_results():
    """
    Trigger an OddsPro result sweep for today.
    GET is provided for easy browser testing; POST is also accepted.
    Calls: data_engine.check_results()
    """
    try:
        from data_engine import check_results
        from datetime import date
        from services.health_service import record_result_check

        target_date = date.today().isoformat()
        result = check_results(target_date)
        ok = result.get("ok", False)

        try:
            record_result_check(
                ok=ok,
                confirmations=result.get("results_written", 0),
                error=result.get("error") or result.get("reason") if not ok else None,
            )
        except Exception:
            pass

        return jsonify({
            "ok": ok,
            "action": "check-results",
            "date": target_date,
            "results_written": result.get("results_written", 0),
            "results_skipped": result.get("results_skipped", 0),
            "error": result.get("error") or result.get("reason") if not ok else None,
            "timestamp": result.get("timestamp"),
        })
    except Exception as e:
        log.error(f"/api/admin/check-results failed: {e}")
        return jsonify({"ok": False, "action": "check-results", "error": "Result check failed"}), 500


@admin_bp.route("/engine-status", methods=["GET"])
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
