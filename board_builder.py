"""
board_builder.py - Builds the internal board from stored data.
Uses race_status.py for NTJ calculation.
Never shows blocked/stale races.
"""

import logging
from datetime import date as _date

import database
import race_status
import integrity_filter

log = logging.getLogger(__name__)


def build_board(date_str: str | None = None) -> dict:
    """
    Build board from stored races:
    1. Get active/non-blocked races from database
    2. Apply integrity_filter
    3. Calculate NTJ internally from jump times
    4. Sort by jump_time
    5. Return board with NTJ marked

    Returns {"ok": bool, "board": list[dict], "ntj": dict|None,
             "blocked_count": int, "stale_count": int}
    """
    if date_str is None:
        date_str = _date.today().isoformat()

    try:
        races = database.get_active_races(date_str)
        races_as_dicts = [_race_to_dict(r) for r in races]

        runners_by_race = {}
        for race in races:
            runners = database.get_runners_for_race(race.race_id)
            runners_by_race[race.race_id] = [_runner_to_dict(r) for r in runners]

        valid_races, blocked_races = integrity_filter.filter_board_races(
            races_as_dicts, runners_by_race
        )

        # Enrich with computed status and provisional odds
        board = []
        for race in valid_races:
            computed_status = race_status.get_race_status(race)
            runners = runners_by_race.get(race.get("race_id", ""), [])
            provisional_odds = database.get_provisional_odds(race.get("race_id", ""))
            entry = get_board_entry(
                {**race, "status": computed_status},
                runners,
                provisional_odds,
            )
            board.append(entry)

        board = race_status.sort_board(board)
        ntj = race_status.calculate_ntj(board)

        stale_count = sum(
            1 for r in blocked_races
            if r.get("_block_reason") == integrity_filter.BlockCode.STALE_DATA
        )

        return {
            "ok": True,
            "board": board,
            "ntj": ntj,
            "blocked_count": len(blocked_races),
            "stale_count": stale_count,
            "date": date_str,
        }

    except Exception as e:
        log.exception(f"build_board failed: {e}")
        return {
            "ok": False,
            "error": str(e),
            "board": [],
            "ntj": None,
            "blocked_count": 0,
            "stale_count": 0,
            "date": date_str,
        }


def get_board_entry(
    race: dict,
    runners: list[dict],
    provisional_odds: dict | None = None,
) -> dict:
    """Build a single board entry dict."""
    mtj = race_status.minutes_to_jump(race)
    return {
        "race_id": race.get("race_id"),
        "meeting_id": race.get("meeting_id"),
        "date": race.get("date"),
        "track": race.get("track"),
        "race_num": race.get("race_num"),
        "code": race.get("code"),
        "race_name": race.get("race_name"),
        "distance": race.get("distance"),
        "grade": race.get("grade"),
        "condition": race.get("condition"),
        "jump_time": race.get("jump_time"),
        "status": race.get("status"),
        "result_official": race.get("result_official", False),
        "minutes_to_jump": round(mtj, 1) if mtj is not None else None,
        "runners": runners,
        "runner_count": len([r for r in runners if not r.get("scratched")]),
        "provisional_odds": provisional_odds,
        "source": race.get("source", "oddspro"),
        "fetched_at": race.get("fetched_at"),
    }


def _race_to_dict(race) -> dict:
    """Convert Race dataclass to dict."""
    if isinstance(race, dict):
        return race
    return {
        "race_id": race.race_id,
        "meeting_id": race.meeting_id,
        "date": race.date,
        "track": race.track,
        "race_num": race.race_num,
        "code": race.code,
        "race_name": race.race_name,
        "distance": race.distance,
        "grade": race.grade,
        "condition": race.condition,
        "jump_time": race.jump_time,
        "status": race.status,
        "result_official": race.result_official,
        "source": race.source,
        "fetched_at": race.fetched_at,
        "blocked": race.blocked,
        "block_reason": race.block_reason,
    }


def _runner_to_dict(runner) -> dict:
    """Convert Runner dataclass to dict."""
    if isinstance(runner, dict):
        return runner
    return {
        "runner_id": runner.runner_id,
        "race_id": runner.race_id,
        "number": runner.number,
        "box_num": runner.box_num,
        "barrier": runner.barrier,
        "name": runner.name,
        "trainer": runner.trainer,
        "jockey": runner.jockey,
        "driver": runner.driver,
        "weight": runner.weight,
        "scratched": runner.scratched,
        "win_odds": runner.win_odds,
        "place_odds": runner.place_odds,
        "source": runner.source,
        "fetched_at": runner.fetched_at,
    }
