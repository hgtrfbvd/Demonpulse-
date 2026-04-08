"""
scheduler.py - DemonPulse Scheduler (Phase 2 Live Engine)
===========================================================
Continuous live engine scheduler. Runs all refresh cycles safely and
concurrently without destructive overlaps.

Cycles:
  startup bootstrap      - one-shot full OddsPro sweep on start
  broad_refresh          - OddsPro meeting/race refresh (default: 150s)
  near_jump              - OddsPro near-jump refresh + FormFav overlay (default: 60s)
  result_check           - OddsPro day-level result sweep (default: 300s)
  race_state_update      - drive race state machine from stored data (default: 90s)
  health_snapshot        - aggregate health metrics snapshot (default: 120s)
  formfav_sync           - FormFav persistent enrichment sync (default: 300s)

Safety rules:
  - per-cycle threading.Lock prevents overlapping destructive cycles
  - lock.acquire(blocking=False) — skip cycle if already running
  - errors are logged and preserved; cycles degrade safely
  - no false success: ok=False propagated when fetches fail
  - no duplicate cycle execution within the same interval
"""

import os
import time
import logging
import threading
from datetime import datetime, date

from data_engine import full_sweep, rolling_refresh, check_results, near_jump_refresh

log = logging.getLogger(__name__)


# --------------------------------------------------------
# CONFIG
# --------------------------------------------------------
FULL_SWEEP_ON_START = True
REFRESH_INTERVAL = 150          # 2.5 min — broad OddsPro refresh
NEAR_JUMP_INTERVAL = 60         # 1 min — near-jump OddsPro + FormFav
RESULT_CHECK_INTERVAL = 300     # 5 min — OddsPro result sweep
RACE_STATE_INTERVAL = 90        # 90 s — automated race state machine
HEALTH_SNAPSHOT_INTERVAL = 120  # 2 min — health metrics snapshot
FORMFAV_SYNC_INTERVAL = 180     # 3 min — FormFav persistent enrichment sync
MARKET_SNAPSHOT_INTERVAL = 300  # 5 min — OddsPro movers/drifters/top-favs
LOOP_SLEEP_SECONDS = 10
RESTART_BACKOFF_SECONDS = 5


# --------------------------------------------------------
# INTERNAL STATE
# --------------------------------------------------------
_scheduler_started = False
_scheduler_thread = None
_scheduler_lock = threading.Lock()

# Per-cycle locks — prevent overlapping destructive cycles
_broad_refresh_lock = threading.Lock()
_near_jump_lock = threading.Lock()
_result_check_lock = threading.Lock()
_state_update_lock = threading.Lock()

_scheduler_status = {
    "running": False,
    "thread_alive": False,
    "started_at": None,
    "last_loop_at": None,
    "last_full_sweep_at": None,
    "last_full_sweep_result": None,
    "last_refresh_at": None,
    "last_refresh_result": None,
    "last_near_jump_at": None,
    "last_near_jump_result": None,
    "last_result_check_at": None,
    "last_result_check_result": None,
    "last_race_state_update_at": None,
    "last_health_snapshot_at": None,
    "last_formfav_sync_at": None,
    "last_formfav_sync_result": None,
    "last_market_snapshot_at": None,
    "last_market_snapshot_result": None,
    "last_error": None,
    "refresh_interval": REFRESH_INTERVAL,
    "near_jump_interval": NEAR_JUMP_INTERVAL,
    "result_check_interval": RESULT_CHECK_INTERVAL,
    "race_state_interval": RACE_STATE_INTERVAL,
    "health_snapshot_interval": HEALTH_SNAPSHOT_INTERVAL,
    "formfav_sync_interval": FORMFAV_SYNC_INTERVAL,
    "market_snapshot_interval": MARKET_SNAPSHOT_INTERVAL,
}


# --------------------------------------------------------
# HELPERS
# --------------------------------------------------------
def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def _set_status(**kwargs):
    with _scheduler_lock:
        _scheduler_status.update(kwargs)


def get_status():
    global _scheduler_thread

    with _scheduler_lock:
        status = dict(_scheduler_status)

    status["thread_alive"] = bool(_scheduler_thread and _scheduler_thread.is_alive())
    return status


# --------------------------------------------------------
# CYCLE RUNNERS
# --------------------------------------------------------

