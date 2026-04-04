"""
ai/predictor.py - DemonPulse Predictor
========================================
Generates prediction outputs from validated race snapshots.

Baseline model: deterministic odds-ranked scoring
  - Score = implied probability (1/odds) with a small box-position tiebreak
  - Runners ranked 1 (top pick) to N deterministically
  - Fully contamination-free: only uses pre-result market/runner data

Architecture:
  - accept a race_uid or validated race snapshot
  - build features using feature_builder
  - run prediction pipeline (baseline now; swap in CatBoost later without
    changing the surrounding architecture)
  - save prediction results and lineage via learning_store
  - return structured prediction output with full lineage

Prediction output supports:
  - race_uid / oddspro_race_id
  - prediction_snapshot_id
  - model_version
  - predicted ranking + scores per runner
  - created_at timestamp
  - feature lineage reference (saved by learning_store)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from ai.feature_builder import build_race_features

log = logging.getLogger(__name__)

MODEL_VERSION = "baseline_v1"

# Default box position used when box_num is missing in the baseline tiebreak.
# Represents a mid-field position; actual box numbers start at 1.
_DEFAULT_BOX_NUM = 8


def predict_race(race_uid: str) -> dict[str, Any]:
    """
    Generate a prediction for a race by race_uid.

    Fetches authoritative race and runner data from storage,
    builds features, runs the prediction pipeline, and saves lineage.

    Args:
        race_uid: the race to predict

    Returns:
        Prediction result dict with ok, prediction_snapshot_id, runner_predictions
    """
    if not race_uid:
        return {"ok": False, "error": "race_uid required"}

    try:
        race, runners = _fetch_race_data(race_uid)
    except Exception as e:
        log.error(f"predictor: data fetch failed for {race_uid}: {e}")
        return {"ok": False, "error": "Data fetch failed", "race_uid": race_uid}

    if not race:
        return {"ok": False, "error": "Race not found", "race_uid": race_uid}

    return predict_from_snapshot(race, runners)


def predict_from_snapshot(
    race: dict[str, Any],
    runners: list[dict[str, Any]],
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Generate a prediction from in-memory race + runner data.

    Saves prediction results and feature lineage via learning_store.
    enrichment is optional FormFav data; it is clearly flagged as
    non-authoritative in features and never affects official truth.

    Args:
        race: authoritative race record
        runners: runner records for this race
        enrichment: optional per-runner enrichment dict keyed by runner name

    Returns:
        Full prediction dict with runner_predictions and lineage IDs
    """
    race_uid = race.get("race_uid") or ""
    oddspro_race_id = race.get("oddspro_race_id") or ""

    features = build_race_features(race, runners, enrichment)
    if not features:
        return {
            "ok": False,
            "error": "No active runners to predict",
            "race_uid": race_uid,
        }

    scored = _baseline_score(features)
    prediction_snapshot_id = _make_snapshot_id(race_uid)
    now = datetime.now(timezone.utc).isoformat()

    result: dict[str, Any] = {
        "ok": True,
        "race_uid": race_uid,
        "oddspro_race_id": oddspro_race_id,
        "prediction_snapshot_id": prediction_snapshot_id,
        "model_version": MODEL_VERSION,
        "feature_count": len(features),
        "runner_predictions": scored,
        "created_at": now,
        "lineage_saved": False,
    }

    try:
        from ai.learning_store import save_prediction_snapshot
        saved = save_prediction_snapshot(result, features)
        result["lineage_saved"] = saved
    except Exception as e:
        log.warning(f"predictor: learning_store save failed for {race_uid}: {e}")

    return result


def predict_today() -> dict[str, Any]:
    """
    Generate predictions for all open/upcoming races today.

    Returns:
        Summary dict with total count, prediction IDs, and any errors.
    """
    from datetime import date
    today = date.today().isoformat()

    try:
        from database import get_active_races
        races = get_active_races(today)
    except Exception as e:
        log.error(f"predictor: failed to get active races for {today}: {e}")
        return {"ok": False, "error": "Could not retrieve today's races", "date": today}

    if not races:
        return {"ok": True, "date": today, "total": 0, "predictions": [], "errors": []}

    predictions = []
    errors = []
    for race in races:
        race_uid = race.get("race_uid") or ""
        if not race_uid:
            continue
        result = predict_race(race_uid)
        if result.get("ok"):
            predictions.append({
                "race_uid": race_uid,
                "prediction_snapshot_id": result.get("prediction_snapshot_id"),
                "model_version": result.get("model_version"),
                "runner_count": result.get("feature_count", 0),
            })
        else:
            errors.append({"race_uid": race_uid, "error": result.get("error")})

    log.info(
        f"predictor: today predictions — {len(predictions)} ok, {len(errors)} errors"
    )
    return {
        "ok": True,
        "date": today,
        "total": len(predictions),
        "errors_count": len(errors),
        "predictions": predictions,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# BASELINE MODEL
# ---------------------------------------------------------------------------

def _baseline_score(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Deterministic odds-ranked scoring (baseline model).

    Algorithm:
      1. Primary score = implied probability (1 / win_odds)
      2. Small box-position tiebreak (lower boxes very slightly preferred —
         common greyhound track bias; negligible weight so odds dominate)
      3. Normalize scores to sum to 1.0 within the field
      4. Rank runners 1 (top pick) to N by score descending

    This is deterministic, contamination-free, and produces usable rankings
    immediately. Designed so a stronger model (CatBoost etc.) can replace
    this function without changing the pipeline architecture.
    """
    if not features:
        return []

    raw_scored = []
    for feat in features:
        win_odds = feat.get("win_odds") or 0.0
        implied_prob = (1.0 / win_odds) if win_odds > 1.0 else 0.0
        # Tiny box bias (weight = 0.5% per box position): greyhounds slight inside rail bias
        box_factor = 1.0 / (1.0 + (feat.get("box_num") or _DEFAULT_BOX_NUM) * 0.005)
        raw_score = implied_prob * box_factor
        raw_scored.append({
            "box_num": feat.get("box_num"),
            "runner_name": feat.get("runner_name") or "",
            "win_odds": win_odds,
            "implied_prob": round(implied_prob, 6),
            "raw_score": raw_score,
        })

    total = sum(s["raw_score"] for s in raw_scored) or 1.0
    for s in raw_scored:
        s["score"] = round(s["raw_score"] / total, 6)

    raw_scored.sort(key=lambda x: x["score"], reverse=True)
    for i, s in enumerate(raw_scored):
        s["predicted_rank"] = i + 1
        del s["raw_score"]

    return raw_scored


# ---------------------------------------------------------------------------
# DATA FETCH
# ---------------------------------------------------------------------------

def _fetch_race_data(race_uid: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Fetch authoritative race + runners from Supabase storage."""
    from db import get_db, safe_query, T

    race_rows = safe_query(
        lambda: get_db()
        .table(T("today_races"))
        .select("*")
        .eq("race_uid", race_uid)
        .limit(1)
        .execute()
        .data
    )
    race = (race_rows or [None])[0]
    if not race:
        return None, []

    race_id = race.get("id")
    runners = safe_query(
        lambda: get_db()
        .table(T("today_runners"))
        .select("*")
        .eq("race_id", race_id)
        .execute()
        .data,
        [],
    ) or []

    return race, runners


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _make_snapshot_id(race_uid: str) -> str:
    """Generate a stable unique prediction snapshot ID."""
    short_uid = str(uuid.uuid4()).replace("-", "")[:12]
    safe_race = (race_uid or "unknown").replace("/", "_").replace(" ", "_")[:30]
    return f"pred_{safe_race}_{short_uid}"
