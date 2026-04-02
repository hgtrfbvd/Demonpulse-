"""
scheduler.py - DemonPulse Scheduler (Render Safe)

Handles:
- Full sweep on startup
- Rolling refresh loop
- Result checks
"""

import time
import logging
import threading

from data_engine import full_sweep, rolling_refresh

log = logging.getLogger(__name__)


# --------------------------------------------------------
# CONFIG
# --------------------------------------------------------
FULL_SWEEP_ON_START = True

REFRESH_INTERVAL = 150        # 2.5 minutes
RESULT_CHECK_INTERVAL = 300   # 5 minutes

_scheduler_started = False


# --------------------------------------------------------
# MAIN LOOP (SINGLE THREAD SAFE)
# --------------------------------------------------------
def run_scheduler():
    log.info("=== SCHEDULER STARTED ===")

    last_refresh = 0
    last_result_check = 0

    # ----------------------------------------------------
    # INITIAL FULL SWEEP
    # ----------------------------------------------------
    if FULL_SWEEP_ON_START:
        try:
            log.info("Running initial full sweep...")
            result = full_sweep()
            log.info(f"Initial sweep complete: {result}")
        except Exception as e:
            log.error(f"Initial full sweep failed: {e}")

    # ----------------------------------------------------
    # LOOP
    # ----------------------------------------------------
    while True:
        now = time.time()

        try:
            # -------------------------
            # ROLLING REFRESH
            # -------------------------
            if now - last_refresh >= REFRESH_INTERVAL:
                log.info("Running rolling refresh...")
                result = rolling_refresh()
                log.info(f"Refresh result: {result}")
                last_refresh = now

            # -------------------------
            # RESULT CHECK (extra pass)
            # -------------------------
            if now - last_result_check >= RESULT_CHECK_INTERVAL:
                log.info("Running result check...")
                result = rolling_refresh()
                log.info(f"Result check: {result}")
                last_result_check = now

        except Exception as e:
            log.error(f"Scheduler loop error: {e}")

        time.sleep(10)


# --------------------------------------------------------
# STARTER FOR APP IMPORT
# --------------------------------------------------------
def start_scheduler():
    global _scheduler_started

    if _scheduler_started:
        log.info("Scheduler already started")
        return

    thread = threading.Thread(target=run_scheduler, daemon=True)
    thread.start()
    _scheduler_started = True
    log.info("Scheduler started (threaded)")


# --------------------------------------------------------
# ENTRY POINT
# --------------------------------------------------------
if __name__ == "__main__":
    run_scheduler()
