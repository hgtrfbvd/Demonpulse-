"""
pipeline_state.py — DemonPulse Runtime Pipeline State Tracker
==============================================================
Thread-safe in-memory tracker for the OddsPro → FormFav pipeline.
Used by GET /api/debug/formfav to expose full pipeline state at runtime.

Data is accumulated during the current process lifetime (resets on restart).
The recent_races deque always holds the last 20 discovered races with their
latest pipeline status.
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

_lock = threading.Lock()

_state: dict[str, Any] = {
    "total_races_discovered": 0,
    "total_domestic_races": 0,
    "total_international_filtered": 0,
    "total_formfav_eligible": 0,
    "total_formfav_called": 0,
    "total_formfav_success": 0,
    "total_formfav_failed": 0,
    "last_reset": None,
}

# Ordered deque of recent race dicts, capped at 20 entries.
# Each entry shape:
#   race_uid, track, country, eligible, skip_reason, formfav_called, status
_recent_races: deque[dict[str, Any]] = deque(maxlen=20)


def reset() -> None:
    """Reset all counters and clear recent_races (called at start of each full_sweep)."""
    with _lock:
        for k in list(_state.keys()):
            if k != "last_reset":
                _state[k] = 0
        _recent_races.clear()
        _state["last_reset"] = datetime.now(timezone.utc).isoformat()


def _find_entry(race_uid: str) -> dict[str, Any] | None:
    """Return existing entry for race_uid, or None. Must be called under _lock."""
    for entry in _recent_races:
        if entry.get("race_uid") == race_uid:
            return entry
    return None


def record_race_discovered(race_uid: str, track: str, country: str) -> None:
    """Called when OddsPro discovers a race (before DB write)."""
    with _lock:
        _state["total_races_discovered"] += 1
        existing = _find_entry(race_uid)
        if existing is not None:
            existing["track"] = track
            existing["country"] = country
        else:
            _recent_races.append({
                "race_uid": race_uid,
                "track": track,
                "country": country,
                "eligible": None,
                "skip_reason": None,
                "formfav_called": False,
                "status": "discovered",
            })


def record_race_included(race_uid: str) -> None:
    """Called when a race passes the domestic failsafe and is written to DB."""
    with _lock:
        _state["total_domestic_races"] += 1
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["status"] = "included"


def record_race_excluded(race_uid: str, reason: str) -> None:
    """Called when a race is blocked by the domestic failsafe (NOT written to DB)."""
    with _lock:
        _state["total_international_filtered"] += 1
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["skip_reason"] = reason
            entry["status"] = "excluded"


def record_formfav_skipped(race_uid: str, reason: str) -> None:
    """Called when formfav_sync skips a race (invalid code, missing fields, etc.)."""
    with _lock:
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["eligible"] = False
            entry["skip_reason"] = entry.get("skip_reason") or reason
            entry["status"] = "skipped"


def record_formfav_eligible(race_uid: str) -> None:
    """Called when a race passes all FormFav eligibility checks."""
    with _lock:
        _state["total_formfav_eligible"] += 1
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["eligible"] = True


def record_formfav_called(race_uid: str) -> None:
    """Called when a FormFav API call is issued for a race."""
    with _lock:
        _state["total_formfav_called"] += 1
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["formfav_called"] = True


def record_formfav_success(race_uid: str) -> None:
    """Called when a FormFav API call succeeds and enrichment is stored."""
    with _lock:
        _state["total_formfav_success"] += 1
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["status"] = "success"


def record_formfav_failed(race_uid: str) -> None:
    """Called when a FormFav API call fails."""
    with _lock:
        _state["total_formfav_failed"] += 1
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["status"] = "failed"


def get_state() -> dict[str, Any]:
    """Return a snapshot of the current pipeline state (thread-safe copy)."""
    with _lock:
        return {
            **_state,
            "recent_races": [dict(e) for e in _recent_races],
        }
