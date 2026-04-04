"""
api/health_routes.py - DemonPulse Health & Status Routes
==========================================================
Provides system health endpoints including connector status,
scheduler status, and data source health.
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify

from env import env

log = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__, url_prefix="/api/health")


@health_bp.route("", methods=["GET"])
@health_bp.route("/", methods=["GET"])
def health():
    """Basic liveness probe."""
    return jsonify({"ok": True, "app": "DemonPulse", "mode": env.mode})


@health_bp.route("/connectors", methods=["GET"])
def health_connectors():
    """Check all data connector health."""
    results: dict[str, dict] = {}

    # OddsPro — primary source
    try:
        from connectors.oddspro_connector import OddsProConnector
        results["oddspro"] = OddsProConnector().healthcheck()
    except Exception as e:
        log.error(f"OddsPro healthcheck error: {e}")
        results["oddspro"] = {"ok": False, "error": "OddsPro connector unavailable"}

    # FormFav — provisional overlay
    try:
        from connectors.formfav_connector import FormFavConnector
        results["formfav"] = FormFavConnector().healthcheck()
    except Exception as e:
        log.error(f"FormFav healthcheck error: {e}")
        results["formfav"] = {"ok": False, "error": "FormFav connector unavailable"}

    all_ok = results.get("oddspro", {}).get("ok", False)

    return jsonify({
        "ok": all_ok,
        "connectors": results,
        "primary_source": "oddspro",
        "note": "formfav is provisional overlay only",
    })


@health_bp.route("/scheduler", methods=["GET"])
def health_scheduler():
    """Report scheduler status."""
    try:
        from scheduler import get_status
        return jsonify({"ok": True, "scheduler": get_status()})
    except Exception as e:
        log.error(f"Scheduler status error: {e}")
        return jsonify({"ok": False, "error": "Scheduler status unavailable"}), 500


@health_bp.route("/live", methods=["GET"])
def health_live():
    """
    Live engine health — detailed metrics from the health service.
    Includes last cycle timestamps, blocked/stale counts, and result confirmations.
    """
    try:
        from services.health_service import get_health, is_engine_healthy
        health = get_health()
        return jsonify({
            "ok": is_engine_healthy(),
            "engine": health,
            "primary_source": "oddspro",
            "overlay_source": "formfav (provisional only)",
        })
    except Exception as e:
        log.error(f"/api/health/live failed: {e}")
        return jsonify({"ok": False, "error": "Health service unavailable"}), 500


@health_bp.route("/db", methods=["GET"])
def health_db():
    """Check database connectivity."""
    try:
        from db import get_db, safe_query, T
        row = safe_query(
            lambda: get_db().table(T("system_state")).select("id").limit(1).execute().data,
            None,
        )
        return jsonify({"ok": row is not None, "mode": env.mode})
    except Exception as e:
        log.error(f"DB healthcheck error: {e}")
        return jsonify({"ok": False, "error": "Database unavailable"}), 500


@health_bp.route("/intelligence", methods=["GET"])
def health_intelligence():
    """
    Intelligence layer health — prediction, backtest, and feature-engine observability.

    Reports:
      - last prediction run timestamp + count
      - last backtest run timestamp + run_id
      - last evaluation run timestamp + count
      - last feature build timestamp + count
      - last sectional extraction timestamp + count
      - last race shape build timestamp + count
      - stored prediction snapshot count
      - evaluated prediction count
      - active model version
    """
    try:
        from services.health_service import get_health
        from ai.learning_store import get_prediction_counts

        health = get_health()
        counts = get_prediction_counts()

        return jsonify({
            "ok": True,
            "last_prediction_run_at": health.get("last_prediction_run_at"),
            "last_prediction_run_count": health.get("last_prediction_run_count", 0),
            "last_backtest_run_at": health.get("last_backtest_run_at"),
            "last_backtest_run_id": health.get("last_backtest_run_id"),
            "last_evaluation_run_at": health.get("last_evaluation_run_at"),
            "last_evaluation_run_count": health.get("last_evaluation_run_count", 0),
            "last_feature_build_at": health.get("last_feature_build_at"),
            "last_feature_build_count": health.get("last_feature_build_count", 0),
            "last_sectional_extraction_at": health.get("last_sectional_extraction_at"),
            "last_sectional_extraction_count": health.get("last_sectional_extraction_count", 0),
            "last_race_shape_build_at": health.get("last_race_shape_build_at"),
            "last_race_shape_build_count": health.get("last_race_shape_build_count", 0),
            "prediction_snapshots_stored": counts.get("prediction_snapshots", 0),
            "evaluations_stored": counts.get("learning_evaluations", 0),
            "active_model_version": health.get("active_model_version", "baseline_v1"),
        })
    except Exception as e:
        log.error(f"/api/health/intelligence failed: {e}")
        return jsonify({"ok": False, "error": "Intelligence health unavailable"}), 500