def _run_full_sweep():
    log.info("Running initial full sweep...")
    result = full_sweep()
    ok = result.get("ok", False)
    error_msg = None if ok else (result.get("error") or result.get("reason") or "full_sweep_not_ok")
    if ok:
        log.info(f"Initial sweep complete: {result}")
    else:
        log.warning(f"Initial sweep returned not-ok: {result}")

    _set_status(
        last_full_sweep_at=_utc_now(),
        last_full_sweep_result=result,
        last_error=error_msg,
    )

    # Update health service
    try:
        from services.health_service import record_bootstrap
        record_bootstrap(ok=ok, result=result)
    except Exception:
        pass

    # Trigger FormFav second-stage enrichment immediately after ingestion so
    # races stored by full_sweep are enriched without waiting for the timer.
    if ok and result.get("races_stored", 0) > 0:
        log.info("scheduler: triggering FormFav sync after full sweep (second-stage enrichment)")
        _run_formfav_sync()

    return result


def _run_refresh():
    """Broad OddsPro refresh — skipped if already running."""
    acquired = _broad_refresh_lock.acquire(blocking=False)
    if not acquired:
        log.warning("Broad refresh still running — skipping this cycle")
        return {"ok": False, "reason": "cycle_already_running"}

    try:
        log.info("Running broad refresh...")
        result = rolling_refresh()
        ok = result.get("ok", False)
        if ok:
            log.info(f"Broad refresh result: {result}")
        else:
            log.warning(f"Broad refresh returned not-ok: {result}")

        _set_status(
            last_refresh_at=_utc_now(),
            last_refresh_result=result,
            last_error=None if ok else (result.get("error") or result.get("reason") or "refresh_not_ok"),
        )

        try:
            from services.health_service import record_broad_refresh
            record_broad_refresh(
                ok=ok,
                races_refreshed=result.get("races_refreshed", 0),
                error=result.get("error") or result.get("reason") if not ok else None,
            )
        except Exception:
            pass

        # Rebuild board after meaningful broad refresh
        if ok and result.get("races_refreshed", 0) > 0:
            _trigger_board_rebuild()

        return result
    finally:
        _broad_refresh_lock.release()


def _run_near_jump():
    """Near-jump OddsPro refresh + FormFav overlay — skipped if already running."""
    acquired = _near_jump_lock.acquire(blocking=False)
    if not acquired:
        log.debug("Near-jump refresh still running — skipping this cycle")
        return {"ok": False, "reason": "cycle_already_running"}

    try:
        log.debug("Running near-jump refresh...")
        result = near_jump_refresh()
        ok = result.get("ok", False)
        if result.get("near_jump_races", 0) > 0:
            log.info(f"Near-jump refresh: {result}")

        _set_status(
            last_near_jump_at=_utc_now(),
            last_near_jump_result=result,
            last_error=None if ok else (result.get("error") or result.get("reason") or "near_jump_not_ok"),
        )

        try:
            from services.health_service import record_near_jump_refresh, record_formfav_overlay
            record_near_jump_refresh(
                ok=ok,
                races=result.get("near_jump_races", 0),
                error=result.get("error") or result.get("reason") if not ok else None,
            )
            if result.get("formfav_overlays", 0) > 0:
                record_formfav_overlay(ok=True)
        except Exception:
            pass

        # Rebuild board if near-jump races were refreshed
        if ok and result.get("races_refreshed", 0) > 0:
            _trigger_board_rebuild()

        return result
    finally:
        _near_jump_lock.release()


def _run_result_check():
    """
    OddsPro result sweep — skipped if already running.
    Only marks confirmed results; no false success on failed fetches.
    """
    acquired = _result_check_lock.acquire(blocking=False)
    if not acquired:
        log.warning("Result check still running — skipping this cycle")
        return {"ok": False, "reason": "cycle_already_running"}

    try:
        log.info("Running result check...")
        result = check_results()
        ok = result.get("ok", False)
        if ok:
            log.info(f"Result check: {result}")
        else:
            log.warning(f"Result check returned not-ok: {result}")

        _set_status(
            last_result_check_at=_utc_now(),
            last_result_check_result=result,
            last_error=None if ok else (result.get("error") or result.get("reason") or "result_check_not_ok"),
        )

        try:
            from services.health_service import record_result_check
            record_result_check(
                ok=ok,
                confirmations=result.get("results_written", 0),
                error=result.get("error") or result.get("reason") if not ok else None,
            )
        except Exception:
            pass

        # Rebuild board after result confirmations
        if ok and result.get("results_written", 0) > 0:
            _trigger_race_state_update()
            _trigger_board_rebuild()

        return result
    finally:
        _result_check_lock.release()


