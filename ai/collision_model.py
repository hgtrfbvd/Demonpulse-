"""
ai/collision_model.py - DemonPulse Greyhound Collision / Traffic Model
=======================================================================
Models greyhound traffic and interference risk based on box position,
early speed, and adjacent runner density.

IMPORTANT: This model applies ONLY to greyhound races.
           It must NOT be applied to horse or harness races unless explicitly
           extended to support those race types.

Outputs per runner:
  - collision_risk_score     : float 0-1 — overall collision / interference risk
  - interference_probability : float 0-1 — probability of significant interference
  - boxed_runner_penalty     : float 0-1 — penalty applied to prediction score
  - clean_run_probability    : float 0-1 — probability of a clear, unimpeded run

Inputs:
  - box positions (1-based)
  - early speed scores (from sectionals or market proxy)
  - adjacent runner early-speed density
  - likely early pressure from race shape

References:
  - Standard greyhound box bias research (inside boxes slightly favoured on
    most Australian ovals; wide boxes more exposed to traffic from inside rushers)
  - Penalty weights are deterministic and conservative until a trained model
    replaces them.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Base collision risk by box number (1-indexed, up to 10 boxes).
# Derived from typical Australian greyhound oval track geometry.
# Box 1 has highest base risk (crowded inside rail).
# Boxes 4-6 are lowest risk. Wide boxes (7+) have moderate risk from
# runners crossing from inside.
_BOX_BASE_RISK: dict[int, float] = {
    1:  0.38,
    2:  0.30,
    3:  0.22,
    4:  0.15,
    5:  0.12,
    6:  0.14,
    7:  0.20,
    8:  0.26,
    9:  0.30,
    10: 0.34,
}
_DEFAULT_BOX_RISK = 0.25   # fallback for boxes outside 1-10

# When two adjacent runners both have high early speed, collision risk increases
_ADJACENT_CONFLICT_MULTIPLIER = 1.35


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def is_greyhound_race(race_code: str) -> bool:
    """Return True if the race code indicates a greyhound race."""
    code = (race_code or "").upper()
    return "GREY" in code or code in ("GR", "DOG", "GREYHOUND", "DOGS")


def build_collision_metrics(
    race: dict[str, Any],
    runner_features: list[dict[str, Any]],
    sectional_metrics: list[dict[str, Any]] | None = None,
    race_shape: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Compute greyhound collision metrics for all active runners in a race.

    Must only be called when is_greyhound_race(race.get("code")) is True.

    Args:
        race             : authoritative race record
        runner_features  : feature rows (one per active runner)
        sectional_metrics: optional per-runner sectional metrics
                           from sectionals_engine
        race_shape       : optional race shape dict from race_shape.build_race_shape()

    Returns:
        List of collision metric dicts, one per runner, in box_num order.
        Returns empty list if race is not greyhound.
    """
    race_code = race.get("code") or ""
    if not is_greyhound_race(race_code):
        log.debug(
            f"collision_model: skipping non-greyhound race "
            f"{race.get('race_uid')} (code={race_code})"
        )
        return []

    if not runner_features:
        return []

    race_uid        = race.get("race_uid") or ""
    oddspro_race_id = race.get("oddspro_race_id") or ""

    # Build early-speed index per box_num
    speed_by_box = _build_speed_index(runner_features, sectional_metrics)

    # Race-level pressure from race shape
    leader_pressure  = float((race_shape or {}).get("leader_pressure", 0.3))
    conflict_score   = float((race_shape or {}).get("early_speed_conflict_score", 0.3))
    pace_scenario    = (race_shape or {}).get("pace_scenario", "MODERATE")
    pace_multiplier  = _pace_multiplier(pace_scenario)

    results: list[dict[str, Any]] = []
    for feat in sorted(runner_features, key=lambda f: f.get("box_num") or 99):
        box_num     = feat.get("box_num") or 0
        runner_name = feat.get("runner_name") or ""

        base_risk = _BOX_BASE_RISK.get(box_num, _DEFAULT_BOX_RISK)

        # Adjacent conflict: check left and right neighbours
        adj_conflict = _adjacent_conflict(box_num, speed_by_box)

        # Runner's own early speed (higher early speed = more likely to be in traffic)
        own_speed = speed_by_box.get(box_num, 0.0)
        speed_involvement = own_speed * 0.3   # being fast puts you in traffic too

        # Combine
        raw_risk = (
            base_risk
            + adj_conflict * 0.25
            + speed_involvement * 0.15
            + leader_pressure * 0.10
            + conflict_score * 0.10
        ) * pace_multiplier

        collision_risk_score     = round(min(raw_risk, 1.0), 4)
        interference_probability = round(min(raw_risk * 0.85, 1.0), 4)
        boxed_runner_penalty     = round(min(raw_risk * 0.60, 0.40), 4)   # capped at 0.40
        clean_run_probability    = round(max(1.0 - raw_risk * 0.90, 0.05), 4)

        results.append({
            "race_uid":               race_uid,
            "oddspro_race_id":        oddspro_race_id,
            "box_num":                box_num,
            "runner_name":            runner_name,
            "collision_risk_score":   collision_risk_score,
            "interference_probability": interference_probability,
            "boxed_runner_penalty":   boxed_runner_penalty,
            "clean_run_probability":  clean_run_probability,
        })

    return results


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _build_speed_index(
    runner_features: list[dict[str, Any]],
    sectional_metrics: list[dict[str, Any]] | None,
) -> dict[int, float]:
    """
    Build {box_num: early_speed_score} mapping.
    Prefers OddsPro sectional early_speed_score; falls back to implied_prob.
    """
    # Prefer sectionals
    sec_index: dict[int, float] = {}
    for sm in (sectional_metrics or []):
        bn = sm.get("box_num")
        ess = _safe_float(sm.get("early_speed_score"))
        if bn is not None and ess > 0:
            sec_index[bn] = ess

    speed_index: dict[int, float] = {}
    for feat in runner_features:
        bn = feat.get("box_num") or 0
        if bn in sec_index:
            speed_index[bn] = sec_index[bn]
        else:
            # Fallback: implied_prob as crude proxy for early aggression
            speed_index[bn] = _safe_float(feat.get("implied_prob")) * 0.5

    return speed_index


def _adjacent_conflict(box_num: int, speed_by_box: dict[int, float]) -> float:
    """
    Measure conflict with immediately adjacent boxes.
    Returns 0-1 conflict score.
    """
    left_box  = box_num - 1
    right_box = box_num + 1
    own_speed = speed_by_box.get(box_num, 0.0)

    conflicts: list[float] = []
    for adj in (left_box, right_box):
        adj_speed = speed_by_box.get(adj, 0.0)
        if adj_speed > 0 and own_speed > 0:
            # Two fast adjacent runners → high conflict
            conflict = (own_speed + adj_speed) / 2.0
            conflicts.append(conflict)

    if not conflicts:
        return 0.0
    raw = sum(conflicts) / len(conflicts)
    # Apply multiplier when both adjacent runners are fast
    if len(conflicts) == 2 and all(c > 0.3 for c in conflicts):
        raw *= _ADJACENT_CONFLICT_MULTIPLIER
    return round(min(raw, 1.0), 4)


def _pace_multiplier(pace_scenario: str) -> float:
    """Scale collision risk based on pace scenario."""
    return {
        "VERY_FAST":  1.30,
        "FAST":       1.15,
        "MODERATE":   1.00,
        "SLOW":       0.85,
        "UNKNOWN":    1.00,
    }.get(pace_scenario, 1.00)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
