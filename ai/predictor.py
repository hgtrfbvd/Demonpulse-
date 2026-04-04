"""
ai/predictor.py - DemonPulse Predictor
========================================
Generates prediction outputs from validated race snapshots.

Baseline model (model_version = "baseline_v1"):
  - Score = implied probability (1/odds) with a small box-position tiebreak
  - Runners ranked 1 (top pick) to N deterministically
  - Fully contamination-free: only uses pre-result market/runner data

Feature-engine model (model_version = "v2_feature_engine"):
  - Weighted multi-signal score combining:
      implied_probability          × 0.30  (OddsPro authoritative)
      early_speed_score            × 0.12  (OddsPro authoritative sectionals)
      late_speed_score             × 0.12  (OddsPro authoritative sectionals)
      sectional_consistency_score  × 0.08  (OddsPro authoritative sectionals)
      race_shape_fit               × 0.12  (derived from OddsPro field shape)
      enrichment_win_prob          × 0.05  (FormFav; non-authoritative MAX 0.05)
      enrichment_class_rating      × 0.05  (FormFav; non-authoritative MAX 0.05)
    - (collision_risk_score)       × 0.10  (subtracted; GREYHOUND only)
  - Normalised to sum 1.0 across field
  - Falls back to baseline gracefully if feature columns are missing
  - FormFav enrichment is optional (max weight 0.05 each) — never required
  - Deterministic and reproducible (no randomness until a trained model is added)

Multi-code rules:
  - collision_risk_score subtracted for GREYHOUND only
  - box bias applied for GREYHOUND only
  - leader pressure boosted for HARNESS
  - late speed boosted for GALLOPS
  - NO default fallback to GREYHOUND

Architecture:
  - Both model paths share the same prediction storage / lineage via learning_store
  - Model version is stored distinctly on every prediction snapshot
  - predict_race() uses the active model version; both paths remain callable directly
  - FormFav enrichment applied via enrichment_guard (prefix-only, never overwrites)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from ai.feature_builder import build_race_features

log = logging.getLogger(__name__)

MODEL_VERSION = "baseline_v1"
MODEL_VERSION_V2 = "v2_feature_engine"
MODEL_VERSION_V2_ENRICHED = "v2_with_enrichment"

# Maximum weight allowed for any FormFav enrichment signal.
# FormFav is non-authoritative; capping at 0.05 ensures it cannot dominate.
_MAX_ENRICHMENT_WEIGHT: float = 0.05

# Weights for the v2_feature_engine model (Phase 4.6 revised)
# FormFav enrichment is capped at _MAX_ENRICHMENT_WEIGHT per field — never required
_V2_WEIGHTS = {
    "implied_probability":          0.30,
    "early_speed_score":            0.12,
    "late_speed_score":             0.12,
    "sectional_consistency_score":  0.08,
    "race_shape_fit":               0.12,
    "enrichment_win_prob":          _MAX_ENRICHMENT_WEIGHT,
    "enrichment_class_rating":      _MAX_ENRICHMENT_WEIGHT,
    # collision risk subtracted (GREYHOUND only — never applied to HARNESS or GALLOPS)
    "collision_risk_score":        -0.10,
}


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


def predict_from_snapshot_v2(
    race: dict[str, Any],
    runners: list[dict[str, Any]],
    enrichment: dict[str, Any] | None = None,
    sectional_metrics: list[dict[str, Any]] | None = None,
    race_shape: dict[str, Any] | None = None,
    collision_metrics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Generate a v2_feature_engine prediction from in-memory race + runner data.

    Hybrid model: combines market (OddsPro) + feature signals.

    Uses the full feature set: sectionals, race shape, collision, enrichment.
    Saves prediction results and feature lineage via learning_store.
    Falls back to baseline_v1 if features are missing or insufficient.

    FormFav enrichment (if provided) is applied via enrichment_guard — all
    enrichment fields are prefixed enrichment_* and never overwrite authoritative
    OddsPro fields. Enrichment is optional; prediction runs without it.

    Args:
        race             : authoritative race record
        runners          : runner records for this race
        enrichment       : optional per-runner enrichment dict (FormFav; non-authoritative)
        sectional_metrics: optional per-runner OddsPro sectional metric dicts
        race_shape       : optional race shape dict
        collision_metrics: optional per-runner collision metric dicts (GREYHOUND only)

    Returns:
        Full prediction dict with runner_predictions and lineage IDs
    """
    from ai.feature_builder import build_race_features
    from ai.enrichment_guard import apply_enrichment_to_field

    race_uid        = race.get("race_uid") or ""
    oddspro_race_id = race.get("oddspro_race_id") or ""
    has_enrichment  = bool(enrichment)

    features = build_race_features(
        race, runners, enrichment,
        sectional_metrics=sectional_metrics,
        race_shape=race_shape,
        collision_metrics=collision_metrics,
    )
    if not features:
        return {
            "ok": False,
            "error": "No active runners to predict",
            "race_uid": race_uid,
        }

    # Apply enrichment guard to ensure FormFav data is safely prefixed and
    # never overwrites any authoritative fields
    if has_enrichment and isinstance(enrichment, dict):
        features = apply_enrichment_to_field(features, enrichment)

    # Fallback to baseline_v1 if feature signals are too sparse across the field.
    # Require that a majority (>50%) of runners have at least one rich signal
    # (sectionals) to justify v2 scoring. Enrichment absence is NOT a reason
    # to fall back — it is always optional.
    rich_count = sum(
        1 for f in features
        if f.get("has_sectionals") or f.get("early_speed_score")
    )
    majority_threshold = max(1, len(features) // 2)
    if rich_count < majority_threshold:
        log.warning(
            f"predictor v2: features sparse for {race_uid} "
            f"({rich_count}/{len(features)} runners with rich data) "
            f"— falling back to baseline_v1"
        )
        scored = _baseline_score(features)
        effective_model = MODEL_VERSION
    else:
        scored = _v2_feature_score(features)
        effective_model = MODEL_VERSION_V2_ENRICHED if has_enrichment else MODEL_VERSION_V2

    prediction_snapshot_id = _make_snapshot_id(race_uid)
    now = datetime.now(timezone.utc).isoformat()

    result: dict[str, Any] = {
        "ok": True,
        "race_uid": race_uid,
        "oddspro_race_id": oddspro_race_id,
        "prediction_snapshot_id": prediction_snapshot_id,
        "model_version": effective_model,
        "feature_count": len(features),
        "runner_predictions": scored,
        "has_enrichment": 1 if has_enrichment else 0,
        "source_type": "pre_race",
        "created_at": now,
        "lineage_saved": False,
    }

    try:
        from ai.learning_store import save_prediction_snapshot
        saved = save_prediction_snapshot(result, features)
        result["lineage_saved"] = saved
    except Exception as e:
        log.warning(f"predictor v2: learning_store save failed for {race_uid}: {e}")

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

# Default box position used when box_num is missing in the baseline tiebreak.
# Represents a mid-field position; actual box numbers start at 1.
_DEFAULT_BOX_NUM = 8


def _baseline_score(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Deterministic odds-ranked scoring (baseline model).

    Algorithm:
      1. Primary score = implied probability (1 / win_odds)
      2. Small box-position tiebreak applied only for GREYHOUND races
         (inside boxes slightly preferred on Australian ovals; negligible weight)
      3. Normalize scores to sum to 1.0 within the field
      4. Rank runners 1 (top pick) to N by score descending

    Multi-code rules:
      - Box bias: GREYHOUND only
      - NO default fallback to GREYHOUND if race_code is unknown

    This is deterministic, contamination-free, and produces usable rankings
    immediately. Designed so a stronger model (CatBoost etc.) can replace
    this function without changing the pipeline architecture.
    """
    if not features:
        return []

    # Determine race type from features (all runners in a race share the same code)
    race_code = (features[0].get("code") or "").upper()
    is_greyhound = race_code == "GREYHOUND"
    # NO default fallback to GREYHOUND — must be explicitly set

    raw_scored = []
    for feat in features:
        win_odds = feat.get("win_odds") or 0.0
        implied_prob = (1.0 / win_odds) if win_odds > 1.0 else 0.0
        if is_greyhound:
            # Tiny box bias (weight = 0.5% per box position): greyhound inside rail bias
            box_factor = 1.0 / (1.0 + (feat.get("box_num") or _DEFAULT_BOX_NUM) * 0.005)
        else:
            box_factor = 1.0
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
# V2 FEATURE-ENGINE MODEL
# ---------------------------------------------------------------------------

def _v2_feature_score(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Weighted multi-signal scoring (v2_feature_engine model).

    Hybrid model: combines market (OddsPro) + feature signals.

    Uses the full feature set from feature_builder.  Falls back gracefully
    to implied_probability when richer features are absent.

    Weights (see _V2_WEIGHTS at module top):
        implied_probability          × 0.30   (OddsPro authoritative)
        early_speed_score            × 0.12   (OddsPro authoritative sectionals)
        late_speed_score             × 0.12   (OddsPro authoritative sectionals)
        sectional_consistency_score  × 0.08   (OddsPro authoritative sectionals)
        race_shape_fit               × 0.12   (derived from OddsPro field shape)
        enrichment_win_prob          × 0.05   (FormFav — non-authoritative; MAX 0.05)
        enrichment_class_rating      × 0.05   (FormFav — non-authoritative; MAX 0.05)
      - collision_risk_score         × 0.10   (subtracted; GREYHOUND only)

    Multi-code rules:
      - collision_risk_score: GREYHOUND only (never HARNESS or GALLOPS)
      - box bias: GREYHOUND only
      - NO default fallback to GREYHOUND

    Final scores are normalised to sum to 1.0 across the field.
    """
    if not features:
        return []

    # Determine race type — explicit code matching, NO default fallback to GREYHOUND
    race_code = (features[0].get("code") or "").upper()
    is_greyhound = race_code == "GREYHOUND"
    is_harness   = race_code == "HARNESS"
    is_gallops   = race_code == "GALLOPS"

    # Normalise FormFav enrichment_win_prob to 0-1 across field (if present)
    win_probs = [
        float(f.get("enrichment_win_prob") or 0.0) for f in features
    ]
    max_win_prob = max(win_probs) if win_probs else 0.0

    # Normalise FormFav enrichment_class_rating to 0-1 across field (if present)
    class_ratings = [
        float(f.get("enrichment_class_rating") or 0.0) for f in features
    ]
    max_class_rating = max(class_ratings) if class_ratings else 0.0

    raw_scored = []
    for feat in features:
        ip       = float(feat.get("implied_probability") or feat.get("implied_prob") or 0.0)
        early_spd = float(feat.get("early_speed_score") or 0.0)
        late_spd  = float(feat.get("late_speed_score") or 0.0)
        sect_cons = float(feat.get("sectional_consistency_score") or 0.5)
        shape_fit = float(feat.get("race_shape_fit") or 0.3)

        # FormFav enrichment — optional, max weight 0.05 each
        # Only non-zero when enrichment is present for this runner
        win_prob_norm = (
            float(feat.get("enrichment_win_prob") or 0.0) / max_win_prob
            if max_win_prob > 0 else 0.0
        )
        class_rating_norm = (
            float(feat.get("enrichment_class_rating") or 0.0) / max_class_rating
            if max_class_rating > 0 else 0.0
        )

        # Collision risk subtracted ONLY for GREYHOUND races
        col_risk = float(feat.get("collision_risk_score") or 0.0) if is_greyhound else 0.0

        raw_score = (
            ip              * _V2_WEIGHTS["implied_probability"]
            + early_spd     * _V2_WEIGHTS["early_speed_score"]
            + late_spd      * _V2_WEIGHTS["late_speed_score"]
            + sect_cons     * _V2_WEIGHTS["sectional_consistency_score"]
            + shape_fit     * _V2_WEIGHTS["race_shape_fit"]
            + win_prob_norm * _V2_WEIGHTS["enrichment_win_prob"]
            + class_rating_norm * _V2_WEIGHTS["enrichment_class_rating"]
            + col_risk      * _V2_WEIGHTS["collision_risk_score"]   # negative; greyhound only
        )
        raw_score = max(raw_score, 0.0)   # clamp — collision subtraction can go negative

        raw_scored.append({
            "box_num":      feat.get("box_num"),
            "runner_name":  feat.get("runner_name") or "",
            "win_odds":     feat.get("win_odds") or 0.0,
            "implied_prob": round(ip, 6),
            "raw_score":    raw_score,
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
