"""
ai/feature_builder.py - DemonPulse Feature Builder
====================================================
Builds structured model-ready feature snapshots from stored validated races.

Feature source rules:
  - Uses only stored validated race data (OddsPro-confirmed authoritative storage)
  - FormFav enrichment may be included only when clearly flagged as non-authoritative
  - Never uses raw connector payloads directly if validated data already exists
  - Preserves exact lineage to race_uid and oddspro_race_id

Output:
  - Clean feature dicts, one row per non-scratched runner
  - Stable feature names, serializable output
  - Exact race_uid / oddspro_race_id lineage

Feature groups:
  A. race_metadata       : track, code, distance, grade, condition, field_size, jump time
  B. runner_features     : box/barrier, runner_num, name, trainer, jockey
  C. market_features     : win_odds, implied_prob, odds_rank, relative_to_fav, spread,
                           opening_odds, current_odds, market_movement_pct,
                           market_overround, implied_probability (OddsPro authoritative)
  D. sectional_features  : early_speed_score, late_speed_score, closing_delta,
                           fatigue_index, acceleration_index,
                           sectional_consistency_score (OddsPro authoritative)
  E. race_shape_features : pace_scenario, leader_pressure, early_speed_density,
                           collapse_risk, race_shape_fit
  F. collision_features  : collision_risk_score, interference_probability,
                           boxed_runner_penalty (greyhounds only)
  G. enrichment_features : FormFav-sourced fields clearly flagged as non-authoritative
                           (has_enrichment, running_style, earlySpeedIndex, etc.)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


def build_race_features(
    race: dict[str, Any],
    runners: list[dict[str, Any]],
    enrichment: dict[str, Any] | None = None,
    sectional_metrics: list[dict[str, Any]] | None = None,
    race_shape: dict[str, Any] | None = None,
    collision_metrics: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Build a feature row for every active runner in a race.

    Args:
        race               : authoritative race record from today_races (OddsPro-sourced)
        runners            : runner records from today_runners for this race
        enrichment         : optional FormFav-sourced enrichment keyed by runner name
                             (non-authoritative; flagged in output via has_enrichment=1)
        sectional_metrics  : optional list of per-runner sectional metric dicts
                             from ai.sectionals_engine (OddsPro authoritative)
        race_shape         : optional race shape dict from ai.race_shape
        collision_metrics  : optional list of per-runner collision metric dicts
                             from ai.collision_model (greyhounds only)

    Returns:
        List of feature dicts, one per non-scratched runner, with full lineage.
    """
    if not race or not runners:
        return []

    active_runners = [r for r in runners if not r.get("scratched")]
    if not active_runners:
        return []

    race_meta = _extract_race_meta(race)
    race_meta["field_size"] = len(active_runners)

    odds_map = {
        r.get("box_num"): _safe_float(r.get("price"))
        for r in active_runners
    }
    market_stats = _compute_market_stats(odds_map)

    # Index per-runner extras by box_num
    sec_index: dict[int, dict[str, Any]] = {}
    for sm in (sectional_metrics or []):
        bn = sm.get("box_num")
        if bn is not None:
            sec_index[bn] = sm

    col_index: dict[int, dict[str, Any]] = {}
    for cm in (collision_metrics or []):
        bn = cm.get("box_num")
        if bn is not None:
            col_index[bn] = cm

    features: list[dict[str, Any]] = []
    for runner in active_runners:
        box_num = runner.get("box_num") or 0
        runner_enrichment = None
        if enrichment:
            runner_enrichment = (
                enrichment.get(runner.get("name") or "")
                or enrichment.get(str(box_num or ""))
            )
        row = _build_runner_row(
            race_meta=race_meta,
            runner=runner,
            market_stats=market_stats,
            enrichment=runner_enrichment,
            sectionals=sec_index.get(box_num),
            race_shape=race_shape,
            collision=col_index.get(box_num),
        )
        features.append(row)

    return features


