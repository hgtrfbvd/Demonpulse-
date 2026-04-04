"""
ai/race_shape.py - DemonPulse Race Shape Engine
=================================================
Derives race-level shape, tempo, and pace signals from stored runner features
and (when available) authoritative OddsPro sectionals.

Outputs:
  - pace_scenario       : SLOW / MODERATE / FAST / VERY_FAST
  - leader_pressure     : float 0-1 — how contested the lead is
  - likely_leader_runner_ids : list of box_nums likely to lead
  - early_speed_conflict_score : how many runners compete for the lead
  - collapse_risk       : probability the pace scenario collapses / field bunches
  - closer_advantage_score : how much closers are favoured by the shape

Rules:
  - Primarily based on authoritative OddsPro sectional data when available
  - FormFav speed-map data used only as enrichment / tiebreak, clearly flagged
  - Greyhound races use tighter collision/pressure logic
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Pace scenario boundaries — based on normalised early_speed_density (0-1)
_PACE_THRESHOLDS = {
    "VERY_FAST":  0.70,
    "FAST":       0.45,
    "MODERATE":   0.25,
    # < 0.25 → SLOW
}

# Maximum reasonable field size — used for density normalisation
_MAX_FIELD = 20


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def build_race_shape(
    race: dict[str, Any],
    runner_features: list[dict[str, Any]],
    sectional_metrics: list[dict[str, Any]] | None = None,
    formfav_speed_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Derive race shape for a single race.

    Args:
        race             : authoritative race record (OddsPro-sourced)
        runner_features  : feature rows for this race (one per active runner)
        sectional_metrics: optional list of per-runner sectional metric dicts
                           from sectionals_engine.build_runner_sectional_metrics()
        formfav_speed_map: optional FormFav speed-map enrichment dict
                           (non-authoritative; used only as tiebreak)

    Returns:
        Race shape dict with all outputs described in the module docstring.
    """
    if not runner_features:
        return _null_shape(race)

    race_uid       = race.get("race_uid") or ""
    oddspro_race_id = race.get("oddspro_race_id") or ""
    race_code       = (race.get("code") or "").upper()
    field_size      = len(runner_features)

    is_greyhound = "GREY" in race_code or race_code in ("GR", "DOG", "GREYHOUND")

    # ── Step 1: build early-speed profile per runner ──────────────
    runner_profiles = _build_runner_speed_profiles(
        runner_features, sectional_metrics, formfav_speed_map, is_greyhound
    )

    # ── Step 2: compute field-level pace density ───────────────────
    early_speeds = [p["early_speed"] for p in runner_profiles if p["early_speed"] > 0]
    early_speed_density = _compute_density(early_speeds, field_size)

    # ── Step 3: classify pace scenario ────────────────────────────
    pace_scenario = _classify_pace(early_speed_density, is_greyhound)

    # ── Step 4: identify likely leaders ───────────────────────────
    likely_leaders, leader_pressure, conflict_score = _find_leaders(
        runner_profiles, is_greyhound
    )

    # ── Step 5: collapse risk ──────────────────────────────────────
    collapse_risk = _compute_collapse_risk(
        conflict_score, early_speed_density, is_greyhound
    )

    # ── Step 6: closer advantage ──────────────────────────────────
    closer_advantage = _compute_closer_advantage(
        runner_profiles, early_speed_density, collapse_risk
    )

    shape = {
        "race_uid":                   race_uid,
        "oddspro_race_id":            oddspro_race_id,
        "field_size":                 field_size,
        "pace_scenario":              pace_scenario,
        "early_speed_density":        round(early_speed_density, 4),
        "leader_pressure":            round(leader_pressure, 4),
        "likely_leader_runner_ids":   likely_leaders,
        "early_speed_conflict_score": round(conflict_score, 4),
        "collapse_risk":              round(collapse_risk, 4),
        "closer_advantage_score":     round(closer_advantage, 4),
        "is_greyhound":               is_greyhound,
        "sectionals_used":            bool(sectional_metrics),
        "formfav_enrichment_used":    bool(formfav_speed_map),
    }
    log.debug(
        f"race_shape: {race_uid} → {pace_scenario} "
        f"density={early_speed_density:.2f} collapse={collapse_risk:.2f}"
    )
    return shape


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _build_runner_speed_profiles(
    runner_features: list[dict[str, Any]],
    sectional_metrics: list[dict[str, Any]] | None,
    formfav_speed_map: dict[str, Any] | None,
    is_greyhound: bool,
) -> list[dict[str, Any]]:
    """
    Build a consolidated speed profile for each runner.

    Priority:
      1. OddsPro sectional early_speed_score (authoritative)
      2. Market implied_prob as proxy (if no sectionals)
      3. FormFav earlySpeedIndex as tiebreak enrichment (non-authoritative)
    """
    # Index sectionals by box_num for fast lookup
    sec_index: dict[int, dict[str, Any]] = {}
    for sm in (sectional_metrics or []):
        bn = sm.get("box_num")
        if bn is not None:
            sec_index[bn] = sm

    # Index FormFav enrichment by runner name / box
    ff_index: dict[str, dict[str, Any]] = {}
    if formfav_speed_map:
        for key, val in formfav_speed_map.items():
            ff_index[str(key).upper()] = val

    profiles: list[dict[str, Any]] = []
    for feat in runner_features:
        box_num     = feat.get("box_num") or 0
        runner_name = (feat.get("runner_name") or "").upper()

        # --- Authoritative early speed ---
        sec = sec_index.get(box_num) or {}
        auth_early = float(sec.get("early_speed_score") or 0.0)

        # --- FormFav enrichment (tiebreak only) ---
        ff_data = ff_index.get(runner_name) or ff_index.get(str(box_num)) or {}
        ff_early = _safe_float(
            ff_data.get("earlySpeedIndex") or ff_data.get("early_speed_index"), 0.0
        )
        enrichment_used = bool(ff_data)

        # Combine: authoritative dominates; FormFav adds tiny tiebreak weight
        if auth_early > 0:
            early_speed = auth_early + ff_early * 0.05
        elif feat.get("implied_prob"):
            # Fallback: use implied_prob as crude speed proxy
            early_speed = float(feat.get("implied_prob") or 0.0) * 0.5
            early_speed += ff_early * 0.1
        else:
            early_speed = ff_early * 0.1

        # Settling position enrichment (FormFav)
        settling = _safe_float(ff_data.get("settlingPosition"), 0.0)

        profiles.append({
            "box_num":         box_num,
            "runner_name":     runner_name,
            "early_speed":     round(min(early_speed, 1.0), 4),
            "closing_delta":   float(sec.get("closing_delta") or 0.0),
            "fatigue_index":   float(sec.get("fatigue_index") or 1.0),
            "settling":        settling,
            "enrichment_used": enrichment_used,
        })

    return profiles


