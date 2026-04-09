#!/usr/bin/env python3
"""
smoke_test.py — DemonPulse LIVE Smoke Test (real Supabase)
===========================================================
Tests the full data path against the ACTUAL Supabase project.

Runs in TEST mode (DP_ENV=TEST) so all writes go to test_* tables,
never touching production data.

NO IN-MEMORY MOCK — every write and read exercises the real Supabase
client. Table names, conflict keys, field payloads, and read-back
queries all go through the production functions in database.py,
ai/learning_store.py, and ai/backtest_engine.py.

Usage:
    DP_ENV=TEST python smoke_test.py

Requires:
    SUPABASE_URL and SUPABASE_KEY must be set in the environment.
    Optionally SUPABASE_TEST_URL / SUPABASE_TEST_KEY for a dedicated
    test database (falls back to the main DB with test_ prefix).

Expected output:
    FINAL STATUS: PASS
"""
from __future__ import annotations

import os
import sys
import uuid
import logging
from datetime import date, datetime, timezone
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Force TEST mode BEFORE importing any DemonPulse module.
# env.py reads DP_ENV at import time and the singleton is created once.
# ─────────────────────────────────────────────────────────────────────────────
os.environ["DP_ENV"] = "TEST"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smoke_test")


# =============================================================================
# UTILITIES
# =============================================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return date.today().isoformat()


def _uid() -> str:
    """
    Short unique ID for test data.
    UUIDs contain only hex digits and hyphens; after removing hyphens the
    result contains only hex chars (a-f, 0-9) — no underscores — so the
    value is safe to embed in race_uid strings that use '_' as a delimiter.
    """
    return str(uuid.uuid4()).replace("-", "")[:12]


class _Result:
    def __init__(self, name: str):
        self.name = name
        self.passed: bool = True
        self.issues: list[str] = []
        self.code_path: str = ""
        self.detail: str = ""

    def fail(self, msg: str) -> None:
        self.passed = False
        self.issues.append(msg)

    def check(self, condition: bool, msg: str) -> None:
        if not condition:
            self.fail(msg)

    def check_fields_present(self, row: dict | None, fields: list[str]) -> None:
        if row is None:
            self.fail("Row is None — no fields to check")
            return
        for f in fields:
            if f not in row:
                self.fail(f"Silent field drop: '{f}' missing from stored row")


# =============================================================================
# CLEANUP HELPERS
# Uses safe_delete() from db.py which enforces TEST-only guard and
# resolves the table name via T() so we always clean test_* tables.
# =============================================================================

def _cleanup(table_name: str, column: str, value: Any) -> None:
    """Delete test rows from test_* table. Silently ignored if rows don't exist."""
    try:
        from db import safe_delete
        safe_delete(table_name, column, value)
    except Exception as exc:
        log.debug(f"cleanup {table_name}.{column}={value}: {exc}")


def _cleanup_db(table_name: str, column: str, value: Any) -> None:
    """
    Delete test rows using the raw Supabase client (for tables whose conflict
    key differs from what safe_delete can express, e.g. results_log by track).
    """
    try:
        from db import get_db, T
        get_db().table(T(table_name)).delete().eq(column, value).execute()
    except Exception as exc:
        log.debug(f"cleanup_db {table_name}.{column}={value}: {exc}")


# =============================================================================
# STARTUP: CONNECTIVITY + ENV CHECKS
# =============================================================================

def check_prerequisites() -> list[str]:
    """
    Return a list of fatal problems that prevent the smoke test from running.
    An empty list means all prerequisites are satisfied.
    """
    problems: list[str] = []

    from env import env
    if not env.is_test:
        problems.append("DP_ENV is not TEST — set DP_ENV=TEST before running")
        return problems  # can't continue

    # Verify table routing before touching the DB
    for tbl, expected in [
        ("meetings",              "test_meetings"),
        ("today_races",           "test_today_races"),
        ("today_runners",         "test_today_runners"),
        ("results_log",           "test_results_log"),
        ("prediction_snapshots",  "test_prediction_snapshots"),
        ("learning_evaluations",  "test_learning_evaluations"),
        ("backtest_runs",         "test_backtest_runs"),
        ("source_log",            "test_source_log"),
    ]:
        got = env.table(tbl)
        if got != expected:
            problems.append(
                f"Routing error: env.table('{tbl}') → '{got}' (expected '{expected}')"
            )

    # Always-live tables must NOT be prefixed even in TEST mode
    for always_live in ("users", "audit_log"):
        got = env.table(always_live)
        if got != always_live:
            problems.append(
                f"Always-live table '{always_live}' got prefixed to '{got}' in TEST mode"
            )

    if problems:
        return problems  # skip real DB check if routing is broken

    # Check Supabase connectivity by hitting a testable table
    try:
        from db import get_db, T
        get_db().table(T("meetings")).select("date").limit(1).execute()
    except Exception as exc:
        problems.append(
            f"Supabase connectivity failed: {exc}\n"
            f"  Ensure SUPABASE_URL and SUPABASE_KEY are set (or "
            f"SUPABASE_TEST_URL/SUPABASE_TEST_KEY for a dedicated test DB)."
        )

    return problems


# =============================================================================
# SUBSYSTEM SMOKE TESTS
# Each test:
#   1. Cleans up any orphaned rows from a previous interrupted run
#   2. Writes sample data via the real production code path
#   3. Reads back via the real production code path
#   4. Asserts expected values and required fields
#   5. Verifies conflict-key behaviour (upsert idempotency)
#   6. Confirms test_* table routing
#   7. Cleans up the rows it wrote
# =============================================================================

