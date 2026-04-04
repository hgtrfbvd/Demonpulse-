"""
validation_engine.py - DemonPulse V8 Multi-Source Validation Engine

Purpose:
  A race should not be trusted because one connector says it exists.
  It should be trusted because enough trusted sources agree.

Confidence bands:
  0.85 – 1.00  => VALIDATED  (can build board)
  0.65 – 0.84  => CAUTION    (partial / flagged)
  0.00 – 0.64  => BLOCKED    (do not build board)  [< THRESHOLD_CAUTION]

Key rule:
  If validation cannot confirm enough real data, it must return
  can_build_board=False with an explicit reason code.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

# Import shared stale threshold from integrity_filter to maintain single source of truth
from integrity_filter import MAX_STALE_DATA_SECONDS as STALE_THRESHOLD_SECONDS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------
# CONFIDENCE THRESHOLDS
# ---------------------------------------------------------------
THRESHOLD_VALIDATED = 0.85
THRESHOLD_CAUTION = 0.65
BLOCKED_SCORE_BUFFER = 0.01             # Gap below caution threshold for blocked scores
MAX_BLOCKED_SCORE = THRESHOLD_CAUTION - BLOCKED_SCORE_BUFFER

# Thresholds for runner name overlap scoring
NAME_OVERLAP_HIGH_THRESHOLD = 0.7      # Above → names_overlap bonus
NAME_OVERLAP_LOW_THRESHOLD = 0.4       # Below → names_mismatch penalty

# Runner count variance tolerance
MIN_RUNNER_VARIANCE = 2                 # Absolute minimum allowed count difference
RUNNER_COUNT_TOLERANCE_RATIO = 0.2     # 20% relative tolerance

# ---------------------------------------------------------------
# VALIDATION REASON CODES
# ---------------------------------------------------------------
REASON_PRIMARY_SOURCE_OK = "primary_source_confirmed"
REASON_SUPPLEMENTAL_OK = "supplemental_source_confirmed"
REASON_RUNNER_COUNT_MATCH = "runner_count_match"
REASON_NAMES_OVERLAP = "runner_names_overlap"
REASON_JUMP_TIME_MATCH = "jump_time_match"
REASON_ODDS_PRESENT = "odds_present"

REASON_SOURCE_BLOCKED = "source_blocked"
REASON_SOURCE_PARTIAL = "source_partial"
REASON_SOURCE_FAILED = "source_failed"
REASON_RUNNER_COUNT_MISMATCH = "runner_count_mismatch"
REASON_NAMES_MISMATCH = "runner_names_mismatch"
REASON_JUMP_TIME_DRIFT = "jump_time_drift"
REASON_STALE_DATA = "stale_data"
REASON_STATUS_CONFLICT = "race_status_conflict"
REASON_NO_PRIMARY = "no_primary_source_data"
REASON_INSUFFICIENT_SOURCES = "insufficient_sources"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_name(name: str) -> str:
    """Normalize runner name for comparison."""
    return (name or "").strip().lower().replace("'", "").replace("-", " ")


def _names_overlap_score(names_a: list[str], names_b: list[str]) -> float:
    """Return overlap ratio between two lists of runner names."""
    if not names_a or not names_b:
        return 0.0
    set_a = {_normalize_name(n) for n in names_a}
    set_b = {_normalize_name(n) for n in names_b}
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def _jump_time_drift_seconds(t1: str | None, t2: str | None) -> float | None:
    """Return absolute time difference in seconds, or None if either is missing/unparseable."""
    if not t1 or not t2:
        return None
    try:
        dt1 = datetime.fromisoformat(t1.replace("Z", "+00:00"))
        dt2 = datetime.fromisoformat(t2.replace("Z", "+00:00"))
        return abs((dt1 - dt2).total_seconds())
    except Exception:
        return None


def _data_age_seconds(fetched_at: str | None) -> float | None:
    """Return seconds since fetched_at timestamp."""
    if not fetched_at:
        return None
    try:
        dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds()
    except Exception:
        return None


# ---------------------------------------------------------------
# CONNECTOR ENVELOPE ANALYSIS
# ---------------------------------------------------------------

def _analyse_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """
    Analyse a single connector envelope and return a summary dict
    used during validation scoring.
    """
    status = envelope.get("status", "failed")
    confidence = float(envelope.get("confidence") or 0.0)
    data = envelope.get("data") or {}
    meta = envelope.get("meta") or {}
    fetched_at = envelope.get("fetched_at")

    age_s = _data_age_seconds(fetched_at)
    stale = age_s is not None and age_s > STALE_THRESHOLD_SECONDS

    meetings = data.get("meetings") or []
    races = data.get("races") or []
    runners = data.get("runners") or []
    odds = data.get("odds") or []

    return {
        "source": envelope.get("source", "unknown"),
        "status": status,
        "ok": status == "ok",
        "partial": status == "partial",
        "blocked": status == "blocked",
        "failed": status in ("failed", "disabled"),
        "confidence": confidence,
        "stale": stale,
        "age_seconds": age_s,
        "meeting_count": len(meetings),
        "race_count": len(races),
        "runner_count": len(runners),
        "odds_count": len(odds),
        "runner_names": [r.get("runner_name", "") for r in runners],
        "jump_time": races[0].get("scheduled_jump_time") if races else None,
        "race_status": races[0].get("status") if races else None,
    }


# ---------------------------------------------------------------
# MAIN VALIDATION FUNCTION
# ---------------------------------------------------------------

def validate_race_sources(
    primary_envelopes: list[dict[str, Any]],
    supplemental_envelopes: list[dict[str, Any]] | None = None,
    *,
    jump_time_tolerance_s: float = 300.0,
    min_runner_count: int = 2,
    stale_max_age_s: float = 600.0,
) -> dict[str, Any]:
    """
    Validate race data across multiple connector envelopes.

    Args:
        primary_envelopes: Envelopes from primary (API-first) sources.
        supplemental_envelopes: Envelopes from secondary/scrape sources.
        jump_time_tolerance_s: Max allowed jump time drift (seconds).
        min_runner_count: Minimum runners required.
        stale_max_age_s: Max age in seconds before data is considered stale.

    Returns a validation result dict:
        {
          "validated_at": str,
          "can_build_board": bool,
          "confidence": float,
          "status": "validated" | "caution" | "blocked",
          "reason_codes": [str],
          "summary": str,
          "sources_ok": [str],
          "sources_blocked": [str],
          "sources_failed": [str],
          "runner_count": int,
          "jump_time": str | None,
        }
    """
    supplemental_envelopes = supplemental_envelopes or []

    primary_analyses = [_analyse_envelope(e) for e in primary_envelopes]
    supplemental_analyses = [_analyse_envelope(e) for e in supplemental_envelopes]
    all_analyses = primary_analyses + supplemental_analyses

    score = 0.0
    reason_codes: list[str] = []
    sources_ok: list[str] = []
    sources_blocked: list[str] = []
    sources_failed: list[str] = []

    # ---- Primary source evaluation ----
    primary_ok = [a for a in primary_analyses if a["ok"]]
    primary_partial = [a for a in primary_analyses if a["partial"]]
    primary_blocked = [a for a in primary_analyses if a["blocked"]]
    primary_failed = [a for a in primary_analyses if a["failed"]]

    if primary_ok:
        score += 0.40
        reason_codes.append(REASON_PRIMARY_SOURCE_OK)
        sources_ok.extend(a["source"] for a in primary_ok)
    elif primary_partial:
        score += 0.15
        reason_codes.append(REASON_SOURCE_PARTIAL)
        sources_ok.extend(a["source"] for a in primary_partial)
    else:
        if primary_blocked:
            reason_codes.append(REASON_SOURCE_BLOCKED)
            sources_blocked.extend(a["source"] for a in primary_blocked)
        if primary_failed:
            reason_codes.append(REASON_SOURCE_FAILED)
            sources_failed.extend(a["source"] for a in primary_failed)
        if not primary_analyses:
            reason_codes.append(REASON_NO_PRIMARY)

    # ---- Supplemental source evaluation ----
    supp_ok = [a for a in supplemental_analyses if a["ok"]]
    supp_partial = [a for a in supplemental_analyses if a["partial"]]
    supp_blocked = [a for a in supplemental_analyses if a["blocked"]]
    supp_failed = [a for a in supplemental_analyses if a["failed"]]

    if supp_ok:
        score += 0.15
        reason_codes.append(REASON_SUPPLEMENTAL_OK)
        sources_ok.extend(a["source"] for a in supp_ok)
    if supp_blocked:
        score -= 0.05
        sources_blocked.extend(a["source"] for a in supp_blocked)
    if supp_failed:
        sources_failed.extend(a["source"] for a in supp_failed)

    # ---- Runner count analysis ----
    runner_counts = [a["runner_count"] for a in all_analyses if a["runner_count"] > 0]
    if runner_counts:
        max_count = max(runner_counts)
        min_count = min(runner_counts)
        if max_count >= min_runner_count:
            if (max_count - min_count) <= max(MIN_RUNNER_VARIANCE, max_count * RUNNER_COUNT_TOLERANCE_RATIO):
                score += 0.15
                reason_codes.append(REASON_RUNNER_COUNT_MATCH)
            else:
                score -= 0.10
                reason_codes.append(REASON_RUNNER_COUNT_MISMATCH)
        else:
            score -= 0.15
            reason_codes.append(REASON_RUNNER_COUNT_MISMATCH)
    else:
        score -= 0.15
        reason_codes.append(REASON_RUNNER_COUNT_MISMATCH)

    # ---- Runner name overlap (between first two sources that have runners) ----
    runner_name_lists = [
        a["runner_names"] for a in all_analyses
        if a["runner_names"]
    ]
    if len(runner_name_lists) >= 2:
        overlap = _names_overlap_score(runner_name_lists[0], runner_name_lists[1])
        if overlap >= NAME_OVERLAP_HIGH_THRESHOLD:
            score += 0.10
            reason_codes.append(REASON_NAMES_OVERLAP)
        elif overlap < NAME_OVERLAP_LOW_THRESHOLD:
            score -= 0.10
            reason_codes.append(REASON_NAMES_MISMATCH)

    # ---- Jump time consistency ----
    jump_times = [a["jump_time"] for a in all_analyses if a["jump_time"]]
    if len(jump_times) >= 2:
        drift = _jump_time_drift_seconds(jump_times[0], jump_times[1])
        if drift is not None:
            if drift <= jump_time_tolerance_s:
                score += 0.10
                reason_codes.append(REASON_JUMP_TIME_MATCH)
            else:
                score -= 0.15
                reason_codes.append(REASON_JUMP_TIME_DRIFT)

    # ---- Race status conflict ----
    statuses = [a["race_status"] for a in all_analyses if a["race_status"]]
    distinct = set(statuses)
    if len(distinct) > 1 and "result" in distinct and "upcoming" in distinct:
        score -= 0.20
        reason_codes.append(REASON_STATUS_CONFLICT)

    # ---- Stale data penalty ----
    stale_sources = [a for a in all_analyses if a["stale"]]
    if stale_sources:
        score -= 0.10 * len(stale_sources)
        reason_codes.append(REASON_STALE_DATA)

    # ---- Odds presence bonus ----
    odds_sources = [a for a in all_analyses if a["odds_count"] > 0]
    if odds_sources:
        score += 0.05
        reason_codes.append(REASON_ODDS_PRESENT)

    # ---- Determine best jump_time and runner_count from primary sources ----
    best_jump_time = None
    best_runner_count = 0
    for a in (primary_ok or primary_partial or all_analyses):
        if a["jump_time"] and not best_jump_time:
            best_jump_time = a["jump_time"]
        if a["runner_count"] > best_runner_count:
            best_runner_count = a["runner_count"]

    # ---- Clamp and classify ----
    score = round(max(0.0, min(1.0, score)), 4)

    if score >= THRESHOLD_VALIDATED:
        status = "validated"
        can_build_board = True
    elif score >= THRESHOLD_CAUTION:
        status = "caution"
        can_build_board = False
    else:
        status = "blocked"
        can_build_board = False

    # Hard block: no primary source data at all
    if not primary_analyses or (not primary_ok and not primary_partial):
        can_build_board = False
        if status == "validated":
            status = "blocked"
            score = min(score, MAX_BLOCKED_SCORE)

    summary = (
        f"status={status} score={score} "
        f"sources_ok={sources_ok} blocked={sources_blocked} "
        f"runners={best_runner_count}"
    )
    log.debug("Validation: %s", summary)

    return {
        "validated_at": _utc_now(),
        "can_build_board": can_build_board,
        "confidence": score,
        "status": status,
        "reason_codes": list(dict.fromkeys(reason_codes)),  # deduplicated, ordered
        "summary": summary,
        "sources_ok": sources_ok,
        "sources_blocked": sources_blocked,
        "sources_failed": sources_failed,
        "runner_count": best_runner_count,
        "jump_time": best_jump_time,
    }


# ---------------------------------------------------------------
# MEETING-LEVEL VALIDATION
# ---------------------------------------------------------------

def validate_meeting_exists(
    envelopes: list[dict[str, Any]],
    track_name: str,
    meeting_date: str,
) -> dict[str, Any]:
    """
    Verify that at least one source confirms this meeting exists.

    Returns:
        {"confirmed": bool, "sources": [str], "confidence": float}
    """
    confirmed_by: list[str] = []
    norm_track = _normalize_name(track_name)

    for env in envelopes:
        if env.get("status") not in ("ok", "partial"):
            continue
        data = env.get("data") or {}
        for m in (data.get("meetings") or []):
            m_track = _normalize_name(m.get("track_name") or m.get("track") or "")
            m_date = m.get("meeting_date") or m.get("date") or ""
            if m_date == meeting_date and (norm_track in m_track or m_track in norm_track):
                confirmed_by.append(env.get("source", "unknown"))
                break

    confidence = min(1.0, len(confirmed_by) * 0.5)
    return {
        "confirmed": len(confirmed_by) > 0,
        "sources": confirmed_by,
        "confidence": confidence,
    }
