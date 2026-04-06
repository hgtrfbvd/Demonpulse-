"""
pipeline_state.py — DemonPulse Runtime Pipeline State Tracker
==============================================================
Thread-safe in-memory tracker for the OddsPro → FormFav pipeline.
Used by GET /api/debug/formfav to expose full pipeline state at runtime.

Data is accumulated during the current process lifetime (resets on restart).
The recent_races deque always holds the last 20 discovered races with their
latest pipeline status.

Counters are also persisted to the formfav_debug_stats database table after
each pipeline run so the debug endpoint reflects real execution state even
after process restarts.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_lock = threading.Lock()

_state: dict[str, Any] = {
    "total_races_discovered": 0,
    "total_domestic_races": 0,
    "total_international_filtered": 0,
    # Merge-stage FormFav counters (full_sweep / data_engine)
    "formfav_merge_called": 0,
    "formfav_merge_matched": 0,
    "formfav_merge_failed": 0,
    # Sync-stage FormFav counters (formfav_sync)
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


_COUNTER_KEYS = (
    "total_races_discovered",
    "total_domestic_races",
    "total_international_filtered",
    # Merge-stage
    "formfav_merge_called",
    "formfav_merge_matched",
    "formfav_merge_failed",
    # Sync-stage
    "total_formfav_eligible",
    "total_formfav_called",
    "total_formfav_success",
    "total_formfav_failed",
)


def reset() -> None:
    """Reset all counters and clear recent_races (called at start of each full_sweep)."""
    with _lock:
        for k in _COUNTER_KEYS:
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
    """Called when a FormFav API call is issued for a race (sync stage)."""
    with _lock:
        _state["total_formfav_called"] += 1
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["formfav_called"] = True


def record_formfav_success(race_uid: str) -> None:
    """Called when a FormFav API call succeeds and enrichment is stored (sync stage)."""
    with _lock:
        _state["total_formfav_success"] += 1
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["status"] = "success"


def record_formfav_failed(race_uid: str) -> None:
    """Called when a FormFav API call fails (sync stage)."""
    with _lock:
        _state["total_formfav_failed"] += 1
        entry = _find_entry(race_uid)
        if entry is not None:
            entry["status"] = "failed"


# ---------------------------------------------------------------------------
# MERGE-STAGE FormFav counters (full_sweep / data_engine)
# ---------------------------------------------------------------------------

def record_formfav_merge_called(track: str) -> None:
    """Called for each FormFav race fetched during full_sweep merge stage."""
    with _lock:
        _state["formfav_merge_called"] += 1
    log.info(f"[FORMFAV][MERGE] CALLED track={track!r}")


def record_formfav_merge_matched(race_uid: str) -> None:
    """Called when a FormFav race matches an OddsPro race during merge stage."""
    with _lock:
        _state["formfav_merge_matched"] += 1
    log.info(f"[FORMFAV][MERGE] MATCHED race_uid={race_uid!r}")


def record_formfav_merge_failed(race_uid: str) -> None:
    """Called when a FormFav merge-stage race fails to be stored."""
    with _lock:
        _state["formfav_merge_failed"] += 1


def get_state() -> dict[str, Any]:
    """Return a snapshot of the current pipeline state (thread-safe copy)."""
    with _lock:
        snap = {**_state, "recent_races": [dict(e) for e in _recent_races]}
    # Add structured stage views for easy consumption by the debug endpoint
    snap["merge_stage"] = {
        "called":  snap["formfav_merge_called"],
        "matched": snap["formfav_merge_matched"],
        "failed":  snap["formfav_merge_failed"],
    }
    snap["sync_stage"] = {
        "called":  snap["total_formfav_called"],
        "success": snap["total_formfav_success"],
        "failed":  snap["total_formfav_failed"],
    }
    return snap


def persist_snapshot() -> None:
    """
    Persist the current counter snapshot to the formfav_debug_stats database
    table so it survives process restarts and is visible to all threads.

    Called at the end of full_sweep() and formfav_sync() in data_engine.py.
    Failures are logged at WARNING level and never raise.
    """
    snapshot = get_state()
    log.info(
        f"[DEBUG] COUNTERS updated"
        f" total_races_discovered={snapshot['total_races_discovered']}"
        f" total_domestic_races={snapshot['total_domestic_races']}"
        f" total_international_filtered={snapshot['total_international_filtered']}"
        f" formfav_merge_called={snapshot['formfav_merge_called']}"
        f" formfav_merge_matched={snapshot['formfav_merge_matched']}"
        f" formfav_merge_failed={snapshot['formfav_merge_failed']}"
        f" total_formfav_eligible={snapshot['total_formfav_eligible']}"
        f" total_formfav_called={snapshot['total_formfav_called']}"
        f" total_formfav_success={snapshot['total_formfav_success']}"
        f" total_formfav_failed={snapshot['total_formfav_failed']}"
    )
    try:
        from database import upsert_formfav_debug_stats
        upsert_formfav_debug_stats(snapshot)
    except Exception as e:
        log.warning(f"pipeline_state.persist_snapshot: could not write to DB: {e}")