def _compute_density(early_speeds: list[float], field_size: int) -> float:
    """
    Compute 0-1 density of early-speed runners.
    High density = many runners share pace duties = fast/contested pace.
    """
    if not early_speeds or field_size == 0:
        return 0.0
    threshold = 0.4   # runners above this are considered "pace runners"
    pace_count = sum(1 for s in early_speeds if s > threshold)
    density = pace_count / max(field_size, 1)
    return round(min(density, 1.0), 4)


def _classify_pace(early_speed_density: float, is_greyhound: bool) -> str:
    """Classify pace scenario from density score."""
    # Greyhound races are generally faster — shift thresholds slightly
    if is_greyhound:
        if early_speed_density >= 0.65:
            return "VERY_FAST"
        if early_speed_density >= 0.40:
            return "FAST"
        if early_speed_density >= 0.20:
            return "MODERATE"
        return "SLOW"
    if early_speed_density >= _PACE_THRESHOLDS["VERY_FAST"]:
        return "VERY_FAST"
    if early_speed_density >= _PACE_THRESHOLDS["FAST"]:
        return "FAST"
    if early_speed_density >= _PACE_THRESHOLDS["MODERATE"]:
        return "MODERATE"
    return "SLOW"


def _find_leaders(
    profiles: list[dict[str, Any]],
    is_greyhound: bool,
) -> tuple[list[int], float, float]:
    """
    Identify likely leaders and compute leader pressure and conflict scores.

    Returns:
        (likely_leader_box_nums, leader_pressure_0_1, conflict_score_0_1)
    """
    if not profiles:
        return [], 0.0, 0.0

    sorted_profiles = sorted(profiles, key=lambda p: p["early_speed"], reverse=True)
    max_speed = sorted_profiles[0]["early_speed"]
    if max_speed <= 0:
        return [], 0.0, 0.0

    # Runners within 15% of the leader's speed are considered pace contenders
    threshold = max_speed * 0.85
    contenders = [p for p in sorted_profiles if p["early_speed"] >= threshold]

    likely_leaders = [p["box_num"] for p in contenders[:3] if p["box_num"]]

    # Leader pressure: top-2 speed similarity
    if len(sorted_profiles) >= 2 and sorted_profiles[1]["early_speed"] > 0:
        leader_pressure = sorted_profiles[1]["early_speed"] / max_speed
    else:
        leader_pressure = 0.0

    # Conflict score: normalised number of contenders
    n = min(len(profiles), _MAX_FIELD)
    conflict_score = len(contenders) / max(n, 1)

    # Greyhound penalty: box 1 / 2 pressure in crowded inside rail
    if is_greyhound:
        inside_count = sum(
            1 for p in contenders if p.get("box_num") in (1, 2, 3)
        )
        conflict_score = min(conflict_score + inside_count * 0.05, 1.0)

    return likely_leaders, round(leader_pressure, 4), round(conflict_score, 4)


def _compute_collapse_risk(
    conflict_score: float,
    early_speed_density: float,
    is_greyhound: bool,
) -> float:
    """
    Estimate the probability that the race pace collapses
    (multiple leaders burn out, field bunches).
    """
    base = conflict_score * 0.6 + early_speed_density * 0.4
    if is_greyhound:
        base *= 1.1   # greyhounds more susceptible to bunching/checking
    return round(min(base, 1.0), 4)


def _compute_closer_advantage(
    profiles: list[dict[str, Any]],
    early_speed_density: float,
    collapse_risk: float,
) -> float:
    """
    Score how much closers benefit from the current race shape.
    High pace density + high collapse risk = big closer advantage.
    """
    # Count runners with negative closing_delta (they finish faster than they start)
    closers = [p for p in profiles if p.get("closing_delta", 0.0) > 0]
    closer_ratio = len(closers) / max(len(profiles), 1)
    advantage = (early_speed_density * 0.4 + collapse_risk * 0.4 + closer_ratio * 0.2)
    return round(min(advantage, 1.0), 4)


def _null_shape(race: dict[str, Any]) -> dict[str, Any]:
    """Return a null-safe race shape when there are no runners."""
    return {
        "race_uid":                   race.get("race_uid") or "",
        "oddspro_race_id":            race.get("oddspro_race_id") or "",
        "field_size":                 0,
        "pace_scenario":              "UNKNOWN",
        "early_speed_density":        0.0,
        "leader_pressure":            0.0,
        "likely_leader_runner_ids":   [],
        "early_speed_conflict_score": 0.0,
        "collapse_risk":              0.0,
        "closer_advantage_score":     0.0,
        "is_greyhound":               False,
        "sectionals_used":            False,
        "formfav_enrichment_used":    False,
    }


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