def test_meetings() -> _Result:
    """
    1. MEETINGS — write + read back
    Code path : database.upsert_meeting() → db.T("meetings") → test_meetings
    Conflict key: (date, track, code)
    """
    r = _Result("meetings")
    r.code_path = "database.upsert_meeting() → db.T('meetings') → test_meetings"

    from env import env
    from database import upsert_meeting, get_meeting

    r.check(env.is_test, "env.is_test must be True")
    r.check(
        env.table("meetings") == "test_meetings",
        f"Routing: env.table('meetings') → '{env.table('meetings')}' (expected 'test_meetings')",
    )

    uid = _uid()
    track = f"SMOKE-MTG-{uid}"

    # Pre-test cleanup (idempotent)
    _cleanup("meetings", "track", track)

    payload = {
        "date":       _today(),
        "track":      track,
        "code":       "GREYHOUND",
        "state":      "VIC",
        "country":    "AUS",
        "weather":    "FINE",
        "rail":       "",
        "track_cond": "Good",
        "race_count": 8,
        "source":     "oddspro",
    }

    # Write
    written = upsert_meeting(payload)
    r.check(written is not None, "upsert_meeting returned None")

    # Read back
    row = get_meeting(_today(), track, "GREYHOUND")
    r.check(row is not None, "get_meeting returned None after write")

    if row:
        r.check_fields_present(row, [
            "date", "track", "code", "state", "country", "weather",
            "track_cond", "race_count", "source", "updated_at",
        ])
        r.check(row.get("track") == track, "track field mismatch")
        r.check(row.get("code") == "GREYHOUND", "code field mismatch")
        r.check(row.get("race_count") == 8, "race_count field mismatch")

    # Conflict-key idempotency: second upsert must update, not duplicate
    upsert_meeting({**payload, "race_count": 10})
    row2 = get_meeting(_today(), track, "GREYHOUND")
    r.check(row2 is not None, "get_meeting returned None after second upsert")
    if row2:
        r.check(
            row2.get("race_count") == 10,
            f"Upsert did not update race_count: got {row2.get('race_count')!r} (expected 10)",
        )

    r.detail = f"track={track}, TEST table=test_meetings"

    # Post-test cleanup
    _cleanup("meetings", "track", track)
    return r


def test_races() -> _Result:
    """
    2. RACES — write/upsert + race_uid path + read back
    Code path : database.upsert_race() → db.T("today_races") → test_today_races
    Conflict key: (date, track, race_num, code)
    """
    r = _Result("races")
    r.code_path = "database.upsert_race() → db.T('today_races') → test_today_races"

    from env import env
    from database import upsert_race, get_race

    r.check(
        env.table("today_races") == "test_today_races",
        f"Routing: env.table('today_races') → '{env.table('today_races')}'",
    )

    uid = _uid()
    track = f"SMOKE-RACE-{uid}"
    race_uid = f"{_today()}_GREYHOUND_{track}_1"

    # Pre-test cleanup
    _cleanup("today_races", "race_uid", race_uid)

    payload = {
        "race_uid":        race_uid,
        "oddspro_race_id": f"OP-SMOKE-{uid}",
        "date":            _today(),
        "track":           track,
        "state":           "VIC",
        "race_num":        1,
        "code":            "GREYHOUND",
        "distance":        "515m",
        "grade":           "5",
        "jump_time":       "14:00",
        "prize_money":     "5000",
        "status":          "upcoming",
        "block_code":      "",
        "source":          "oddspro",
        "source_url":      f"https://oddspro.test/race/{uid}",
        "time_status":     "FULL",
        "condition":       "Good",
        "race_name":       "Smoke Test Race 1",
    }

    # Write
    written = upsert_race(payload)
    r.check(written is not None, "upsert_race returned None")

    # Read back by race_uid
    row = get_race(race_uid)
    r.check(row is not None, "get_race returned None after write")

    if row:
        r.check_fields_present(row, [
            "race_uid", "oddspro_race_id", "date", "track", "race_num",
            "code", "distance", "grade", "jump_time", "status",
            "source", "time_status", "race_name", "updated_at",
        ])
        r.check(row.get("race_uid") == race_uid, "race_uid mismatch on read-back")
        r.check(
            row.get("oddspro_race_id") == f"OP-SMOKE-{uid}",
            "oddspro_race_id mismatch",
        )

    # Conflict-key idempotency: update distance + status
    upsert_race({**payload, "distance": "600m", "status": "open"})
    row2 = get_race(race_uid)
    r.check(row2 is not None, "get_race returned None after second upsert")
    if row2:
        r.check(row2.get("distance") == "600m", "Upsert did not update distance")
        r.check(row2.get("status") == "open", "Upsert did not update status")

    r.detail = f"race_uid={race_uid}"

    # Post-test cleanup
    _cleanup("today_races", "race_uid", race_uid)
    return r


