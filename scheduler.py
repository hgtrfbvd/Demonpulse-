"""
scheduler.py - DemonPulse Scheduler (Claude Pipeline)
======================================================
Continuous scheduler for the Claude-API-powered data pipeline.

Cycles:
  full_sweep          - fetch all venues for today (every 10 min)
  board_rebuild       - rebuild board from stored data (every 90 s)
  result_check        - detect jumped races, update statuses (every 3 min)
  race_state_update   - drive race state machine from stored data (every 90 s)
  health_snapshot     - aggregate health metrics (every 2 min)

Safety rules:
  - per-cycle threading.Lock prevents overlapping destructive cycles
  - lock.acquire(blocking=False) — skip cycle if already running
  - errors are logged and preserved; cycles degrade safely
"""

import os
import time
import logging
import threading
from datetime import datetime, date

log = logging.getLogger(__name__)


# --------------------------------------------------------
# CONFIG
# --------------------------------------------------------
FULL_SWEEP_ON_START = True
FULL_SWEEP_INTERVAL = 600       # 10 min — fetch all venues
BOARD_REBUILD_INTERVAL = 90     # 90 s — rebuild board from stored data
RESULT_CHECK_INTERVAL = 180     # 3 min — check for jumped races
RACE_STATE_INTERVAL = 90        # 90 s — automated race state machine
HEALTH_SNAPSHOT_INTERVAL = 120  # 2 min — health metrics snapshot
EVAL_BACKFILL_INTERVAL = 3600   # once per hour
LOOP_SLEEP_SECONDS = 10
RESTART_BACKOFF_SECONDS = 5


# --------------------------------------------------------
# INTERNAL STATE
# --------------------------------------------------------
_scheduler_started = False
_scheduler_thread = None
_scheduler_lock = threading.Lock()

_sweep_lock = threading.Lock()
_result_check_lock = threading.Lock()
_state_update_lock = threading.Lock()

