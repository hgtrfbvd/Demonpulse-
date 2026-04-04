"""
services/odds_service.py - DemonPulse Odds Service
====================================================
OddsPro data service for broad and near-jump refresh cycles.

Architecture rules:
  - OddsPro is primary and authoritative for all data
  - Broad refresh: periodic meeting/race refresh via OddsPro
  - Near-jump: frequent refresh for races < 10 min from jump
  - FormFav overlay: applied only for near-jump races, never authoritative
  - Board rebuild triggered after meaningful updates
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

log = logging.getLogger(__name__)


def run_broad_refresh(target_date: str | None = None) -> dict[str, Any]:
    """
    Broad OddsPro meeting/race refresh cycle.

    Updates races/runners/odds from the authoritative OddsPro source.
    Avoids duplicate race creation. Preserves authoritative truth.
    Rebuilds board after broad refresh if changes occurred.
    """
    today = target_date or date.today().isoformat()
    try:
        from data_engine import rolling_refresh
        from services.health_service import record_broad_refresh

        result = rolling_refresh(today)
        ok = result.get("ok", False)
        record_broad_refresh(ok=ok, races_refreshed=result.get("races_refreshed", 0))

        if ok and result.get("races_refreshed", 0) > 0:
            _trigger_board_rebuild()

        return result

    except Exception as e:
        log.error(f"odds_service: run_broad_refresh failed: {e}")
        try:
            from services.health_service import record_broad_refresh
            record_broad_refresh(ok=False)
        except Exception:
            pass
        return {"ok": False, "error": "Broad refresh failed", "date": today}


def run_near_jump_refresh(target_date: str | None = None) -> dict[str, Any]:
    """
    Near-jump refresh cycle: OddsPro authoritative refresh + FormFav overlay.

    Runs more frequently than broad refresh (every ~60s).
    Only processes races with NTJ < 10 min.
    FormFav overlay is provisional enrichment only — never overwrites
    authoritative OddsPro fields.
    """
    today = target_date or date.today().isoformat()
    try:
        from data_engine import near_jump_refresh
        from services.health_service import record_near_jump_refresh, record_formfav_overlay

        result = near_jump_refresh(today)
        ok = result.get("ok", False)
        record_near_jump_refresh(ok=ok, races=result.get("near_jump_races", 0))

        if result.get("formfav_overlays", 0) > 0:
            record_formfav_overlay(ok=True)

        if ok and result.get("races_refreshed", 0) > 0:
            _trigger_board_rebuild()

        return result

    except Exception as e:
        log.error(f"odds_service: run_near_jump_refresh failed: {e}")
        try:
            from services.health_service import record_near_jump_refresh
            record_near_jump_refresh(ok=False)
        except Exception:
            pass
        return {"ok": False, "error": "Near-jump refresh failed", "date": today}


def run_bootstrap(target_date: str | None = None) -> dict[str, Any]:
    """
    Daily OddsPro bootstrap sweep.
    Uses full_sweep() to discover all meetings and races for the day.
    FormFav is NOT called here.
    """
    today = target_date or date.today().isoformat()
    try:
        from data_engine import full_sweep
        from services.health_service import record_bootstrap

        result = full_sweep(today)
        ok = result.get("ok", False)
        record_bootstrap(ok=ok, result=result)

        if ok and result.get("races", 0) > 0:
            _trigger_board_rebuild()

        return result

    except Exception as e:
        log.error(f"odds_service: run_bootstrap failed: {e}")
        try:
            from services.health_service import record_bootstrap
            record_bootstrap(ok=False)
        except Exception:
            pass
        return {"ok": False, "error": "Bootstrap failed", "date": today}


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _trigger_board_rebuild() -> None:
    """Trigger a board rebuild from stored validated data."""
    try:
        from board_builder import get_board_for_today
        get_board_for_today()
        log.debug("odds_service: board rebuild triggered after refresh")
    except Exception as e:
        log.error(f"odds_service: board rebuild failed: {e}")
