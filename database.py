"""
database.py - DemonPulse SQLite storage layer.
Thread-safe via threading.local(). All writes use transactions.
"""

import os
import json
import uuid
import sqlite3
import logging
import threading
from datetime import datetime, timezone

from models import Meeting, Race, Runner, OddsSnapshot, RaceResult
from migrations import run_migrations

log = logging.getLogger(__name__)

_DATABASE_PATH = os.environ.get("DATABASE_PATH", "./demonpulse.db")
_local = threading.local()


def _db_path() -> str:
    return os.environ.get("DATABASE_PATH", _DATABASE_PATH)


def get_connection() -> sqlite3.Connection:
    """Return a thread-local SQLite connection, creating it if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(_db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    """Create all tables if they don't exist."""
    run_migrations(_db_path())
    # Warm this thread's connection
    get_connection()
    log.info("Database initialised")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────
# MEETINGS
# ──────────────────────────────────────────────────────────────────

def upsert_meeting(meeting: Meeting) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO meetings
                (meeting_id, date, track, code, state, country, status,
                 race_count, venue_name, raw_source, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(meeting_id) DO UPDATE SET
                date=excluded.date, track=excluded.track, code=excluded.code,
                state=excluded.state, country=excluded.country, status=excluded.status,
                race_count=excluded.race_count, venue_name=excluded.venue_name,
                raw_source=excluded.raw_source, fetched_at=excluded.fetched_at
            """,
            (
                meeting.meeting_id, meeting.date, meeting.track, meeting.code,
                meeting.state, meeting.country, meeting.status, meeting.race_count,
                meeting.venue_name, meeting.raw_source, meeting.fetched_at,
            ),
        )


def get_today_meetings(date_str: str) -> list[Meeting]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM meetings WHERE date = ?", (date_str,)
    ).fetchall()
    return [_row_to_meeting(r) for r in rows]


def get_all_meetings(date_str: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM meetings WHERE date = ? ORDER BY track", (date_str,)
    ).fetchall()
    return [dict(r) for r in rows]


def _row_to_meeting(row) -> Meeting:
    d = dict(row)
    return Meeting(
        meeting_id=d["meeting_id"], date=d["date"], track=d["track"],
        code=d["code"], state=d.get("state", ""), country=d.get("country", ""),
        status=d.get("status", "scheduled"), race_count=d.get("race_count", 0),
        venue_name=d.get("venue_name", ""), raw_source=d.get("raw_source", "oddspro"),
        fetched_at=d["fetched_at"],
    )


# ──────────────────────────────────────────────────────────────────
# RACES
# ──────────────────────────────────────────────────────────────────

def upsert_race(race: Race) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO races
                (race_id, meeting_id, date, track, race_num, code, race_name,
                 distance, grade, condition, jump_time, status, result_official,
                 source, fetched_at, blocked, block_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(race_id) DO UPDATE SET
                meeting_id=excluded.meeting_id, date=excluded.date, track=excluded.track,
                race_num=excluded.race_num, code=excluded.code, race_name=excluded.race_name,
                distance=excluded.distance, grade=excluded.grade, condition=excluded.condition,
                jump_time=excluded.jump_time, status=excluded.status,
                result_official=excluded.result_official, source=excluded.source,
                fetched_at=excluded.fetched_at, blocked=excluded.blocked,
                block_reason=excluded.block_reason
            """,
            (
                race.race_id, race.meeting_id, race.date, race.track,
                race.race_num, race.code, race.race_name, race.distance,
                race.grade, race.condition, race.jump_time, race.status,
                1 if race.result_official else 0, race.source, race.fetched_at,
                1 if race.blocked else 0, race.block_reason,
            ),
        )


def get_race(race_id: str) -> Race | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM races WHERE race_id = ?", (race_id,)).fetchone()
    return _row_to_race(row) if row else None


def get_races_for_meeting(meeting_id: str) -> list[Race]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM races WHERE meeting_id = ? ORDER BY race_num", (meeting_id,)
    ).fetchall()
    return [_row_to_race(r) for r in rows]


