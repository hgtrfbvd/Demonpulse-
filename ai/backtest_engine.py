"""
ai/backtest_engine.py - DemonPulse Backtest Engine
====================================================
Replays historical races safely using only information that would have
existed at prediction time.

Critical contamination rules:
  - NO future leakage: feature inputs use only pre-result race/runner/odds data
  - Official results (results_log) are used ONLY for evaluation, never as inputs
  - FormFav provisional data is never used as evaluation source
  - Historical feature snapshots (when available) take priority over rebuilding
    from current mutable tables — avoids feature contamination

Backtest approach:
  1. Check feature_snapshots table for pre-stored historical feature vectors
  2. If snapshots exist, use them directly (contamination-safe)
  3. If not, fall back to rebuilding features from today_races / today_runners
  4. Run the specified model scorer (baseline_v1 or v2_feature_engine)
  5. Compare against results_log (official confirmed results only)
  6. Aggregate metrics and save run summary

Model comparison:
  - Pass compare_models=True to run both baseline_v1 and v2_feature_engine
    over the same date range and produce a side-by-side comparison

Backtest output:
  - total_races, total_runners, hit_rate, top2_rate, top3_rate
  - avg_winner_odds for winning picks
  - model_version used, run timestamp
  - run_id for lineage
  - optional model_comparison dict when compare_models=True
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
    compare_models: bool = False,
) -> dict[str, Any]:
    """
    Backtest all races for a single date.

    Args:
        target_date   : ISO date string (YYYY-MM-DD)
        code_filter   : optional race code filter (e.g. 'GREYHOUND')
        track_filter  : optional track name filter (substring match)
        model_version : model version label for the run record
        compare_models: when True, also runs v2_feature_engine and compares

    Returns:
        Backtest run summary dict
    """
    return backtest_date_range(
        date_from=target_date,
        date_to=target_date,
        code_filter=code_filter,
        track_filter=track_filter,
        model_version=model_version,
        compare_models=compare_models,
    )


def backtest_date_range(
    date_from: str,
    date_to: str,
    code_filter: str | None = None,
    track_filter: str | None = None,
    model_version: str = "baseline_v1",
    compare_models: bool = False,
) -> dict[str, Any]:
    """
    Backtest a date range of races.

    Uses stored historical feature snapshots when available to avoid
    contamination from current mutable tables.

    Args:
        date_from     : start date ISO string (inclusive)
        date_to       : end date ISO string (inclusive)
        code_filter   : optional race code filter
        track_filter  : optional track name filter (substring match)
        model_version : model version label for the run record
        compare_models: when True, also runs v2_feature_engine and adds
                        model_comparison to the output

    Returns:
        Full backtest run summary dict with hit rates and run_id for lineage.
    """
    from datetime import date as _date

    # Guard: reject future dates
    today_str = _date.today().isoformat()
    if date_from > today_str or date_to > today_str:
        return {
            "ok": False,
            "error": "Backtest cannot use future dates (no leakage rule)",
            "date_from": date_from,
            "date_to": date_to,
            "today": today_str,
        }

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

    summary: dict[str, Any] = {
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

    # Optional model comparison
    if compare_models and model_version != "v2_feature_engine":
        v2_summary = backtest_date_range(
            date_from=date_from,
            date_to=date_to,
            code_filter=code_filter,
            track_filter=track_filter,
            model_version="v2_feature_engine",
            compare_models=False,
        )
        summary["model_comparison"] = {
            model_version: {
                "hit_rate": hit_rate,
                "top2_rate": top2_rate,
                "top3_rate": top3_rate,
                "avg_winner_odds": avg_winner_odds,
            },
            "v2_feature_engine": {
                "hit_rate": v2_summary.get("hit_rate"),
                "top2_rate": v2_summary.get("top2_rate"),
                "top3_rate": v2_summary.get("top3_rate"),
                "avg_winner_odds": v2_summary.get("avg_winner_odds"),
                "run_id": v2_summary.get("run_id"),
            },
        }

    _save_backtest_run(summary)
    _save_backtest_items(run_items)

    log.info(
        f"backtest: run {run_id} complete — {races_tested} races "
        f"hit_rate={hit_rate:.1%} top2={top2_rate:.1%} top3={top3_rate:.1%} "
        f"model={model_version}"
    )
    return summary


def compare_models(
    start_date: str,
    end_date: str,
    code_filter: str | None = None,
    track_filter: str | None = None,
) -> dict[str, Any]:
    """
    Run a three-way model comparison over a date range.

    Runs all three model variants over the same historical data:
      1. baseline_v1          — odds-ranked, no feature signals
      2. v2_feature_engine    — full feature signals, no enrichment
      3. v2_with_enrichment   — full feature signals + FormFav enrichment

    Returns a performance comparison table with win_rate, top3_rate, and
    ROI estimate per model.

    Future dates are rejected (no leakage rule).

    Args:
        start_date  : start date ISO string (YYYY-MM-DD), inclusive
        end_date    : end date ISO string (YYYY-MM-DD), inclusive
        code_filter : optional race code filter ('GREYHOUND', 'HARNESS', 'GALLOPS')
        track_filter: optional track name filter (substring match)

    Returns:
        dict with:
          - ok
          - date_from / date_to
          - models: dict keyed by model version with performance stats
          - best_model: model version with highest win_rate
    """
    from datetime import date as _date

    # Guard: reject future dates
    today_str = _date.today().isoformat()
    if start_date > today_str or end_date > today_str:
        return {
            "ok": False,
            "error": "Backtest cannot use future dates (no leakage rule)",
            "date_from": start_date,
            "date_to": end_date,
            "today": today_str,
        }

    models_to_run = [
        "baseline_v1",
        "v2_feature_engine",
        "v2_with_enrichment",
    ]

    model_results: dict[str, Any] = {}

    for model_version in models_to_run:
        result = backtest_date_range(
            date_from=start_date,
            date_to=end_date,
            code_filter=code_filter,
            track_filter=track_filter,
            model_version=model_version,
            compare_models=False,
        )
        if result.get("ok"):
            model_results[model_version] = {
                "win_rate": result.get("hit_rate", 0.0),
                "top3_rate": result.get("top3_rate", 0.0),
                "total_races": result.get("total_races", 0),
                "winner_hit_count": result.get("winner_hit_count", 0),
                "avg_winner_odds": result.get("avg_winner_odds"),
                "run_id": result.get("run_id"),
                # ROI estimate: avg_winner_odds * win_rate - 1 (unit stake)
                "roi_estimate": (
                    round(
                        result.get("avg_winner_odds", 0.0) * result.get("hit_rate", 0.0) - 1.0,
                        4,
                    )
                    if result.get("avg_winner_odds") and result.get("hit_rate")
                    else None
                ),
            }
        else:
            model_results[model_version] = {
                "ok": False,
                "error": result.get("error", "Unknown error"),
            }

    # Determine best model by win_rate
    best_model = max(
        (m for m in model_results if model_results[m].get("win_rate") is not None),
        key=lambda m: model_results[m].get("win_rate", 0.0),
        default="baseline_v1",
    )

    log.info(
        f"backtest compare_models: {start_date}—{end_date} "
        f"best={best_model} "
        + " ".join(
            f"{m}={model_results[m].get('win_rate', 0.0):.1%}"
            for m in models_to_run
            if model_results[m].get("win_rate") is not None
        )
    )

    return {
        "ok": True,
        "date_from": start_date,
        "date_to": end_date,
        "code_filter": code_filter or "",
        "track_filter": track_filter or "",
        "models": model_results,
        "best_model": best_model,
    }


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
      1. Try to use stored historical feature snapshot (contamination-safe)
      2. If not found, rebuild features from today_runners (fallback)
      3. Score with the specified model
      4. Fetch official result (evaluation only — never as input)
      5. Compute hit metrics

    Returns None if the race lacks either runners or a confirmed result.
    """
    race_uid = race.get("race_uid") or ""
    race_id = race.get("id")
    race_date_str = str(race.get("date") or "")
    track = race.get("track") or ""
    code = race.get("code") or ""

    # -- Step 1: try stored historical feature snapshot --
    stored_features = _load_stored_features(race_uid)
    used_snapshot = False

    if stored_features:
        features = stored_features
        used_snapshot = True
    else:
        # -- Step 2: rebuild features from current storage (fallback) --
        runners = _fetch_runners(race_id)
        if not runners:
            return None

        from ai.feature_builder import build_race_features
        features = build_race_features(race, runners)
        if not features:
            return None

    # -- Step 3: score with the requested model --
    if model_version == "v2_feature_engine":
        from ai.predictor import _v2_feature_score
        scored = _v2_feature_score(features)
    else:
        from ai.predictor import _baseline_score
        scored = _baseline_score(features)

    if not scored:
        return None

    predicted_winner_name = scored[0].get("runner_name") or ""
    predicted_winner_box = scored[0].get("box_num")
    predicted_score = scored[0].get("score", 0.0)
    top2_names = {s.get("runner_name") for s in scored[:2] if s.get("runner_name")}
    top3_names = {s.get("runner_name") for s in scored[:3] if s.get("runner_name")}

    # -- Step 4: official result — evaluation ONLY, never a feature input --
    result = _fetch_result_for_race(race)
    if not result:
        return None

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
        "model_version": model_version,
        "used_stored_snapshot": used_snapshot,
        "created_at": _now(),
    }


# ---------------------------------------------------------------------------
# INTERNAL — DATA ACCESS (read-only, authoritative storage)
# ---------------------------------------------------------------------------

def _load_stored_features(race_uid: str) -> list[dict[str, Any]]:
    """
    Load the most recent stored feature snapshot for a race.
    Returns empty list if none found.
    Stored snapshots are prefered over live table rebuilds in backtest.
    """
    try:
        import json
        from db import get_db, safe_query, T

        rows = safe_query(
            lambda: get_db()
            .table(T("feature_snapshots"))
            .select("features")
            .eq("race_uid", race_uid)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data,
            [],
        ) or []
        if not rows or not rows[0].get("features"):
            return []
        raw = rows[0]["features"]
        if isinstance(raw, str):
            return json.loads(raw) or []
        if isinstance(raw, list):
            return raw
        return []
    except Exception as e:
        log.debug(f"backtest: _load_stored_features failed for {race_uid}: {e}")
        return []


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
