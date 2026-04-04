"""
services/race_service.py - DemonPulse Race Intelligence Service
================================================================
Orchestrates the full Phase 4 intelligence pipeline for a single race:
  1. Build base features (OddsPro authoritative)
  2. Extract / load sectional metrics (OddsPro authoritative)
  3. Build race shape (authoritative sectionals + optional FormFav enrichment)
  4. Build collision metrics (greyhounds only)
  5. Build enriched feature snapshot including all Phase 4 signals
  6. Generate prediction (v2_feature_engine or baseline)
  7. Persist all snapshots via learning_store

Rules:
  - OddsPro data is always authoritative
  - FormFav enrichment clearly flagged and never overwrites authoritative data
  - Sectionals are extracted from result payloads by result_service; this
    service loads them from storage for pre-result feature enrichment
  - No official truth tables are modified here
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def build_intelligence_snapshot(
    race: dict[str, Any],
    runners: list[dict[str, Any]],
    enrichment: dict[str, Any] | None = None,
    model_version: str = "v2_feature_engine",
) -> dict[str, Any]:
    """
    Build a full Phase 4 intelligence snapshot for a race.

    Orchestrates sectionals → race shape → collision → features → prediction.

    Args:
        race          : authoritative race record (OddsPro-sourced)
        runners       : runner records for this race
        enrichment    : optional per-runner FormFav enrichment dict
        model_version : 'v2_feature_engine' or 'baseline_v1'

    Returns:
        Intelligence snapshot dict including features, race shape,
        sectional metrics, collision metrics, and prediction.
    """
    race_uid        = race.get("race_uid") or ""
    race_code       = (race.get("code") or "").upper()
    is_greyhound    = "GREY" in race_code or race_code in ("GR", "DOG", "GREYHOUND")

    # ── Step 1: Load stored sectional metrics (OddsPro authoritative) ──────
    sectional_metrics = _load_sectional_metrics(race_uid)

    # ── Step 2: Build race shape ────────────────────────────────────────────
    from ai.feature_builder import build_race_features
    from ai.race_shape import build_race_shape

    # Build base features first (without shape/collision) for shape inputs
    base_features = build_race_features(race, runners, enrichment)

    formfav_speed_map = None
    if enrichment:
        formfav_speed_map = {
            k: v for k, v in enrichment.items()
            if isinstance(v, dict)
        }

    race_shape = build_race_shape(
        race=race,
        runner_features=base_features,
        sectional_metrics=sectional_metrics or None,
        formfav_speed_map=formfav_speed_map,
    )

    # ── Step 3: Build collision metrics (greyhounds only) ───────────────────
    collision_metrics: list[dict[str, Any]] = []
    if is_greyhound and base_features:
        from ai.collision_model import build_collision_metrics
        collision_metrics = build_collision_metrics(
            race=race,
            runner_features=base_features,
            sectional_metrics=sectional_metrics or None,
            race_shape=race_shape,
        )

    # ── Step 4: Build enriched feature snapshot ─────────────────────────────
    features = build_race_features(
        race, runners, enrichment,
        sectional_metrics=sectional_metrics or None,
        race_shape=race_shape,
        collision_metrics=collision_metrics or None,
    )

    # ── Step 5: Generate prediction ──────────────────────────────────────────
    prediction: dict[str, Any] = {}
    if model_version == "v2_feature_engine":
        from ai.predictor import predict_from_snapshot_v2
        prediction = predict_from_snapshot_v2(
            race=race,
            runners=runners,
            enrichment=enrichment,
            sectional_metrics=sectional_metrics or None,
            race_shape=race_shape,
            collision_metrics=collision_metrics or None,
        )
    else:
        from ai.predictor import predict_from_snapshot
        prediction = predict_from_snapshot(race=race, runners=runners, enrichment=enrichment)

    # ── Step 6: Persist race shape and sectionals snapshots ──────────────────
    try:
        from ai.learning_store import save_race_shape_snapshot
        save_race_shape_snapshot(race_uid, race_shape)
    except Exception as e:
        log.warning(f"race_service: save_race_shape failed for {race_uid}: {e}")

    # Update health metrics
    try:
        from services.health_service import record_feature_build, record_race_shape_build
        record_feature_build(count=len(features))
        record_race_shape_build(count=1)
    except Exception:
        pass

    return {
        "ok": True,
        "race_uid": race_uid,
        "model_version": model_version,
        "feature_count": len(features),
        "features": features,
        "race_shape": race_shape,
        "sectional_metrics": sectional_metrics,
        "collision_metrics": collision_metrics,
        "prediction": prediction,
    }


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _load_sectional_metrics(race_uid: str) -> list[dict[str, Any]]:
    """
    Load stored per-runner sectional metrics for a race from sectional_snapshots.
    Returns empty list if none are stored yet.
    """
    try:
        from db import get_db, safe_query, T

        rows = safe_query(
            lambda: get_db()
            .table(T("sectional_snapshots"))
            .select("*")
            .eq("race_uid", race_uid)
            .order("box_num")
            .execute()
            .data,
            [],
        ) or []
        return rows
    except Exception as e:
        log.debug(f"race_service: _load_sectional_metrics failed for {race_uid}: {e}")
        return []
