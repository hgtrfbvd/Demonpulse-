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
