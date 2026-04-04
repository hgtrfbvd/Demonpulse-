"""
validation_engine.py - Validates OddsPro payloads before storage.
Confidence threshold: 0.85
"""

import logging
from datetime import datetime

log = logging.getLogger(__name__)

REQUIRED_MEETING_FIELDS = ["meeting_id", "track", "date", "code"]
REQUIRED_RACE_FIELDS = ["race_id", "meeting_id", "race_num", "jump_time"]
REQUIRED_RUNNER_FIELDS = ["name"]

CONFIDENCE_THRESHOLD = 0.85


def validate_meeting_payload(data: dict) -> tuple[bool, list[str]]:
    """Returns (valid, list_of_issues)."""
    issues = []
    for field in REQUIRED_MEETING_FIELDS:
        if not data.get(field):
            issues.append(f"Missing required field: {field}")

    code = data.get("code", "")
    if code and code.upper() not in ("HORSE", "HARNESS", "GREYHOUND"):
        issues.append(f"Unknown race code: {code}")

    date_str = data.get("date", "")
    if date_str:
        try:
            datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            issues.append(f"Invalid date format: {date_str}")

    return len(issues) == 0, issues


def validate_race_payload(data: dict) -> tuple[bool, list[str]]:
    """Returns (valid, list_of_issues)."""
    issues = []
    for field in REQUIRED_RACE_FIELDS:
        if data.get(field) is None:
            issues.append(f"Missing required field: {field}")

    jump_time = data.get("jump_time")
    if jump_time:
        try:
            datetime.fromisoformat(str(jump_time).replace("Z", "+00:00"))
        except ValueError:
            issues.append(f"Invalid jump_time format: {jump_time}")

    race_num = data.get("race_num")
    if race_num is not None:
        try:
            n = int(race_num)
            if n < 1 or n > 30:
                issues.append(f"race_num out of range: {n}")
        except (TypeError, ValueError):
            issues.append(f"race_num not an integer: {race_num}")

    distance = data.get("distance")
    if distance is not None:
        try:
            d = int(distance)
            if d < 100 or d > 10000:
                issues.append(f"distance out of range: {d}")
        except (TypeError, ValueError):
            issues.append(f"distance not an integer: {distance}")

    return len(issues) == 0, issues


def validate_runner_payload(data: dict) -> tuple[bool, list[str]]:
    """Returns (valid, list_of_issues)."""
    issues = []
    for field in REQUIRED_RUNNER_FIELDS:
        if not data.get(field):
            issues.append(f"Missing required field: {field}")

    win_odds = data.get("win_odds")
    if win_odds is not None:
        try:
            o = float(win_odds)
            if o < 1.0 or o > 1000.0:
                issues.append(f"win_odds out of range: {o}")
        except (TypeError, ValueError):
            issues.append(f"win_odds not a number: {win_odds}")

    return len(issues) == 0, issues


def validate_result_payload(data: dict) -> tuple[bool, list[str]]:
    """Returns (valid, list_of_issues)."""
    issues = []
    if not data.get("race_id"):
        issues.append("Missing required field: race_id")

    positions = data.get("positions")
    if not positions:
        issues.append("Missing positions data")
    elif isinstance(positions, dict) and not positions:
        issues.append("Positions dict is empty")

    return len(issues) == 0, issues


def score_data_quality(race: dict, runners: list[dict]) -> float:
    """Return confidence score 0.0-1.0 based on data completeness."""
    score = 0.0
    total = 0

    # Race-level checks (weight: 0.5)
    race_checks = [
        bool(race.get("race_id")),
        bool(race.get("meeting_id")),
        bool(race.get("jump_time")),
        bool(race.get("track")),
        bool(race.get("race_num")),
        bool(race.get("code")),
        bool(race.get("distance")),
        bool(race.get("race_name")),
    ]
    race_score = sum(race_checks) / len(race_checks)
    score += race_score * 0.5
    total += 0.5

    # Runner-level checks (weight: 0.5)
    if not runners:
        # No runners is a hard failure
        return 0.0

    runner_scores = []
    for r in runners:
        checks = [
            bool(r.get("name")),
            r.get("win_odds") is not None,
            r.get("number") is not None or r.get("box_num") is not None,
        ]
        runner_scores.append(sum(checks) / len(checks))

    avg_runner_score = sum(runner_scores) / len(runner_scores)
    score += avg_runner_score * 0.5
    total += 0.5

    return round(score / total, 4) if total > 0 else 0.0
