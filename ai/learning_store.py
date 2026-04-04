"""
ai/learning_store.py - DemonPulse Learning Store
==================================================
Stores prediction lineage and evaluates outcomes after official results arrive.

Rules:
  - Predictions never overwrite official race/result tables
  - Evaluation always uses official confirmed results only (OddsPro-sourced)
  - No provisional FormFav data may trigger final evaluation
  - Preserves clean lineage: prediction → features → race → official result

Storage:
  - feature_snapshots: serialized feature arrays with race lineage
  - prediction_snapshots: prediction run metadata
  - prediction_runner_outputs: per-runner scores and ranks
  - learning_evaluations: post-result evaluation records
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


def save_prediction_snapshot(
    prediction: dict[str, Any],
    features: list[dict[str, Any]],
) -> bool:
    """
    Save a prediction snapshot: feature lineage, prediction metadata,
    and per-runner outputs.

    Args:
        prediction: output dict from predictor.predict_from_snapshot()
        features: feature rows used to generate this prediction

    Returns:
        True if all records saved successfully, False otherwise.
    """
    snap_id = prediction.get("prediction_snapshot_id") or ""
    race_uid = prediction.get("race_uid") or ""
    oddspro_race_id = prediction.get("oddspro_race_id") or ""
    model_version = prediction.get("model_version") or "baseline_v1"
    created_at = prediction.get("created_at") or _now()
    runner_predictions = prediction.get("runner_predictions") or []

    try:
        from db import get_db, safe_query, T

        feature_snapshot_id = _save_feature_snapshot(
            race_uid=race_uid,
            oddspro_race_id=oddspro_race_id,
            features=features,
        )

        snap_row = {
            "prediction_snapshot_id": snap_id,
            "race_uid": race_uid,
            "oddspro_race_id": oddspro_race_id,
            "model_version": model_version,
            "feature_snapshot_id": feature_snapshot_id,
            "runner_count": len(runner_predictions),
            "created_at": created_at,
        }
        safe_query(
            lambda: get_db()
            .table(T("prediction_snapshots"))
            .upsert(snap_row, on_conflict="prediction_snapshot_id")
            .execute()
        )

        runner_rows = [
            {
                "prediction_snapshot_id": snap_id,
                "race_uid": race_uid,
                "runner_name": rp.get("runner_name") or "",
                "box_num": rp.get("box_num"),
                "predicted_rank": rp.get("predicted_rank"),
                "score": rp.get("score"),
                "model_version": model_version,
                "created_at": created_at,
            }
            for rp in runner_predictions
        ]
        if runner_rows:
            safe_query(
                lambda: get_db()
                .table(T("prediction_runner_outputs"))
                .insert(runner_rows)
                .execute()
            )

        log.info(
            f"learning_store: saved prediction {snap_id} for {race_uid} "
            f"({len(runner_rows)} runners)"
        )
        return True

    except Exception as e:
        log.error(
            f"learning_store: save_prediction_snapshot failed "
            f"for {race_uid}: {e}"
        )
        return False


def evaluate_prediction(
    race_uid: str,
    official_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate outstanding predictions for a race after official result confirmation.

    This must only be called after OddsPro-confirmed results are written.
    No provisional FormFav data may trigger evaluation.

    Args:
        race_uid: the race to evaluate
        official_result: confirmed result dict from results_log (OddsPro source only)

    Returns:
        evaluation summary dict
    """
    if not race_uid or not official_result:
        return {"ok": False, "error": "race_uid and official_result required"}

    actual_winner = official_result.get("winner") or ""
    winner_box = official_result.get("winner_box")
    place_2 = official_result.get("place_2") or ""
    place_3 = official_result.get("place_3") or ""
    win_price = official_result.get("win_price")

    if not actual_winner:
        return {"ok": False, "error": "No winner in official result", "race_uid": race_uid}

    try:
        from db import get_db, safe_query, T

        snapshots = safe_query(
            lambda: get_db()
            .table(T("prediction_snapshots"))
            .select("prediction_snapshot_id,model_version,oddspro_race_id")
            .eq("race_uid", race_uid)
            .execute()
            .data,
            [],
        ) or []

        if not snapshots:
            return {
                "ok": True,
                "race_uid": race_uid,
                "evaluated": 0,
                "reason": "no_predictions_stored",
            }

        evaluations = []
        for snap in snapshots:
            snap_id = snap.get("prediction_snapshot_id")
            model_version = snap.get("model_version") or "baseline_v1"

            outputs = safe_query(
                lambda: get_db()
                .table(T("prediction_runner_outputs"))
                .select("runner_name,box_num,predicted_rank,score")
                .eq("prediction_snapshot_id", snap_id)
                .order("predicted_rank")
                .execute()
                .data,
                [],
            ) or []

            if not outputs:
                continue

            predicted_winner_name = outputs[0].get("runner_name") or ""
            predicted_winner_box = outputs[0].get("box_num")
            top2_names = {r.get("runner_name") for r in outputs[:2] if r.get("runner_name")}
            top3_names = {r.get("runner_name") for r in outputs[:3] if r.get("runner_name")}

            winner_hit = bool(
                (predicted_winner_name and
                 predicted_winner_name.upper() == actual_winner.upper())
                or (predicted_winner_box is not None and
                    predicted_winner_box == winner_box)
            )
            top2_hit = bool(
                actual_winner.upper() in {n.upper() for n in top2_names}
            )
            top3_hit = bool(
                actual_winner.upper() in {n.upper() for n in top3_names}
            )

            pred_rank_of_winner = None
            for r in outputs:
                if (r.get("runner_name") or "").upper() == actual_winner.upper():
                    pred_rank_of_winner = r.get("predicted_rank")
                    break
                if r.get("box_num") is not None and r.get("box_num") == winner_box:
                    pred_rank_of_winner = r.get("predicted_rank")
                    break

            eval_row = {
                "prediction_snapshot_id": snap_id,
                "race_uid": race_uid,
                "oddspro_race_id": snap.get("oddspro_race_id") or "",
                "model_version": model_version,
                "predicted_winner": predicted_winner_name,
                "actual_winner": actual_winner,
                "winner_hit": winner_hit,
                "top2_hit": top2_hit,
                "top3_hit": top3_hit,
                "predicted_rank_of_winner": pred_rank_of_winner,
                "winner_odds": _safe_float(win_price),
                "evaluation_source": "oddspro",
                "evaluated_at": _now(),
            }
            safe_query(
                lambda: get_db()
                .table(T("learning_evaluations"))
                .upsert(eval_row, on_conflict="prediction_snapshot_id")
                .execute()
            )
            evaluations.append(eval_row)

        log.info(
            f"learning_store: evaluated {len(evaluations)} predictions "
            f"for {race_uid} (winner={actual_winner})"
        )
        return {
            "ok": True,
            "race_uid": race_uid,
            "evaluated": len(evaluations),
            "evaluations": evaluations,
        }

    except Exception as e:
        log.error(f"learning_store: evaluate_prediction failed for {race_uid}: {e}")
        return {"ok": False, "error": "Evaluation failed", "race_uid": race_uid}