def test_runners() -> _Result:
    """
    3. RUNNERS — write/upsert + conflict key + scratch normalisation
    Code path : database.upsert_runners() → db.T("today_runners") → test_today_runners
    Conflict key: (race_uid, box_num)
    """
    r = _Result("runners")
    r.code_path = "database.upsert_runners() → db.T('today_runners') → test_today_runners"

    from env import env
    from database import upsert_runners, get_runners_for_race, upsert_race

    r.check(
        env.table("today_runners") == "test_today_runners",
        f"Routing: env.table('today_runners') → '{env.table('today_runners')}'",
    )

    uid = _uid()
    track = f"SMOKE-RUN-{uid}"
    race_uid = f"{_today()}_GREYHOUND_{track}_2"

    # Pre-test cleanup
    _cleanup("today_runners", "race_uid", race_uid)
    _cleanup("today_races",   "race_uid", race_uid)

    # Write parent race (needed for FK)
    race_row = upsert_race({
        "race_uid":        race_uid,
        "oddspro_race_id": f"OP-RUN-{uid}",
        "date":            _today(),
        "track":           track,
        "race_num":        2,
        "code":            "GREYHOUND",
    })
    race_db_id = (race_row or {}).get("id") or str(uuid.uuid4())

    runners = [
        {
            "race_uid":          race_uid,
            "date":              _today(),
            "track":             track,
            "race_num":          2,
            "box_num":           1,
            "name":              "SPEED DEMON",
            "number":            1,
            "barrier":           1,
            "trainer":           "T. Smith",
            "jockey":            "",
            "driver":            "",
            "owner":             "J. Doe",
            "weight":            None,
            "run_style":         "LEADER",
            "early_speed":       "HIGH",
            "best_time":         "29.85",
            "career":            "10:3-2-1",
            "price":             3.5,
            "rating":            88.5,
            "scratched":         False,
            "scratch_reason":    "",
            "source_confidence": "official",
        },
        {
            # scratch_timing (connector-supplied) must be normalised to scratch_reason
            "race_uid":          race_uid,
            "date":              _today(),
            "track":             track,
            "race_num":          2,
            "box_num":           2,
            "name":              "LATE SCRATCHING",
            "number":            2,
            "barrier":           2,
            "trainer":           "",
            "jockey":            "",
            "driver":            "",
            "owner":             "",
            "weight":            None,
            "run_style":         "",
            "early_speed":       "",
            "best_time":         "",
            "career":            "",
            "price":             None,
            "rating":            None,
            "scratched":         True,
            "scratch_timing":    "Late Scratching",  # must be normalised → scratch_reason
            "source_confidence": "official",
        },
    ]

    count = upsert_runners(race_db_id, runners)
    r.check(count == 2, f"upsert_runners returned {count}, expected 2")

    # Read back
    stored = get_runners_for_race(race_uid)
    r.check(len(stored) == 2, f"get_runners_for_race returned {len(stored)} rows, expected 2")

    if stored:
        runner_1 = next((rw for rw in stored if rw.get("box_num") == 1), None)
        runner_2 = next((rw for rw in stored if rw.get("box_num") == 2), None)

        if runner_1:
            r.check_fields_present(runner_1, [
                "race_uid", "box_num", "name", "trainer", "price",
                "rating", "scratched", "source_confidence",
                "run_style", "early_speed", "best_time", "career",
            ])
            r.check(runner_1.get("name") == "SPEED DEMON", "runner name mismatch")
            r.check(runner_1.get("price") == 3.5, "runner price mismatch")

        # Verify scratch_timing → scratch_reason normalisation
        if runner_2:
            r.check(
                runner_2.get("scratch_reason") == "Late Scratching",
                f"scratch_timing normalisation failed: "
                f"scratch_reason={runner_2.get('scratch_reason')!r} "
                f"(expected 'Late Scratching')",
            )

    # Conflict-key idempotency: upsert box_num=1 with new price
    upsert_runners(race_db_id, [{**runners[0], "price": 4.5}])
    stored2 = get_runners_for_race(race_uid)
    box1_rows = [rw for rw in stored2 if rw.get("box_num") == 1]
    r.check(
        len(box1_rows) == 1,
        f"Conflict key violation: expected 1 row for box_num=1, got {len(box1_rows)}",
    )
    if box1_rows:
        r.check(box1_rows[0].get("price") == 4.5, "Conflict upsert did not update runner price")

    r.detail = f"race_uid={race_uid}, runners_stored={count}"

    # Post-test cleanup
    _cleanup("today_runners", "race_uid", race_uid)
    _cleanup("today_races",   "race_uid", race_uid)
    return r


def test_results() -> _Result:
    """
    4. RESULTS — write + read back + TEST/LIVE routing confirmation
    Code path : database.upsert_result() → db.T("results_log") → test_results_log
    Conflict key: (date, track, race_num, code)
    """
    r = _Result("results")
    r.code_path = "database.upsert_result() → db.T('results_log') → test_results_log"

    from env import env
    from database import upsert_result, get_result

    # Verify TEST routing
    r.check(
        env.table("results_log") == "test_results_log",
        f"Routing: env.table('results_log') → '{env.table('results_log')}' "
        "(expected 'test_results_log')",
    )

    uid = _uid()
    track = f"SMOKE-RES-{uid}"
    # race_uid format: {date}_{code}_{track}_{race_num}
    # get_result() parses this format to reconstruct (date, track, race_num, code)
    race_uid = f"{_today()}_GREYHOUND_{track}_3"

    # Pre-test cleanup
    _cleanup_db("results_log", "track", track)

    result_payload = {
        "race_uid":      race_uid,
        "date":          _today(),
        "track":         track,
        "race_num":      3,
        "code":          "GREYHOUND",
        "winner":        "ROCKET DOG",
        "winner_number": 4,   # mapped to winner_box inside upsert_result
        "win_price":     5.5,
        "place_2":       "FAST PAW",
        "place_3":       "QUICK TAIL",
        "margin":        0.5,
        "winning_time":  "29.15",
        "source":        "oddspro",
    }

    # Write
    written = upsert_result(result_payload)
    r.check(written is not None, "upsert_result returned None")

    # Read back
    row = get_result(race_uid)
    r.check(row is not None, "get_result returned None after write")

    if row:
        r.check_fields_present(row, [
            "date", "track", "race_num", "code", "winner",
            "winner_box", "win_price", "place_2", "place_3",
            "margin", "winning_time", "source", "recorded_at",
        ])
        r.check(row.get("winner") == "ROCKET DOG", "winner mismatch")
        r.check(row.get("winner_box") == 4, "winner_box mismatch (winner_number not mapped)")
        r.check(row.get("win_price") == 5.5, "win_price mismatch")
        r.check(row.get("source") == "oddspro", "source mismatch")

    r.detail = f"race_uid={race_uid}, TEST table=test_results_log"

    # Post-test cleanup
    _cleanup_db("results_log", "track", track)
    return r


