"""
api/formfav_routes.py - DemonPulse FormFav Secondary Source API
================================================================
Exposes FormFav enrichment data that has been fetched and persisted to the
formfav_race_enrichment and formfav_runner_enrichment tables.

FormFav is a secondary, non-authoritative enrichment source.
All primary race/runner data is sourced from OddsPro.

Endpoints:
  GET /api/formfav/enrichment              — all race enrichment for today
  GET /api/formfav/enrichment?date=...     — all race enrichment for a date
  GET /api/formfav/enrichment/<race_uid>   — enrichment for a specific race
  GET /api/formfav/runners/<race_uid>      — runner enrichment for a race
  POST /api/formfav/sync                   — manually trigger FormFav sync
  GET /api/formfav/health                  — FormFav connector health
"""
from __future__ import annotations

import logging
from datetime import date
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

formfav_bp = Blueprint("formfav", __name__, url_prefix="/api/formfav")


@formfav_bp.route("/health", methods=["GET"])
def formfav_health():
    """FormFav connector health check."""
    try:
        from connectors.formfav_connector import FormFavConnector
        ff = FormFavConnector()
        return jsonify(ff.healthcheck())
    except Exception as e:
        log.error(f"/api/formfav/health failed: {e}")
        return jsonify({"ok": False, "error": "Health check failed"}), 500


@formfav_bp.route("/enrichment", methods=["GET"])
def list_enrichment():
    """
    List FormFav race enrichment for a date.
    ?date=YYYY-MM-DD  (defaults to today)
    """
    target_date = request.args.get("date") or date.today().isoformat()
    try:
        from database import get_formfav_race_enrichments_for_date
        items = get_formfav_race_enrichments_for_date(target_date)
        return jsonify({
            "ok": True,
            "items": items,
            "count": len(items),
            "date": target_date,
            "source": "formfav",
        })
    except Exception as e:
        log.error(f"/api/formfav/enrichment failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve FormFav enrichment"}), 500


@formfav_bp.route("/enrichment/<race_uid>", methods=["GET"])
def get_race_enrichment(race_uid: str):
    """
    Get FormFav race enrichment for a specific race.
    Includes runner-level enrichment (form trends, stats, win/place probabilities).
    """
    try:
        from database import get_formfav_race_enrichment, get_formfav_runner_enrichments
        race_enrichment = get_formfav_race_enrichment(race_uid)
        runner_enrichment = get_formfav_runner_enrichments(race_uid)

        if race_enrichment is None:
            return jsonify({
                "ok": False,
                "error": f"No FormFav enrichment found for race {race_uid}",
                "race_uid": race_uid,
            }), 404

        return jsonify({
            "ok": True,
            "race_uid": race_uid,
            "race_enrichment": race_enrichment,
            "runner_enrichment": runner_enrichment,
            "runner_count": len(runner_enrichment),
            "source": "formfav",
        })
    except Exception as e:
        log.error(f"/api/formfav/enrichment/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve FormFav enrichment"}), 500


@formfav_bp.route("/runners/<race_uid>", methods=["GET"])
def get_runner_enrichment(race_uid: str):
    """Get FormFav per-runner enrichment for a race (form trends, stats, probabilities)."""
    try:
        from database import get_formfav_runner_enrichments
        runners = get_formfav_runner_enrichments(race_uid)
        return jsonify({
            "ok": True,
            "race_uid": race_uid,
            "runners": runners,
            "count": len(runners),
            "source": "formfav",
        })
    except Exception as e:
        log.error(f"/api/formfav/runners/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve runner enrichment"}), 500


@formfav_bp.route("/sync", methods=["POST"])
def trigger_sync():
    """
    Manually trigger a FormFav enrichment sync for today (or a given date).
    Body (optional JSON): {"date": "YYYY-MM-DD"}
    """
    try:
        body = request.get_json(silent=True) or {}
        target_date = body.get("date") or request.args.get("date") or date.today().isoformat()
        from data_engine import formfav_sync
        result = formfav_sync(target_date)
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/formfav/sync failed: {e}")
        return jsonify({"ok": False, "error": "FormFav sync failed"}), 500


@formfav_bp.route("/predictions/<race_uid>", methods=["GET"])
def get_predictions(race_uid: str):
    """
    Get FormFav win/place probabilities for a race (from stored enrichment).
    """
    try:
        from database import get_formfav_runner_enrichments
        runners = get_formfav_runner_enrichments(race_uid)
        predictions = [
            {
                "runner_name": r.get("runner_name"),
                "barrier": r.get("barrier"),
                "number": r.get("number"),
                "win_prob": r.get("win_prob"),
                "place_prob": r.get("place_prob"),
            }
            for r in runners
            if r.get("win_prob") is not None or r.get("place_prob") is not None
        ]
        return jsonify({
            "ok": True,
            "race_uid": race_uid,
            "predictions": predictions,
            "count": len(predictions),
            "source": "formfav",
        })
    except Exception as e:
        log.error(f"/api/formfav/predictions/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve predictions"}), 500
