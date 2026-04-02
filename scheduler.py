"""
scheduler.py - DemonPulse Scheduler (Render Safe)

Handles:
- Full sweep on startup
- Rolling refresh loop
- Result checks
- Thread-safe scheduler status reporting
- Safer startup / restart behaviour
"""

import time
import logging
import threading
from datetime import datetime

from data_engine import full_sweep, rolling_refresh

log = logging.getLogger(__name__)


# --------------------------------------------------------
# CONFIG
# --------------------------------------------------------
FULL_SWEEP_ON_START = True
REFRESH_INTERVAL = 150          # 2.5 minutes
RESULT_CHECK_INTERVAL = 300     # 5 minutes
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
    "last_full_sweep_at": None,
    "last_full_sweep_result": None,
    "last_refresh_at": None,
    "last_refresh_result": None,
    "last_result_check_at": None,
    "last_result_check_result": None,
    "last_error": None,
    "refresh_interval": REFRESH_INTERVAL,
    "result_check_interval": RESULT_CHECK_INTERVAL,
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


def _run_full_sweep():
    log.info("Running initial full sweep...")
    result = full_sweep()
    log.info(f"Initial sweep complete: {result}")
    _set_status(
        last_full_sweep_at=_utc_now(),
        last_full_sweep_result=result,
        last_error=None,
    )
    return result


def _run_refresh():
    log.info("Running rolling refresh...")
    result = rolling_refresh()
    log.info(f"Refresh result: {result}")
    _set_status(
        last_refresh_at=_utc_now(),
        last_refresh_result=result,
        last_error=None,
    )
    return result


def _run_result_check():
    """
    Separate status bucket for result-check pass.
    For now this still uses rolling_refresh(), but keeps result-check
    telemetry distinct so it can be split later without changing app.py.
    """
    log.info("Running result check...")
    result = rolling_refresh()
    log.info(f"Result check: {result}")
    _set_status(
        last_result_check_at=_utc_now(),
        last_result_check_result=result,
        last_error=None,
    )
    return result


# --------------------------------------------------------
# MAIN LOOP
# --------------------------------------------------------
def run_scheduler():
    global _scheduler_thread

    log.info("=== SCHEDULER STARTED ===")
    _set_status(
        running=True,
        thread_alive=True,
        started_at=_utc_now(),
        last_error=None,
    )

    now = time.time()
    last_refresh = now
    last_result_check = now

    if FULL_SWEEP_ON_START:
        try:
            _run_full_sweep()
            # After full sweep, reset timers so we do not immediately
            # hammer refresh + result check again on first loop tick.
            now = time.time()
            last_refresh = now
            last_result_check = now
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

            if now - last_refresh >= REFRESH_INTERVAL:
                _run_refresh()
                last_refresh = now

            if now - last_result_check >= RESULT_CHECK_INTERVAL:
                _run_result_check()
                last_result_check = now

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
    log.info("Scheduler started (threaded)")


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