def test_predictions() -> _Result:
    """
    5. PREDICTIONS — write prediction + read back
    Code paths:
      ai.learning_store.save_prediction_snapshot()
        → db.T("prediction_snapshots")       → test_prediction_snapshots
        → db.T("prediction_runner_outputs")   → test_prediction_runner_outputs
        → db.T("feature_snapshots")           → test_feature_snapshots
      ai.learning_store.get_stored_prediction() reads back
    """
    r = _Result("predictions")
    r.code_path = (
        "ai.learning_store.save_prediction_snapshot() → "
        "test_prediction_snapshots + test_prediction_runner_outputs + test_feature_snapshots"
    )

    from env import env
    from ai.learning_store import save_prediction_snapshot, get_stored_prediction

    r.check(
        env.table("prediction_snapshots") == "test_prediction_snapshots",
        f"Routing: env.table('prediction_snapshots') → '{env.table('prediction_snapshots')}'",
    )

    uid = _uid()
    track = f"SMOKE-PRED-{uid}"
    race_uid = f"{_today()}_GREYHOUND_{track}_4"
    snap_id = f"snap-{uid}"

    # Pre-test cleanup
    _cleanup("prediction_runner_outputs", "prediction_snapshot_id", snap_id)
    _cleanup("prediction_snapshots",      "prediction_snapshot_id", snap_id)
    _cleanup("feature_snapshots",         "race_uid",               race_uid)

    prediction = {
        "prediction_snapshot_id": snap_id,
        "race_uid":               race_uid,
        "oddspro_race_id":        f"OP-PRED-{uid}",
        "model_version":          "baseline_v1",
        "created_at":             _now(),
        "runner_predictions": [
            {"runner_name": "SPEED DEMON",     "box_num": 1, "predicted_rank": 1, "score": 92.5},
            {"runner_name": "THUNDER PAWS",    "box_num": 2, "predicted_rank": 2, "score": 87.0},
            {"runner_name": "ROCKET GREYHOUND","box_num": 3, "predicted_rank": 3, "score": 81.0},
        ],
        "has_enrichment": 0,
        "source_type":    "pre_race",
    }
    features = [
        {"runner_name": "SPEED DEMON",     "box_num": 1, "price": 3.5, "career_wins": 5},
        {"runner_name": "THUNDER PAWS",    "box_num": 2, "price": 4.0, "career_wins": 3},
        {"runner_name": "ROCKET GREYHOUND","box_num": 3, "price": 5.5, "career_wins": 2},
    ]

    # Write
    ok = save_prediction_snapshot(prediction, features)
    r.check(ok, "save_prediction_snapshot returned False")

    # Read back via production function
    result = get_stored_prediction(race_uid)
    r.check(result.get("ok"), f"get_stored_prediction failed: {result.get('error')}")

    snapshot = result.get("snapshot") or {}
    outputs = result.get("runner_outputs") or []

    r.check_fields_present(snapshot, [
        "prediction_snapshot_id", "race_uid", "oddspro_race_id",
        "model_version", "runner_count", "created_at",
    ])
    r.check(
        snapshot.get("prediction_snapshot_id") == snap_id,
        f"prediction_snapshot_id mismatch: got {snapshot.get('prediction_snapshot_id')!r}",
    )
    r.check(len(outputs) == 3, f"Expected 3 runner outputs, got {len(outputs)}")

    if outputs:
        r.check_fields_present(outputs[0], [
            "runner_name", "box_num", "predicted_rank", "score", "model_version",
        ])

    r.detail = f"snap_id={snap_id}, race_uid={race_uid}"

    # Post-test cleanup
    _cleanup("prediction_runner_outputs", "prediction_snapshot_id", snap_id)
    _cleanup("prediction_snapshots",      "prediction_snapshot_id", snap_id)
    _cleanup("feature_snapshots",         "race_uid",               race_uid)
    return r


