"""
api/prediction_routes.py - DemonPulse Prediction API Routes
============================================================
Minimal API endpoints for interacting with the Phase 3 intelligence layer.

Endpoints:
  POST /api/predictions/race/<race_uid>     — trigger prediction for one race
  POST /api/predictions/today              — trigger predictions for today's board
  GET  /api/predictions/race/<race_uid>    — inspect stored prediction for a race
  GET  /api/predictions/performance        — model performance summary
  POST /api/predictions/backtest           — run a backtest for date or date range
  GET  /api/predictions/backtest/<run_id>  — inspect a stored backtest run

Architecture rules enforced here:
  - Predictions never overwrite official race or result data
  - Backtest cannot be run for future dates
  - FormFav data is never used as evaluation source
"""
from __future__ import annotations

import logging
from datetime import date
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

prediction_bp = Blueprint("predictions", __name__, url_prefix="/api/predictions")


# ---------------------------------------------------------------------------
# PREDICTION TRIGGERS
# ---------------------------------------------------------------------------

@prediction_bp.route("/race/<race_uid>", methods=["POST"])
def predict_race(race_uid: str):
    """Generate and store a prediction for a single race by race_uid."""
    try:
        from ai.predictor import predict_race as _predict
        result = _predict(race_uid)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.error(f"POST /api/predictions/race/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Prediction failed"}), 500


@prediction_bp.route("/today", methods=["POST"])
def predict_today():
    """Generate and store predictions for all open/upcoming races today."""
    try:
        from ai.predictor import predict_today as _predict_today
        result = _predict_today()
        return jsonify(result)
    except Exception as e:
        log.error(f"POST /api/predictions/today failed: {e}")
        return jsonify({"ok": False, "error": "Today prediction run failed"}), 500


# ---------------------------------------------------------------------------
# PREDICTION INSPECTION
# ---------------------------------------------------------------------------

@prediction_bp.route("/race/<race_uid>", methods=["GET"])
def get_prediction(race_uid: str):
    """Retrieve the most recent stored prediction for a race."""
    try:
        from ai.learning_store import get_stored_prediction
        result = get_stored_prediction(race_uid)
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        log.error(f"GET /api/predictions/race/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve prediction"}), 500


@prediction_bp.route("/performance", methods=["GET"])
def performance_summary():
    """
    Return model performance summary from stored evaluations.

    Query params:
      model_version: filter to a specific model version (optional)
      limit: max evaluations to include (default 200)
    """
    try:
        from ai.learning_store import get_performance_summary
        model_version = request.args.get("model_version")
        limit = int(request.args.get("limit", 200))
        result = get_performance_summary(model_version=model_version, limit=limit)
        return jsonify(result)
    except Exception as e:
        log.error(f"GET /api/predictions/performance failed: {e}")
        return jsonify({"ok": False, "error": "Performance summary unavailable"}), 500


# ---------------------------------------------------------------------------
# BACKTEST
# ---------------------------------------------------------------------------

@prediction_bp.route("/backtest", methods=["POST"])
def run_backtest():
    """
    Run a backtest for a date or date range.

    POST body (JSON):
      date:         ISO date string — backtest a single day
      date_from:    ISO date string — start of range (requires date_to)
      date_to:      ISO date string — end of range
      code_filter:  optional race code filter (e.g. 'GREYHOUND')
      track_filter: optional track name filter (substring match)

    Backtesting rules enforced:
      - Cannot backtest future dates (no leakage)
      - Results are evaluation-only, never used as features
    """
    try:
        data = request.get_json(silent=True) or {}
        today = date.today().isoformat()

        date_single = data.get("date")
        date_from = data.get("date_from") or date_single
        date_to = data.get("date_to") or date_single
        code_filter = data.get("code_filter")
        track_filter = data.get("track_filter")

        if not date_from or not date_to:
            return jsonify({
                "ok": False,
                "error": "date or date_from + date_to required",
            }), 400

        # Guard: no future leakage
        if date_from > today or date_to > today:
            return jsonify({
                "ok": False,
                "error": "Backtest cannot use future dates (no leakage rule)",
                "today": today,
            }), 400

        from ai.backtest_engine import backtest_date_range
        from services.health_service import record_backtest_run

        result = backtest_date_range(
            date_from=date_from,
            date_to=date_to,
            code_filter=code_filter,
            track_filter=track_filter,
            model_version=data.get("model_version") or "baseline_v1",
            compare_models=bool(data.get("compare_models", False)),
        )
        if result.get("ok"):
            record_backtest_run(run_id=result.get("run_id", ""))

        return jsonify(result)

    except Exception as e:
        log.error(f"POST /api/predictions/backtest failed: {e}")
        return jsonify({"ok": False, "error": "Backtest failed"}), 500


