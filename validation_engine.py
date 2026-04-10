"""
validation_engine.py - DemonPulse Data Validation (simplified)
===============================================================
Basic validation that checks for required fields.
No OddsPro/FormFav confidence scoring.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.5


def validate_race(race: dict[str, Any]) -> tuple[bool, float, list[str]]:
    """
    Validate a race dict against completeness requirements.

    Returns:
        (passes, confidence_score, issues_list)
        passes = True if confidence_score >= CONFIDENCE_THRESHOLD
    """
    issues: list[str] = []
    score = 1.0

    track = race.get("track") or race.get("track_name")
    if not track:
        issues.append("MISSING_TRACK")
        score -= 0.3

    race_num = race.get("race_num") or race.get("race_number")
    if not race_num:
        issues.append("MISSING_RACE_NUM")
        score -= 0.3

    if not race.get("date"):
        issues.append("MISSING_DATE")
        score -= 0.2

    if not race.get("code"):
        issues.append("MISSING_CODE")
        score -= 0.1

    score = max(0.0, score)
    return score >= CONFIDENCE_THRESHOLD, score, issues
