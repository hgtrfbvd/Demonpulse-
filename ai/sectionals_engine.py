"""
ai/sectionals_engine.py - DemonPulse Sectionals Engine
========================================================
Extract and normalize authoritative sectionals/timing metrics from
OddsPro race and result payloads.

Rules:
  - ONLY OddsPro data is used — no FormFav sectionals
  - Raw sectionals are preserved separately from derived metrics
  - race_uid and oddspro_race_id lineage is always attached
  - Missing sectionals produce null-safe zero-valued metrics, never fabricated values

Per-runner derived outputs:
  - early_split_rank            : rank in field by early section time (1 = fastest)
  - early_speed_score           : normalised 0-1 score for early speed
  - mid_race_change             : speed change from early to mid section
  - late_speed_score            : normalised 0-1 score for late/closing speed
  - closing_delta               : improvement (positive) or fade (negative) over last section
  - fatigue_index               : ratio of late speed to early speed (>1 = strong finisher)
  - acceleration_index          : peak mid-to-late acceleration relative to field average
  - sectional_consistency_score : how consistent speed was across all available sections
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Keys we recognise in OddsPro payloads for section times (seconds).
# Prefer explicit keys; fall back to positional arrays if present.
_EARLY_KEYS = ("split_1", "sectional_1", "early_split", "section_1", "s1")
_MID_KEYS   = ("split_2", "sectional_2", "mid_split",   "section_2", "s2")
_LATE_KEYS  = ("split_3", "sectional_3", "late_split",  "section_3", "s3",
               "finishing_split", "closing_split")


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def extract_sectionals_from_race_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Extract raw sectional data from an OddsPro live-race payload.

    These are PRE-RACE sectionals (from prior runs / form data).
    source_type is set to "pre_race" to distinguish from post-result data.

    Args:
        payload: raw OddsPro race payload (from /api/external/race/:id)

    Returns:
        Dict with keys:
          race_uid, oddspro_race_id, has_sectionals (bool), source_type,
          runners (list of per-runner raw-sectional dicts)
    """
    if not payload:
        return _empty_sectionals_result()

    race_uid       = payload.get("race_uid") or payload.get("uid") or ""
    oddspro_race_id = (
        payload.get("oddspro_race_id")
        or payload.get("id")
        or payload.get("race_id")
        or ""
    )

    runners_raw = (
        payload.get("runners")
        or payload.get("selections")
        or []
    )

    runner_sectionals = []
    for r in runners_raw:
        raw = _extract_runner_raw_sectionals(r, source_type="pre_race")
        if raw is not None:
            runner_sectionals.append(raw)

    return {
        "race_uid": race_uid,
        "oddspro_race_id": oddspro_race_id,
        "has_sectionals": bool(runner_sectionals),
        "source_type": "pre_race",
        "runners": runner_sectionals,
    }


