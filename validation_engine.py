"""
validation_engine.py - DemonPulse Data Validation
===================================================
Gates race data at a minimum confidence threshold before it reaches the board.

Architecture rule:
  - OddsPro-sourced data starts at high confidence
  - FormFav provisional data is lower confidence
  - Anything below CONFIDENCE_THRESHOLD is rejected from the official board

Confidence scoring:
  1.0 = fully verified OddsPro record with jump_time, runners, status
  0.85 = minimum threshold (threshold gate)
  <0.85 = rejected
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85


def validate_race(race: dict[str, Any]) -> tuple[bool, float, list[str]]:
    """
    Validate a race dict against completeness requirements.

    Returns:
        (passes, confidence_score, issues_list)
        passes = True if confidence_score >= CONFIDENCE_THRESHOLD
    """
    issues: list[str] = []
    score = 1.0

    # Required fields
    if not race.get("track"):
        issues.append("MISSING_TRACK")
        score -= 0.3
    if not race.get("race_num"):
        issues.append("MISSING_RACE_NUM")
        score -= 0.3
    if not race.get("date"):
        issues.append("MISSING_DATE")
        score -= 0.2
    if not race.get("code"):
        issues.append("MISSING_CODE")
        score -= 0.1

    # Source authority — OddsPro gets full credit; others are lower
    source = (race.get("source") or "").lower()
    if source == "oddspro":
        pass  # no deduction
    elif source == "formfav":
        score -= 0.1  # provisional overlay, slight deduction
        issues.append("SOURCE_PROVISIONAL")
    elif source in ("racenet", "thedogs"):
        score -= 0.15
        issues.append("SOURCE_SCRAPE")
    else:
        score -= 0.2
        issues.append("SOURCE_UNKNOWN")

    # Jump time presence boosts confidence
    if race.get("jump_time"):
        time_status = (race.get("time_status") or "PARTIAL").upper()
        if time_status == "VERIFIED":
            pass  # full credit
        else:
            score -= 0.05  # partial time data
    else:
        issues.append("NO_JUMP_TIME")
        score -= 0.1

    # Status sanity
    status = (race.get("status") or "").lower()
    if status in ("abandoned", "blocked", "invalid"):
        issues.append(f"BAD_STATUS:{status.upper()}")
        score -= 0.5

    # Runner presence
    runner_count = int(race.get("runner_count") or 0)
    if runner_count == 0:
        issues.append("NO_RUNNERS")
        score -= 0.05  # soft deduction (runners may not be loaded yet)

    score = round(max(0.0, min(1.0, score)), 4)
    passes = score >= CONFIDENCE_THRESHOLD

    if not passes:
        log.warning(
            f"validation_engine: REJECT race {race.get('race_uid')} "
            f"confidence={score} issues={issues}"
        )

    return passes, score, issues


def validate_runner(runner: dict[str, Any]) -> tuple[bool, float, list[str]]:
    """
    Validate a runner dict.
    Returns (passes, confidence, issues).
    """
    issues: list[str] = []
    score = 1.0

    if not runner.get("name"):
        issues.append("MISSING_NAME")
        score -= 0.4

    if not runner.get("race_uid"):
        issues.append("MISSING_RACE_UID")
        score -= 0.3

    number = runner.get("number")
    box_num = runner.get("box_num")
    if number is None and box_num is None:
        issues.append("MISSING_NUMBER")
        score -= 0.2

    source_confidence = (runner.get("source_confidence") or "api").lower()
    if source_confidence not in ("official", "api"):
        score -= 0.05
        issues.append("SOURCE_UNVERIFIED")

    score = round(max(0.0, min(1.0, score)), 4)
    return score >= CONFIDENCE_THRESHOLD, score, issues
