"""
repositories/learning_repo.py — AI learning data access
========================================================
Covers: learning_evaluations, feature_snapshots (for learning lineage).

Learning records tie back to their source predictions and official results.
No provisional or FormFav data should trigger final evaluation writes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import TABLE_LEARNING_EVALS

log = logging.getLogger(__name__)

_TABLE = TABLE_LEARNING_EVALS


class LearningRepo:
    """Repository for learning_evaluations table."""

    # ── WRITE ────────────────────────────────────────────────────

    @staticmethod
    def save_evaluation(eval_row: dict[str, Any]) -> Optional[dict]:
        """
        Insert a learning evaluation record (append-only).

        The evaluation must reference a valid prediction_snapshot_id and race_uid
        so lineage can be traced back to the original prediction event.

        Args:
            eval_row: Evaluation data. Should include race_uid,
                      prediction_snapshot_id, model_version, winner_hit, etc.

        Returns:
            Saved record dict, or None on failure.
        """
        if not eval_row.get("race_uid"):
            log.warning("LearningRepo: evaluation missing race_uid")
            return None
        if not eval_row.get("prediction_snapshot_id"):
            log.warning("LearningRepo: evaluation missing prediction_snapshot_id")
            return None

        payload = LearningRepo._build_payload(eval_row)
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(_TABLE))
                .upsert(payload, on_conflict="prediction_snapshot_id")
                .execute()
                .data,
            default=None,
            context="LearningRepo.save_evaluation",
        )
        if result:
            log.debug(f"LearningRepo: saved evaluation for {payload.get('race_uid')}")
            return result[0] if isinstance(result, list) else result
        return None

    @staticmethod
    def save_many(evals: list[dict[str, Any]]) -> int:
        """Save multiple evaluation records. Returns count saved."""
        return sum(1 for e in evals if LearningRepo.save_evaluation(e))

    # ── READS ────────────────────────────────────────────────────

    @staticmethod
    def get_for_race(race_uid: str) -> list[dict]:
        """Fetch all evaluation rows for a race."""
        return safe_execute(
            lambda: get_client()
                .table(resolve_table(_TABLE))
                .select("*")
                .eq("race_uid", race_uid)
                .execute()
                .data,
            default=[],
            context="LearningRepo.get_for_race",
        ) or []

    @staticmethod
    def get_recent(limit: int = 100, model_version: Optional[str] = None) -> list[dict]:
        """Fetch recent evaluation records, optionally filtered by model."""
        q = (
            get_client()
                .table(resolve_table(_TABLE))
                .select("*")
                .order("evaluated_at", desc=True)
                .limit(limit)
        )
        if model_version:
            q = q.eq("model_version", model_version)
        return safe_execute(
            lambda: q.execute().data,
            default=[],
            context="LearningRepo.get_recent",
        ) or []

    @staticmethod
    def get_accuracy_summary(model_version: Optional[str] = None, limit: int = 500) -> dict:
        """
        Return a simple accuracy summary dict:
        {total, winner_hits, top2_hits, top3_hits, winner_accuracy}
        """
        q = (
            get_client()
                .table(resolve_table(_TABLE))
                .select("winner_hit,top2_hit,top3_hit")
                .order("evaluated_at", desc=True)
                .limit(limit)
        )
        if model_version:
            q = q.eq("model_version", model_version)

        rows = safe_execute(
            lambda: q.execute().data,
            default=[],
            context="LearningRepo.get_accuracy_summary",
        ) or []

        total = len(rows)
        winner_hits = sum(1 for r in rows if r.get("winner_hit"))
        top2_hits   = sum(1 for r in rows if r.get("top2_hit"))
        top3_hits   = sum(1 for r in rows if r.get("top3_hit"))

        return {
            "total":           total,
            "winner_hits":     winner_hits,
            "top2_hits":       top2_hits,
            "top3_hits":       top3_hits,
            "winner_accuracy": round(winner_hits / total, 4) if total else 0.0,
        }

    # ── INTERNAL ─────────────────────────────────────────────────

    @staticmethod
    def _build_payload(row: dict[str, Any]) -> dict:
        return {
            "prediction_snapshot_id":   str(row["prediction_snapshot_id"]),
            "race_uid":                 str(row["race_uid"]),
            "oddspro_race_id":          row.get("oddspro_race_id", ""),
            "model_version":            row.get("model_version", "baseline_v1"),
            "predicted_winner":         row.get("predicted_winner", ""),
            "actual_winner":            row.get("actual_winner", ""),
            "winner_hit":               bool(row.get("winner_hit", False)),
            "top2_hit":                 bool(row.get("top2_hit", False)),
            "top3_hit":                 bool(row.get("top3_hit", False)),
            "predicted_rank_of_winner": row.get("predicted_rank_of_winner"),
            "winner_odds":              _to_numeric(row.get("winner_odds") or row.get("win_price")),
            "used_enrichment":          bool(row.get("used_enrichment", False)),
            "disagreement_score":       _to_numeric(row.get("disagreement_score")),
            "formfav_rank":             row.get("formfav_rank"),
            "your_rank":                row.get("your_rank"),
            "evaluation_source":        row.get("evaluation_source", "oddspro"),
            "evaluated_at":             row.get("evaluated_at") or _now(),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_numeric(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