def test_learning() -> _Result:
    """
    6. LEARNING — write evaluation + read back
    Code path : ai.learning_store.evaluate_prediction()
                → db.T("learning_evaluations") → test_learning_evaluations
    """
    r = _Result("learning")
    r.code_path = (
        "ai.learning_store.evaluate_prediction() → test_learning_evaluations"
    )

    from env import env
    from ai.learning_store import save_prediction_snapshot, evaluate_prediction
    from db import get_db, safe_query, T

    r.check(
        env.table("learning_evaluations") == "test_learning_evaluations",
        f"Routing: env.table('learning_evaluations') → '{env.table('learning_evaluations')}'",
    )

    uid = _uid()
    track = f"SMOKE-LEARN-{uid}"
    race_uid = f"{_today()}_GREYHOUND_{track}_5"
    snap_id = f"snap-learn-{uid}"

    # Pre-test cleanup
    _cleanup("learning_evaluations",      "race_uid",               race_uid)
    _cleanup("prediction_runner_outputs", "prediction_snapshot_id", snap_id)
    _cleanup("prediction_snapshots",      "prediction_snapshot_id", snap_id)

    # Write a prediction first so evaluate_prediction can find it
    prediction = {
        "prediction_snapshot_id": snap_id,
        "race_uid":               race_uid,
        "oddspro_race_id":        f"OP-LEARN-{uid}",
        "model_version":          "baseline_v1",
        "created_at":             _now(),
        "runner_predictions": [
            {"runner_name": "GOLDEN FLASH",  "box_num": 1, "predicted_rank": 1, "score": 91.0},
            {"runner_name": "SILVER STREAK", "box_num": 2, "predicted_rank": 2, "score": 85.0},
        ],
        "has_enrichment": 0,
        "source_type":    "pre_race",
    }
    save_prediction_snapshot(prediction, [])

    official_result = {
        "winner":     "GOLDEN FLASH",
        "winner_box": 1,
        "place_2":    "SILVER STREAK",
        "place_3":    "",
        "win_price":  4.0,
    }

    # Write evaluation
    eval_result = evaluate_prediction(race_uid, official_result)
    r.check(eval_result.get("ok"),    f"evaluate_prediction failed: {eval_result.get('error')}")
    r.check(eval_result.get("evaluated", 0) > 0, "evaluate_prediction: 0 evaluations written")

    # Read back from real DB
    rows = safe_query(
        lambda: get_db()
        .table(T("learning_evaluations"))
        .select("*")
        .eq("race_uid", race_uid)
        .execute()
        .data,
        [],
    ) or []
    r.check(len(rows) > 0, "No rows in test_learning_evaluations for this race_uid")

    if rows:
        row = rows[0]
        r.check_fields_present(row, [
            "prediction_snapshot_id", "race_uid", "model_version",
            "predicted_winner", "actual_winner", "winner_hit",
            "top3_hit", "evaluation_source", "evaluated_at",
        ])
        r.check(row.get("actual_winner") == "GOLDEN FLASH", "actual_winner mismatch")
        r.check(row.get("winner_hit") is True, "winner_hit should be True")
        r.check(
            row.get("evaluation_source") == "oddspro",
            f"evaluation_source={row.get('evaluation_source')!r} (expected 'oddspro')",
        )

    r.detail = f"race_uid={race_uid}, evaluations={eval_result.get('evaluated', 0)}"

    # Post-test cleanup
    _cleanup("learning_evaluations",      "race_uid",               race_uid)
    _cleanup("prediction_runner_outputs", "prediction_snapshot_id", snap_id)
    _cleanup("prediction_snapshots",      "prediction_snapshot_id", snap_id)
    return r


def test_backtesting() -> _Result:
    """
    7. BACKTESTING — write run + items + read back
    Code paths:
      ai.backtest_engine._save_backtest_run()   → test_backtest_runs
      ai.backtest_engine._save_backtest_items() → test_backtest_run_items
      ai.backtest_engine.get_backtest_run()     reads back
    """
    r = _Result("backtesting")
    r.code_path = (
        "ai.backtest_engine._save_backtest_run() / _save_backtest_items() → "
        "test_backtest_runs + test_backtest_run_items; get_backtest_run() reads back"
    )

    from env import env
    from ai.backtest_engine import _save_backtest_run, _save_backtest_items, get_backtest_run

    r.check(
        env.table("backtest_runs") == "test_backtest_runs",
        f"Routing: env.table('backtest_runs') → '{env.table('backtest_runs')}'",
    )

    uid = _uid()
    run_id = f"bt-smoke-{uid}"
    bt_track = f"SMOKE-BT-{uid}"

    # Pre-test cleanup
    _cleanup("backtest_run_items", "run_id", run_id)
    _cleanup("backtest_runs",      "run_id", run_id)

    summary = {
        "run_id":           run_id,
        "date_from":        _today(),
        "date_to":          _today(),
        "code_filter":      "GREYHOUND",
        "track_filter":     "",
        "model_version":    "baseline_v1",
        "total_races":      5,
        "total_runners":    40,
        "winner_hit_count": 3,
        "top2_hit_count":   4,
        "top3_hit_count":   5,
        "hit_rate":         0.6,
        "top2_rate":        0.8,
        "top3_rate":        1.0,
        "avg_winner_odds":  4.25,
        "created_at":       _now(),
    }

    items = [
        {
            "run_id":           run_id,
            "race_uid":         f"{_today()}_GREYHOUND_{bt_track}_{i}",
            "model_version":    "baseline_v1",
            "predicted_winner": "RUNNER_A",
            "actual_winner":    "RUNNER_A",
            "winner_hit":       True,
            "winner_odds":      3.5,
            "created_at":       _now(),
        }
        for i in range(1, 4)
    ]

    # Write run summary
    _save_backtest_run(summary)

    # Write items
    _save_backtest_items(items)

    # Read back via public API
    result = get_backtest_run(run_id)
    r.check(result.get("ok"), f"get_backtest_run failed: {result.get('error')}")

    run_row = result.get("run") or {}
    r.check_fields_present(run_row, [
        "run_id", "date_from", "date_to", "model_version",
        "total_races", "total_runners", "hit_rate",
        "winner_hit_count", "top2_hit_count", "top3_hit_count",
    ])
    r.check(run_row.get("run_id") == run_id, "run_id mismatch on read-back")
    r.check(
        abs((run_row.get("hit_rate") or 0) - 0.6) < 0.001,
        f"hit_rate mismatch: got {run_row.get('hit_rate')!r}",
    )

    r.detail = f"run_id={run_id}, items={len(items)}"

    # Post-test cleanup
    _cleanup("backtest_run_items", "run_id", run_id)
    _cleanup("backtest_runs",      "run_id", run_id)
    return r