def get_active_races(date_str: str) -> list[Race]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM races
           WHERE date = ? AND status IN ('scheduled','open','near_jump') AND blocked = 0
           ORDER BY jump_time""",
        (date_str,),
    ).fetchall()
    return [_row_to_race(r) for r in rows]


def get_near_jump_races(date_str: str, minutes: int = 30) -> list[Race]:
    """Races within N minutes of jump_time."""
    conn = get_connection()
    now = _utcnow()
    rows = conn.execute(
        """SELECT * FROM races
           WHERE date = ? AND blocked = 0
             AND status NOT IN ('settled','abandoned')
             AND jump_time IS NOT NULL
             AND jump_time >= ?
             AND jump_time <= datetime(?, '+' || ? || ' minutes')
           ORDER BY jump_time""",
        (date_str, now, now, minutes),
    ).fetchall()
    return [_row_to_race(r) for r in rows]


def update_race_status(race_id: str, status: str) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE races SET status = ? WHERE race_id = ?", (status, race_id)
        )


def block_race(race_id: str, reason: str) -> None:
    conn = get_connection()
    now = _utcnow()
    with conn:
        conn.execute(
            "UPDATE races SET blocked = 1, block_reason = ? WHERE race_id = ?",
            (reason, race_id),
        )
        conn.execute(
            """INSERT INTO blocked_races (race_id, reason, blocked_at, resolved, resolved_at)
               VALUES (?,?,?,0,NULL)
               ON CONFLICT(race_id) DO UPDATE SET
                   reason=excluded.reason, blocked_at=excluded.blocked_at,
                   resolved=0, resolved_at=NULL""",
            (race_id, reason, now),
        )


def get_blocked_races(date_str: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT r.*, b.reason AS block_reason_detail, b.blocked_at
           FROM races r JOIN blocked_races b ON r.race_id = b.race_id
           WHERE r.date = ? AND b.resolved = 0""",
        (date_str,),
    ).fetchall()
    return [dict(r) for r in rows]


def _row_to_race(row) -> Race:
    d = dict(row)
    return Race(
        race_id=d["race_id"], meeting_id=d["meeting_id"], date=d["date"],
        track=d["track"], race_num=d["race_num"], code=d["code"],
        race_name=d.get("race_name", ""), distance=d.get("distance", 0),
        grade=d.get("grade", ""), condition=d.get("condition", ""),
        jump_time=d.get("jump_time"), status=d.get("status", "scheduled"),
        result_official=bool(d.get("result_official", 0)),
        source=d.get("source", "oddspro"), fetched_at=d["fetched_at"],
        blocked=bool(d.get("blocked", 0)), block_reason=d.get("block_reason"),
    )


# ──────────────────────────────────────────────────────────────────
# RUNNERS
# ──────────────────────────────────────────────────────────────────

def upsert_runners(runners: list[Runner]) -> None:
    if not runners:
        return
    conn = get_connection()
    with conn:
        conn.executemany(
            """
            INSERT INTO runners
                (runner_id, race_id, number, box_num, barrier, name, trainer,
                 jockey, driver, weight, scratched, win_odds, place_odds,
                 source, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(runner_id) DO UPDATE SET
                race_id=excluded.race_id, number=excluded.number,
                box_num=excluded.box_num, barrier=excluded.barrier,
                name=excluded.name, trainer=excluded.trainer,
                jockey=excluded.jockey, driver=excluded.driver,
                weight=excluded.weight, scratched=excluded.scratched,
                win_odds=excluded.win_odds, place_odds=excluded.place_odds,
                source=excluded.source, fetched_at=excluded.fetched_at
            """,
            [
                (
                    r.runner_id, r.race_id, r.number, r.box_num, r.barrier,
                    r.name, r.trainer, r.jockey, r.driver, r.weight,
                    1 if r.scratched else 0, r.win_odds, r.place_odds,
                    r.source, r.fetched_at,
                )
                for r in runners
            ],
        )


def get_runners_for_race(race_id: str) -> list[Runner]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM runners WHERE race_id = ? ORDER BY number, box_num, name",
        (race_id,),
    ).fetchall()
    return [_row_to_runner(r) for r in rows]


