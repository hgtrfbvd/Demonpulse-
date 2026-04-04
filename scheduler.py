"""
scheduler.py - DemonPulse V8 Scheduler (Render Safe)

Handles:
- Full sweep on startup
- Separate rolling refresh loop (meetings + near-start races)
- Separate scratchings refresh loop
- Separate result detection loop
- Connector health check loop
- Thread-safe scheduler status reporting
- Safer startup / restart behaviour

Law 6: A refresh cycle is not considered successful unless at least one
       approved production-safe external source fetch succeeded and
       validation rules passed.
"""

import time
import logging
import threading
from datetime import datetime

from data_engine import full_sweep, rolling_refresh, check_results, check_connector_health

log = logging.getLogger(__name__)


# --------------------------------------------------------
# CONFIG
# --------------------------------------------------------
FULL_SWEEP_ON_START = True

# Separate interval constants (seconds)
MEETINGS_REFRESH_INTERVAL = 180      # 3 minutes — meeting discovery
RACE_DETAIL_REFRESH_INTERVAL = 90    # 1.5 minutes — near-start race detail
SCRATCHINGS_REFRESH_INTERVAL = 90    # 1.5 minutes — scratchings
RESULT_CHECK_INTERVAL = 180          # 3 minutes — result detection
HEALTH_CHECK_INTERVAL = 300          # 5 minutes — connector availability

LOOP_SLEEP_SECONDS = 10
RESTART_BACKOFF_SECONDS = 5


# --------------------------------------------------------
# INTERNAL STATE
# --------------------------------------------------------
_scheduler_started = False
_scheduler_thread = None
_scheduler_lock = threading.Lock()