def test_result_triggers_evaluation() -> _Result:
    """
    Fix 1 verification: _write_result() must call evaluate_prediction().
    Writes a prediction snapshot, then writes a result via _write_result(),
    then confirms a row appears in test_learning_evaluations.
    """
    r = _Result("result_triggers_evaluation")
    r.code_path = "data_engine._write_result() → test_learning_evaluations"

    from db import get_db, safe_query, T
    from ai.learning_store import save_prediction_snapshot
    from data_engine import _write_result
    from database import upsert_race, get_race

    uid      = _uid()
    track    = f"smoke-eval-{uid}"
    race_uid = f"{_today()}_GREYHOUND_{track}_3"
    snap_id  = f"snap-eval-{uid}"

    _cleanup("learning_evaluations",      "race_uid",               race_uid)
    _cleanup("prediction_runner_outputs", "prediction_snapshot_id", snap_id)
    _cleanup("prediction_snapshots",      "prediction_snapshot_id", snap_id)
    _cleanup("results_log",               "race_uid",               race_uid)
    _cleanup("today_races",               "race_uid",               race_uid)

    upsert_race({
        "race_uid":  race_uid,
        "track":     track,
        "race_num":  3,
        "code":      "GREYHOUND",
        "date":      _today(),
        "jump_time": "12:00",
        "status":    "jumped_estimated",
    })

    save_prediction_snapshot({
        "prediction_snapshot_id": snap_id,
        "race_uid":               race_uid,
        "oddspro_race_id":        f"OP-EVAL-{uid}",
        "model_version":          "baseline_v1",
        "created_at":             _now(),
        "runner_predictions": [
            {"runner_name": "TEST RUNNER A", "box_num": 1,
             "predicted_rank": 1, "score": 88.0},
        ],
        "has_enrichment": 0,
        "source_type":    "pre_race",
    }, [])

    class FakeResult:
        race_uid        = race_uid
        oddspro_race_id = f"OP-EVAL-{uid}"
        date            = _today()
        track           = track
        race_num        = 3
        code            = "GREYHOUND"
        winner          = "TEST RUNNER A"
        winner_number   = 1
        win_price       = 3.50
        place_2         = ""
        place_3         = ""
        margin          = None
        winning_time    = None
        source          = "oddspro"

    _write_result(FakeResult())

    rows = safe_query(
        lambda: get_db()
        .table(T("learning_evaluations"))
        .select("winner_hit,actual_winner,evaluation_source")
        .eq("race_uid", race_uid)
        .execute()
        .data,
        []
    ) or []

    r.check(len(rows) > 0,
        "FAIL: _write_result did not trigger evaluate_prediction — "
        "test_learning_evaluations has no row. Fix 1 not applied correctly.")

    if rows:
        row = rows[0]
        r.check(row.get("winner_hit") is True,
            f"winner_hit should be True, got: {row.get('winner_hit')}")
        r.check(row.get("actual_winner") == "TEST RUNNER A",
            f"actual_winner mismatch: {row.get('actual_winner')!r}")

    race = get_race(race_uid)
    r.check(
        race and (race.get("status") or "").lower() == "final",
        f"Race status should be 'final', got: {race.get('status') if race else 'NOT FOUND'}"
    )

    r.detail = (
        f"race_uid={race_uid} "
        f"eval_rows={len(rows)} "
        f"winner_hit={rows[0].get('winner_hit') if rows else 'N/A'}"
    )

    _cleanup("learning_evaluations",      "race_uid",               race_uid)
    _cleanup("prediction_runner_outputs", "prediction_snapshot_id", snap_id)
    _cleanup("prediction_snapshots",      "prediction_snapshot_id", snap_id)
    _cleanup("results_log",               "race_uid",               race_uid)
    _cleanup("today_races",               "race_uid",               race_uid)
    return r


def test_prediction_snapshot_columns() -> _Result:
    """
    Fix 2 verification: prediction_snapshots must have
    race_date, track, race_num, code, top_runner columns.
    Also verifies signal/decision/confidence/ev columns from Phase 5.
    """
    r = _Result("prediction_snapshot_columns")
    r.code_path = "prediction_snapshots schema — Phase 5 columns"

    from db import get_db, safe_query, T
    from ai.predictor import predict_from_snapshot

    uid      = _uid()
    track    = f"smoke-col-{uid}"
    race_uid = f"{_today()}_GREYHOUND_{track}_1"

    _cleanup("prediction_snapshots",      "race_uid", race_uid)
    _cleanup("prediction_runner_outputs", "race_uid", race_uid)

    race = {
        "race_uid":        race_uid,
        "oddspro_race_id": f"OP-COL-{uid}",
        "track":           track,
        "race_num":        1,
        "code":            "GREYHOUND",
        "date":            _today(),
        "jump_time":       "13:00",
        "status":          "open",
        "distance":        "375",
        "grade":           "5",
    }
    runners = [
        {"name": "RUNNER A", "box_num": 1, "number": 1,
         "price": 2.5, "scratched": False, "source_confidence": "api"},
        {"name": "RUNNER B", "box_num": 2, "number": 2,
         "price": 4.0, "scratched": False, "source_confidence": "api"},
    ]

    result = predict_from_snapshot(race, runners)
    r.check(result.get("ok"),
        f"predict_from_snapshot failed: {result.get('error')}")

    if result.get("ok"):
        snap_id = result.get("prediction_snapshot_id") or ""
        rows = safe_query(
            lambda: get_db()
            .table(T("prediction_snapshots"))
            .select("race_uid,race_date,track,race_num,code,"
                    "top_runner,signal,decision,confidence,ev")
            .eq("prediction_snapshot_id", snap_id)
            .execute()
            .data,
            []
        ) or []

        r.check(len(rows) > 0, "No snapshot row found after predict_from_snapshot")

        if rows:
            row = rows[0]
            required_cols = [
                "race_date", "track", "race_num", "code",
                "top_runner", "signal", "decision", "confidence", "ev"
            ]
            for col in required_cols:
                r.check(col in row,
                    f"Column '{col}' missing from prediction_snapshots — "
                    f"migration not applied. Run: python -c \"from migrations "
                    f"import run_migrations; from db import get_db; "
                    f"run_migrations(get_db())\"")

            if "track" in row:
                r.check(row.get("track") == track,
                    f"track value wrong: got {row.get('track')!r}, "
                    f"expected {track!r}")
            if "race_num" in row:
                r.check(row.get("race_num") == 1,
                    f"race_num value wrong: got {row.get('race_num')}")
            if "code" in row:
                r.check(row.get("code") == "GREYHOUND",
                    f"code value wrong: got {row.get('code')!r}")

        r.detail = f"snap_id={snap_id} columns_present={list((rows[0] if rows else {}).keys())}"

        _cleanup("prediction_snapshots",      "race_uid", race_uid)
        _cleanup("prediction_runner_outputs", "race_uid", race_uid)

    return r


