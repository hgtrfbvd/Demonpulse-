"""
ai/backtest_engine.py - DemonPulse Backtest Engine
====================================================
Replays historical races safely using only information that would have
existed at prediction time.

Critical contamination rules:
  - NO future leakage: feature inputs use only pre-result race/runner/odds data
  - Official results (results_log) are used ONLY for evaluation, never as inputs
  - FormFav provisional data is never used as evaluation source
  - Race snapshots (runners, odds) are the sole feature inputs

Backtest approach:
  1. Query today_races for the date range (pre-result authoritative snapshots)
  2. For each race, fetch runners from today_runners
  3. Build features using feature_builder (same pipeline as live predictions)
  4. Run baseline scorer to generate predicted rankings
  5. Compare against results_log (official confirmed results only)
  6. Aggregate metrics and save run summary

Backtest output:
  - total_races, total_runners, hit_rate, top2_rate, top3_rate
  - avg_winner_odds for winning picks
  - model_version used, run timestamp
  - run_id for lineage
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


def backtest_date(
    target_date: str,
    code_filter: str | None = None,
    track_filter: str | None = None,
    model_version: str = "baseline_v1",
) -> dict[str, Any]:
    """
    Backtest all races for a single date.

    Args:
        target_date: ISO date string (YYYY-MM-DD)
        code_filter: optional race code filter (e.g. 'GREYHOUND')
        track_filter: optional track name filter (substring match)
        model_version: model version label for the run record

    Returns:
        Backtest run summary dict
    """
    return backtest_date_range(
        date_from=target_date,
        date_to=target_date,
        code_filter=code_filter,
        track_filter=track_filter,
        model_version=model_version,
    )


def backtest_date_range(
    date_from: str,
    date_to: str,
    code_filter: str | None = None,
    track_filter: str | None = None,
    model_version: str = "baseline_v1",
) -> dict[str, Any]:
    """
    Backtest a date range of races.

    Args:
        date_from: start date ISO string (inclusive)
        date_to: end date ISO string (inclusive)
        code_filter: optional race code filter
        track_filter: optional track name filter (substring match)
        model_version: model version label for the run record

    Returns:
        Full backtest run summary dict with hit rates and run_id for lineage.
    """
    run_id = _make_run_id()
    started_at = _now()

    try:
        races = _fetch_races_for_range(date_from, date_to, code_filter, track_filter)
    except Exception as e:
        log.error(f"backtest: failed to fetch races {date_from}—{date_to}: {e}")
        return {
            "ok": False,
            "error": "Failed to fetch race data",
            "run_id": run_id,
            "date_from": date_from,
            "date_to": date_to,
        }

    empty_summary = {
        "ok": True,
        "run_id": run_id,
        "date_from": date_from,
        "date_to": date_to,
        "code_filter": code_filter or "",
        "track_filter": track_filter or "",
        "model_version": model_version,
        "total_races": 0,
        "total_runners": 0,
        "winner_hit_count": 0,
        "top2_hit_count": 0,
        "top3_hit_count": 0,
        "hit_rate": 0.0,
        "top2_rate": 0.0,
        "top3_rate": 0.0,
        "avg_winner_odds": None,
        "created_at": started_at,
    }

    if not races:
        _save_backtest_run(empty_summary)
        return empty_summary

    run_items: list[dict[str, Any]] = []
    total_runners = 0
    winner_hits = 0
    top2_hits = 0
    top3_hits = 0
    winning_odds_list: list[float] = []

    for race in races:
        race_uid = race.get("race_uid") or ""
        if not race_uid:
            continue

        item = _backtest_single_race(race, model_version)
        if item is None:
            continue

        run_items.append({**item, "run_id": run_id})
        total_runners += item.get("runner_count", 0)
        if item.get("winner_hit"):
            winner_hits += 1
            odds = item.get("winner_odds")
            if odds:
                winning_odds_list.append(float(odds))
        if item.get("top2_hit"):
            top2_hits += 1
        if item.get("top3_hit"):
            top3_hits += 1

    races_tested = len(run_items)
    hit_rate = round(winner_hits / races_tested, 4) if races_tested else 0.0
    top2_rate = round(top2_hits / races_tested, 4) if races_tested else 0.0
    top3_rate = round(top3_hits / races_tested, 4) if races_tested else 0.0
    avg_winner_odds = (
        round(sum(winning_odds_list) / len(winning_odds_list), 2)
        if winning_odds_list else None
    )

    summary = {
        "ok": True,
        "run_id": run_id,
        "date_from": date_from,
        "date_to": date_to,
        "code_filter": code_filter or "",
        "track_filter": track_filter or "",
        "model_version": model_version,
        "total_races": races_tested,
        "total_runners": total_runners,
        "winner_hit_count": winner_hits,
        "top2_hit_count": top2_hits,
        "top3_hit_count": top3_hits,
        "hit_rate": hit_rate,
        "top2_rate": top2_rate,
        "top3_rate": top3_rate,
        "avg_winner_odds": avg_winner_odds,
        "created_at": started_at,
    }

    _save_backtest_run(summary)
    _save_backtest_items(run_items)

    log.info(
        f"backtest: run {run_id} complete — {races_tested} races "
        f"hit_rate={hit_rate:.1%} top2={top2_rate:.1%} top3={top3_rate:.1%}"
    )
    return summary


def get_backtest_run(run_id: str) -> dict[str, Any]:
    """Retrieve a stored backtest run summary by run_id."""
    try:
        from db import get_db, safe_query, T

        rows = safe_query(
            lambda: get_db()
            .table(T("backtest_runs"))
            .select("*")
            .eq("run_id", run_id)
            .limit(1)
            .execute()
            .data,
            [],
        ) or []
        if not rows:
            return {"ok": False, "error": "Backtest run not found", "run_id": run_id}
        return {"ok": True, "run": rows[0]}
    except Exception as e:
        log.error(f"backtest: get_backtest_run failed for {run_id}: {e}")
        return {"ok": False, "error": "Could not retrieve backtest run", "run_id": run_id}


# ---------------------------------------------------------------------------
# INTERNAL — SINGLE RACE BACKTEST
# ---------------------------------------------------------------------------

def _backtest_single_race(
    race: dict[str, Any],
    model_version: str,
) -> dict[str, Any] | None:
    """
    Backtest one race:
      1. Fetch runners (pre-result snapshot — no result data used)
      2. Build features and score with baseline model
      3. Fetch official result (evaluation only — never as input)
      4. Compute hit metrics

    Returns None if the race lacks either runners or a confirmed result.
    """
    race_uid = race.get("race_uid") or ""
    race_id = race.get("id")
    race_date_str = str(race.get("date") or "")
    track = race.get("track") or ""
    code = race.get("code") or ""

    runners = _fetch_runners(race_id)
    if not runners:
        return None

    from ai.feature_builder import build_race_features
    from ai.predictor import _baseline_score

    features = build_race_features(race, runners)
    if not features:
        return None

    scored = _baseline_score(features)
    if not scored:
        return None

    predicted_winner_name = scored[0].get("runner_name") or ""
    predicted_winner_box = scored[0].get("box_num")
    predicted_score = scored[0].get("score", 0.0)
    top2_names = {s.get("runner_name") for s in scored[:2] if s.get("runner_name")}
    top3_names = {s.get("runner_name") for s in scored[:3] if s.get("runner_name")}

    # Official result — evaluation ONLY, never used as a feature input
    result = _fetch_result_for_race(race)
    if not result:
        return None  # no confirmed result yet — skip this race

    actual_winner = result.get("winner") or ""
    winner_box = result.get("winner_box")
    win_price = result.get("win_price")

    winner_hit = bool(
        (predicted_winner_name and
         predicted_winner_name.upper() == actual_winner.upper())
        or (predicted_winner_box is not None and
            predicted_winner_box == winner_box)
    )
    top2_hit = bool(
        actual_winner and
        actual_winner.upper() in {n.upper() for n in top2_names}
    )
    top3_hit = bool(
        actual_winner and
        actual_winner.upper() in {n.upper() for n in top3_names}
    )

    return {
        "race_uid": race_uid,
        "race_date": race_date_str,
        "track": track,
        "code": code,
        "runner_count": len(features),
        "predicted_winner": predicted_winner_name,
        "actual_winner": actual_winner,
        "winner_hit": winner_hit,
        "top2_hit": top2_hit,
        "top3_hit": top3_hit,
        "score": predicted_score,
        "winner_odds": _safe_float(win_price),
        "created_at": _now(),
    }


# ---------------------------------------------------------------------------
# INTERNAL — DATA ACCESS (read-only, authoritative storage)
# ---------------------------------------------------------------------------

def _fetch_races_for_range(
    date_from: str,
    date_to: str,
    code_filter: str | None,
    track_filter: str | None,
) -> list[dict[str, Any]]:
    """Fetch non-blocked races within a date range from authoritative storage."""
    from db import get_db, safe_query, T

    q = (
        get_db()
        .table(T("today_races"))
        .select("*")
        .gte("date", date_from)
        .lte("date", date_to)
        .not_.eq("status", "blocked")
        .order("date")
        .order("jump_time")
    )
    if code_filter:
        q = q.eq("code", code_filter.upper())
    if track_filter:
        q = q.ilike("track", f"%{track_filter}%")

    return safe_query(lambda: q.execute().data, []) or []


def _fetch_runners(race_id: str) -> list[dict[str, Any]]:
    """Fetch runner records for a race from authoritative storage."""
    from db import get_db, safe_query, T

    return safe_query(
        lambda: get_db()
        .table(T("today_runners"))
        .select("*")
        .eq("race_id", race_id)
        .execute()
        .data,
        [],
    ) or []


def _fetch_result_for_race(race: dict[str, Any]) -> dict[str, Any] | None:
    """
    Fetch the official confirmed result for a race from results_log.

    This is ONLY used for evaluation after the fact.
    It is NEVER used as a feature input in backtest or live prediction.
    Source must be OddsPro-confirmed.
    """
    from db import get_db, safe_query, T

    rows = safe_query(
        lambda: get_db()
        .table(T("results_log"))
        .select("*")
        .eq("date", str(race.get("date") or ""))
        .eq("track", race.get("track") or "")
        .eq("race_num", race.get("race_num") or 0)
        .eq("code", race.get("code") or "GREYHOUND")
        .limit(1)
        .execute()
        .data,
        [],
    ) or []
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# INTERNAL — STORAGE
# ---------------------------------------------------------------------------

def _save_backtest_run(summary: dict[str, Any]) -> None:
    """Persist backtest run summary to backtest_runs table."""
    try:
        from db import get_db, safe_query, T

        row = {
            "run_id": summary["run_id"],
            "date_from": summary["date_from"],
            "date_to": summary["date_to"],
            "code_filter": summary.get("code_filter") or "",
            "track_filter": summary.get("track_filter") or "",
            "model_version": summary.get("model_version") or "baseline_v1",
            "total_races": summary.get("total_races") or 0,
            "total_runners": summary.get("total_runners") or 0,
            "winner_hit_count": summary.get("winner_hit_count") or 0,
            "top2_hit_count": summary.get("top2_hit_count") or 0,
            "top3_hit_count": summary.get("top3_hit_count") or 0,
            "hit_rate": summary.get("hit_rate") or 0.0,
            "top2_rate": summary.get("top2_rate") or 0.0,
            "top3_rate": summary.get("top3_rate") or 0.0,
            "avg_winner_odds": summary.get("avg_winner_odds"),
            "created_at": summary.get("created_at") or _now(),
        }
        safe_query(
            lambda: get_db()
            .table(T("backtest_runs"))
            .upsert(row, on_conflict="run_id")
            .execute()
        )
        log.info(f"backtest: saved run summary {summary['run_id']}")
    except Exception as e:
        log.error(f"backtest: _save_backtest_run failed: {e}")


def _save_backtest_items(items: list[dict[str, Any]]) -> None:
    """Persist per-race backtest results to backtest_run_items table."""
    if not items:
        return
    try:
        from db import get_db, safe_query, T

        safe_query(
            lambda: get_db()
            .table(T("backtest_run_items"))
            .insert(items)
            .execute()
        )
    except Exception as e:
        log.error(f"backtest: _save_backtest_items failed: {e}")


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _make_run_id() -> str:
    short_uid = str(uuid.uuid4()).replace("-", "")[:12]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"bt_{ts}_{short_uid}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default
