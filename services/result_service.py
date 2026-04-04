"""
services/result_service.py - DemonPulse Result Service
========================================================
Reliable and safe result handling for the live engine.

Rules:
  - Only OddsPro authoritative paths write official results
  - Unfinished race result misses are treated safely (no false states)
  - No provisional data ever becomes official result truth
  - Race states are rebuilt after result confirmation
  - Board is rebuilt after confirmed results
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

log = logging.getLogger(__name__)


def run_result_sweep(target_date: str | None = None) -> dict[str, Any]:
    """
    Day-level OddsPro result sweep.

    Calls check_results() which:
      1. Fetches day-level results via GET /api/external/results
      2. Confirms each via GET /api/races/:id/results before writing
    Updates health metrics.
    Rebuilds race states and board after confirmation.
    """
    today = target_date or date.today().isoformat()
    try:
        from data_engine import check_results
        from services.health_service import record_result_check

        result = check_results(today)
        ok = result.get("ok", False)
        confirmed = result.get("results_written", 0)

        record_result_check(ok=ok, confirmations=confirmed)

        if ok and confirmed > 0:
            _rebuild_after_results(today, confirmed)

        return result

    except Exception as e:
        log.error(f"result_service: run_result_sweep failed: {e}")
        try:
            from services.health_service import record_result_check
            record_result_check(ok=False)
        except Exception:
            pass
        return {"ok": False, "error": "Result sweep failed", "date": today}


def confirm_race_result(race_uid: str, oddspro_race_id: str) -> dict[str, Any]:
    """
    Single-race OddsPro result confirmation.

    Only writes after authoritative OddsPro GET /api/races/:id/results response.
    Never writes a false or provisional result.
    """
    if not race_uid or not oddspro_race_id:
        return {"ok": False, "error": "race_uid and oddspro_race_id required"}

    try:
        from connectors.oddspro_connector import OddsProConnector
        from data_engine import _write_result

        conn = OddsProConnector()
        if not conn.is_enabled():
            return {"ok": False, "reason": "oddspro_not_configured"}

        confirmed = conn.fetch_race_result(oddspro_race_id)
        if not confirmed:
            log.warning(
                f"result_service: no confirmed result for {race_uid} "
                f"(oddspro_race_id={oddspro_race_id}) — not writing"
            )
            return {"ok": False, "reason": "no_result_confirmed", "race_uid": race_uid}

        _write_result(confirmed)
        log.info(f"result_service: result confirmed and written for {race_uid}")

        # Extract and store sectionals from the result payload (OddsPro authoritative)
        try:
            from ai.sectionals_engine import (
                extract_sectionals_from_result_payload,
                build_runner_sectional_metrics,
            )
            from ai.learning_store import save_sectional_snapshot

            sec_raw = extract_sectionals_from_result_payload(confirmed)
            if sec_raw.get("has_sectionals"):
                sec_metrics = build_runner_sectional_metrics(sec_raw["runners"])
                save_sectional_snapshot(
                    race_uid=race_uid,
                    oddspro_race_id=oddspro_race_id,
                    sectional_metrics=sec_metrics,
                    source="oddspro_result",
                )
                log.info(
                    f"result_service: stored {len(sec_metrics)} sectional metrics "
                    f"for {race_uid}"
                )
        except Exception as sec_err:
            log.warning(
                f"result_service: sectionals extraction failed for {race_uid}: {sec_err}"
            )

        # Evaluate any outstanding predictions for this race
        try:
            from ai.learning_store import evaluate_prediction
            from database import get_result
            stored_result = get_result(race_uid)
            if stored_result:
                eval_result = evaluate_prediction(race_uid, stored_result)
                eval_count = eval_result.get("evaluated", 0)
                if eval_count > 0:
                    from services.health_service import record_evaluation_run
                    record_evaluation_run(count=eval_count)
                    log.info(
                        f"result_service: evaluated {eval_count} predictions "
                        f"for {race_uid}"
                    )
        except Exception as eval_err:
            log.warning(
                f"result_service: prediction evaluation failed for {race_uid}: {eval_err}"
            )

        # Update state for this race only
        try:
            from database import get_race
            from race_status import compute_race_status
            from database import update_race_status
            race = get_race(race_uid)
            if race:
                new_status = compute_race_status(race)
                update_race_status(race_uid, new_status)
        except Exception as state_err:
            log.warning(f"result_service: state update failed for {race_uid}: {state_err}")

        return {"ok": True, "race_uid": race_uid, "source": "oddspro"}

    except Exception as e:
        log.error(f"result_service: confirm_race_result failed for {race_uid}: {e}")
        return {"ok": False, "error": "Confirmation failed", "race_uid": race_uid}


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _rebuild_after_results(target_date: str, confirmed_count: int) -> None:
    """
    Rebuild race states and board after results are confirmed.
    Transitions races to result_posted/final as appropriate.
    """
    try:
        from database import get_races_for_date, update_race_status
        from race_status import compute_race_status

        races = get_races_for_date(target_date)
        updated = 0
        for race in races:
            race_uid = race.get("race_uid") or ""
            if not race_uid:
                continue
            new_status = compute_race_status(race)
            current = (race.get("status") or "").lower()
            if new_status != current:
                update_race_status(race_uid, new_status)
                updated += 1

        log.info(
            f"result_service: rebuilt {updated} race states after "
            f"{confirmed_count} result confirmations for {target_date}"
        )

        # Trigger board rebuild
        try:
            from board_builder import get_board_for_today
            get_board_for_today()
        except Exception as board_err:
            log.error(f"result_service: board rebuild after results failed: {board_err}")

    except Exception as e:
        log.error(f"result_service: _rebuild_after_results failed: {e}")