_scheduler_status = {
    "running": False,
    "thread_alive": False,
    "started_at": None,
    "last_loop_at": None,
    # Full sweep
    "last_full_sweep_at": None,
    "last_full_sweep_result": None,
    # Meetings refresh
    "last_meetings_refresh_at": None,
    "last_meetings_refresh_result": None,
    # Race detail refresh
    "last_race_detail_refresh_at": None,
    "last_race_detail_refresh_result": None,
    # Scratchings
    "last_scratchings_refresh_at": None,
    "last_scratchings_refresh_result": None,
    # Results
    "last_result_check_at": None,
    "last_result_check_result": None,
    # Connector health
    "last_health_check_at": None,
    "last_health_check_result": None,
    # Error tracking
    "last_error": None,
    # Config echo
    "meetings_refresh_interval": MEETINGS_REFRESH_INTERVAL,
    "race_detail_refresh_interval": RACE_DETAIL_REFRESH_INTERVAL,
    "scratchings_refresh_interval": SCRATCHINGS_REFRESH_INTERVAL,
    "result_check_interval": RESULT_CHECK_INTERVAL,
    "health_check_interval": HEALTH_CHECK_INTERVAL,
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
# INDIVIDUAL CYCLE RUNNERS
# --------------------------------------------------------

def _run_full_sweep():
    log.info("[scheduler] Running full sweep...")
    result = full_sweep()
    ok = result.get("ok", False)
    log.info("[scheduler] Full sweep complete: ok=%s meetings=%d", ok, result.get("meetings_found", 0))
    _set_status(
        last_full_sweep_at=_utc_now(),
        last_full_sweep_result=result,
        last_error=None if ok else f"full_sweep: {result.get('reason', 'unknown')}",
    )
    return result


def _run_meetings_refresh():
    """Refresh meeting discovery from all sources."""
    log.info("[scheduler] Running meetings refresh...")
    result = rolling_refresh()
    ok = result.get("ok", False)
    log.info("[scheduler] Meetings refresh: ok=%s", ok)
    _set_status(
        last_meetings_refresh_at=_utc_now(),
        last_meetings_refresh_result=result,
        last_error=None if ok else f"meetings_refresh: {result.get('reason', 'unknown')}",
    )
    return result


def _run_race_detail_refresh():
    """Refresh near-start race details (odds, scratchings, status)."""
    log.info("[scheduler] Running race detail refresh...")
    result = rolling_refresh()
    ok = result.get("ok", False)
    log.info("[scheduler] Race detail refresh: ok=%s", ok)
    _set_status(
        last_race_detail_refresh_at=_utc_now(),
        last_race_detail_refresh_result=result,
        last_error=None if ok else f"race_detail_refresh: {result.get('reason', 'unknown')}",
    )
    return result


def _run_scratchings_refresh():
    """Refresh scratchings for active races."""
    log.info("[scheduler] Running scratchings refresh...")
    # Scratchings refresh uses rolling_refresh which handles FormFav/TheDogs
    result = rolling_refresh()
    ok = result.get("ok", False)
    log.info("[scheduler] Scratchings refresh: ok=%s", ok)
    _set_status(
        last_scratchings_refresh_at=_utc_now(),
        last_scratchings_refresh_result=result,
        last_error=None if ok else f"scratchings_refresh: {result.get('reason', 'unknown')}",
    )
    return result


def _run_result_check():
    """Check for new race results."""
    log.info("[scheduler] Running result check...")
    result = check_results()
    ok = result.get("ok", False)
    log.info("[scheduler] Result check: ok=%s captured=%d", ok, result.get("results_captured", 0))
    _set_status(
        last_result_check_at=_utc_now(),
        last_result_check_result=result,
        last_error=None if ok else f"result_check: {result.get('reason', 'unknown')}",
    )
    return result


def _run_health_check():
    """Check connector availability."""
    log.info("[scheduler] Running health check...")
    result = check_connector_health()
    log.info("[scheduler] Health check: %s", {k: v.get("ok") for k, v in result.items()})
    _set_status(
        last_health_check_at=_utc_now(),
        last_health_check_result=result,
    )
    return result


# --------------------------------------------------------
# MAIN LOOP
# --------------------------------------------------------
def run_scheduler():
    global _scheduler_thread

    log.info("[scheduler] === SCHEDULER STARTED ===")
    _set_status(
        running=True,
        thread_alive=True,
        started_at=_utc_now(),
        last_error=None,
    )

    now = time.time()
    last_meetings_refresh = now
    last_race_detail_refresh = now
    last_scratchings_refresh = now
    last_result_check = now
    last_health_check = now

    if FULL_SWEEP_ON_START:
        try:
            _run_full_sweep()
            # After full sweep, reset all timers so we don't immediately
            # trigger every loop on first tick.
            now = time.time()
            last_meetings_refresh = now
            last_race_detail_refresh = now
            last_scratchings_refresh = now
            last_result_check = now
            last_health_check = now
        except Exception as e:
            log.error("[scheduler] Initial full sweep failed: %s", e)
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

            # Meetings refresh — every 3 minutes
            if now - last_meetings_refresh >= MEETINGS_REFRESH_INTERVAL:
                _run_meetings_refresh()
                last_meetings_refresh = now

            # Race detail refresh — every 1.5 minutes
            if now - last_race_detail_refresh >= RACE_DETAIL_REFRESH_INTERVAL:
                _run_race_detail_refresh()
                last_race_detail_refresh = now

            # Scratchings refresh — every 1.5 minutes
            if now - last_scratchings_refresh >= SCRATCHINGS_REFRESH_INTERVAL:
                _run_scratchings_refresh()
                last_scratchings_refresh = now

            # Result check — every 3 minutes
            if now - last_result_check >= RESULT_CHECK_INTERVAL:
                _run_result_check()
                last_result_check = now

            # Health check — every 5 minutes
            if now - last_health_check >= HEALTH_CHECK_INTERVAL:
                _run_health_check()
                last_health_check = now

        except Exception as e:
            log.error("[scheduler] Loop error: %s", e)
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
        log.exception("[scheduler] Scheduler thread crashed fatally: %s", e)
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

    with _scheduler_lock:
        if _scheduler_thread and _scheduler_thread.is_alive():
            log.info("[scheduler] Already started")
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
    log.info("[scheduler] Scheduler started (threaded)")


# --------------------------------------------------------
# ENTRY POINT
# --------------------------------------------------------
if __name__ == "__main__":
    while True:
        try:
            _scheduler_runner()
            break
        except Exception as e:
            log.exception("[scheduler] Standalone scheduler crashed, restarting in %ds: %s", RESTART_BACKOFF_SECONDS, e)
            time.sleep(RESTART_BACKOFF_SECONDS)