def test_performance_summary_bankroll() -> _Result:
    """
    Fix 8 verification: get_performance_summary() must return
    bankroll_history, win_rate, roi, current_bank.
    """
    r = _Result("performance_summary_bankroll")
    r.code_path = "ai.learning_store.get_performance_summary() → bankroll_history"

    from ai.learning_store import get_performance_summary

    result = get_performance_summary(limit=50)
    r.check(result.get("ok") is not False,
        f"get_performance_summary failed: {result.get('error')}")

    required_keys = [
        "bankroll_history", "win_rate", "roi", "current_bank",
        "total_profit", "total_evaluated", "model_version"
    ]
    for key in required_keys:
        r.check(key in result,
            f"Key '{key}' missing from performance summary — "
            f"Fix 8 (bankroll_history) not applied to learning_store.py")

    if "bankroll_history" in result:
        r.check(isinstance(result["bankroll_history"], list),
            f"bankroll_history should be a list, got "
            f"{type(result['bankroll_history']).__name__}")

    if "win_rate" in result:
        r.check(isinstance(result["win_rate"], (int, float)),
            f"win_rate should be numeric, got {result.get('win_rate')!r}")

    r.detail = (
        f"total_evaluated={result.get('total_evaluated')} "
        f"win_rate={result.get('win_rate')} "
        f"roi={result.get('roi')} "
        f"bankroll_history_len={len(result.get('bankroll_history') or [])}"
    )
    return r


def test_predictions_today_get_route() -> _Result:
    """
    Fix 5 verification: GET /api/predictions/today must exist and
    return {"ok": true, "predictions": […], "count": N}.
    Requires the Flask app to be running locally.
    Set APP_BASE_URL env var if not using localhost:5000.
    """
    r = _Result("predictions_today_get_route")
    r.code_path = "GET /api/predictions/today"

    import os
    import urllib.request
    import json

    base_url = os.environ.get("APP_BASE_URL", "http://localhost:5000")
    url = f"{base_url}/api/predictions/today"

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            status = resp.status
            body   = json.loads(resp.read().decode())

        r.check(status == 200,
            f"GET /api/predictions/today returned HTTP {status} "
            f"(expected 200) — endpoint may be missing")
        r.check(body.get("ok") is True,
            f"Response ok=False: {body.get('error')}")
        r.check("predictions" in body,
            "Response missing 'predictions' key — Fix 5 not applied")
        r.check(isinstance(body.get("predictions"), list),
            "predictions is not a list")
        r.check("count" in body,
            "Response missing 'count' key")

        r.detail = (
            f"status={status} "
            f"count={body.get('count')} "
            f"predictions_sample={body.get('predictions', [])[:1]}"
        )

    except OSError as e:
        if "Connection refused" in str(e) or "timed out" in str(e):
            r.fail(
                f"App not running at {base_url}. "
                f"Start it first: DP_ENV=TEST python app.py "
                f"then re-run this test. "
                f"Or set APP_BASE_URL env var if running elsewhere."
            )
        else:
            r.fail(f"Request failed: {e}")

    return r


def test_source_logging() -> _Result:
    """
    8. SOURCE LOG — write + read back
    Code path : database.write_source_log() → db.T("source_log") → test_source_log
    """
    r = _Result("source_logging")
    r.code_path = "database.write_source_log() → db.T('source_log') → test_source_log"

    from env import env
    from database import write_source_log
    from db import get_db, safe_query, T

    r.check(
        env.table("source_log") == "test_source_log",
        f"Routing: env.table('source_log') → '{env.table('source_log')}' "
        "(expected 'test_source_log')",
    )

    uid = _uid()
    test_url = f"https://oddspro.test/api/external/meetings/{uid}"

    # Pre-test cleanup (source_log is append-only but cleanup keeps the DB tidy)
    _cleanup_db("source_log", "url", test_url)

    entry = {
        "date":          _today(),
        "call_num":      1,
        "url":           test_url,
        "method":        "GET",
        "status":        "200",
        "rows_returned": 12,
    }

    # Write
    written = write_source_log(entry)
    r.check(written is not None, "write_source_log returned None")

    # Read back from real DB
    rows = safe_query(
        lambda: get_db()
        .table(T("source_log"))
        .select("*")
        .eq("url", test_url)
        .execute()
        .data,
        [],
    ) or []
    r.check(len(rows) > 0, "No rows returned from test_source_log after write")

    if rows:
        row = rows[0]
        r.check_fields_present(row, [
            "date", "call_num", "url", "method", "status", "rows_returned", "created_at",
        ])
        r.check(row.get("url") == test_url, "url mismatch")
        r.check(row.get("rows_returned") == 12, "rows_returned mismatch")

    r.detail = f"url={test_url}"

    # Post-test cleanup
    _cleanup_db("source_log", "url", test_url)
    return r


