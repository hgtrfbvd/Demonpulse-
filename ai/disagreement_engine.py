"""
ai/disagreement_engine.py - DemonPulse Disagreement Engine
===========================================================
Compares the DemonPulse model predictions against FormFav model predictions.

This engine is purely analytical — it never changes predictions, never writes
to authoritative tables, and never makes FormFav authoritative.

Purpose:
  - Quantify how much our model disagrees with FormFav
  - Flag high-disagreement races for closer inspection
  - Track disagreement over time for model evaluation

Architecture rules:
  - OddsPro / DemonPulse predictions are authoritative
  - FormFav predictions are reference-only
  - Disagreement is a SIGNAL not a correction
  - High disagreement may indicate an edge (not necessarily an error)
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Threshold: flag as high disagreement if top pick rank difference >= this value
_FLAG_RANK_DIFF_THRESHOLD = 3


def compute_disagreement(
    your_preds: list[dict[str, Any]],
    formfav_preds: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compare DemonPulse model predictions against FormFav model predictions.

    Args:
        your_preds   : list of runner prediction dicts from DemonPulse model,
                       each with 'runner_name' and 'predicted_rank'
        formfav_preds: list of runner prediction dicts from FormFav,
                       each with 'runner_name' and 'predicted_rank'

    Returns:
        dict with:
          - disagreement_score  : float 0-1, 0 = perfect agreement, 1 = max disagreement
          - rank_diff_top_pick  : int, rank difference for the DemonPulse top pick
                                  (positive = FormFav ranks it lower)
          - avg_rank_diff       : float, mean absolute rank difference across matched runners
          - flagged             : bool, True if high disagreement (|rank_diff_top_pick| >= 3)
          - matched_runners     : int, number of runners found in both prediction lists
          - your_top_pick       : str, DemonPulse top-ranked runner name
          - formfav_top_pick    : str, FormFav top-ranked runner name
    """
    if not your_preds or not formfav_preds:
        return _empty_disagreement(reason="missing_predictions")

    # Build lookup: runner_name → predicted_rank
    your_ranks = _build_rank_map(your_preds)
    ff_ranks = _build_rank_map(formfav_preds)

    if not your_ranks or not ff_ranks:
        return _empty_disagreement(reason="empty_rank_maps")

    # Top picks
    your_top = _top_pick(your_preds)
    ff_top = _top_pick(formfav_preds)

    # Rank of DemonPulse top pick in FormFav rankings
    your_top_in_ff = ff_ranks.get(your_top, None)
    rank_diff_top_pick = (your_top_in_ff - 1) if your_top_in_ff is not None else 0

    # Average rank difference across all matched runners
    matched_runners = 0
    total_rank_diff = 0.0
    field_size = max(len(your_preds), len(formfav_preds))

    for runner_name, your_rank in your_ranks.items():
        ff_rank = ff_ranks.get(runner_name)
        if ff_rank is not None:
            total_rank_diff += abs(your_rank - ff_rank)
            matched_runners += 1

    avg_rank_diff = (
        round(total_rank_diff / matched_runners, 4) if matched_runners > 0 else 0.0
    )

    # Normalise disagreement score to 0-1
    # Max possible average rank diff for N runners ≈ N/2
    max_possible_diff = max(field_size / 2.0, 1.0)
    disagreement_score = round(min(avg_rank_diff / max_possible_diff, 1.0), 4)

    flagged = abs(rank_diff_top_pick) >= _FLAG_RANK_DIFF_THRESHOLD

    result = {
        "disagreement_score": disagreement_score,
        "rank_diff_top_pick": rank_diff_top_pick,
        "avg_rank_diff": avg_rank_diff,
        "flagged": flagged,
        "matched_runners": matched_runners,
        "your_top_pick": your_top,
        "formfav_top_pick": ff_top,
    }

    if flagged:
        log.info(
            f"disagreement_engine: HIGH DISAGREEMENT flagged — "
            f"your_top={your_top!r} ff_top={ff_top!r} "
            f"rank_diff={rank_diff_top_pick} score={disagreement_score}"
        )

    return result


def compare_predictions(
    race_uid: str,
    your_preds: list[dict[str, Any]],
    formfav_preds: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Full comparison of DemonPulse vs FormFav predictions for a race.

    Args:
        race_uid      : race identifier for logging/tracing
        your_preds    : DemonPulse runner predictions
        formfav_preds : FormFav runner predictions

    Returns:
        dict with:
          - race_uid
          - your_predictions    : full DemonPulse prediction list
          - formfav_predictions : FormFav prediction list (enrichment only)
          - disagreement        : output of compute_disagreement()
          - source_note         : reminder that FormFav is enrichment only
    """
    disagreement = compute_disagreement(your_preds, formfav_preds)

    return {
        "race_uid": race_uid,
        "your_predictions": your_preds,
        "formfav_predictions": formfav_preds,
        "disagreement": disagreement,
        "source_note": (
            "DemonPulse predictions are authoritative (OddsPro-sourced). "
            "FormFav predictions are enrichment-only and never override results."
        ),
    }


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _build_rank_map(preds: list[dict[str, Any]]) -> dict[str, int]:
    """Build a runner_name → predicted_rank lookup from a prediction list."""
    result: dict[str, int] = {}
    for p in preds:
        name = (p.get("runner_name") or "").strip()
        rank = p.get("predicted_rank")
        if name and rank is not None:
            result[name] = int(rank)
    return result


def _top_pick(preds: list[dict[str, Any]]) -> str:
    """Return the runner_name of the top-ranked prediction (predicted_rank == 1)."""
    for p in sorted(preds, key=lambda x: x.get("predicted_rank") or 9999):
        name = (p.get("runner_name") or "").strip()
        if name:
            return name
    return ""


def _empty_disagreement(reason: str = "") -> dict[str, Any]:
    """Return a zeroed-out disagreement dict for cases where comparison is impossible."""
    return {
        "disagreement_score": 0.0,
        "rank_diff_top_pick": 0,
        "avg_rank_diff": 0.0,
        "flagged": False,
        "matched_runners": 0,
        "your_top_pick": "",
        "formfav_top_pick": "",
        "reason": reason,
    }