def extract_sectionals_from_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Extract raw sectional data from an OddsPro result payload.

    These are POST-RESULT official sectionals. source_type is set to "result"
    to clearly separate from pre-race data. Stored result sectionals must NOT
    overwrite pre-race sectionals — they serve different analytical purposes.

    Args:
        payload: raw OddsPro result payload (from /api/races/:id/results)

    Returns:
        Same structure as extract_sectionals_from_race_payload() with
        source_type = "result".
    """
    if not payload:
        return _empty_sectionals_result()

    race_uid       = payload.get("race_uid") or payload.get("uid") or ""
    oddspro_race_id = (
        payload.get("oddspro_race_id")
        or payload.get("id")
        or payload.get("race_id")
        or ""
    )

    # Results payloads often nest runners under "results" or "finishers"
    runners_raw = (
        payload.get("results")
        or payload.get("finishers")
        or payload.get("runners")
        or []
    )

    runner_sectionals = []
    for r in runners_raw:
        raw = _extract_runner_raw_sectionals(r, source_type="result")
        if raw is not None:
            runner_sectionals.append(raw)

    return {
        "race_uid": race_uid,
        "oddspro_race_id": oddspro_race_id,
        "has_sectionals": bool(runner_sectionals),
        "source_type": "result",
        "runners": runner_sectionals,
    }


def build_runner_sectional_metrics(
    runner_sectionals: list[dict[str, Any]],
    field_sectionals: list[dict[str, Any]] | None = None,
    source_type: str = "pre_race",
) -> list[dict[str, Any]]:
    """
    Derive per-runner sectional metrics from raw section times.

    Args:
        runner_sectionals : list of raw-sectional dicts (from extract_* above).
                            Each must have box_num and at least one section time.
        field_sectionals  : same list (or a superset) used to compute field-level
                            norms.  If None, runner_sectionals itself is used as
                            the reference field.
        source_type       : "pre_race" for pre-result form data, "result" for
                            official post-race sectionals. Never overwrite stored
                            pre_race sectionals with result data — they serve
                            different purposes. Default: "pre_race".

    Returns:
        List of metric dicts, one per runner, including lineage fields and all
        derived sectional scores.  Runners with no usable section data receive
        all-zero / null-safe metrics.
    """
    field = field_sectionals if field_sectionals is not None else runner_sectionals
    if not runner_sectionals:
        return []

    # Gather field-level early / late averages for normalisation
    field_early  = [r["early_time"] for r in field if r.get("early_time")]
    field_late   = [r["late_time"]  for r in field if r.get("late_time")]
    field_mid    = [r["mid_time"]   for r in field if r.get("mid_time")]

    avg_early = _safe_avg(field_early)
    avg_late  = _safe_avg(field_late)
    avg_mid   = _safe_avg(field_mid)

    # Sort for ranking (lower time = faster)
    ranked_early = sorted(
        [r for r in field if r.get("early_time")],
        key=lambda x: x["early_time"],
    )
    early_rank_map: dict[int, int] = {
        r["box_num"]: idx + 1
        for idx, r in enumerate(ranked_early)
        if r.get("box_num") is not None
    }

    metrics: list[dict[str, Any]] = []
    for r in runner_sectionals:
        box_num        = r.get("box_num")
        early_time     = r.get("early_time")
        mid_time       = r.get("mid_time")
        late_time      = r.get("late_time")
        all_sections   = r.get("all_sections") or []

        # --- Early speed ---
        early_speed_score = _speed_score(early_time, avg_early)
        early_split_rank  = early_rank_map.get(box_num, 0) if box_num is not None else 0

        # --- Late speed ---
        late_speed_score = _speed_score(late_time, avg_late)

        # --- Mid-race change: negative = slowed, positive = accelerated ---
        if early_time and mid_time and early_time > 0:
            mid_race_change = round((early_time - mid_time) / early_time, 4)
        else:
            mid_race_change = 0.0

        # --- Closing delta: how much better late vs early (lower time = better) ---
        if early_time and late_time:
            closing_delta = round(early_time - late_time, 3)
        else:
            closing_delta = 0.0

        # --- Fatigue index: >1 means runner sustained / improved ---
        if early_time and late_time and late_time > 0:
            fatigue_index = round(early_time / late_time, 4)
        else:
            fatigue_index = 1.0

        # --- Acceleration index: mid-to-late vs field average ---
        if mid_time and late_time and avg_mid and avg_mid > 0:
            runner_accel   = mid_time - late_time           # positive = speeding up
            field_avg_accel = avg_mid - (avg_late or avg_mid)
            acceleration_index = round(
                (runner_accel - field_avg_accel) / avg_mid, 4
            )
        else:
            acceleration_index = 0.0

        # --- Consistency: std-dev of available section times (lower = more consistent) ---
        sectional_consistency_score = _consistency_score(all_sections)

        metrics.append({
            "race_uid":                    r.get("race_uid", ""),
            "oddspro_race_id":             r.get("oddspro_race_id", ""),
            "box_num":                     box_num,
            "runner_name":                 r.get("runner_name", ""),
            # source_type: "pre_race" (form-based) or "result" (official post-race)
            "source_type":                 r.get("source_type") or source_type,
            # Raw preserved
            "raw_early_time":              early_time,
            "raw_mid_time":                mid_time,
            "raw_late_time":               late_time,
            "raw_all_sections":            all_sections,
            # Derived metrics
            "early_split_rank":            early_split_rank,
            "early_speed_score":           early_speed_score,
            "mid_race_change":             mid_race_change,
            "late_speed_score":            late_speed_score,
            "closing_delta":               closing_delta,
            "fatigue_index":               fatigue_index,
            "acceleration_index":          acceleration_index,
            "sectional_consistency_score": sectional_consistency_score,
        })

    return metrics


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _extract_runner_raw_sectionals(
    runner: dict[str, Any],
    source_type: str = "pre_race",
) -> dict[str, Any] | None:
    """
    Extract raw section times from a single runner dict.
    Returns None if runner has no usable timing data at all.

    Args:
        runner      : raw runner dict from OddsPro payload
        source_type : "pre_race" for form data, "result" for official result
    """
    if not runner:
        return None

    box_num      = runner.get("box_num") or runner.get("box") or runner.get("barrier")
    runner_name  = runner.get("name") or runner.get("runner_name") or ""
    race_uid     = runner.get("race_uid") or ""
    oddspro_race_id = runner.get("oddspro_race_id") or runner.get("race_id") or ""

    early_time = _pick_time(runner, _EARLY_KEYS)
    mid_time   = _pick_time(runner, _MID_KEYS)
    late_time  = _pick_time(runner, _LATE_KEYS)

    # Also try positional "splits" / "sections" array
    splits = runner.get("splits") or runner.get("sections") or runner.get("sectionals") or []
    if splits and isinstance(splits, list):
        parsed_splits = [_safe_float(s) for s in splits if _safe_float(s) is not None]
        if parsed_splits:
            if early_time is None and len(parsed_splits) >= 1:
                early_time = parsed_splits[0]
            if mid_time is None and len(parsed_splits) >= 2:
                mid_time = parsed_splits[1]
            if late_time is None and len(parsed_splits) >= 3:
                late_time = parsed_splits[-1]
    else:
        parsed_splits = []

    # Collect all available splits into one list
    all_sections: list[float] = []
    for t in [early_time, mid_time, late_time]:
        if t is not None and t > 0:
            all_sections.append(t)
    for t in parsed_splits:
        if t > 0 and t not in all_sections:
            all_sections.append(t)

    if not all_sections and early_time is None and late_time is None:
        return None   # no usable data — caller may skip this runner

    return {
        "race_uid":        race_uid,
        "oddspro_race_id": oddspro_race_id,
        "box_num":         _safe_int(box_num),
        "runner_name":     runner_name,
        "source_type":     source_type,
        "early_time":      early_time,
        "mid_time":        mid_time,
        "late_time":       late_time,
        "all_sections":    all_sections,
    }


def _empty_sectionals_result() -> dict[str, Any]:
    return {
        "race_uid": "",
        "oddspro_race_id": "",
        "has_sectionals": False,
        "source_type": "pre_race",
        "runners": [],
    }


def _pick_time(runner: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        val = runner.get(k)
        result = _safe_float(val)
        if result is not None and result > 0:
            return result
    return None


def _speed_score(section_time: float | None, avg_time: float | None) -> float:
    """
    Convert a section time into a 0-1 normalised speed score.
    Faster (lower) time → higher score.
    Returns 0.0 when data is unavailable.
    """
    if not section_time or not avg_time or avg_time <= 0 or section_time <= 0:
        return 0.0
    raw = avg_time / section_time   # ratio: >1 means faster than average
    return round(min(max(raw, 0.0), 2.0) / 2.0, 4)   # clamp [0, 2] then scale to [0, 1]


def _consistency_score(sections: list[float]) -> float:
    """
    Produce a 0-1 consistency score from a list of section times.
    Lower coefficient of variation → higher score (more consistent).
    Returns 0.5 (neutral) when fewer than 2 sections available.
    """
    if len(sections) < 2:
        return 0.5
    mean = sum(sections) / len(sections)
    if mean <= 0:
        return 0.5
    variance = sum((s - mean) ** 2 for s in sections) / len(sections)
    std_dev = variance ** 0.5
    cv = std_dev / mean   # coefficient of variation
    # cv of 0 = perfect consistency → score 1.0
    # cv of 0.1 (10% deviation) → score ~0
    score = max(0.0, 1.0 - cv * 10.0)
    return round(score, 4)


def _safe_avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _safe_float(val: Any) -> float | None:
    try:
        f = float(val)
        return f if f == f else None   # NaN check
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
