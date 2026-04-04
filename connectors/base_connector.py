"""
connectors/base_connector.py - DemonPulse V8 Connector Contract

Every connector must inherit from BaseConnector and implement the
standard interface defined here.

Connector response envelope:
{
  "source": str,
  "status": "ok" | "partial" | "failed" | "blocked",
  "confidence": 0.0–1.0,
  "fetched_at": ISO timestamp str,
  "error": null | str,
  "meta": {
    "request_url": str,
    "response_type": "json" | "html" | "browser" | "api",
    "latency_ms": int,
  },
  "data": {
    "meetings": [...],
    "races": [...],
    "runners": [...],
    "odds": [...],
    "results": [...],
  }
}

Laws enforced here:
- status must reflect reality, not optimism
- confidence is computed, not hardcoded
- empty data is never silently counted as success
- partial data is explicitly marked partial
- blocked requests are explicitly marked blocked
- all timestamps must be UTC ISO strings
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------
# CONNECTOR STATUS CONSTANTS
# ---------------------------------------------------------------
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
STATUS_DISABLED = "disabled"

# ---------------------------------------------------------------
# RESPONSE HELPERS
# ---------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_envelope(
    source: str,
    *,
    status: str = STATUS_FAILED,
    confidence: float = 0.0,
    error: str | None = None,
    request_url: str = "",
    response_type: str = "api",
    latency_ms: int = 0,
    meetings: list | None = None,
    races: list | None = None,
    runners: list | None = None,
    odds: list | None = None,
    results: list | None = None,
) -> dict[str, Any]:
    """Build a standard connector response envelope."""
    return {
        "source": source,
        "status": status,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "fetched_at": _utc_now(),
        "error": error,
        "meta": {
            "request_url": request_url,
            "response_type": response_type,
            "latency_ms": latency_ms,
        },
        "data": {
            "meetings": meetings or [],
            "races": races or [],
            "runners": runners or [],
            "odds": odds or [],
            "results": results or [],
        },
    }


def make_blocked_envelope(source: str, reason: str, request_url: str = "") -> dict[str, Any]:
    """Return a blocked response envelope."""
    return make_envelope(
        source,
        status=STATUS_BLOCKED,
        confidence=0.0,
        error=f"BLOCKED: {reason}",
        request_url=request_url,
    )


def make_failed_envelope(source: str, error: str, request_url: str = "") -> dict[str, Any]:
    """Return a failed response envelope."""
    return make_envelope(
        source,
        status=STATUS_FAILED,
        confidence=0.0,
        error=error,
        request_url=request_url,
    )


def make_disabled_envelope(source: str) -> dict[str, Any]:
    """Return a disabled (not configured) response envelope."""
    return make_envelope(
        source,
        status=STATUS_DISABLED,
        confidence=0.0,
        error="DISABLED: connector not configured",
    )


# ---------------------------------------------------------------
# NORMALIZED DATA MODELS
# ---------------------------------------------------------------

def make_meeting(
    *,
    meeting_id_internal: str,
    source_meeting_id: str,
    source: str,
    code: str,
    track_name: str,
    country: str = "AU",
    state: str = "",
    meeting_date: str,
    fetched_at: str | None = None,
    status: str = "unknown",
    extra: dict | None = None,
) -> dict[str, Any]:
    return {
        "meeting_id_internal": meeting_id_internal,
        "source_meeting_id": source_meeting_id,
        "source": source,
        "code": code,
        "track_name": track_name,
        "country": country,
        "state": state,
        "meeting_date": meeting_date,
        "fetched_at": fetched_at or _utc_now(),
        "status": status,
        "extra": extra or {},
    }


def make_race(
    *,
    race_id_internal: str,
    source_race_id: str,
    source: str,
    meeting_id_internal: str,
    race_number: int,
    scheduled_jump_time: str | None = None,
    distance: str = "",
    grade: str = "",
    status: str = "unknown",
    fetched_at: str | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    return {
        "race_id_internal": race_id_internal,
        "source_race_id": source_race_id,
        "source": source,
        "meeting_id_internal": meeting_id_internal,
        "race_number": race_number,
        "scheduled_jump_time": scheduled_jump_time,
        "distance": distance,
        "grade": grade,
        "status": status,
        "fetched_at": fetched_at or _utc_now(),
        "extra": extra or {},
    }


def make_runner(
    *,
    runner_id_internal: str,
    source_runner_id: str,
    source: str,
    race_id_internal: str,
    box_or_barrier: int | None,
    runner_name: str,
    trainer: str = "",
    odds_win: float | None = None,
    odds_place: float | None = None,
    scratched: bool = False,
    raw_number: int | None = None,
    fetched_at: str | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    return {
        "runner_id_internal": runner_id_internal,
        "source_runner_id": source_runner_id,
        "source": source,
        "race_id_internal": race_id_internal,
        "box_or_barrier": box_or_barrier,
        "runner_name": runner_name,
        "trainer": trainer,
        "odds_win": odds_win,
        "odds_place": odds_place,
        "scratched": scratched,
        "raw_number": raw_number,
        "fetched_at": fetched_at or _utc_now(),
        "extra": extra or {},
    }


def make_result(
    *,
    race_id_internal: str,
    source: str,
    result_status: str = "official",
    finishing_order: list | None = None,
    margins: list | None = None,
    official_time: str | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    return {
        "race_id_internal": race_id_internal,
        "source": source,
        "result_status": result_status,
        "finishing_order": finishing_order or [],
        "margins": margins or [],
        "official_time": official_time,
        "fetched_at": fetched_at or _utc_now(),
    }


# ---------------------------------------------------------------
# BASE CONNECTOR CLASS
# ---------------------------------------------------------------

class BaseConnector(ABC):
    """
    Abstract base class that all connectors must implement.

    Subclasses must:
    - set `source_name` class attribute
    - set `source_type` class attribute ("api" | "scrape" | "browser")
    - implement all abstract methods
    - always return a standard envelope dict from public methods
    - never silently swallow empty or failed fetches
    """

    source_name: str = "base"
    source_type: str = "api"

    def _timed_call(self, fn, *args, **kwargs) -> tuple[Any, int]:
        """Run fn(*args, **kwargs) and return (result, latency_ms)."""
        start = time.monotonic()
        result = fn(*args, **kwargs)
        latency_ms = int((time.monotonic() - start) * 1000)
        return result, latency_ms

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return True if this connector is properly configured and usable."""

    @abstractmethod
    def healthcheck(self) -> dict[str, Any]:
        """
        Check connector availability.
        Must return a dict with at least:
          {"ok": bool, "source": str, "reason": str | None}
        """

    @abstractmethod
    def fetch_meetings(self, target_date: str) -> dict[str, Any]:
        """
        Fetch all meetings for a given date.
        Must return a standard envelope.
        Empty meetings must NOT return status=ok.
        """

    @abstractmethod
    def fetch_race(self, meeting_id: str, race_number: int) -> dict[str, Any]:
        """
        Fetch details for a single race.
        Must return a standard envelope.
        """

    @abstractmethod
    def fetch_runners(self, race_id: str) -> dict[str, Any]:
        """
        Fetch runners for a race.
        Must return a standard envelope.
        """

    @abstractmethod
    def fetch_odds(self, race_id: str) -> dict[str, Any]:
        """
        Fetch current odds for a race.
        Must return a standard envelope.
        """

    def fetch_results(self, race_id: str) -> dict[str, Any]:
        """
        Fetch results for a completed race.
        Default: unsupported.
        Subclasses should override if the source supports results.
        """
        return make_failed_envelope(
            self.source_name,
            "fetch_results not supported by this connector",
        )

    def _not_enabled_response(self) -> dict[str, Any]:
        return make_disabled_envelope(self.source_name)

    def _blocked_response(self, reason: str, url: str = "") -> dict[str, Any]:
        return make_blocked_envelope(self.source_name, reason, url)

    def _failed_response(self, error: str, url: str = "") -> dict[str, Any]:
        return make_failed_envelope(self.source_name, error, url)