def get_performance_summary(
    model_version: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """
    Return performance summary across stored learning evaluations.

    Args:
        model_version: filter to a specific version (None = all versions)
        limit: max evaluations to include in summary

    Returns:
        Performance stats including hit rates and average winner odds.
    """
    try:
        from db import get_db, safe_query, T

        q = (
            get_db()
            .table(T("learning_evaluations"))
            .select(
                "model_version,winner_hit,top2_hit,top3_hit,winner_odds,race_uid"
            )
            .order("evaluated_at", desc=True)
            .limit(limit)
        )
        if model_version:
            q = q.eq("model_version", model_version)

        rows = safe_query(lambda: q.execute().data, []) or []

        if not rows:
            return {
                "ok": True,
                "total_evaluated": 0,
                "model_version": model_version or "all",
            }

        total = len(rows)
        winner_hits = sum(1 for r in rows if r.get("winner_hit"))
        top2_hits = sum(1 for r in rows if r.get("top2_hit"))
        top3_hits = sum(1 for r in rows if r.get("top3_hit"))
        winning_odds = [
            float(r["winner_odds"])
            for r in rows
            if r.get("winner_hit") and r.get("winner_odds")
        ]
        avg_winner_odds = (
            round(sum(winning_odds) / len(winning_odds), 2)
            if winning_odds else None
        )

        return {
            "ok": True,
            "model_version": model_version or "all",
            "total_evaluated": total,
            "winner_hit_count": winner_hits,
            "top2_hit_count": top2_hits,
            "top3_hit_count": top3_hits,
            "winner_hit_rate": round(winner_hits / total, 4) if total else 0.0,
            "top2_hit_rate": round(top2_hits / total, 4) if total else 0.0,
            "top3_hit_rate": round(top3_hits / total, 4) if total else 0.0,
            "avg_winner_odds": avg_winner_odds,
        }

    except Exception as e:
        log.error(f"learning_store: get_performance_summary failed: {e}")
        return {"ok": False, "error": "Performance summary unavailable"}


def get_stored_prediction(race_uid: str) -> dict[str, Any]:
    """
    Retrieve the most recent stored prediction for a race.

    Returns:
        Dict with snapshot metadata and runner_outputs, or error.
    """
    try:
        from db import get_db, safe_query, T

        snaps = safe_query(
            lambda: get_db()
            .table(T("prediction_snapshots"))
            .select("*")
            .eq("race_uid", race_uid)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data,
            [],
        ) or []

        if not snaps:
            return {"ok": False, "error": "No prediction found", "race_uid": race_uid}

        snap = snaps[0]
        snap_id = snap.get("prediction_snapshot_id")

        outputs = safe_query(
            lambda: get_db()
            .table(T("prediction_runner_outputs"))
            .select("*")
            .eq("prediction_snapshot_id", snap_id)
            .order("predicted_rank")
            .execute()
            .data,
            [],
        ) or []

        return {
            "ok": True,
            "race_uid": race_uid,
            "snapshot": snap,
            "runner_outputs": outputs,
        }

    except Exception as e:
        log.error(f"learning_store: get_stored_prediction failed for {race_uid}: {e}")
        return {"ok": False, "error": "Could not retrieve prediction", "race_uid": race_uid}


def get_prediction_counts() -> dict[str, int]:
    """Return counts of stored prediction snapshots and evaluated predictions."""
    try:
        from db import get_db, safe_query, T

        snap_count = safe_query(
            lambda: get_db()
            .table(T("prediction_snapshots"))
            .select("id", count="exact")
            .execute()
            .count,
            0,
        ) or 0

        eval_count = safe_query(
            lambda: get_db()
            .table(T("learning_evaluations"))
            .select("id", count="exact")
            .execute()
            .count,
            0,
        ) or 0

        return {
            "prediction_snapshots": snap_count or 0,
            "learning_evaluations": eval_count or 0,
        }
    except Exception as e:
        log.warning(f"learning_store: get_prediction_counts failed: {e}")
        return {"prediction_snapshots": 0, "learning_evaluations": 0}


def get_recent_backtest_runs(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent backtest run summaries."""
    try:
        from db import get_db, safe_query, T

        rows = safe_query(
            lambda: get_db()
            .table(T("backtest_runs"))
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data,
            [],
        ) or []
        return rows
    except Exception as e:
        log.warning(f"learning_store: get_recent_backtest_runs failed: {e}")
        return []


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _save_feature_snapshot(
    race_uid: str,
    oddspro_race_id: str,
    features: list[dict[str, Any]],
) -> str:
    """Save feature snapshot to feature_snapshots table. Returns the UUID."""
    try:
        from db import get_db, safe_query, T

        feature_snapshot_id = str(uuid.uuid4())
        row = {
            "id": feature_snapshot_id,
            "race_uid": race_uid,
            "oddspro_race_id": oddspro_race_id,
            "snapshot_date": (features[0].get("race_date") if features else None),
            "runner_count": len(features),
            "features": json.dumps(features),
            "created_at": _now(),
        }
        safe_query(
            lambda: get_db()
            .table(T("feature_snapshots"))
            .insert(row)
            .execute()
        )
        return feature_snapshot_id

    except Exception as e:
        log.warning(f"learning_store: _save_feature_snapshot failed: {e}")
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
