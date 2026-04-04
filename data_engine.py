"""
data_engine.py - DemonPulse Data Engine
Primary: OddsPro (official source)
Secondary: FormFav (provisional overlay for near-jump races only)
"""

import json
import uuid
import logging
from datetime import date as _date, datetime, timezone

import database
from models import Meeting, Race, Runner, OddsSnapshot, RaceResult
from validation_engine import (
    validate_meeting_payload,
    validate_race_payload,
    validate_runner_payload,
    score_data_quality,
    CONFIDENCE_THRESHOLD,
)
from integrity_filter import BlockCode

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# STATE TRACKING
# ──────────────────────────────────────────────────────────────────
_state = {
    "last_full_sweep_at": None,
    "last_full_sweep_ok": None,
    "last_refresh_at": None,
    "last_refresh_ok": None,
    "last_result_check_at": None,
    "last_result_check_ok": None,
    "last_formfav_overlay_at": None,
    "last_formfav_overlay_ok": None,
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today(date_str: str | None) -> str:
    return date_str or _date.today().isoformat()


def get_engine_state() -> dict:
    return dict(_state)


# ──────────────────────────────────────────────────────────────────
# CONNECTOR HELPERS
# ──────────────────────────────────────────────────────────────────
def _get_oddspro():
    from connectors.oddspro_connector import OddsProConnector
    return OddsProConnector()


def _get_formfav():
    try:
        from connectors.formfav_connector import FormFavConnector
        return FormFavConnector()
    except Exception as e:
        log.warning(f"FormFav connector unavailable: {e}")
        return None


# ──────────────────────────────────────────────────────────────────
# PAYLOAD MAPPING HELPERS
# ──────────────────────────────────────────────────────────────────
def _map_meeting(raw: dict, date_str: str) -> Meeting | None:
    valid, issues = validate_meeting_payload(raw)
    if not valid:
        log.warning(f"Invalid meeting payload: {issues} — {raw.get('meeting_id')}")
        return None
    return Meeting(
        meeting_id=str(raw["meeting_id"]),
        date=raw.get("date", date_str)[:10],
        track=raw.get("track", ""),
        code=(raw.get("code") or raw.get("race_type") or "HORSE").upper(),
        state=raw.get("state", ""),
        country=raw.get("country", "AUS"),
        status=raw.get("status", "scheduled"),
        race_count=int(raw.get("race_count") or raw.get("number_of_races") or 0),
        venue_name=raw.get("venue_name") or raw.get("venue") or raw.get("track", ""),
        raw_source="oddspro",
        fetched_at=_utcnow(),
    )


def _map_race(raw: dict, meeting_id: str, date_str: str, track: str, code: str) -> Race | None:
    valid, issues = validate_race_payload(raw)
    if not valid:
        log.warning(f"Invalid race payload: {issues} — {raw.get('race_id')}")
        return None
    return Race(
        race_id=str(raw["race_id"]),
        meeting_id=str(raw.get("meeting_id") or meeting_id),
        date=raw.get("date", date_str)[:10] if raw.get("date") else date_str,
        track=raw.get("track") or track,
        race_num=int(raw["race_num"]),
        code=(raw.get("code") or code or "HORSE").upper(),
        race_name=raw.get("race_name") or raw.get("name") or f"Race {raw['race_num']}",
        distance=int(raw.get("distance") or 0),
        grade=raw.get("grade") or "",
        condition=raw.get("condition") or raw.get("track_condition") or "",
        jump_time=raw.get("jump_time") or raw.get("start_time"),
        status=raw.get("status", "scheduled"),
        result_official=bool(raw.get("result_official") or raw.get("official")),
        source="oddspro",
        fetched_at=_utcnow(),
        blocked=False,
        block_reason=None,
    )


def _map_runner(raw: dict, race_id: str, code: str) -> Runner | None:
    valid, issues = validate_runner_payload(raw)
    if not valid:
        log.debug(f"Invalid runner payload: {issues} — {raw.get('name')}")
        return None
    runner_id = str(
        raw.get("runner_id")
        or raw.get("id")
        or f"{race_id}_{raw.get('number') or raw.get('box_num') or raw.get('name', 'unk')}"
    )
    return Runner(
        runner_id=runner_id,
        race_id=race_id,
        number=_int_or_none(raw.get("number") or raw.get("saddle_cloth")),
        box_num=_int_or_none(raw.get("box_num") or raw.get("box")),
        barrier=_int_or_none(raw.get("barrier")),
        name=raw.get("name", ""),
        trainer=raw.get("trainer") or "",
        jockey=raw.get("jockey") or "",
        driver=raw.get("driver") or "",
        weight=_float_or_none(raw.get("weight") or raw.get("handicap")),
        scratched=bool(raw.get("scratched") or raw.get("is_scratched")),
        win_odds=_float_or_none(raw.get("win_odds") or raw.get("fixed_win")),
        place_odds=_float_or_none(raw.get("place_odds") or raw.get("fixed_place")),
        source="oddspro",
        fetched_at=_utcnow(),
    )


def _int_or_none(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _float_or_none(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────
# FULL SWEEP
# ──────────────────────────────────────────────────────────────────
def full_sweep(date_str: str | None = None) -> dict:
    """
    Bootstrap today from OddsPro:
    1. Fetch meetings from OddsPro /api/external/meetings
    2. For each meeting, fetch detail + races
    3. For each race, fetch detail + runners + odds
    4. Validate and store everything
    5. Block invalid races
    Returns {"ok": bool, "meetings": int, "races": int, "runners": int, "blocked": int, "errors": [...]}
    """
    date_str = _today(date_str)
    log.info(f"[full_sweep] Starting for {date_str}")
    conn = _get_oddspro()
    errors = []
    meeting_count = 0
    race_count = 0
    runner_count = 0
    blocked_count = 0

    if not conn.is_enabled():
        log.warning("[full_sweep] OddsPro not enabled — skipping")
        _state.update(last_full_sweep_at=_utcnow(), last_full_sweep_ok=False)
        return {"ok": False, "reason": "OddsPro not enabled", "meetings": 0,
                "races": 0, "runners": 0, "blocked": 0, "errors": []}

    try:
        raw_meetings = conn.fetch_today_meetings(date_str)
    except Exception as e:
        log.error(f"[full_sweep] fetch_today_meetings failed: {e}")
        _state.update(last_full_sweep_at=_utcnow(), last_full_sweep_ok=False)
        return {"ok": False, "error": "Data fetch failed", "meetings": 0,
                "races": 0, "runners": 0, "blocked": 0, "errors": []}

    for raw_mtg in raw_meetings:
        meeting = _map_meeting(raw_mtg, date_str)
        if not meeting:
            errors.append(f"Failed to map meeting: {raw_mtg.get('meeting_id')}")
            continue

        database.upsert_meeting(meeting)
        meeting_count += 1

        # Fetch meeting detail for race list
        try:
            detail = conn.fetch_meeting_detail(meeting.meeting_id) or raw_mtg
        except Exception as e:
            log.warning(f"[full_sweep] fetch_meeting_detail({meeting.meeting_id}) failed: {e}")
            errors.append(f"meeting_detail_failed:{meeting.meeting_id}")
            detail = raw_mtg

        raw_races = (
            detail.get("races")
            or detail.get("race_cards")
            or []
        )

        for raw_race in raw_races:
            race = _map_race(raw_race, meeting.meeting_id, date_str, meeting.track, meeting.code)
            if not race:
                errors.append(f"Failed to map race: {raw_race.get('race_id')}")
                continue

            # Fetch full race detail
            try:
                race_detail = conn.fetch_race_detail(race.race_id)
                if race_detail:
                    # Re-map with enriched data
                    enriched = _map_race(
                        race_detail, meeting.meeting_id, date_str, meeting.track, meeting.code
                    )
                    if enriched:
                        race = enriched
            except Exception as e:
                log.warning(f"[full_sweep] fetch_race_detail({race.race_id}) failed: {e}")
                errors.append(f"race_detail_failed:{race.race_id}")
                race_detail = raw_race

            raw_runners_list = (
                (race_detail or raw_race).get("runners")
                or (race_detail or raw_race).get("competitors")
                or []
            )

            runners = []
            for raw_runner in raw_runners_list:
                r = _map_runner(raw_runner, race.race_id, race.code)
                if r:
                    runners.append(r)

            # Score and block if needed
            race_dict = {
                "race_id": race.race_id, "meeting_id": race.meeting_id,
                "jump_time": race.jump_time, "track": race.track,
                "race_num": race.race_num, "code": race.code,
                "distance": race.distance, "race_name": race.race_name,
            }
            runner_dicts = [
                {"name": r.name, "win_odds": r.win_odds,
                 "number": r.number, "box_num": r.box_num}
                for r in runners
            ]
            confidence = score_data_quality(race_dict, runner_dicts)
            # Confidence score (0.0-1.0) based on data completeness — must meet CONFIDENCE_THRESHOLD

            if confidence < CONFIDENCE_THRESHOLD:
                log.warning(
                    f"[full_sweep] Race {race.race_id} confidence {confidence:.2f} < threshold — blocking"
                )
                race.blocked = True
                race.block_reason = BlockCode.LOW_CONFIDENCE

            database.upsert_race(race)
            race_count += 1

            if race.blocked:
                database.block_race(race.race_id, race.block_reason or BlockCode.VALIDATION_FAILED)
                blocked_count += 1
            else:
                database.upsert_runners(runners)
                runner_count += len(runners)

                # Store odds snapshot
                if runner_dicts:
                    snap = OddsSnapshot(
                        snapshot_id=str(uuid.uuid4()),
                        race_id=race.race_id,
                        source="oddspro",
                        payload=json.dumps(runner_dicts),
                        is_provisional=False,
                        captured_at=_utcnow(),
                    )
                    try:
                        database.store_odds_snapshot(snap)
                    except Exception as e:
                        log.warning(f"[full_sweep] store_odds_snapshot failed: {e}")

    _state.update(last_full_sweep_at=_utcnow(), last_full_sweep_ok=True)
    log.info(
        f"[full_sweep] Done: meetings={meeting_count} races={race_count} "
        f"runners={runner_count} blocked={blocked_count} errors={len(errors)}"
    )
    return {
        "ok": True,
        "meetings": meeting_count,
        "races": race_count,
        "runners": runner_count,
        "blocked": blocked_count,
        "errors": errors,
    }


# ──────────────────────────────────────────────────────────────────
# ROLLING REFRESH
# ──────────────────────────────────────────────────────────────────
def rolling_refresh(date_str: str | None = None) -> dict:
    """
    Refresh active/near-jump races from OddsPro:
    1. Get races where status in (scheduled, open, near_jump) and jump_time is in next 2 hours
    2. For each, call OddsPro /api/external/race/:id
    3. Update stored data
    4. Also run FormFav overlay for near-jump races
    Returns {"ok": bool, "refreshed": int, "errors": [...]}
    """
    date_str = _today(date_str)
    log.info(f"[rolling_refresh] Starting for {date_str}")
    conn = _get_oddspro()
    errors = []
    refreshed = 0

    if not conn.is_enabled():
        _state.update(last_refresh_at=_utcnow(), last_refresh_ok=False)
        return {"ok": False, "reason": "OddsPro not enabled", "refreshed": 0, "errors": []}

    races = database.get_active_races(date_str)

    for race in races:
        try:
            race_detail = conn.fetch_race_detail(race.race_id)
            if not race_detail:
                continue

            updated = _map_race(
                race_detail, race.meeting_id, date_str, race.track, race.code
            )
            if not updated:
                continue

            raw_runners_list = (
                race_detail.get("runners") or race_detail.get("competitors") or []
            )
            runners = []
            for raw_runner in raw_runners_list:
                r = _map_runner(raw_runner, race.race_id, race.code)
                if r:
                    runners.append(r)

            database.upsert_race(updated)
            if runners:
                database.upsert_runners(runners)
                snap = OddsSnapshot(
                    snapshot_id=str(uuid.uuid4()),
                    race_id=race.race_id,
                    source="oddspro",
                    payload=json.dumps([
                        {"name": r.name, "win_odds": r.win_odds,
                         "number": r.number, "box_num": r.box_num}
                        for r in runners
                    ]),
                    is_provisional=False,
                    captured_at=_utcnow(),
                )
                try:
                    database.store_odds_snapshot(snap)
                except Exception:
                    pass

            refreshed += 1
        except Exception as e:
            log.warning(f"[rolling_refresh] Race {race.race_id} failed: {e}")
            errors.append(f"race_failed:{race.race_id}")

    # Run FormFav overlay for near-jump races
    try:
        overlay_result = run_formfav_overlay(date_str)
        log.debug(f"[rolling_refresh] FormFav overlay: {overlay_result}")
    except Exception as e:
        log.warning(f"[rolling_refresh] FormFav overlay failed: {e}")
        errors.append("formfav_overlay_failed")

    _state.update(last_refresh_at=_utcnow(), last_refresh_ok=True)
    return {"ok": True, "refreshed": refreshed, "errors": errors}


# ──────────────────────────────────────────────────────────────────
# CHECK RESULTS
# ──────────────────────────────────────────────────────────────────
def check_results(date_str: str | None = None) -> dict:
    """
    Poll OddsPro for results:
    1. Call /api/external/results for today
    2. For each settled race, store official result
    3. Confirm any provisional FormFav results
    4. For races without results yet, try /api/races/:id/results (404 = not ready)
    Returns {"ok": bool, "results_found": int, "confirmed": int, "errors": [...]}
    """
    date_str = _today(date_str)
    log.info(f"[check_results] Starting for {date_str}")
    conn = _get_oddspro()
    errors = []
    results_found = 0
    confirmed = 0

    if not conn.is_enabled():
        _state.update(last_result_check_at=_utcnow(), last_result_check_ok=False)
        return {"ok": False, "reason": "OddsPro not enabled", "results_found": 0,
                "confirmed": 0, "errors": []}

    # Batch results endpoint
    try:
        raw_results = conn.fetch_results(date_str)
        for raw in raw_results:
            race_id = str(raw.get("race_id") or "")
            if not race_id:
                continue
            try:
                _store_official_result(race_id, raw)
                results_found += 1
                confirmed += 1
            except Exception as e:
                log.warning(f"[check_results] store result {race_id}: {e}")
                errors.append(f"result_failed:{race_id}")
    except Exception as e:
        log.warning(f"[check_results] fetch_results failed: {e}")
        errors.append("fetch_results_failed")

    # Per-race polling for races that may not have appeared in the batch
    provisional = database.get_provisional_results(date_str)
    for prov in provisional:
        race_id = prov["race_id"]
        try:
            raw = conn.fetch_race_result(race_id)  # None on 404
            if raw:
                _store_official_result(race_id, raw)
                confirmed += 1
        except Exception as e:
            log.warning(f"[check_results] fetch_race_result({race_id}) failed: {e}")
            errors.append(f"race_result_failed:{race_id}")

    _state.update(last_result_check_at=_utcnow(), last_result_check_ok=True)
    return {"ok": True, "results_found": results_found, "confirmed": confirmed, "errors": errors}


def _store_official_result(race_id: str, raw: dict) -> None:
    positions = raw.get("positions") or raw.get("placings") or {}
    dividends = raw.get("dividends") or raw.get("payouts") or {}
    result = RaceResult(
        result_id=str(uuid.uuid4()),
        race_id=race_id,
        positions=json.dumps(positions) if isinstance(positions, dict) else str(positions),
        dividends=json.dumps(dividends) if isinstance(dividends, dict) else str(dividends),
        is_official=True,
        provisional_source=None,
        confirmed_at=_utcnow(),
        fetched_at=_utcnow(),
    )
    database.store_result(result)
    database.confirm_result(race_id, {"positions": positions, "dividends": dividends})
    log.info(f"[check_results] Official result stored for race {race_id}")


# ──────────────────────────────────────────────────────────────────
# REFRESH MEETING / RACE
# ──────────────────────────────────────────────────────────────────
def refresh_meeting(meeting_id: str) -> dict:
    """Refresh a specific meeting from OddsPro."""
    log.info(f"[refresh_meeting] {meeting_id}")
    conn = _get_oddspro()
    if not conn.is_enabled():
        return {"ok": False, "reason": "OddsPro not enabled"}

    try:
        detail = conn.fetch_meeting_detail(meeting_id)
        if not detail:
            return {"ok": False, "error": f"Meeting {meeting_id} not found"}

        date_str = _today(None)
        meeting = _map_meeting(detail, date_str)
        if meeting:
            database.upsert_meeting(meeting)

        races_refreshed = 0
        for raw_race in detail.get("races") or []:
            race = _map_race(raw_race, meeting_id, date_str,
                             detail.get("track", ""), detail.get("code", "HORSE"))
            if race:
                database.upsert_race(race)
                races_refreshed += 1

        return {"ok": True, "races_refreshed": races_refreshed}
    except Exception as e:
        log.error(f"[refresh_meeting] {meeting_id} failed: {e}")
        return {"ok": False, "error": "Operation failed"}


def refresh_race(race_id: str) -> dict:
    """Refresh a specific race from OddsPro."""
    log.info(f"[refresh_race] {race_id}")
    conn = _get_oddspro()
    if not conn.is_enabled():
        return {"ok": False, "reason": "OddsPro not enabled"}

    existing = database.get_race(race_id)
    if not existing:
        return {"ok": False, "error": f"Race {race_id} not found in DB"}

    try:
        race_detail = conn.fetch_race_detail(race_id)
        if not race_detail:
            return {"ok": False, "error": f"Race {race_id} not found at OddsPro"}

        updated = _map_race(
            race_detail, existing.meeting_id, existing.date, existing.track, existing.code
        )
        if not updated:
            return {"ok": False, "error": "Failed to map race payload"}

        raw_runners_list = race_detail.get("runners") or race_detail.get("competitors") or []
        runners = [_map_runner(r, race_id, existing.code) for r in raw_runners_list]
        runners = [r for r in runners if r]

        database.upsert_race(updated)
        database.upsert_runners(runners)
        return {"ok": True, "runners_updated": len(runners)}
    except Exception as e:
        log.error(f"[refresh_race] {race_id} failed: {e}")
        return {"ok": False, "error": "Operation failed"}


# ──────────────────────────────────────────────────────────────────
# FORMFAV OVERLAY (secondary, near-jump only)
# ──────────────────────────────────────────────────────────────────
def run_formfav_overlay(date_str: str | None = None) -> dict:
    """
    FormFav overlay for near-jump races ONLY:
    1. Get races within 30 min of jump
    2. Fetch FormFav odds for those races
    3. Store as provisional (not official)
    4. These will be confirmed/corrected by next OddsPro sweep
    Returns {"ok": bool, "updated": int, "errors": [...]}
    """
    date_str = _today(date_str)
    ff = _get_formfav()
    if not ff or not ff.is_enabled():
        log.debug("[formfav_overlay] FormFav not enabled — skipping")
        _state.update(last_formfav_overlay_at=_utcnow(), last_formfav_overlay_ok=False)
        return {"ok": False, "reason": "FormFav not enabled", "updated": 0, "errors": []}

    near_jump_races = database.get_near_jump_races(date_str, minutes=30)
    updated = 0
    errors = []

    for race in near_jump_races:
        try:
            race_form = ff.fetch_race_form(
                target_date=date_str,
                track=race.track,
                race_num=race.race_num,
                code=race.code,
            )
            if not race_form:
                continue

            ff_race, ff_runners = race_form
            payload = {
                "race": ff_race.__dict__ if hasattr(ff_race, "__dict__") else ff_race,
                "runners": [
                    r.__dict__ if hasattr(r, "__dict__") else r for r in ff_runners
                ],
            }

            database.store_provisional_odds(race.race_id, "formfav", payload)

            snap = OddsSnapshot(
                snapshot_id=str(uuid.uuid4()),
                race_id=race.race_id,
                source="formfav",
                payload=json.dumps(payload),
                is_provisional=True,
                captured_at=_utcnow(),
            )
            try:
                database.store_odds_snapshot(snap)
            except Exception:
                pass

            updated += 1
        except Exception as e:
            log.warning(f"[formfav_overlay] Race {race.race_id} ({race.track} R{race.race_num}): {e}")
            errors.append(f"race_failed:{race.race_id}")

    _state.update(last_formfav_overlay_at=_utcnow(), last_formfav_overlay_ok=True)
    return {"ok": True, "updated": updated, "errors": errors}


# ──────────────────────────────────────────────────────────────────
# REBUILD BOARD
# ──────────────────────────────────────────────────────────────────
def rebuild_board(date_str: str | None = None) -> dict:
    """Rebuild the board from stored data."""
    from board_builder import build_board
    return build_board(date_str)