def _row_to_runner(row) -> Runner:
    d = dict(row)
    return Runner(
        runner_id=d["runner_id"], race_id=d["race_id"],
        number=d.get("number"), box_num=d.get("box_num"),
        barrier=d.get("barrier"), name=d["name"],
        trainer=d.get("trainer", ""), jockey=d.get("jockey", ""),
        driver=d.get("driver", ""), weight=d.get("weight"),
        scratched=bool(d.get("scratched", 0)),
        win_odds=d.get("win_odds"), place_odds=d.get("place_odds"),
        source=d.get("source", "oddspro"), fetched_at=d["fetched_at"],
    )


# ──────────────────────────────────────────────────────────────────
# ODDS SNAPSHOTS
# ──────────────────────────────────────────────────────────────────

def store_odds_snapshot(snapshot: OddsSnapshot) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            """INSERT INTO odds_snapshots
               (snapshot_id, race_id, source, payload, is_provisional, captured_at)
               VALUES (?,?,?,?,?,?)""",
            (
                snapshot.snapshot_id, snapshot.race_id, snapshot.source,
                snapshot.payload, 1 if snapshot.is_provisional else 0,
                snapshot.captured_at,
            ),
        )


def get_latest_odds(race_id: str) -> OddsSnapshot | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT * FROM odds_snapshots WHERE race_id = ?
           ORDER BY captured_at DESC LIMIT 1""",
        (race_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    return OddsSnapshot(
        snapshot_id=d["snapshot_id"], race_id=d["race_id"],
        source=d["source"], payload=d["payload"],
        is_provisional=bool(d["is_provisional"]),
        captured_at=d["captured_at"],
    )


# ──────────────────────────────────────────────────────────────────
# RESULTS
# ──────────────────────────────────────────────────────────────────

def store_result(result: RaceResult) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            """INSERT INTO race_results
               (result_id, race_id, positions, dividends, is_official,
                provisional_source, confirmed_at, fetched_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(race_id) DO UPDATE SET
                   positions=excluded.positions, dividends=excluded.dividends,
                   is_official=excluded.is_official,
                   provisional_source=excluded.provisional_source,
                   confirmed_at=excluded.confirmed_at,
                   fetched_at=excluded.fetched_at""",
            (
                result.result_id, result.race_id, result.positions,
                result.dividends, 1 if result.is_official else 0,
                result.provisional_source, result.confirmed_at, result.fetched_at,
            ),
        )


def get_result(race_id: str) -> RaceResult | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM race_results WHERE race_id = ?", (race_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    return RaceResult(
        result_id=d["result_id"], race_id=d["race_id"],
        positions=d["positions"], dividends=d.get("dividends", "{}"),
        is_official=bool(d["is_official"]),
        provisional_source=d.get("provisional_source"),
        confirmed_at=d.get("confirmed_at"), fetched_at=d["fetched_at"],
    )


def confirm_result(race_id: str, official_data: dict) -> None:
    conn = get_connection()
    now = _utcnow()
    with conn:
        conn.execute(
            """UPDATE race_results SET
               is_official = 1,
               positions = ?,
               dividends = ?,
               confirmed_at = ?,
               fetched_at = ?
               WHERE race_id = ?""",
            (
                json.dumps(official_data.get("positions", {})),
                json.dumps(official_data.get("dividends", {})),
                now, now, race_id,
            ),
        )
        conn.execute(
            "UPDATE races SET result_official = 1, status = 'settled' WHERE race_id = ?",
            (race_id,),
        )


def get_provisional_results(date_str: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT rr.* FROM race_results rr
           JOIN races r ON r.race_id = rr.race_id
           WHERE r.date = ? AND rr.is_official = 0""",
        (date_str,),
    ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────────
# PROVISIONAL ODDS (FormFav overlay)
# ──────────────────────────────────────────────────────────────────

def store_provisional_odds(race_id: str, source: str, payload: dict) -> None:
    conn = get_connection()
    now = _utcnow()
    with conn:
        conn.execute(
            """INSERT INTO provisional_odds (race_id, source, payload, captured_at)
               VALUES (?,?,?,?)
               ON CONFLICT(race_id) DO UPDATE SET
                   source=excluded.source, payload=excluded.payload,
                   captured_at=excluded.captured_at""",
            (race_id, source, json.dumps(payload), now),
        )


def get_provisional_odds(race_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM provisional_odds WHERE race_id = ?", (race_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        return json.loads(d["payload"])
    except Exception:
        return None
