"""
services/health_service.py - DemonPulse Live Health Service
=============================================================
Thread-safe in-memory health metrics store for the live engine.

Tracks:
  - last successful bootstrap
  - last successful broad refresh
  - last successful near-jump refresh
  - last successful result check
  - last successful FormFav overlay check
  - blocked race count
  - stale race count
  - result confirmation count

Exposed via /api/health/live for observability.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_lock = threading.Lock()

_state: dict[str, Any] = {
    "last_bootstrap_at": None,
    "last_bootstrap_ok": None,
    "last_broad_refresh_at": None,
    "last_broad_refresh_ok": None,
    "last_broad_refresh_races": 0,
    "last_near_jump_refresh_at": None,
    "last_near_jump_refresh_ok": None,
    "last_near_jump_refresh_races": 0,
    "last_result_check_at": None,
    "last_result_check_ok": None,
    "last_formfav_overlay_at": None,
    "last_formfav_overlay_ok": None,
    "last_health_snapshot_at": None,
    "blocked_race_count": 0,
    "stale_race_count": 0,
    "result_confirmation_count": 0,
    # Phase 3 — intelligence layer
    "last_prediction_run_at": None,
    "last_prediction_run_count": 0,
    "last_backtest_run_at": None,
    "last_backtest_run_id": None,
    "last_evaluation_run_at": None,
    "last_evaluation_run_count": 0,
    "active_model_version": "baseline_v1",
    # Phase 4 — feature engine / sectionals
    "last_feature_build_at": None,
    "last_feature_build_count": 0,
    "last_sectional_extraction_at": None,
    "last_sectional_extraction_count": 0,
    "last_race_shape_build_at": None,
    "last_race_shape_build_count": 0,
}


# ---------------------------------------------------------------------------
# RECORD HELPERS — called by scheduler and services
# ---------------------------------------------------------------------------

def record_bootstrap(*, ok: bool, result: dict[str, Any] | None = None) -> None:
    """Record the result of a full bootstrap sweep."""
    _update(
        last_bootstrap_at=_now(),
        last_bootstrap_ok=ok,
    )
    log.debug(f"health_service: bootstrap recorded ok={ok}")


def record_broad_refresh(*, ok: bool, races_refreshed: int = 0) -> None:
    """Record the result of a broad OddsPro refresh cycle."""
    _update(
        last_broad_refresh_at=_now(),
        last_broad_refresh_ok=ok,
        last_broad_refresh_races=races_refreshed,
    )
    log.debug(f"health_service: broad_refresh recorded ok={ok} races={races_refreshed}")


def record_near_jump_refresh(*, ok: bool, races: int = 0) -> None:
    """Record the result of a near-jump refresh cycle."""
    _update(
        last_near_jump_refresh_at=_now(),
        last_near_jump_refresh_ok=ok,
        last_near_jump_refresh_races=races,
    )
    log.debug(f"health_service: near_jump_refresh recorded ok={ok} races={races}")


def record_result_check(*, ok: bool, confirmations: int = 0) -> None:
    """Record the result of a result check cycle."""
    _update(
        last_result_check_at=_now(),
        last_result_check_ok=ok,
    )
    if ok and confirmations > 0:
        with _lock:
            _state["result_confirmation_count"] += confirmations
    log.debug(f"health_service: result_check recorded ok={ok} confirmations={confirmations}")


def record_formfav_overlay(*, ok: bool) -> None:
    """Record the result of a FormFav overlay check."""
    _update(
        last_formfav_overlay_at=_now(),
        last_formfav_overlay_ok=ok,
    )
    log.debug(f"health_service: formfav_overlay recorded ok={ok}")


# ---------------------------------------------------------------------------
# PHASE 3 — INTELLIGENCE LAYER RECORD HELPERS
# ---------------------------------------------------------------------------

def record_prediction_run(*, count: int = 0) -> None:
    """Record completion of a prediction generation run."""
    _update(
        last_prediction_run_at=_now(),
        last_prediction_run_count=count,
    )
    log.debug(f"health_service: prediction_run recorded count={count}")


def record_backtest_run(*, run_id: str = "") -> None:
    """Record completion of a backtest run."""
    _update(
        last_backtest_run_at=_now(),
        last_backtest_run_id=run_id,
    )
    log.debug(f"health_service: backtest_run recorded run_id={run_id}")


def record_evaluation_run(*, count: int = 0) -> None:
    """Record completion of a prediction evaluation pass."""
    _update(
        last_evaluation_run_at=_now(),
        last_evaluation_run_count=count,
    )
    log.debug(f"health_service: evaluation_run recorded count={count}")


# ---------------------------------------------------------------------------
# PHASE 4 — FEATURE ENGINE / SECTIONALS RECORD HELPERS
# ---------------------------------------------------------------------------

def record_feature_build(*, count: int = 0) -> None:
    """Record completion of a feature build run."""
    _update(
        last_feature_build_at=_now(),
        last_feature_build_count=count,
    )
    log.debug(f"health_service: feature_build recorded count={count}")


def record_sectional_extraction(*, count: int = 0) -> None:
    """Record completion of a sectional extraction pass."""
    _update(
        last_sectional_extraction_at=_now(),
        last_sectional_extraction_count=count,
    )
    log.debug(f"health_service: sectional_extraction recorded count={count}")


def record_race_shape_build(*, count: int = 0) -> None:
    """Record completion of a race shape build pass."""
    _update(
        last_race_shape_build_at=_now(),
        last_race_shape_build_count=count,
    )
    log.debug(f"health_service: race_shape_build recorded count={count}")


def set_active_model_version(model_version: str) -> None:
    """Update the active model version label."""
    _update(active_model_version=model_version)
    log.info(f"health_service: active_model_version set to {model_version}")


def update_snapshot(
    *,
    blocked: int = 0,
    stale: int = 0,
    confirmations: int | None = None,
) -> None:
    """
    Update aggregate counts from a health snapshot cycle.
    confirmations is only updated if explicitly provided (not None).
    """
    updates: dict[str, Any] = {
        "last_health_snapshot_at": _now(),
        "blocked_race_count": blocked,
        "stale_race_count": stale,
    }
    if confirmations is not None:
        updates["result_confirmation_count"] = confirmations
    _update(**updates)
    log.debug(
        f"health_service: snapshot updated blocked={blocked} stale={stale}"
    )


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------

def get_health() -> dict[str, Any]:
    """Return a copy of the current health state."""
    with _lock:
        return dict(_state)


def is_engine_healthy() -> bool:
    """
    Rough liveness check: returns True if at least one bootstrap or
    broad refresh has completed successfully in this process lifetime.
    """
    with _lock:
        return bool(
            _state.get("last_bootstrap_ok")
            or _state.get("last_broad_refresh_ok")
        )


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update(**kwargs: Any) -> None:
    with _lock:
        _state.update(kwargs)
