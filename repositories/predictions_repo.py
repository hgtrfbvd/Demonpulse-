"""
repositories/predictions_repo.py — Prediction data access
==========================================================
Covers: feature_snapshots, prediction_snapshots,
        prediction_runner_outputs, sectional_snapshots,
        race_shape_snapshots.

Predictions are append-only by design — never overwrite official race
or result tables. Lineage is preserved through prediction_snapshot_id.

Race-code contamination: code is stored on prediction_snapshots so
queries can always be scoped to one code.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import (
    TABLE_FEATURE_SNAPS,
    TABLE_PRED_SNAPS,
    TABLE_PRED_OUTPUTS,
    TABLE_SECTIONALS,
    TABLE_RACE_SHAPE,
    UPSERT_KEYS,
)

log = logging.getLogger(__name__)


class PredictionsRepo:
    """Repository for prediction-related tables."""

    # ── FEATURE SNAPSHOTS ────────────────────────────────────────

    @staticmethod
    def save_feature_snapshot(snap: dict[str, Any]) -> Optional[dict]:
        """Insert a feature snapshot row (append-only)."""
        if not snap.get("race_uid"):
            log.warning("PredictionsRepo: feature snapshot missing race_uid")
            return None
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_FEATURE_SNAPS))
                .insert(snap)
                .execute()
                .data,
            default=None,
            context="PredictionsRepo.save_feature_snapshot",
        )
        return (result[0] if isinstance(result, list) else result) if result else None

    @staticmethod
    def get_feature_snapshots(race_uid: str) -> list[dict]:
        """Fetch all feature snapshots for a race."""
        return safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_FEATURE_SNAPS))
                .select("*")
                .eq("race_uid", race_uid)
                .execute()
                .data,
            default=[],
            context="PredictionsRepo.get_feature_snapshots",
        ) or []

    # ── PREDICTION SNAPSHOTS ─────────────────────────────────────

    @staticmethod
    def save_prediction_snapshot(snap: dict[str, Any]) -> Optional[dict]:
        """
        Upsert a prediction snapshot.
        Conflict key: prediction_snapshot_id
        """
        if not snap.get("prediction_snapshot_id"):
            log.warning("PredictionsRepo: snapshot missing prediction_snapshot_id")
            return None
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_PRED_SNAPS))
                .upsert(snap, on_conflict=UPSERT_KEYS[TABLE_PRED_SNAPS])
                .execute()
                .data,
            default=None,
            context="PredictionsRepo.save_prediction_snapshot",
        )
        return (result[0] if isinstance(result, list) else result) if result else None

    @staticmethod
    def get_prediction_snapshot(snap_id: str) -> Optional[dict]:
        """Fetch a prediction snapshot by ID."""
        rows = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_PRED_SNAPS))
                .select("*")
                .eq("prediction_snapshot_id", snap_id)
                .limit(1)
                .execute()
                .data,
            default=[],
            context="PredictionsRepo.get_prediction_snapshot",
        ) or []
        return rows[0] if rows else None

    @staticmethod
    def get_snapshots_for_race(race_uid: str) -> list[dict]:
        """Fetch all prediction snapshots for a race."""
        return safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_PRED_SNAPS))
                .select("*")
                .eq("race_uid", race_uid)
                .order("created_at", desc=True)
                .execute()
                .data,
            default=[],
            context="PredictionsRepo.get_snapshots_for_race",
        ) or []

    # ── RUNNER OUTPUTS ───────────────────────────────────────────

    @staticmethod
    def save_runner_outputs(outputs: list[dict[str, Any]]) -> int:
        """Insert per-runner prediction outputs (append-only)."""
        if not outputs:
            return 0
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_PRED_OUTPUTS))
                .insert(outputs)
                .execute()
                .data,
            default=None,
            context="PredictionsRepo.save_runner_outputs",
        )
        return len(result) if result else 0

    @staticmethod
    def get_runner_outputs(snap_id: str) -> list[dict]:
        """Fetch all runner outputs for a prediction snapshot."""
        return safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_PRED_OUTPUTS))
                .select("*")
                .eq("prediction_snapshot_id", snap_id)
                .order("predicted_rank")
                .execute()
                .data,
            default=[],
            context="PredictionsRepo.get_runner_outputs",
        ) or []

    # ── SECTIONAL SNAPSHOTS ──────────────────────────────────────

    @staticmethod
    def save_sectional_snapshots(rows: list[dict[str, Any]]) -> int:
        """Insert sectional metric rows (append-only)."""
        if not rows:
            return 0
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_SECTIONALS))
                .insert(rows)
                .execute()
                .data,
            default=None,
            context="PredictionsRepo.save_sectional_snapshots",
        )
        return len(result) if result else 0

    @staticmethod
    def get_sectionals(race_uid: str) -> list[dict]:
        """Fetch sectional snapshots for a race."""
        return safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_SECTIONALS))
                .select("*")
                .eq("race_uid", race_uid)
                .execute()
                .data,
            default=[],
            context="PredictionsRepo.get_sectionals",
        ) or []

    # ── RACE SHAPE ───────────────────────────────────────────────

    @staticmethod
    def save_race_shape(shape: dict[str, Any]) -> Optional[dict]:
        """
        Upsert race shape snapshot.
        Conflict key: race_uid (one shape per race)
        """
        if not shape.get("race_uid"):
            log.warning("PredictionsRepo: race shape missing race_uid")
            return None
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_RACE_SHAPE))
                .upsert(shape, on_conflict=UPSERT_KEYS[TABLE_RACE_SHAPE])
                .execute()
                .data,
            default=None,
            context="PredictionsRepo.save_race_shape",
        )
        return (result[0] if isinstance(result, list) else result) if result else None

    @staticmethod
    def get_race_shape(race_uid: str) -> Optional[dict]:
        """Fetch race shape snapshot for a race."""
        rows = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_RACE_SHAPE))
                .select("*")
                .eq("race_uid", race_uid)
                .limit(1)
                .execute()
                .data,
            default=[],
            context="PredictionsRepo.get_race_shape",
        ) or []
        return rows[0] if rows else None

    # ── PERFORMANCE BY MODEL ─────────────────────────────────────

    @staticmethod
    def get_performance_by_model(model_version: str, limit: int = 100) -> list[dict]:
        """Fetch prediction snapshots for a specific model version."""
        return safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_PRED_SNAPS))
                .select("*")
                .eq("model_version", model_version)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
                .data,
            default=[],
            context="PredictionsRepo.get_performance_by_model",
        ) or []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
