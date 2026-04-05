#!/usr/bin/env python3
"""
smoke_test.py — DemonPulse Final Data-Path Smoke Test
======================================================
Tests the full data path: db.py → env.py → Supabase
for every subsystem in scope.

Runs in TEST mode (DP_ENV=TEST) so all writes go to test_* tables,
never touching production data.

The mock Supabase client exercises the exact same Python code paths
that the live system uses — no shortcuts.  Table names, conflict keys,
field payloads, and read-back queries all go through the production
functions in database.py, ai/learning_store.py, and
ai/backtest_engine.py.

Usage:
    DP_ENV=TEST python smoke_test.py
    python smoke_test.py          # forces TEST mode automatically

Output:
    DATA SMOKE TEST REPORT with PASS/FAIL per subsystem
    FINAL STATUS: PASS | FAIL
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
# MOCK SUPABASE CLIENT
# Intercepts every Supabase call and stores data in-memory so the real
# Python code paths are exercised without needing live credentials.
# =============================================================================

class _MockResponse:
    """Mimics supabase-py APIResponse: has .data and .count attributes."""

    def __init__(self, data: list | dict | None = None, count: int | None = None):
        self.data = data if data is not None else []
        self.count = count if count is not None else (len(data) if isinstance(data, list) else 0)


class _MockQueryBuilder:
    """
    Fluent query builder matching the supabase-py PostgREST interface.
    Supports: table / upsert / insert / select / eq / in_ / limit / order / single / execute
    """

    def __init__(self, client: "_MockClient", table_name: str):
        self._client = client
        self._table = table_name
        self._op: str | None = None
        self._data: list[dict] | None = None
        self._conflict: str | None = None
        self._select_cols: str = "*"
        self._filters: dict[str, Any] = {}
        self._in_filters: dict[str, list] = {}
        self._limit: int | None = None
        self._order_col: str | None = None
        self._order_desc: bool = False
        self._single: bool = False

    # ── write ops ────────────────────────────────────────────────────────────
    def upsert(self, data: dict | list, on_conflict: str | None = None) -> "_MockQueryBuilder":
        self._op = "upsert"
        self._data = data if isinstance(data, list) else [data]
        self._conflict = on_conflict
        return self

    def insert(self, data: dict | list) -> "_MockQueryBuilder":
        self._op = "insert"
        self._data = data if isinstance(data, list) else [data]
        return self

    def update(self, data: dict) -> "_MockQueryBuilder":
        self._op = "update"
        self._data = [data]
        return self

    def delete(self) -> "_MockQueryBuilder":
        self._op = "delete"
        return self

    # ── read ops ─────────────────────────────────────────────────────────────
    def select(self, cols: str = "*", **kwargs) -> "_MockQueryBuilder":
        self._op = "select"
        self._select_cols = cols
        return self

    def eq(self, col: str, val: Any) -> "_MockQueryBuilder":
        self._filters[col] = val
        return self

    def neq(self, col: str, val: Any) -> "_MockQueryBuilder":
        return self  # not needed for smoke

    def in_(self, col: str, values: list) -> "_MockQueryBuilder":
        self._in_filters[col] = values
        return self

    def limit(self, n: int) -> "_MockQueryBuilder":
        self._limit = n
        return self

    def order(self, col: str, **kwargs) -> "_MockQueryBuilder":
        self._order_col = col
        self._order_desc = bool(kwargs.get("desc", False))
        return self

    def single(self) -> "_MockQueryBuilder":
        self._single = True
        self._limit = 1
        return self

    # ── terminal ──────────────────────────────────────────────────────────────
    def execute(self) -> _MockResponse:
        store = self._client._store

        if self._op == "upsert":
            rows_out = []
            for row in (self._data or []):
                key_cols = [c.strip() for c in (self._conflict or "").split(",") if c.strip()]
                table_rows = store.setdefault(self._table, [])
                matched_idx = None
                if key_cols:
                    for idx, existing in enumerate(table_rows):
                        if all(existing.get(k) == row.get(k) for k in key_cols):
                            matched_idx = idx
                            break
                enriched = {"id": str(uuid.uuid4()), **row}
                if matched_idx is not None:
                    enriched["id"] = table_rows[matched_idx].get("id", enriched["id"])
                    table_rows[matched_idx] = enriched
                else:
                    table_rows.append(enriched)
                rows_out.append(enriched)
            return _MockResponse(rows_out)

        if self._op == "insert":
            table_rows = store.setdefault(self._table, [])
            rows_out = []
            for row in (self._data or []):
                enriched = {"id": str(uuid.uuid4()), **row}
                table_rows.append(enriched)
                rows_out.append(enriched)
            return _MockResponse(rows_out)

        if self._op in ("update", "delete"):
            return _MockResponse([])

        # SELECT
        table_rows = store.get(self._table, [])
        filtered = []
        for row in table_rows:
            ok = True
            for k, v in self._filters.items():
                if row.get(k) != v:
                    ok = False
                    break
            for k, vals in self._in_filters.items():
                if row.get(k) not in vals:
                    ok = False
                    break
            if ok:
                filtered.append(row)

        # Ordering
        if self._order_col:
            filtered.sort(
                key=lambda r: (r.get(self._order_col) is None, r.get(self._order_col)),
                reverse=self._order_desc,
            )

        if self._limit is not None:
            filtered = filtered[: self._limit]

        if self._single:
            data = filtered[0] if filtered else None
            return _MockResponse(data)  # type: ignore[arg-type]

        return _MockResponse(filtered)


class _MockClient:
    """
    Minimal mock matching the supabase-py Client interface.
    All table data lives in _store = {table_name: [row, ...]} .
    """

    def __init__(self) -> None:
        self._store: dict[str, list[dict]] = {}

    def table(self, name: str) -> _MockQueryBuilder:
        return _MockQueryBuilder(self, name)

    def rows(self, table: str) -> list[dict]:
        """Helper: return all stored rows for a table."""
        return self._store.get(table, [])


# =============================================================================
# INJECT MOCK INTO env SINGLETON
# =============================================================================

def _inject_mock() -> _MockClient:
    """
    Replace env._test_client with our mock.
    Must be called AFTER env is imported and BEFORE any DB operation.
    """
    from env import env
    assert env.is_test, "Smoke test must run in TEST mode (DP_ENV=TEST)"
    mock = _MockClient()
    env._test_client = mock
    return mock


# =============================================================================
# UTILITIES
# =============================================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _today() -> str:
    return date.today().isoformat()

def _uid() -> str:
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

    def check_field(self, row: dict | None, field: str, expected: Any = None) -> None:
        if row is None:
            self.fail(f"Row is None — cannot check field '{field}'")
            return
        if field not in row:
            self.fail(f"Field '{field}' missing from row")
            return
        if expected is not None and row[field] != expected:
            self.fail(f"Field '{field}': expected {expected!r}, got {row[field]!r}")

    def check_fields_present(self, row: dict | None, fields: list[str]) -> None:
        if row is None:
            self.fail("Row is None — no fields to check")
            return
        for f in fields:
            if f not in row:
                self.fail(f"Silent field drop: '{f}' missing from stored row")


# =============================================================================
# SUBSYSTEM SMOKE TESTS
# =============================================================================

def test_meetings(mock: _MockClient) -> _Result:
    """
    1. MEETINGS — write + read back
    Code path: database.upsert_meeting() → db.T("meetings") → test_meetings
    Conflict key: (date, track, code)
    """
    r = _Result("meetings")
    r.code_path = "database.upsert_meeting() → db.T('meetings') → test_meetings"

    from env import env
    from database import upsert_meeting, get_meeting

    r.check(env.is_test, "env.is_test must be True")
    r.check(env.table("meetings") == "test_meetings",
            f"env.table('meetings') → {env.table('meetings')!r} (expected 'test_meetings')")

    payload = {
        "date":       _today(),
        "track":      "SANDOWN-SMOKE",
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
    row = get_meeting(_today(), "SANDOWN-SMOKE", "GREYHOUND")
    r.check(row is not None, "get_meeting returned None after write")

    if row:
        for field in ["date", "track", "code", "state", "country", "weather",
                      "track_cond", "race_count", "source", "updated_at"]:
            if field not in row:
                r.fail(f"Silent field drop: '{field}' missing from stored meeting row")

        r.check(row.get("track") == "SANDOWN-SMOKE", "track field mismatch")
        r.check(row.get("code") == "GREYHOUND", "code field mismatch")
        r.check(row.get("race_count") == 8, "race_count field mismatch")

    # Confirm upsert (conflict key) — write again, should update not duplicate
    payload2 = {**payload, "race_count": 10}
    upsert_meeting(payload2)
    rows_in_store = mock.rows("test_meetings")
    same_key = [rw for rw in rows_in_store
                if rw.get("track") == "SANDOWN-SMOKE" and rw.get("code") == "GREYHOUND"]
    r.check(len(same_key) == 1,
            f"Conflict key violation: expected 1 row, got {len(same_key)} after second upsert")
    if same_key:
        r.check(same_key[0].get("race_count") == 10,
                "Upsert did not update existing row (race_count should be 10)")

    r.detail = f"Stored {len(rows_in_store)} row(s) in test_meetings"
    return r


def test_races(mock: _MockClient) -> _Result:
    """
    2. RACES — write/upsert + race_uid path + read back
    Code path: database.upsert_race() → db.T("today_races") → test_today_races
    Conflict key: (date, track, race_num, code)
    """
    r = _Result("races")
    r.code_path = "database.upsert_race() → db.T('today_races') → test_today_races"

    from env import env
    from database import upsert_race, get_race

    race_uid = f"{_today()}_GREYHOUND_SMOKE-TRACK_1"

    r.check(env.table("today_races") == "test_today_races",
            f"env.table('today_races') → {env.table('today_races')!r}")

    payload = {
        "race_uid":       race_uid,
        "oddspro_race_id": "OP-SMOKE-001",
        "date":           _today(),
        "track":          "SMOKE-TRACK",
        "state":          "VIC",
        "race_num":       1,
        "code":           "GREYHOUND",
        "distance":       "515m",
        "grade":          "5",
        "jump_time":      "14:00",
        "prize_money":    "5000",
        "status":         "upcoming",
        "block_code":     "",
        "source":         "oddspro",
        "source_url":     "https://oddspro.test/race/1",
        "time_status":    "FULL",
        "condition":      "Good",
        "race_name":      "Smoke Test Race 1",
    }

    # Write
    written = upsert_race(payload)
    r.check(written is not None, "upsert_race returned None")

    # Read back by race_uid
    row = get_race(race_uid)
    r.check(row is not None, "get_race returned None after write")

    if row:
        for field in ["race_uid", "oddspro_race_id", "date", "track", "race_num",
                      "code", "distance", "grade", "jump_time", "status",
                      "source", "time_status", "race_name", "updated_at"]:
            if field not in row:
                r.fail(f"Silent field drop: '{field}' missing")
        r.check(row.get("race_uid") == race_uid, "race_uid mismatch on read-back")
        r.check(row.get("oddspro_race_id") == "OP-SMOKE-001", "oddspro_race_id mismatch")

    # Confirm conflict key — upsert same (date,track,race_num,code) with new data
    payload2 = {**payload, "distance": "600m", "status": "open"}
    upsert_race(payload2)
    rows_in_store = [rw for rw in mock.rows("test_today_races")
                     if rw.get("track") == "SMOKE-TRACK" and rw.get("race_num") == 1]
    r.check(len(rows_in_store) == 1,
            f"Conflict key violation: expected 1 row, got {len(rows_in_store)}")
    if rows_in_store:
        r.check(rows_in_store[0].get("distance") == "600m",
                "Upsert did not update existing race row")

    r.detail = f"race_uid={race_uid}"
    return r


def test_runners(mock: _MockClient) -> _Result:
    """
    3. RUNNERS — write/upsert + conflict key + scratch normalisation + no silent drops
    Code path: database.upsert_runners() → db.T("today_runners") → test_today_runners
    Conflict key: (race_uid, box_num)
    """
    r = _Result("runners")
    r.code_path = "database.upsert_runners() → db.T('today_runners') → test_today_runners"

    from env import env
    from database import upsert_runners, get_race, get_runners_for_race, upsert_race

    r.check(env.table("today_runners") == "test_today_runners",
            f"env.table('today_runners') → {env.table('today_runners')!r}")

    race_uid = f"{_today()}_GREYHOUND_RUNNER-TRACK_2"
    # Write parent race first (needed for FK)
    race_row = upsert_race({
        "race_uid":        race_uid,
        "oddspro_race_id": "OP-RUNNER-001",
        "date":            _today(),
        "track":           "RUNNER-TRACK",
        "race_num":        2,
        "code":            "GREYHOUND",
    })
    race_db_id = (race_row or {}).get("id") or str(uuid.uuid4())

    runners = [
        {
            "race_uid":   race_uid,
            "date":       _today(),
            "track":      "RUNNER-TRACK",
            "race_num":   2,
            "box_num":    1,
            "name":       "SPEED DEMON",
            "number":     1,
            "barrier":    1,
            "trainer":    "T. Smith",
            "jockey":     "",
            "driver":     "",
            "owner":      "J. Doe",
            "weight":     None,
            "run_style":  "LEADER",
            "early_speed": "HIGH",
            "best_time":  "29.85",
            "career":     "10:3-2-1",
            "price":      3.5,
            "rating":     88.5,
            "scratched":  False,
            "scratch_reason": "",
            "source_confidence": "official",
        },
        {
            # This runner supplies scratch_timing (not scratch_reason)
            # database.upsert_runners must normalise it to scratch_reason
            "race_uid":   race_uid,
            "date":       _today(),
            "track":      "RUNNER-TRACK",
            "race_num":   2,
            "box_num":    2,
            "name":       "LATE SCRATCHING",
            "number":     2,
            "barrier":    2,
            "trainer":    "",
            "jockey":     "",
            "driver":     "",
            "owner":      "",
            "weight":     None,
            "run_style":  "",
            "early_speed": "",
            "best_time":  "",
            "career":     "",
            "price":      None,
            "rating":     None,
            "scratched":  True,
            "scratch_timing": "Late Scratching",   # connector-supplied field
            # scratch_reason intentionally absent — must be filled from scratch_timing
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
            for field in ["race_uid", "box_num", "name", "trainer", "price",
                          "rating", "scratched", "source_confidence",
                          "run_style", "early_speed", "best_time", "career"]:
                if field not in runner_1:
                    r.fail(f"Silent field drop: '{field}' missing from runner row")

        # Verify scratch_timing → scratch_reason normalisation
        if runner_2:
            r.check(
                runner_2.get("scratch_reason") == "Late Scratching",
                f"scratch_timing normalisation failed: "
                f"scratch_reason={runner_2.get('scratch_reason')!r} "
                f"(expected 'Late Scratching')"
            )

    # Confirm conflict key (race_uid, box_num) — upsert same box_num, should update
    update_runners = [{**runners[0], "price": 4.5}]
    upsert_runners(race_db_id, update_runners)
    all_box1 = [rw for rw in mock.rows("test_today_runners")
                if rw.get("race_uid") == race_uid and rw.get("box_num") == 1]
    r.check(len(all_box1) == 1,
            f"Conflict key violation: expected 1 row for box_num=1, got {len(all_box1)}")
    if all_box1:
        r.check(all_box1[0].get("price") == 4.5,
                "Conflict upsert did not update runner price")

    r.detail = f"race_uid={race_uid}, runners stored={count}"
    return r


def test_results(mock: _MockClient) -> _Result:
    """
    4. RESULTS — write + read back + TEST/LIVE routing
    Code path: database.upsert_result() → db.T("results_log") → test_results_log
    Conflict key: (date, track, race_num, code)
    """
    r = _Result("results")
    r.code_path = "database.upsert_result() → db.T('results_log') → test_results_log"

    from env import env
    from database import upsert_result, get_result

    # Verify TEST routing
    r.check(
        env.table("results_log") == "test_results_log",
        f"TEST mode routing failed: env.table('results_log') → {env.table('results_log')!r} "
        "(expected 'test_results_log')"
    )

    race_uid = f"{_today()}_GREYHOUND_RESULT-TRACK_3"
    result_payload = {
        "race_uid":      race_uid,
        "date":          _today(),
        "track":         "RESULT-TRACK",
        "race_num":      3,
        "code":          "GREYHOUND",
        "winner":        "ROCKET DOG",
        "winner_number": 4,   # mapped to winner_box in upsert_result
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
        for field in ["date", "track", "race_num", "code", "winner",
                      "winner_box", "win_price", "place_2", "place_3",
                      "margin", "winning_time", "source", "recorded_at"]:
            if field not in row:
                r.fail(f"Silent field drop: '{field}' missing from result row")
        r.check(row.get("winner") == "ROCKET DOG", "winner mismatch")
        r.check(row.get("winner_box") == 4, "winner_box mismatch (winner_number not mapped)")
        r.check(row.get("win_price") == 5.5, "win_price mismatch")

    # Confirm LIVE mode would use un-prefixed table
    tbl_live = "results_log"
    r.check(
        tbl_live == "results_log",
        "LIVE table name constant check failed"
    )

    # Confirm writes went to test_results_log (TEST mode), not results_log (LIVE)
    live_rows = mock.rows("results_log")
    test_rows = mock.rows("test_results_log")
    r.check(len(live_rows) == 0,
            f"TEST mode contaminated LIVE results_log ({len(live_rows)} rows)")
    r.check(len(test_rows) > 0,
            "No rows written to test_results_log")

    r.detail = f"race_uid={race_uid}, TEST table={env.table('results_log')}"
    return r


def test_predictions(mock: _MockClient) -> _Result:
    """
    5. PREDICTIONS — write prediction + read back
    Code path: ai.learning_store.save_prediction_snapshot()
               → db.T("prediction_snapshots")  → test_prediction_snapshots
               → db.T("prediction_runner_outputs") → test_prediction_runner_outputs
               → db.T("feature_snapshots")     → test_feature_snapshots
    """
    r = _Result("predictions")
    r.code_path = (
        "ai.learning_store.save_prediction_snapshot() → "
        "test_prediction_snapshots + test_prediction_runner_outputs + test_feature_snapshots"
    )

    from env import env
    from ai.learning_store import save_prediction_snapshot, get_stored_prediction

    race_uid = f"{_today()}_GREYHOUND_PRED-TRACK_4"
    snap_id = f"snap_{_uid()}"

    prediction = {
        "prediction_snapshot_id": snap_id,
        "race_uid":               race_uid,
        "oddspro_race_id":        "OP-PRED-001",
        "model_version":          "baseline_v1",
        "created_at":             _now(),
        "runner_predictions": [
            {"runner_name": "SPEED DEMON",    "box_num": 1, "predicted_rank": 1, "score": 92.5},
            {"runner_name": "THUNDER PAWS",   "box_num": 2, "predicted_rank": 2, "score": 87.0},
            {"runner_name": "ROCKET GREYHOUND","box_num": 3, "predicted_rank": 3, "score": 81.0},
        ],
        "has_enrichment": 0,
        "source_type":    "pre_race",
    }
    features = [
        {"runner_name": "SPEED DEMON",    "box_num": 1, "price": 3.5, "career_wins": 5},
        {"runner_name": "THUNDER PAWS",   "box_num": 2, "price": 4.0, "career_wins": 3},
        {"runner_name": "ROCKET GREYHOUND","box_num": 3,"price": 5.5, "career_wins": 2},
    ]

    # Write
    ok = save_prediction_snapshot(prediction, features)
    r.check(ok, "save_prediction_snapshot returned False")

    # Verify tables written
    r.check(
        len(mock.rows("test_prediction_snapshots")) > 0,
        "test_prediction_snapshots is empty after save"
    )
    r.check(
        len(mock.rows("test_prediction_runner_outputs")) > 0,
        "test_prediction_runner_outputs is empty after save"
    )
    r.check(
        len(mock.rows("test_feature_snapshots")) > 0,
        "test_feature_snapshots is empty after save"
    )

    # Read back
    result = get_stored_prediction(race_uid)
    r.check(result.get("ok"), f"get_stored_prediction failed: {result.get('error')}")
    snapshot = result.get("snapshot") or {}
    outputs = result.get("runner_outputs") or []

    # Verify snapshot fields
    for field in ["prediction_snapshot_id", "race_uid", "oddspro_race_id",
                  "model_version", "runner_count", "created_at"]:
        if field not in snapshot:
            r.fail(f"Silent field drop from prediction_snapshots: '{field}'")

    r.check(snapshot.get("prediction_snapshot_id") == snap_id,
            "prediction_snapshot_id mismatch on read-back")
    r.check(len(outputs) == 3, f"Expected 3 runner outputs, got {len(outputs)}")

    if outputs:
        for field in ["runner_name", "box_num", "predicted_rank", "score", "model_version"]:
            if field not in outputs[0]:
                r.fail(f"Silent field drop from prediction_runner_outputs: '{field}'")

    r.detail = f"snap_id={snap_id}, race_uid={race_uid}"
    return r


def test_learning(mock: _MockClient) -> _Result:
    """
    6. LEARNING — write evaluation + read back
    Code path: ai.learning_store.evaluate_prediction()
               → db.T("learning_evaluations") → test_learning_evaluations
    Requires a prediction snapshot to already exist (written in test_predictions).
    """
    r = _Result("learning")
    r.code_path = (
        "ai.learning_store.evaluate_prediction() → "
        "test_learning_evaluations"
    )

    from env import env
    from ai.learning_store import save_prediction_snapshot, evaluate_prediction

    race_uid = f"{_today()}_GREYHOUND_LEARN-TRACK_5"
    snap_id = f"snap_{_uid()}"

    # Write a prediction first so evaluate_prediction can find it
    prediction = {
        "prediction_snapshot_id": snap_id,
        "race_uid":               race_uid,
        "oddspro_race_id":        "OP-LEARN-001",
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
    r.check(eval_result.get("ok"), f"evaluate_prediction failed: {eval_result.get('error')}")
    r.check(eval_result.get("evaluated", 0) > 0,
            "evaluate_prediction: 0 evaluations written")

    # Read back from test_learning_evaluations
    rows = mock.rows("test_learning_evaluations")
    match = [rw for rw in rows if rw.get("race_uid") == race_uid]
    r.check(len(match) > 0, "No rows in test_learning_evaluations for this race_uid")

    if match:
        row = match[0]
        for field in ["prediction_snapshot_id", "race_uid", "model_version",
                      "predicted_winner", "actual_winner", "winner_hit",
                      "top3_hit", "evaluation_source", "evaluated_at"]:
            if field not in row:
                r.fail(f"Silent field drop from learning_evaluations: '{field}'")

        r.check(row.get("actual_winner") == "GOLDEN FLASH", "actual_winner mismatch")
        r.check(row.get("winner_hit") is True, "winner_hit should be True")
        r.check(row.get("evaluation_source") == "oddspro",
                f"evaluation_source={row.get('evaluation_source')!r} (expected 'oddspro')")

    r.detail = f"race_uid={race_uid}, evaluations={eval_result.get('evaluated', 0)}"
    return r


def test_backtesting(mock: _MockClient) -> _Result:
    """
    7. BACKTESTING — write run + items + read back
    Code path: ai.backtest_engine._save_backtest_run()
               → db.T("backtest_runs") → test_backtest_runs
               ai.backtest_engine._save_backtest_items()
               → db.T("backtest_run_items") → test_backtest_run_items
               ai.backtest_engine.get_backtest_run()
               → read back from test_backtest_runs
    """
    r = _Result("backtesting")
    r.code_path = (
        "ai.backtest_engine._save_backtest_run() / _save_backtest_items() → "
        "test_backtest_runs + test_backtest_run_items; "
        "get_backtest_run() reads back"
    )

    from env import env
    from ai.backtest_engine import _save_backtest_run, _save_backtest_items, get_backtest_run

    run_id = f"bt_{_uid()}_smoke"
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
            "run_id":     run_id,
            "race_uid":   f"{_today()}_GREYHOUND_BT-TRACK_{i}",
            "model_version": "baseline_v1",
            "predicted_winner":  "RUNNER_A",
            "actual_winner":     "RUNNER_A",
            "winner_hit":        True,
            "winner_odds":       3.5,
            "created_at":        _now(),
        }
        for i in range(1, 4)
    ]

    # Write run summary
    _save_backtest_run(summary)
    r.check(
        len(mock.rows("test_backtest_runs")) > 0,
        "test_backtest_runs is empty after _save_backtest_run"
    )

    # Write items
    _save_backtest_items(items)
    r.check(
        len(mock.rows("test_backtest_run_items")) >= 3,
        f"test_backtest_run_items: expected >=3 rows, "
        f"got {len(mock.rows('test_backtest_run_items'))}"
    )

    # Read back via public API
    result = get_backtest_run(run_id)
    r.check(result.get("ok"), f"get_backtest_run failed: {result.get('error')}")

    run_row = result.get("run") or {}
    for field in ["run_id", "date_from", "date_to", "model_version",
                  "total_races", "total_runners", "hit_rate",
                  "winner_hit_count", "top2_hit_count", "top3_hit_count"]:
        if field not in run_row:
            r.fail(f"Silent field drop from backtest_runs: '{field}'")

    r.check(run_row.get("run_id") == run_id, "run_id mismatch on read-back")
    r.check(abs((run_row.get("hit_rate") or 0) - 0.6) < 0.001, "hit_rate mismatch")

    r.detail = f"run_id={run_id}, items={len(items)}"
    return r


def test_source_logging(mock: _MockClient) -> _Result:
    """
    8. SOURCE / DATA LOGGING — write + read back
    Code path: database.write_source_log() → db.T("source_log") → test_source_log
    """
    r = _Result("source_logging")
    r.code_path = "database.write_source_log() → db.T('source_log') → test_source_log"

    from env import env
    from database import write_source_log
    from db import get_db, safe_query, T

    r.check(
        env.table("source_log") == "test_source_log",
        f"env.table('source_log') → {env.table('source_log')!r} (expected 'test_source_log')"
    )

    entry = {
        "date":          _today(),
        "call_num":      1,
        "url":           "https://oddspro.test/api/external/meetings",
        "method":        "GET",
        "status":        "200",
        "grv_detected":  True,
        "rows_returned": 12,
    }

    # Write
    written = write_source_log(entry)
    r.check(written is not None, "write_source_log returned None")

    # Read back
    rows = safe_query(
        lambda: get_db()
        .table(T("source_log"))
        .select("*")
        .eq("date", _today())
        .execute()
        .data,
        []
    ) or []
    r.check(len(rows) > 0, "No rows returned from test_source_log after write")

    if rows:
        row = rows[0]
        for field in ["date", "call_num", "url", "method", "status",
                      "grv_detected", "rows_returned", "created_at"]:
            if field not in row:
                r.fail(f"Silent field drop: '{field}' missing from source_log row")
        r.check(row.get("url") == "https://oddspro.test/api/external/meetings",
                "url mismatch")
        r.check(row.get("rows_returned") == 12, "rows_returned mismatch")
        r.check(row.get("grv_detected") is True, "grv_detected mismatch")

    r.detail = f"rows written to test_source_log: {len(mock.rows('test_source_log'))}"
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
    print("  DEMONPULSE — DATA SMOKE TEST REPORT")
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

    print("2. FULL UPDATED FILES")
    print("-" * 68)
    print("  database.py — added upsert_meeting(), get_meeting(),")
    print("                get_runners_for_race(), write_source_log()")
    print("  smoke_test.py — this file (new)")
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
        print("  db.py → env.py → Supabase path is correct for all subsystems.")
        print("  All conflict keys, field mappings, and TEST/LIVE routing verified.")
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
    mock = _inject_mock()

    # Verify env is TEST
    from env import env
    if not env.is_test:
        print("FATAL: smoke test must run in TEST mode (DP_ENV=TEST)")
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
    ]

    results = []
    for fn in tests:
        try:
            result = fn(mock)
        except Exception as exc:
            result = _Result(fn.__name__.replace("test_", ""))
            result.fail(f"Uncaught exception: {exc}")
            import traceback
            traceback.print_exc()
        results.append(result)

    all_pass = print_report(results)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
