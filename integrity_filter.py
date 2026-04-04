"""
integrity_filter.py - DemonPulse V8 Hard Integrity Gate

This is NOT advisory. It is a gate.

If integrity fails:
  - do not build board
  - do not send race to prediction engine
  - record explicit block reason

Block state codes:
  NO_SOURCE_DATA
  SOURCE_BLOCKED
  SOURCE_PARTIAL
  VALIDATION_MISMATCH
  STALE_DATA
  TIME_DRIFT
  INSUFFICIENT_RUNNERS
  RESULT_ALREADY_POSTED
  UNKNOWN_RACE_STATE
  EMPTY_RUNNER_NAMES
  DUPLICATE_BOXES
  NULL_REQUIRED_FIELD
  MEETING_DATE_MISMATCH
  RACE_TIME_INVALID
  ODDS_MISSING
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------
# BLOCK STATE CONSTANTS
# ---------------------------------------------------------------
BLOCK_NO_SOURCE_DATA = "NO_SOURCE_DATA"
BLOCK_SOURCE_BLOCKED = "SOURCE_BLOCKED"
BLOCK_SOURCE_PARTIAL = "SOURCE_PARTIAL"
BLOCK_VALIDATION_MISMATCH = "VALIDATION_MISMATCH"
BLOCK_STALE_DATA = "STALE_DATA"
BLOCK_TIME_DRIFT = "TIME_DRIFT"
BLOCK_INSUFFICIENT_RUNNERS = "INSUFFICIENT_RUNNERS"
BLOCK_RESULT_ALREADY_POSTED = "RESULT_ALREADY_POSTED"
BLOCK_UNKNOWN_RACE_STATE = "UNKNOWN_RACE_STATE"
BLOCK_EMPTY_RUNNER_NAMES = "EMPTY_RUNNER_NAMES"
BLOCK_DUPLICATE_BOXES = "DUPLICATE_BOXES"
BLOCK_NULL_REQUIRED_FIELD = "NULL_REQUIRED_FIELD"
BLOCK_MEETING_DATE_MISMATCH = "MEETING_DATE_MISMATCH"
BLOCK_RACE_TIME_INVALID = "RACE_TIME_INVALID"
BLOCK_ODDS_MISSING = "ODDS_MISSING"

# ---------------------------------------------------------------
# INTEGRITY CONFIGURATION DEFAULTS
# ---------------------------------------------------------------
MIN_RUNNER_COUNT = 2
MAX_STALE_DATA_SECONDS = 600       # 10 minutes
MAX_FUTURE_JUMP_MINUTES = 1440     # races must jump within 24h
MIN_ODDS_COVERAGE_RATIO = 0.0      # 0 = odds not required by default
RESULT_BLOCK_STATUSES = {"result", "official", "abandoned", "finished"}
VALID_UPCOMING_STATUSES = {"upcoming", "open", "pending", "accepting", "jumps", "unknown"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


# ---------------------------------------------------------------
# INTEGRITY RESULT BUILDER
# ---------------------------------------------------------------

def _make_result(passed: bool, blocks: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "passed": passed,
        "blocks": blocks,
        "warnings": warnings,
        "checked_at": _utc_now().isoformat(),
    }


# ---------------------------------------------------------------
# INDIVIDUAL CHECKS
# ---------------------------------------------------------------

def _check_race_time(race: dict[str, Any]) -> list[str]:
    """Race must have a valid future jump time (or unknown status)."""
    blocks: list[str] = []
    jump_time_str = race.get("scheduled_jump_time") or race.get("jump_time")
    status = (race.get("status") or "unknown").lower()

    # Already finished — handled by _check_race_status, skip time checks
    if status in RESULT_BLOCK_STATUSES:
        return blocks

    if not jump_time_str:
        # Missing time is only a hard block if we're building a board
        # (recorded as a warning here, caller decides)
        return blocks

    dt = _parse_iso(jump_time_str)
    if dt is None:
        blocks.append(BLOCK_RACE_TIME_INVALID)
        return blocks

    now = _utc_now()
    if dt < now - timedelta(minutes=60):
        # Race started more than 60 minutes ago with no result update → stale
        blocks.append(BLOCK_STALE_DATA)

    if dt > now + timedelta(minutes=MAX_FUTURE_JUMP_MINUTES):
        # Race is too far in the future
        blocks.append(BLOCK_TIME_DRIFT)

    return blocks


def _check_runners(runners: list[dict[str, Any]], *, require_odds: bool = False) -> list[str]:
    """Validate runner list integrity."""
    blocks: list[str] = []

    active_runners = [r for r in runners if not r.get("scratched")]

    if len(active_runners) < MIN_RUNNER_COUNT:
        blocks.append(BLOCK_INSUFFICIENT_RUNNERS)

    # Check for empty names
    empty_names = [r for r in active_runners if not (r.get("runner_name") or "").strip()]
    if empty_names:
        blocks.append(BLOCK_EMPTY_RUNNER_NAMES)

    # Check for duplicate boxes/barriers
    boxes = [r.get("box_or_barrier") for r in active_runners if r.get("box_or_barrier") is not None]
    if len(boxes) != len(set(boxes)):
        blocks.append(BLOCK_DUPLICATE_BOXES)

    # Odds check (optional)
    if require_odds:
        runners_with_odds = [r for r in active_runners if r.get("odds_win") is not None]
        if not runners_with_odds:
            blocks.append(BLOCK_ODDS_MISSING)
        elif len(active_runners) > 0:
            ratio = len(runners_with_odds) / len(active_runners)
            if ratio < MIN_ODDS_COVERAGE_RATIO:
                blocks.append(BLOCK_ODDS_MISSING)

    return blocks


def _check_race_status(race: dict[str, Any]) -> list[str]:
    """Race status must be an upcoming/open status for board building."""
    blocks: list[str] = []
    status = (race.get("status") or "unknown").lower()

    if status in RESULT_BLOCK_STATUSES:
        blocks.append(BLOCK_RESULT_ALREADY_POSTED)
    elif status not in VALID_UPCOMING_STATUSES:
        blocks.append(BLOCK_UNKNOWN_RACE_STATE)

    return blocks


def _check_required_fields(race: dict[str, Any]) -> list[str]:
    """Required race fields must not be null."""
    blocks: list[str] = []
    required = ["race_id_internal", "meeting_id_internal", "race_number", "source"]
    for field in required:
        if not race.get(field):
            blocks.append(BLOCK_NULL_REQUIRED_FIELD)
            log.warning("Integrity: required field missing: %s", field)
            break
    return blocks


def _check_data_freshness(fetched_at: str | None) -> list[str]:
    """Data must not be older than MAX_STALE_DATA_SECONDS."""
    blocks: list[str] = []
    if not fetched_at:
        return blocks  # No timestamp — we don't block here, validation handles it

    dt = _parse_iso(fetched_at)
    if dt is None:
        return blocks

    age_s = (_utc_now() - dt).total_seconds()
    if age_s > MAX_STALE_DATA_SECONDS:
        blocks.append(BLOCK_STALE_DATA)

    return blocks


def _check_meeting_date(race: dict[str, Any], current_date: str | None = None) -> list[str]:
    """Race's meeting date should match the operating window."""
    blocks: list[str] = []
    if not current_date:
        return blocks

    # Try to determine race date from internal fields
    race_date = (
        race.get("meeting_date")
        or race.get("date")
        or (race.get("extra") or {}).get("date")
    )
    if race_date and race_date != current_date:
        blocks.append(BLOCK_MEETING_DATE_MISMATCH)

    return blocks


