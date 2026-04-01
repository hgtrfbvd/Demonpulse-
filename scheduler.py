"""
scheduler.py - Background job scheduling
Feature coverage: G36, I48
Runs in a daemon thread. App boots and serves without waiting.
"""
import threading
import time
import logging
from datetime import datetime

log = logging.getLogger(__name__)

class Scheduler:
    FULL_SWEEP_INTERVAL = 600     # 10 minutes
    ROLLING_INTERVAL = 120        # 2 minutes

    def __init__(self):
        self.running = False
        self.thread = None
        self.last_full_sweep = 0
        self.last_rolling = 0
        self.errors = []
        self.sweep_count = 0
        self.rolling_count = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        log.info("Scheduler started")

    def stop(self):
        self.running = False
        log.info("Scheduler stopped")

    def _run(self):
        from data_engine import full_sweep, rolling_refresh
        from safety import circuit_breaker

        log.info("Startup sweep...")
        try:
            result = full_sweep()
            self.last_full_sweep = time.time()
            self.sweep_count += 1
            circuit_breaker.record_success()
            log.info(f"Startup sweep complete: {result}")
        except Exception as e:
            log.error(f"Startup sweep failed: {e}")
            circuit_breaker.record_failure(str(e))
            self._log_error("startup_sweep", str(e))

        while self.running:
            now = time.time()
            try:
                if circuit_breaker.is_open():
                    log.warning("Circuit breaker open — skipping scheduled jobs")
                    time.sleep(30)
                    continue

                if now - self.last_full_sweep >= self.FULL_SWEEP_INTERVAL:
                    log.info("Scheduled full sweep...")
                    result = full_sweep()
                    self.last_full_sweep = now
                    self.sweep_count += 1
                    circuit_breaker.record_success()

                elif now - self.last_rolling >= self.ROLLING_INTERVAL:
                    result = rolling_refresh()
                    self.last_rolling = now
                    self.rolling_count += 1
                    if result.get("ok"):
                        circuit_breaker.record_success()

            except Exception as e:
                log.error(f"Scheduler job error: {e}")
                circuit_breaker.record_failure(str(e))
                self._log_error("scheduled_job", str(e))

            time.sleep(30)

    def _log_error(self, job, error):
        self.errors.append({
            "job": job,
            "error": error,
            "time": datetime.utcnow().isoformat()
        })
        self.errors = self.errors[-20:]

    def status(self):
        now = time.time()
        return {
            "running": self.running,
            "sweep_count": self.sweep_count,
            "rolling_count": self.rolling_count,
            "last_full_sweep": datetime.fromtimestamp(self.last_full_sweep).isoformat() if self.last_full_sweep else None,
            "last_rolling": datetime.fromtimestamp(self.last_rolling).isoformat() if self.last_rolling else None,
            "next_full_sweep_min": round((self.FULL_SWEEP_INTERVAL - (now - self.last_full_sweep)) / 60, 1) if self.last_full_sweep else 0,
            "next_rolling_min": round((self.ROLLING_INTERVAL - (now - self.last_rolling)) / 60, 1) if self.last_rolling else 0,
            "recent_errors": self.errors[-5:],
        }

scheduler = Scheduler()

def start_scheduler():
    scheduler.start()

def stop_scheduler():
    scheduler.stop()

def get_status():
    return scheduler.status()