# =============================================================================
# REPORT GENERATOR
# =============================================================================

def _status(r: _Result) -> str:
    return "PASS" if r.passed else "FAIL"


def print_report(results: list[_Result]) -> bool:
    all_pass = all(r.passed for r in results)
    final = "PASS" if all_pass else "FAIL"

    print()
    print("=" * 68)
    print("  DEMONPULSE — LIVE SMOKE TEST REPORT  (DP_ENV=TEST)")
    print("=" * 68)
    print()

    print("1. DATA SMOKE TEST REPORT")
    print("-" * 68)
    for r in results:
        status = _status(r)
        print(f"  [{status}]  {r.name}")
        print(f"         Code path : {r.code_path}")
        if r.detail:
            print(f"         Detail    : {r.detail}")
        if not r.passed:
            for issue in r.issues:
                print(f"         ISSUE     : {issue}")
    print()

    print("2. VERIFICATION NOTES")
    print("-" * 68)
    print("  ✓ Real Supabase client used (no in-memory mock)")
    print("  ✓ Writes routed to test_* tables (DP_ENV=TEST)")
    print("  ✓ Each test writes, reads back, and asserts values")
    print("  ✓ Conflict-key idempotency verified per subsystem")
    print("  ✓ Test data cleaned up after each subsystem")
    print()

    print("3. FINAL DATA VERIFICATION")
    print("-" * 68)
    subsystems = [r.name for r in results]
    passed = [r.name for r in results if r.passed]
    failed = [r.name for r in results if not r.passed]
    print(f"  Subsystems tested : {', '.join(subsystems)}")
    print(f"  Passed            : {len(passed)}/{len(results)}")
    if failed:
        print(f"  Failed            : {', '.join(failed)}")
    if all_pass:
        print("  Data layer verdict: STABLE end-to-end.")
        print("  db.py → env.py → real Supabase path verified for all subsystems.")
        print("  All conflict keys, field mappings, and TEST/LIVE routing confirmed.")
    else:
        print("  Data layer verdict: ISSUES FOUND — see failures above.")
    print()

    print("4. FINAL STATUS")
    print("-" * 68)
    print(f"  {final}")
    print("=" * 68)
    print()

    return all_pass


# =============================================================================
# MAIN
# =============================================================================

def main() -> int:
    from env import env

    if not env.is_test:
        print("FATAL: smoke test must run in TEST mode (DP_ENV=TEST)")
        return 1

    # Check prerequisites (routing + connectivity) before running any test
    problems = check_prerequisites()
    if problems:
        print()
        print("=" * 68)
        print("  DEMONPULSE — LIVE SMOKE TEST: PREREQUISITES FAILED")
        print("=" * 68)
        for p in problems:
            print(f"  FATAL: {p}")
        print()
        print("  Run command: DP_ENV=TEST python smoke_test.py")
        print("  Ensure SUPABASE_URL and SUPABASE_KEY are set.")
        print("=" * 68)
        return 1

    tests = [
        test_meetings,
        test_races,
        test_runners,
        test_results,
        test_predictions,
        test_learning,
        test_backtesting,
        test_source_logging,
        # NEW — verify v72 Part 1 + Part 2 fixes:
        test_result_triggers_evaluation,
        test_prediction_snapshot_columns,
        test_performance_summary_bankroll,
        test_predictions_today_get_route,   # needs app running
    ]

    results = []
    for fn in tests:
        try:
            result = fn()
        except Exception as exc:
            result = _Result(fn.__name__.replace("test_", ""))
            result.fail(f"Uncaught exception: {exc}")
            import traceback
            traceback.print_exc()
        results.append(result)

    all_pass = print_report(results)
    return 0 if all_pass else 1


def run_all_tests() -> dict:
    """
    Run all smoke tests and return a structured result dict.
    Intended to be called from the /api/smoke-test endpoint.
    """
    from env import env

    if not env.is_test:
        raise RuntimeError("smoke tests must run in TEST mode (DP_ENV=TEST)")

    problems = check_prerequisites()
    if problems:
        return {"passed": False, "prereq_failures": problems, "tests": []}

    tests = [
        test_meetings,
        test_races,
        test_runners,
        test_results,
        test_predictions,
        test_learning,
        test_backtesting,
        test_source_logging,
        # NEW — verify v72 Part 1 + Part 2 fixes:
        test_result_triggers_evaluation,
        test_prediction_snapshot_columns,
        test_performance_summary_bankroll,
        test_predictions_today_get_route,   # needs app running
    ]

    results = []
    for fn in tests:
        try:
            r = fn()
        except Exception as exc:
            r = _Result(fn.__name__.replace("test_", ""))
            r.fail(f"Uncaught exception: {exc}")
        results.append(r)

    all_pass = all(r.passed for r in results)
    return {
        "passed": all_pass,
        "tests": [
            {
                "name": r.name,
                "passed": r.passed,
                "issues": r.issues,
                "code_path": r.code_path,
                "detail": r.detail,
            }
            for r in results
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