def build_runner_features(
    race: dict[str, Any],
    runner: dict[str, Any],
    all_runners: list[dict[str, Any]],
    enrichment: dict[str, Any] | None = None,
    sectional_metrics: list[dict[str, Any]] | None = None,
    race_shape: dict[str, Any] | None = None,
    collision_metrics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Build features for a single runner within a race context.

    all_runners is needed to compute market-level features such as odds_rank
    and relative_to_fav correctly across the full field.
    """
    runner_name = runner.get("name") or ""
    box_num = runner.get("box_num")
    enrichment_map = {runner_name: enrichment} if enrichment else None
    rows = build_race_features(
        race, all_runners, enrichment_map,
        sectional_metrics=sectional_metrics,
        race_shape=race_shape,
        collision_metrics=collision_metrics,
    )
    for row in rows:
        if row.get("box_num") == box_num:
            return row
    return {}


def batch_build_features(
    races_with_runners: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    enrichment_map: dict[str, dict] | None = None,
    sectionals_map: dict[str, list] | None = None,
    race_shape_map: dict[str, dict] | None = None,
    collision_map: dict[str, list] | None = None,
) -> list[dict[str, Any]]:
    """
    Build features for a batch of historical races.

    Args:
        races_with_runners : list of (race_dict, runners_list) tuples
        enrichment_map     : optional dict keyed by race_uid mapping to per-runner
                             enrichment dicts (non-authoritative FormFav data)
        sectionals_map     : optional dict keyed by race_uid → list of sectional
                             metric dicts (OddsPro authoritative)
        race_shape_map     : optional dict keyed by race_uid → race shape dict
        collision_map      : optional dict keyed by race_uid → list of collision
                             metric dicts (greyhounds only)

    Returns:
        Flat list of runner-level feature rows across all races with lineage.
    """
    all_features: list[dict[str, Any]] = []
    for race, runners in races_with_runners:
        race_uid = race.get("race_uid") or ""
        enrichment      = (enrichment_map or {}).get(race_uid)
        sec_metrics     = (sectionals_map or {}).get(race_uid)
        rshape          = (race_shape_map or {}).get(race_uid)
        col_metrics     = (collision_map or {}).get(race_uid)
        rows = build_race_features(
            race, runners, enrichment,
            sectional_metrics=sec_metrics,
            race_shape=rshape,
            collision_metrics=col_metrics,
        )
        all_features.extend(rows)
    return all_features


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _extract_race_meta(race: dict[str, Any]) -> dict[str, Any]:
    """Extract stable race metadata fields."""
    jump_hour, jump_minute, jump_dow = _parse_jump_time(race.get("jump_time"))
    return {
        "race_uid": race.get("race_uid") or "",
        "oddspro_race_id": race.get("oddspro_race_id") or "",
        "race_date": str(race.get("date") or ""),
        "track": (race.get("track") or "").upper(),
        "code": (race.get("code") or "").upper(),
        "distance_m": _parse_distance(race.get("distance")),
        "grade": (race.get("grade") or "").upper(),
        "condition": (race.get("condition") or "").upper(),
        "jump_hour": jump_hour,
        "jump_minute": jump_minute,
        "jump_day_of_week": jump_dow,
        "field_size": 0,  # overwritten by caller after computing active count
        # FormFav race-level paceScenario (non-authoritative; empty when no overlay)
        "formfav_pace_scenario": str(race.get("formfav_pace_scenario") or ""),
    }


def _build_runner_row(
    race_meta: dict[str, Any],
    runner: dict[str, Any],
    market_stats: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
    sectionals: dict[str, Any] | None = None,
    race_shape: dict[str, Any] | None = None,
    collision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete feature row for one runner."""
    box_num = runner.get("box_num") or 0
    win_odds = _safe_float(runner.get("price"))
    implied_prob = (1.0 / win_odds) if win_odds and win_odds > 1.0 else 0.0
    fav_odds = market_stats.get("fav_odds") or win_odds or 1.0

    # Opening odds (if available from runner record)
    opening_odds = _safe_float(runner.get("opening_price") or runner.get("open_price"))
    current_odds = win_odds
    market_movement_pct = 0.0
    if opening_odds and opening_odds > 1.0 and current_odds and current_odds > 1.0:
        market_movement_pct = round((opening_odds - current_odds) / opening_odds * 100, 2)

    # Race shape features
    rs = race_shape or {}
    pace_scenario      = rs.get("pace_scenario") or "UNKNOWN"
    leader_pressure    = _safe_float(rs.get("leader_pressure"))
    early_speed_density = _safe_float(rs.get("early_speed_density"))
    collapse_risk      = _safe_float(rs.get("collapse_risk"))
    likely_leaders     = rs.get("likely_leader_runner_ids") or []
    race_shape_fit     = 1.0 if box_num in likely_leaders else (
        _safe_float(rs.get("closer_advantage_score")) * 0.6
        if implied_prob < 0.25 else 0.3
    )

    # Sectional features (OddsPro authoritative)
    sec = sectionals or {}
    early_speed_score           = _safe_float(sec.get("early_speed_score"))
    late_speed_score            = _safe_float(sec.get("late_speed_score"))
    closing_delta               = _safe_float(sec.get("closing_delta"))
    fatigue_index               = _safe_float(sec.get("fatigue_index"), 1.0)
    acceleration_index          = _safe_float(sec.get("acceleration_index"))
    sectional_consistency_score = _safe_float(sec.get("sectional_consistency_score"), 0.5)
    has_sectionals              = 1 if sec else 0

    # Collision features (greyhounds only — zero for others)
    col = collision or {}
    collision_risk_score     = _safe_float(col.get("collision_risk_score"))
    interference_probability = _safe_float(col.get("interference_probability"))
    boxed_runner_penalty     = _safe_float(col.get("boxed_runner_penalty"))

    row: dict[str, Any] = {
        # --- Lineage ---
        "race_uid": race_meta["race_uid"],
        "oddspro_race_id": race_meta["oddspro_race_id"],
        "race_date": race_meta["race_date"],
        # --- Race metadata ---
        "track": race_meta["track"],
        "code": race_meta["code"],
        "distance_m": race_meta["distance_m"],
        "grade": race_meta["grade"],
        "condition": race_meta["condition"],
        "field_size": race_meta["field_size"],
        "jump_hour": race_meta["jump_hour"],
        "jump_minute": race_meta["jump_minute"],
        "jump_day_of_week": race_meta["jump_day_of_week"],
        # --- Runner features ---
        "box_num": box_num,
        "runner_num": runner.get("number") or box_num,
        "barrier": runner.get("barrier") or box_num,
        "runner_name": runner.get("name") or "",
        "trainer": runner.get("trainer") or "",
        "jockey": runner.get("jockey") or runner.get("driver") or "",
        "is_scratched": 0,
        # --- Market features (OddsPro authoritative) ---
        "win_odds": win_odds,
        "opening_odds": opening_odds,
        "current_odds": current_odds,
        "market_movement_pct": market_movement_pct,
        "implied_prob": round(implied_prob, 6),
        "implied_probability": round(implied_prob, 6),   # alias for clarity
        "odds_rank": market_stats.get("odds_ranks", {}).get(box_num, 0),
        "relative_to_fav": round(win_odds / fav_odds, 4) if (win_odds and fav_odds) else 0.0,
        "market_spread": market_stats.get("market_spread", 0.0),
        "overround": market_stats.get("overround", 0.0),
        "market_overround": market_stats.get("overround", 0.0),   # alias
        # --- Sectional features (OddsPro authoritative) ---
        "has_sectionals": has_sectionals,
        "early_speed_score": early_speed_score,
        "late_speed_score": late_speed_score,
        "closing_delta": closing_delta,
        "fatigue_index": fatigue_index,
        "acceleration_index": acceleration_index,
        "sectional_consistency_score": sectional_consistency_score,
        # --- Race shape features ---
        "pace_scenario": pace_scenario,
        "leader_pressure": round(leader_pressure, 4),
        "early_speed_density": round(early_speed_density, 4),
        "collapse_risk": round(collapse_risk, 4),
        "race_shape_fit": round(race_shape_fit, 4),
        # --- Greyhound collision features ---
        "collision_risk_score": collision_risk_score,
        "interference_probability": interference_probability,
        "boxed_runner_penalty": boxed_runner_penalty,
        # --- Enrichment (FormFav — non-authoritative) ---
        "enrichment_win_pct": 0.0,
        "enrichment_place_pct": 0.0,
        "enrichment_track_win_pct": 0.0,
        "enrichment_distance_win_pct": 0.0,
        "enrichment_recent_form": "",
        "enrichment_speed_map": {},
        "enrichment_running_style": "",
        "enrichment_early_speed_index": 0.0,
        "enrichment_settling_position": 0.0,
        "enrichment_pace_scenario": "",
        "enrichment_class_profile": "",
        "enrichment_race_class_fit": 0.0,
        "enrichment_decorators": [],
        "enrichment_form_badges": "",
        "enrichment_jockey_stats": "",
        "has_enrichment": 0,
    }

    if enrichment:
        row["enrichment_win_pct"]            = _safe_float(enrichment.get("win_pct"), 0.0)
        row["enrichment_place_pct"]          = _safe_float(enrichment.get("place_pct"), 0.0)
        row["enrichment_track_win_pct"]      = _safe_float(enrichment.get("track_win_pct"), 0.0)
        row["enrichment_distance_win_pct"]   = _safe_float(enrichment.get("distance_win_pct"), 0.0)
        row["enrichment_recent_form"]        = str(enrichment.get("form_string") or "")
        # speedMap stored as full object {runningStyle, earlySpeedIndex, settlingPosition}
        speed_map = enrichment.get("speedMap") or {}
        row["enrichment_speed_map"]          = speed_map if isinstance(speed_map, dict) else {}
        row["enrichment_running_style"]      = str(speed_map.get("runningStyle") or enrichment.get("running_style") or "")
        row["enrichment_early_speed_index"]  = _safe_float(speed_map.get("earlySpeedIndex") or enrichment.get("earlySpeedIndex"), 0.0)
        row["enrichment_settling_position"]  = _safe_float(speed_map.get("settlingPosition") or enrichment.get("settlingPosition"), 0.0)
        # paceScenario is race-level; read from race_meta (set by formfav_overlay)
        row["enrichment_pace_scenario"]      = race_meta.get("formfav_pace_scenario") or ""
        # class_profile and race_class_fit stored separately
        row["enrichment_class_profile"]      = str(enrichment.get("classProfile") or "")
        row["enrichment_race_class_fit"]     = _safe_float(enrichment.get("raceClassFit"), 0.0)
        # decorators stored as full array
        decorators = enrichment.get("decorators") or []
        row["enrichment_decorators"]         = decorators if isinstance(decorators, list) else []
        row["enrichment_form_badges"]        = str(enrichment.get("form_badges") or "")
        row["enrichment_jockey_stats"]       = str(enrichment.get("jockey_stats") or "")
        row["has_enrichment"] = 1

    return row


def _compute_market_stats(odds_map: dict[int, float]) -> dict[str, Any]:
    """
    Compute market-level stats: favourite, overround, odds ranks, spread.

    Args:
        odds_map: {box_num: win_odds} dict — may include None values

    Returns:
        Dict with fav_odds, overround, market_spread, odds_ranks
    """
    valid_odds = {
        box: odds for box, odds in odds_map.items()
        if odds and odds > 1.0
    }
    if not valid_odds:
        return {
            "fav_odds": None,
            "overround": 0.0,
            "market_spread": 0.0,
            "odds_ranks": {},
        }

    fav_box = min(valid_odds, key=lambda b: valid_odds[b])
    fav_odds = valid_odds[fav_box]
    implied_probs = [1.0 / o for o in valid_odds.values()]
    overround = sum(implied_probs)
    spread = max(valid_odds.values()) - min(valid_odds.values())

    # Rank ascending by odds: rank 1 = favourite (lowest odds)
    sorted_boxes = sorted(valid_odds, key=lambda b: valid_odds[b])
    odds_ranks = {box: rank + 1 for rank, box in enumerate(sorted_boxes)}

    return {
        "fav_odds": fav_odds,
        "overround": round(overround, 4),
        "market_spread": round(spread, 2),
        "odds_ranks": odds_ranks,
    }


def _parse_jump_time(jump_time_str: str | None) -> tuple[int, int, int]:
    """
    Parse jump time string to (hour, minute, day_of_week).
    Tries ISO format first, then HH:MM. Returns (0, 0, -1) on failure.
    """
    if not jump_time_str:
        return 0, 0, -1
    try:
        dt = datetime.fromisoformat(jump_time_str.replace("Z", "+00:00"))
        return dt.hour, dt.minute, dt.weekday()
    except Exception:
        pass
    try:
        parts = str(jump_time_str).strip().split(":")
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1]), -1
    except Exception:
        pass
    return 0, 0, -1


def _parse_distance(distance_val: Any) -> int:
    """Parse distance to integer metres."""
    if isinstance(distance_val, int):
        return distance_val
    if isinstance(distance_val, float):
        return int(distance_val)
    s = (
        str(distance_val or "")
        .strip()
        .lower()
        .replace("m", "")
        .replace("metres", "")
        .replace("meters", "")
        .strip()
    )
    try:
        return int(float(s))
    except Exception:
        return 0


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safe float conversion with a default."""
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