def _run_race_state_update():
    """Drive the race state machine from stored data."""
    acquired = _state_update_lock.acquire(blocking=False)
    if not acquired:
        log.debug("Race state update still running — skipping")
        return

    try:
        today = date.today().isoformat()
        try:
            from database import get_races_for_date, update_race_status
            from race_status import bulk_update_race_states

            races = get_races_for_date(today)
            changes = bulk_update_race_states(races)
            for race_uid, old_status, new_status in changes:
                update_race_status(race_uid, new_status)
                log.debug(f"scheduler: race state {race_uid}: {old_status} → {new_status}")

            if changes:
                log.info(f"scheduler: race_state_update: {len(changes)} transitions for {today}")
                _trigger_board_rebuild()

            _set_status(last_race_state_update_at=_utc_now())

        except Exception as e:
            log.error(f"Race state update failed: {e}")
    finally:
        _state_update_lock.release()


def _run_health_snapshot():
    """Aggregate health metrics from stored data."""
    try:
        today = date.today().isoformat()
        from database import get_blocked_races, get_active_races, get_races_for_date
        from race_status import STATUS_STALE_UNKNOWN

        all_today = get_races_for_date(today)
        blocked = get_blocked_races(today)
        active = get_active_races(today)
        stale = [r for r in active if (r.get("status") or "") == STATUS_STALE_UNKNOWN]

        try:
            from services.health_service import update_snapshot
            update_snapshot(
                blocked=len(blocked),
                stale=len(stale),
                stored_today=len(all_today),
            )
        except Exception:
            pass

        _set_status(last_health_snapshot_at=_utc_now())
        log.debug(f"scheduler: health snapshot: blocked={len(blocked)} stale={len(stale)} stored={len(all_today)}")
    except Exception as e:
        log.error(f"Health snapshot failed: {e}")


def _run_formfav_sync():
    """
    FormFav persistent enrichment sync — stores full FormFav data for all
    today's active races in the formfav_race_enrichment / formfav_runner_enrichment
    tables. Skipped silently when FormFav is not enabled.
    """
    try:
        from data_engine import formfav_sync
        result = formfav_sync()
        ok = result.get("ok", False)
        races = result.get("races_enriched", 0)
        runners = result.get("runners_enriched", 0)

        _set_status(
            last_formfav_sync_at=_utc_now(),
            last_formfav_sync_result=result,
        )

        if races > 0:
            log.info(f"scheduler: formfav_sync: races={races} runners={runners}")
        else:
            log.debug(f"scheduler: formfav_sync: {result.get('reason', 'no races enriched')}")
    except Exception as e:
        log.error(f"FormFav sync failed: {e}")


def _run_market_snapshot():
    """Fetch OddsPro movers/drifters/top-favs and store in market_snapshots."""
    try:
        from data_engine import market_snapshot_sweep
        result = market_snapshot_sweep()
        _set_status(
            last_market_snapshot_at=_utc_now(),
            last_market_snapshot_result=result,
        )
        stored = result.get("stored", 0)
        if stored > 0:
            log.info(f"scheduler: market_snapshot: stored={stored}")
        else:
            log.debug(f"scheduler: market_snapshot: {result.get('reason', 'nothing stored')}")
    except Exception as e:
        log.error(f"Market snapshot failed: {e}")


# --------------------------------------------------------
# BOARD REBUILD HELPERS
# --------------------------------------------------------

def _trigger_board_rebuild():
    """Trigger a board rebuild from stored validated data."""
    try:
        from board_builder import get_board_for_today
        result = get_board_for_today()
        count = result.get("count", 0)
        try:
            from services.health_service import record_board_rebuild
            record_board_rebuild(count=count)
        except Exception:
            pass
        log.debug(f"scheduler: board rebuilt count={count}")
    except Exception as e:
        log.error(f"scheduler: board rebuild failed: {e}")


def _trigger_race_state_update():
    """Synchronously run a race state update (e.g. after results arrive)."""
    try:
        _run_race_state_update()
    except Exception as e:
        log.error(f"scheduler: triggered race state update failed: {e}")


