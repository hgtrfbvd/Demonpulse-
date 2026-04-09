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


@prediction_bp.route("/today", methods=["GET"])
def get_today_predictions():
    """Return stored predictions for today with result outcomes."""
    try:
        from db import get_db, safe_query, T
        today = date.today().isoformat()

        # Prediction snapshots for today
        snaps = safe_query(
            lambda: get_db()
            .table(T("prediction_snapshots"))
            .select(
                "race_uid,race_date,track,race_num,code,"
                "signal,decision,confidence,ev,model_version,"
                "top_runner,created_at"
            )
            .eq("race_date", today)
            .order("created_at", desc=True)
            .limit(60)
            .execute()
            .data,
            []
        ) or []

        # Today's results for WIN/LOSS outcome
        results = safe_query(
            lambda: get_db()
            .table(T("results_log"))
            .select("race_uid,winner,win_price")
            .eq("date", today)
            .execute()
            .data,
            []
        ) or []
        result_map = {r["race_uid"]: r for r in results if r.get("race_uid")}

        predictions = []
        for snap in snaps:
            race_uid = snap.get("race_uid") or ""
            res_row  = result_map.get(race_uid)
            signal   = snap.get("signal") or "—"
            decision = snap.get("decision") or "—"

            if res_row:
                top = snap.get("top_runner") or ""
                winner = res_row.get("winner") or ""
                outcome = "WIN" if (top and winner and
                    top.strip().upper() == winner.strip().upper()) else "LOSS"
            else:
                outcome = "PENDING"

            track_display = (snap.get("track") or "").replace("-", " ").title()
            predictions.append({
                "race_uid":   race_uid,
                "track":      track_display,
                "race_num":   snap.get("race_num"),
                "signal":     signal,
                "decision":   decision,
                "confidence": snap.get("confidence"),
                "ev":         snap.get("ev"),
                "selection":  snap.get("top_runner") or "—",
                "result":     outcome,
                "winner":     res_row.get("winner") if res_row else None,
            })

        return jsonify({
            "ok":          True,
            "predictions": predictions,
            "count":       len(predictions),
            "date":        today,
        })
    except Exception as e:
        log.error(f"GET /api/predictions/today failed: {e}")
        return jsonify({"ok": False, "predictions": [], "error": "Could not retrieve today's predictions"}), 500


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


def _shape_backtest_response(
    result: dict, date_from: str, date_to: str,
    code_filter: str, batch_size: int
):
    """Shape backtest_engine output into the format backtesting.js expects."""
    if not result.get("ok"):
        return jsonify(result), 400

    total    = result.get("total_races") or 0
    hits     = result.get("winner_hit_count") or 0
    wrong    = total - hits
    hit_rate = result.get("hit_rate") or 0.0
    avg_odds = result.get("avg_winner_odds") or 0.0

    # ROI: (avg_winner_odds * hit_rate) - 1, expressed as percentage
    roi_float = round((avg_odds * hit_rate) - 1.0, 4) if avg_odds is not None and hit_rate is not None else 0.0
    roi_str   = f"{roi_float * 100:+.1f}%"

    # Profit: simulating $1 flat stake per race
    profit = round(hits * (avg_odds - 1) - wrong * 1.0, 2) if avg_odds else 0.0

    # Verdict
    if total == 0:
        verdict = "NO_DATA"
    elif roi_float > 0.10:
        verdict = "APPROVE"
    elif roi_float > 0:
        verdict = "BETTER"
    elif roi_float > -0.05:
        verdict = "CAUTION"
    else:
        verdict = "PASS"

    # Summary text
    if total == 0:
        summary_text = "No races found in the selected date range."
    else:
        summary_text = (
            f"Tested {total} races from {date_from} to {date_to}. "
            f"Model selected the winner {hits} times ({hit_rate*100:.1f}% hit rate). "
            f"Simulated ROI: {roi_str} at flat $1 stake."
        )

    # Fetch rows from backtest_run_items for this run
    rows = []
    try:
        from db import get_db, safe_query, T
        run_id = result.get("run_id") or ""
        if run_id:
            raw_rows = safe_query(
                lambda: get_db()
                .table(T("backtest_run_items"))
                .select("race_uid,race_date,track,code,predicted_winner,"
                        "actual_winner,winner_hit,winner_odds,score,model_version")
                .eq("run_id", run_id)
                .order("race_date")
                .limit(batch_size)
                .execute()
                .data,
                []
            ) or []

            rows = [
                {
                    "date":       r.get("race_date") or "",
                    "race":       f"{(r.get('track') or '').replace('-', ' ').title()} {r.get('code', '')}",
                    "selection":  r.get("predicted_winner") or "—",
                    "actual":     r.get("actual_winner") or "—",
                    "decision":   "WIN" if r.get("winner_hit") else "LOSS",
                    "confidence": f"{float(r.get('score') or 0):.2f}",
                    "pl":         f"+${float(r.get('winner_odds') or 0) - 1:.2f}"
                                  if r.get("winner_hit") else "-$1.00",
                }
                for r in raw_rows
            ]
    except Exception as _re:
        log.warning(f"backtest rows fetch failed: {_re}")

    # Error pattern analysis
    errors = []
    if rows:
        loss_rows = [r for r in rows if r["decision"] == "LOSS"]
        if len(loss_rows) > 3:
            errors.append({
                "tag": "LOSS_STREAK",
                "count": len(loss_rows),
            })

    summary = {
        "samples":        total,
        "correct":        hits,
        "wrong":          wrong,
        "hit_rate":       f"{hit_rate*100:.1f}%",
        "roi":            roi_str,
        "profit":         f"${profit:+.2f}",
        "avg_confidence": "—",
        "verdict":        verdict,
        "summary_text":   summary_text,
        "model_version":  result.get("model_version") or "baseline_v1",
        "run_id":         result.get("run_id") or "",
        "date_from":      date_from,
        "date_to":        date_to,
        "code_filter":    code_filter,
    }

    return jsonify({
        "ok":              True,
        "summary":         summary,
        "rows":            rows,
        "errors":          errors,
        "model_comparison": result.get("model_comparison"),
    })


@prediction_bp.route("/backtest-run", methods=["POST"])
def run_backtest_ui():
    """
    UI-accessible backtest runner — no admin role required.
    Delegates to backtest_engine with UI-friendly response shape.
    """
    try:
        from ai.backtest_engine import backtest_date_range
        data = request.get_json(silent=True) or {}
        today = date.today().isoformat()

        date_from = data.get("date_from") or data.get("date")
        date_to   = data.get("date_to")   or data.get("date")

        if not date_from or not date_to:
            return jsonify({"ok": False, "error": "date_from and date_to required"}), 400
        if date_from > today or date_to > today:
            return jsonify({"ok": False,
                "error": "Cannot backtest future dates — no result leakage"}), 400

        code_filter  = data.get("code_filter")
        batch_size   = int(data.get("batch_size") or 50)

        result = backtest_date_range(
            date_from=date_from,
            date_to=date_to,
            code_filter=code_filter if code_filter and code_filter != "ALL" else None,
        )

        return _shape_backtest_response(result, date_from, date_to,
                                        code_filter or "ALL", batch_size)
    except Exception as e:
        log.error(f"POST /api/predictions/backtest-run failed: {e}")
        return jsonify({"ok": False, "error": "Backtest failed"}), 500


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
