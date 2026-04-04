"""
api/health_routes.py - Health check endpoints.
"""

import logging
from datetime import date as _date
from flask import Blueprint, jsonify

log = logging.getLogger(__name__)
health_bp = Blueprint("health", __name__)


@health_bp.route("/api/health")
def api_health():
    """Returns app status + connector health + scheduler + board summary."""
    from env import env

    oddspro_enabled = False
    try:
        from connectors.oddspro_connector import OddsProConnector
        oddspro_enabled = OddsProConnector().is_enabled()
    except Exception as e:
        log.warning(f"OddsPro connector check failed: {e}")

    formfav_enabled = False
    try:
        from connectors.formfav_connector import FormFavConnector
        formfav_enabled = FormFavConnector().is_enabled()
    except Exception as e:
        log.warning(f"FormFav connector check failed: {e}")

    engine_state = {}
    try:
        from data_engine import get_engine_state
        engine_state = get_engine_state()
    except Exception as e:
        log.warning(f"get_engine_state failed: {e}")

    scheduler_status = {}
    try:
        from scheduler import get_status
        scheduler_status = get_status()
    except Exception as e:
        log.warning(f"scheduler get_status failed: {e}")

    board_race_count = 0
    blocked_count = 0
    stale_count = 0
    try:
        from board_builder import build_board
        board = build_board()
        board_race_count = len(board.get("board", []))
        blocked_count = board.get("blocked_count", 0)
        stale_count = board.get("stale_count", 0)
    except Exception as e:
        log.warning(f"build_board failed in health check: {e}")

    return jsonify({
        "ok": True,
        "app": "DemonPulse",
        "mode": env.mode,
        "oddspro": {"enabled": oddspro_enabled},
        "formfav": {"enabled": formfav_enabled},
        "last_full_sweep_at": engine_state.get("last_full_sweep_at"),
        "last_refresh_at": engine_state.get("last_refresh_at"),
        "last_result_check_at": engine_state.get("last_result_check_at"),
        "last_formfav_overlay_at": engine_state.get("last_formfav_overlay_at"),
        "board_race_count": board_race_count,
        "stale_count": stale_count,
        "blocked_count": blocked_count,
        "scheduler": scheduler_status,
    })


@health_bp.route("/api/health/detailed")
def api_health_detailed():
    """Full detail including DB stats."""
    from env import env

    oddspro_status = {"enabled": False}
    try:
        from connectors.oddspro_connector import OddsProConnector
        conn = OddsProConnector()
        hc = conn.healthcheck()
        # Remove any raw error strings that may contain internals
        oddspro_status = {
            "enabled": hc.get("enabled", False),
            "ok": hc.get("ok", False),
        }
        if "status_code" in hc:
            oddspro_status["status_code"] = hc["status_code"]
    except Exception as e:
        log.warning(f"OddsPro healthcheck failed: {e}")
        oddspro_status = {"enabled": False}

    formfav_enabled = False
    try:
        from connectors.formfav_connector import FormFavConnector
        formfav_enabled = FormFavConnector().is_enabled()
    except Exception as e:
        log.warning(f"FormFav connector check failed: {e}")

    engine_state = {}
    try:
        from data_engine import get_engine_state
        engine_state = get_engine_state()
    except Exception as e:
        log.warning(f"get_engine_state failed: {e}")

    scheduler_status = {}
    try:
        from scheduler import get_status
        scheduler_status = get_status()
    except Exception as e:
        log.warning(f"scheduler get_status failed: {e}")

    db_stats = {}
    try:
        import database
        today = _date.today().isoformat()
        meetings = database.get_all_meetings(today)
        races = database.get_active_races(today)
        blocked = database.get_blocked_races(today)
        db_stats = {
            "meetings_today": len(meetings),
            "active_races": len(races),
            "blocked_races": len(blocked),
        }
    except Exception as e:
        log.warning(f"DB stats failed: {e}")
        db_stats = {"error": "unavailable"}

    return jsonify({
        "ok": True,
        "app": "DemonPulse",
        "mode": env.mode,
        "oddspro": oddspro_status,
        "formfav": {"enabled": formfav_enabled},
        "engine_state": engine_state,
        "scheduler": scheduler_status,
        "db": db_stats,
    })