_scheduler_status = {
    "running": False,
    "thread_alive": False,
    "started_at": None,
    "last_loop_at": None,
    "last_full_sweep_at": None,
    "last_full_sweep_result": None,
    "last_board_rebuild_at": None,
    "last_result_check_at": None,
    "last_result_check_result": None,
    "last_race_state_update_at": None,
    "last_health_snapshot_at": None,
    "last_error": None,
    "full_sweep_interval": FULL_SWEEP_INTERVAL,
    "board_rebuild_interval": BOARD_REBUILD_INTERVAL,
    "result_check_interval": RESULT_CHECK_INTERVAL,
    "race_state_interval": RACE_STATE_INTERVAL,
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
    """Fetch all venues for today via ClaudeScraper. Skipped if already running."""
    acquired = _sweep_lock.acquire(blocking=False)
    if not acquired:
        log.warning("Full sweep still running — skipping this cycle")
        return {"ok": False, "reason": "cycle_already_running"}

    try:
        log.info("scheduler: running full_sweep...")
        from pipeline import full_sweep
        result = full_sweep()
        ok = result.get("ok", False)
        sweep_status = result.get("status", "")
        # Rebuild the board for both full successes and partial-cached sweeps so
        # that a 429 rate-limit never leaves the dashboard blank.
        should_rebuild = ok or sweep_status == "partial_cached"
        if ok:
            log.info(f"scheduler: full_sweep complete: {result}")
        else:
            log.warning(f"scheduler: full_sweep returned not-ok: {result}")

        _set_status(
            last_full_sweep_at=_utc_now(),
            last_full_sweep_result=result,
            last_error=None if ok else (result.get("error") or "full_sweep_not_ok"),
        )

        try:
            from services.health_service import record_bootstrap
            record_bootstrap(ok=ok, result=result)
        except Exception:
            pass

        if should_rebuild:
            _trigger_board_rebuild()

        return result
    finally:
        _sweep_lock.release()


def _run_result_check():
    """
    Detect races that have jumped (race_time passed) and update statuses.
    Skipped if already running.
    """
    acquired = _result_check_lock.acquire(blocking=False)
    if not acquired:
        log.warning("Result check still running — skipping this cycle")
        return {"ok": False, "reason": "cycle_already_running"}

    try:
        today = date.today().isoformat()
        from database import get_races_for_date, update_race_status
        from race_status import bulk_update_race_states

        races = get_races_for_date(today)
        changes = bulk_update_race_states(races)
        for race_uid, old_status, new_status in changes:
            update_race_status(race_uid, new_status)
            log.debug(f"scheduler: result_check status {race_uid}: {old_status} → {new_status}")

        result = {"ok": True, "date": today, "status_changes": len(changes)}
        _set_status(
            last_result_check_at=_utc_now(),
            last_result_check_result=result,
        )

        try:
            from services.health_service import record_result_check
            record_result_check(ok=True, confirmations=len(changes))
        except Exception:
            pass

        if changes:
            _trigger_board_rebuild()

        return result
    except Exception as e:
        log.error(f"scheduler: result_check failed: {e}")
        _set_status(last_error=f"result_check: {e}")
        return {"ok": False, "error": str(e)}
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
        from database import get_races_for_date, update_race_status
        from race_status import bulk_update_race_states

        races = get_races_for_date(today)
        changes = bulk_update_race_states(races)
        for race_uid, old_status, new_status in changes:
            update_race_status(race_uid, new_status)
            log.debug(f"scheduler: race_state {race_uid}: {old_status} → {new_status}")

        if changes:
            log.info(f"scheduler: race_state_update: {len(changes)} transitions for {today}")
            _trigger_board_rebuild()

        _set_status(last_race_state_update_at=_utc_now())

    except Exception as e:
        log.error(f"scheduler: race_state_update failed: {e}")
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
        log.debug(
            f"scheduler: health snapshot: blocked={len(blocked)} "
            f"stale={len(stale)} stored={len(all_today)}"
        )
    except Exception as e:
        log.error(f"scheduler: health_snapshot failed: {e}")


def _run_evaluation_backfill():
    """Evaluate any predictions that have results but no evaluation record."""
    try:
        from db import get_db, safe_query, T
        from ai.learning_store import evaluate_prediction
        from database import get_result

        snaps = safe_query(
            lambda: get_db()
            .table(T("prediction_snapshots"))
            .select("race_uid")
            .execute()
            .data,
            []
        ) or []

        evaluated = safe_query(
            lambda: get_db()
            .table(T("learning_evaluations"))
            .select("race_uid")
            .execute()
            .data,
            []
        ) or []

        evaluated_uids = {r["race_uid"] for r in evaluated if r.get("race_uid")}
        snap_uids = {r["race_uid"] for r in snaps if r.get("race_uid")}
        pending = snap_uids - evaluated_uids

        backfilled = 0
        for race_uid in list(pending)[:50]:
            stored = get_result(race_uid)
            if stored and stored.get("winner"):
                try:
                    evaluate_prediction(race_uid, stored)
                    backfilled += 1
                except Exception:
                    pass

        if backfilled:
            log.info(f"scheduler: backfilled {backfilled} evaluations")

    except Exception as e:
        log.warning(f"scheduler: evaluation_backfill failed: {e}")


# --------------------------------------------------------
# BOARD REBUILD HELPERS
# --------------------------------------------------------

def _trigger_board_rebuild():
    """Trigger a board rebuild from stored data."""
    try:
        from board_service import get_board_for_today
        result = get_board_for_today()
        count = result.get("count", 0)
        try:
            from services.health_service import record_board_rebuild
            record_board_rebuild(count=count)
        except Exception:
            pass
        _set_status(last_board_rebuild_at=_utc_now())
        log.debug(f"scheduler: board rebuilt count={count}")
    except Exception as e:
        log.error(f"scheduler: board rebuild failed: {e}")


# --------------------------------------------------------
# MAIN LOOP
# --------------------------------------------------------
def run_scheduler():
    global _scheduler_thread

    log.info("=== SCHEDULER STARTED (Claude Pipeline) ===")
    _set_status(
        running=True,
        thread_alive=True,
        started_at=_utc_now(),
        last_error=None,
    )

    now = time.time()
    last_sweep = now
    last_board_rebuild = now
    last_result_check = now
    last_race_state = now
    last_health_snapshot = now
    last_eval_backfill = 0

    if FULL_SWEEP_ON_START:
        try:
            _run_full_sweep()
            now = time.time()
            last_sweep = now
            last_board_rebuild = now
            last_result_check = now
            last_race_state = now
            last_health_snapshot = now
        except Exception as e:
            log.error(f"scheduler: initial full_sweep failed: {e}")
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

            if now - last_sweep >= FULL_SWEEP_INTERVAL:
                _run_full_sweep()
                last_sweep = now

            if now - last_board_rebuild >= BOARD_REBUILD_INTERVAL:
                _trigger_board_rebuild()
                last_board_rebuild = now

            if now - last_result_check >= RESULT_CHECK_INTERVAL:
                _run_result_check()
                last_result_check = now

            if now - last_race_state >= RACE_STATE_INTERVAL:
                _run_race_state_update()
                last_race_state = now

            if now - last_health_snapshot >= HEALTH_SNAPSHOT_INTERVAL:
                _run_health_snapshot()
                last_health_snapshot = now

            if now - last_eval_backfill >= EVAL_BACKFILL_INTERVAL:
                _run_evaluation_backfill()
                last_eval_backfill = now

        except Exception as e:
            log.error(f"scheduler: loop error: {e}")
            _set_status(last_error=f"scheduler_loop: {e}")

        time.sleep(LOOP_SLEEP_SECONDS)


def _scheduler_runner():
    """Protective wrapper — fatal crash updates status."""
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
        _set_status(running=False, thread_alive=False)


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

    _set_status(running=True, thread_alive=True, last_error=None)
    log.info("Scheduler started (Claude Pipeline)")


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