# --------------------------------------------------------
# MAIN LOOP
# --------------------------------------------------------
def run_scheduler():
    global _scheduler_thread

    log.info("=== SCHEDULER STARTED (Phase 2 Live Engine) ===")
    _set_status(
        running=True,
        thread_alive=True,
        started_at=_utc_now(),
        last_error=None,
    )

    now = time.time()
    last_refresh = now
    last_near_jump = now
    last_result_check = now
    last_race_state = now
    last_health_snapshot = now
    last_formfav_sync = now  # full_sweep triggers formfav_sync directly when races are stored
    last_market_snapshot = now - MARKET_SNAPSHOT_INTERVAL  # run on first loop

    if FULL_SWEEP_ON_START:
        try:
            _run_full_sweep()
            # Reset timers after bootstrap so we don't immediately hammer every cycle
            now = time.time()
            last_refresh = now
            last_near_jump = now
            last_result_check = now
            last_race_state = now
            last_health_snapshot = now
        except Exception as e:
            log.error(f"Initial full sweep failed: {e}")
            _set_status(
                last_full_sweep_at=_utc_now(),
                last_full_sweep_result={"ok": False, "error": str(e)},
                last_error=f"initial_full_sweep: {e}",
            )

    while True:
        try:
            _set_status(
                running=True,
                thread_alive=bool(_scheduler_thread and _scheduler_thread.is_alive()),
                last_loop_at=_utc_now(),
            )

            now = time.time()

            if now - last_near_jump >= NEAR_JUMP_INTERVAL:
                _run_near_jump()
                last_near_jump = now

            if now - last_refresh >= REFRESH_INTERVAL:
                _run_refresh()
                last_refresh = now

            if now - last_result_check >= RESULT_CHECK_INTERVAL:
                _run_result_check()
                last_result_check = now

            if now - last_race_state >= RACE_STATE_INTERVAL:
                _run_race_state_update()
                last_race_state = now

            if now - last_health_snapshot >= HEALTH_SNAPSHOT_INTERVAL:
                _run_health_snapshot()
                last_health_snapshot = now

            if now - last_formfav_sync >= FORMFAV_SYNC_INTERVAL:
                _run_formfav_sync()
                last_formfav_sync = now

            if now - last_market_snapshot >= MARKET_SNAPSHOT_INTERVAL:
                _run_market_snapshot()
                last_market_snapshot = now

        except Exception as e:
            log.error(f"Scheduler loop error: {e}")
            _set_status(last_error=f"scheduler_loop: {e}")

        time.sleep(LOOP_SLEEP_SECONDS)


def _scheduler_runner():
    """
    Protective wrapper so an unexpected fatal error does not leave
    stale status claiming the scheduler is healthy.
    """
    try:
        run_scheduler()
    except Exception as e:
        log.exception(f"Scheduler thread crashed fatally: {e}")
        _set_status(
            running=False,
            thread_alive=False,
            last_error=f"fatal_scheduler_crash: {e}",
        )
        raise
    finally:
        _set_status(
            running=False,
            thread_alive=False,
        )


# --------------------------------------------------------
# STARTER FOR APP IMPORT
# --------------------------------------------------------
def start_scheduler():
    global _scheduler_started, _scheduler_thread

    if os.getenv("SCHEDULER_ENABLED", "true") != "true":
        log.info("Scheduler disabled by SCHEDULER_ENABLED env var — not starting")
        return

    with _scheduler_lock:
        if _scheduler_thread and _scheduler_thread.is_alive():
            log.info("Scheduler already started")
            return

        _scheduler_thread = threading.Thread(
            target=_scheduler_runner,
            name="demonpulse-scheduler",
            daemon=True,
        )
        _scheduler_thread.start()
        _scheduler_started = True

    _set_status(
        running=True,
        thread_alive=True,
        last_error=None,
    )
    log.info("Scheduler started (threaded, Phase 2 Live Engine)")


# --------------------------------------------------------
# ENTRY POINT
# --------------------------------------------------------
if __name__ == "__main__":
    while True:
        try:
            _scheduler_runner()
            break
        except Exception as e:
            log.exception(f"Standalone scheduler crashed, restarting in {RESTART_BACKOFF_SECONDS}s: {e}")
            time.sleep(RESTART_BACKOFF_SECONDS)