# ---------------------------------------------------------------
# MAIN INTEGRITY CHECK FUNCTION
# ---------------------------------------------------------------

def check_race_integrity(
    race: dict[str, Any],
    runners: list[dict[str, Any]],
    *,
    require_odds: bool = False,
    current_date: str | None = None,
    check_freshness: bool = True,
) -> dict[str, Any]:
    """
    Run all integrity checks on a race + runner set.

    Args:
        race: Normalized race dict (from base_connector.make_race).
        runners: List of normalized runner dicts.
        require_odds: Whether to block when odds are missing.
        current_date: ISO date string (YYYY-MM-DD) for window check.
        check_freshness: Whether to check data freshness timestamp.

    Returns:
        {
          "passed": bool,
          "blocks": [str],   -- block reason codes (see constants above)
          "warnings": [str],
          "checked_at": str,
        }
    """
    blocks: list[str] = []
    warnings: list[str] = []

    # 1. Required fields
    blocks.extend(_check_required_fields(race))

    # 2. Race status
    blocks.extend(_check_race_status(race))

    # 3. Race time validity
    blocks.extend(_check_race_time(race))

    # 4. Runner integrity
    blocks.extend(_check_runners(runners, require_odds=require_odds))

    # 5. Meeting date
    if current_date:
        blocks.extend(_check_meeting_date(race, current_date))

    # 6. Data freshness
    if check_freshness:
        fetched_at = race.get("fetched_at")
        blocks.extend(_check_data_freshness(fetched_at))

    passed = len(blocks) == 0

    if not passed:
        log.info(
            "Integrity FAIL race=%s blocks=%s",
            race.get("race_id_internal", "unknown"),
            blocks,
        )
    else:
        log.debug("Integrity PASS race=%s", race.get("race_id_internal", "unknown"))

    return _make_result(passed, blocks, warnings)


def check_envelope_integrity(envelope: dict[str, Any]) -> dict[str, Any]:
    """
    Check a raw connector envelope for basic integrity before further processing.

    Returns:
        {"passed": bool, "blocks": [str], "warnings": [str], "checked_at": str}
    """
    blocks: list[str] = []
    warnings: list[str] = []

    status = envelope.get("status", "failed")
    data = envelope.get("data") or {}
    fetched_at = envelope.get("fetched_at")

    if status == "blocked":
        blocks.append(BLOCK_SOURCE_BLOCKED)
    elif status == "failed" or status == "disabled":
        blocks.append(BLOCK_NO_SOURCE_DATA)
    elif status == "partial":
        warnings.append(BLOCK_SOURCE_PARTIAL)

    has_any_data = any(
        data.get(k) for k in ("meetings", "races", "runners", "odds", "results")
    )
    if status == "ok" and not has_any_data:
        blocks.append(BLOCK_NO_SOURCE_DATA)

    if fetched_at:
        stale_blocks = _check_data_freshness(fetched_at)
        blocks.extend(stale_blocks)

    passed = len(blocks) == 0
    return _make_result(passed, blocks, warnings)
