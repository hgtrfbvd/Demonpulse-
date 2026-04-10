"""
services/result_service.py - DemonPulse Result Service
========================================================
Result handling for the live engine.
Results are detected from stored race times (no external result APIs).

Rules:
  - Race states are derived from stored jump_time
  - Board is rebuilt after status changes
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

log = logging.getLogger(__name__)


def run_result_sweep(target_date: str | None = None) -> dict[str, Any]:
    """
    Detect jumped races and update statuses from stored jump times.
    """
    today = target_date or date.today().isoformat()
    try:
        from database import get_races_for_date, update_race_status
        from race_status import bulk_update_race_states
        from services.health_service import record_result_check

        races = get_races_for_date(today)
        changes = bulk_update_race_states(races)
        for race_uid, old_status, new_status in changes:
            update_race_status(race_uid, new_status)

        ok = True
        confirmed = len(changes)
        record_result_check(ok=ok, confirmations=confirmed)

        if confirmed > 0:
            _rebuild_after_results(today, confirmed)

        return {"ok": ok, "date": today, "status_changes": confirmed}

    except Exception as e:
        log.error(f"result_service: run_result_sweep failed: {e}")
        try:
            from services.health_service import record_result_check
            record_result_check(ok=False)
        except Exception:
            pass
        return {"ok": False, "error": "Result sweep failed", "date": today}


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _rebuild_after_results(target_date: str, confirmed_count: int) -> None:
    """Rebuild board after status changes."""
    try:
        from board_service import get_board_for_today
        get_board_for_today()
        log.info(
            f"result_service: board rebuilt after {confirmed_count} "
            f"status changes for {target_date}"
        )
    except Exception as e:
        log.error(f"result_service: board rebuild failed: {e}")
