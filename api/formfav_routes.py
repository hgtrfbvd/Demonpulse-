"""
api/formfav_routes.py - DemonPulse FormFav Enrichment API Routes
=================================================================
Provides read-only access to the persisted FormFav enrichment data stored
in formfav_race_enrichment and formfav_runner_enrichment tables.

FormFav is a SECONDARY enrichment source — these routes expose the stored
enrichment data, not OddsPro-authoritative race records.
"""
from __future__ import annotations

import logging
from datetime import date
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

formfav_bp = Blueprint("formfav", __name__, url_prefix="/api/formfav")


@formfav_bp.route("/races", methods=["GET"])
def list_formfav_races():
    """
    List stored FormFav race enrichment records for a given date.
    Query params:
      - date: ISO date string (default: today)
    """
    target_date = request.args.get("date") or date.today().isoformat()
    try:
        from database import get_formfav_enrichments_for_date
        rows = get_formfav_enrichments_for_date(target_date)
        return jsonify({"ok": True, "items": rows, "count": len(rows), "date": target_date})
    except Exception as e:
        log.error(f"/api/formfav/races failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve FormFav race enrichments"}), 500


@formfav_bp.route("/races/<race_uid>", methods=["GET"])
def get_formfav_race(race_uid: str):
    """Get stored FormFav race enrichment for a specific race_uid."""
    try:
        from database import get_formfav_race_enrichment
        row = get_formfav_race_enrichment(race_uid)
        if not row:
            return jsonify({"ok": False, "error": "FormFav enrichment not found for this race"}), 404
        return jsonify({"ok": True, "race": row})
    except Exception as e:
        log.error(f"/api/formfav/races/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve FormFav race enrichment"}), 500


@formfav_bp.route("/races/<race_uid>/runners", methods=["GET"])
def get_formfav_runners(race_uid: str):
    """Get all stored FormFav runner enrichments for a specific race_uid."""
    try:
        from database import get_formfav_runner_enrichments
        rows = get_formfav_runner_enrichments(race_uid)
        return jsonify({"ok": True, "runners": rows, "count": len(rows), "race_uid": race_uid})
    except Exception as e:
        log.error(f"/api/formfav/races/{race_uid}/runners failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve FormFav runner enrichments"}), 500


@formfav_bp.route("/sync", methods=["POST"])
def trigger_formfav_sync():
    """
    Manually trigger a FormFav enrichment sync for a given date.
    Body (optional JSON): {"date": "YYYY-MM-DD"}
    """
    try:
        body = request.get_json(silent=True) or {}
        target_date = body.get("date") or date.today().isoformat()

        from data_engine import formfav_sync
        result = formfav_sync(target_date)
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/formfav/sync failed: {e}")
        return jsonify({"ok": False, "error": "FormFav sync failed"}), 500


@formfav_bp.route("/status", methods=["GET"])
def formfav_status():
    """Return FormFav connector status and enrichment counts for today."""
    try:
        from connectors.formfav_connector import FormFavConnector
        from database import get_formfav_enrichments_for_date

        connector = FormFavConnector()
        today = date.today().isoformat()
        enrichments = get_formfav_enrichments_for_date(today)

        return jsonify({
            "ok": True,
            "connector": connector.healthcheck(),
            "today": today,
            "enriched_races_today": len(enrichments),
            "source": "formfav",
        })
    except Exception as e:
        log.error(f"/api/formfav/status failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve FormFav status"}), 500