@prediction_bp.route("/backtest/<run_id>", methods=["GET"])
def get_backtest_run(run_id: str):
    """Retrieve a stored backtest run summary by run_id."""
    try:
        from ai.backtest_engine import get_backtest_run as _get_run
        result = _get_run(run_id)
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        log.error(f"GET /api/predictions/backtest/{run_id} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve backtest run"}), 500


# ---------------------------------------------------------------------------
# MODEL COMPARISON (Phase 4.6)
# ---------------------------------------------------------------------------

@prediction_bp.route("/compare", methods=["POST"])
def compare_predictions():
    """
    Compare DemonPulse model predictions against FormFav predictions for a race.

    POST body (JSON):
      race_uid        : race identifier (required)
      your_preds      : list of DemonPulse runner prediction dicts
                        (each with runner_name and predicted_rank)
      formfav_preds   : list of FormFav runner prediction dicts
                        (each with runner_name and predicted_rank)

    Returns:
      - disagreement score (0-1)
      - rank_diff_top_pick
      - flagged (True if high disagreement)
      - both model prediction lists
      - source_note confirming FormFav is enrichment only

    Architecture rules enforced:
      - FormFav predictions are reference only
      - DemonPulse predictions are authoritative (OddsPro-sourced)
      - Disagreement is a signal, not a correction
    """
    try:
        data = request.get_json(silent=True) or {}
        race_uid = data.get("race_uid") or ""
        your_preds = data.get("your_preds") or []
        formfav_preds = data.get("formfav_preds") or []

        if not race_uid:
            return jsonify({"ok": False, "error": "race_uid required"}), 400
        if not your_preds:
            return jsonify({"ok": False, "error": "your_preds required"}), 400

        from ai.disagreement_engine import compare_predictions as _compare
        from services.health_service import record_disagreement

        result = _compare(race_uid, your_preds, formfav_preds)
        disagreement = result.get("disagreement", {})

        # Record disagreement for health metrics
        record_disagreement(flagged=disagreement.get("flagged", False))

        return jsonify({"ok": True, **result})

    except Exception as e:
        log.error(f"POST /api/predictions/compare failed: {e}")
        return jsonify({"ok": False, "error": "Prediction comparison failed"}), 500


@prediction_bp.route("/model-performance", methods=["GET"])
def model_performance():
    """
    Aggregated performance stats per model version.

    Query params:
      model_version: filter to a specific model version (optional)
      limit: max evaluations to include (default 500)

    Returns performance stats per model version including:
      - winner_hit_rate
      - top3_hit_rate
      - avg_winner_odds
      - enrichment_usage_rate (fraction of predictions with enrichment)
      - total_evaluated
    """
    try:
        from ai.learning_store import get_performance_by_model
        limit = int(request.args.get("limit", 500))
        result = get_performance_by_model(limit=limit)
        return jsonify(result)
    except Exception as e:
        log.error(f"GET /api/predictions/model-performance failed: {e}")
        return jsonify({"ok": False, "error": "Model performance unavailable"}), 500
