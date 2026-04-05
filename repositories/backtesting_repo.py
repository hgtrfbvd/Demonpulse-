"""
repositories/backtesting_repo.py — Backtest data access
========================================================
Covers: backtest_runs, backtest_run_items.

Backtests operate on historical snapshots only — they must never read
from or write to live race/runner tables.

Identity rule: run_id on backtest_run_items links back to backtest_runs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import TABLE_BACKTEST_RUNS, TABLE_BACKTEST_ITEMS

log = logging.getLogger(__name__)


class BacktestingRepo:
    """Repository for backtest_runs and backtest_run_items tables."""

    # ── BACKTEST RUN ─────────────────────────────────────────────

    @staticmethod
    def create_run(run: dict[str, Any]) -> Optional[dict]:
        """
        Insert a new backtest run record.

        Args:
            run: Must include run_id, model_version.

        Returns:
            Saved record dict, or None on failure.
        """
        if not run.get("run_id"):
            log.warning("BacktestingRepo: run missing run_id")
            return None

        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_BACKTEST_RUNS))
                .insert(BacktestingRepo._build_run_payload(run))
                .execute()
                .data,
            default=None,
            context="BacktestingRepo.create_run",
        )
        return (result[0] if isinstance(result, list) else result) if result else None

    @staticmethod
    def update_run(run_id: str, **fields) -> bool:
        """Update fields on a backtest run (e.g. totals after completion)."""
        fields["updated_at"] = _now()
        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_BACKTEST_RUNS))
                .update(fields)
                .eq("run_id", run_id)
                .execute()
                .data,
            default=None,
            context="BacktestingRepo.update_run",
        )
        return bool(result)

    @staticmethod
    def get_run(run_id: str) -> Optional[dict]:
        """Fetch a backtest run by run_id."""
        rows = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_BACKTEST_RUNS))
                .select("*")
                .eq("run_id", run_id)
                .limit(1)
                .execute()
                .data,
            default=[],
            context="BacktestingRepo.get_run",
        ) or []
        return rows[0] if rows else None

    @staticmethod
    def list_runs(limit: int = 50, model_version: Optional[str] = None) -> list[dict]:
        """List recent backtest runs."""
        q = (
            get_client()
                .table(resolve_table(TABLE_BACKTEST_RUNS))
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
        )
        if model_version:
            q = q.eq("model_version", model_version)
        return safe_execute(
            lambda: q.execute().data,
            default=[],
            context="BacktestingRepo.list_runs",
        ) or []

    # ── BACKTEST RUN ITEMS ────────────────────────────────────────

    @staticmethod
    def save_item(item: dict[str, Any]) -> Optional[dict]:
        """
        Insert a single backtest run item (one race within a run).

        Args:
            item: Must include run_id and race_uid.
        """
        if not item.get("run_id") or not item.get("race_uid"):
            log.warning("BacktestingRepo: item missing run_id or race_uid")
            return None

        result = safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_BACKTEST_ITEMS))
                .insert(BacktestingRepo._build_item_payload(item))
                .execute()
                .data,
            default=None,
            context="BacktestingRepo.save_item",
        )
        return (result[0] if isinstance(result, list) else result) if result else None

    @staticmethod
    def save_items(items: list[dict[str, Any]]) -> int:
        """Bulk insert backtest items. Returns count saved."""
        return sum(1 for i in items if BacktestingRepo.save_item(i))

    @staticmethod
    def get_items(run_id: str) -> list[dict]:
        """Fetch all items for a backtest run."""
        return safe_execute(
            lambda: get_client()
                .table(resolve_table(TABLE_BACKTEST_ITEMS))
                .select("*")
                .eq("run_id", run_id)
                .order("created_at")
                .execute()
                .data,
            default=[],
            context="BacktestingRepo.get_items",
        ) or []

    # ── INTERNAL ─────────────────────────────────────────────────

    @staticmethod
    def _build_run_payload(run: dict[str, Any]) -> dict:
        return {
            "run_id":           str(run["run_id"]),
            "model_version":    run.get("model_version", "baseline_v1"),
            "code_filter":      run["code_filter"] if "code_filter" in run else run.get("race_code", ""),
            "track_filter":     run.get("track_filter", ""),
            "date_from":        run.get("date_from"),
            "date_to":          run.get("date_to"),
            "total_races":      int(run.get("total_races") or 0),
            "total_runners":    int(run.get("total_runners") or 0),
            "winner_hit_count": int(run["winner_hit_count"] if "winner_hit_count" in run else run.get("winner_hits", 0)),
            "top2_hit_count":   int(run["top2_hit_count"] if "top2_hit_count" in run else run.get("top2_hits", 0)),
            "top3_hit_count":   int(run["top3_hit_count"] if "top3_hit_count" in run else run.get("top3_hits", 0)),
            "hit_rate":         _to_numeric(run["hit_rate"] if "hit_rate" in run else run.get("winner_accuracy")),
            "top2_rate":        _to_numeric(run.get("top2_rate")),
            "top3_rate":        _to_numeric(run.get("top3_rate")),
            "avg_winner_odds":  _to_numeric(run.get("avg_winner_odds")),
            "created_at":       run.get("created_at", _now()),
            "updated_at":       _now(),
        }

    @staticmethod
    def _build_item_payload(item: dict[str, Any]) -> dict:
        return {
            "run_id":               str(item["run_id"]),
            "race_uid":             str(item["race_uid"]),
            "race_date":            item.get("race_date"),
            "track":                item.get("track", ""),
            "code":                 item.get("code", "GREYHOUND"),
            "runner_count":         int(item.get("runner_count") or 0),
            "predicted_winner":     item.get("predicted_winner", ""),
            "actual_winner":        item.get("actual_winner", ""),
            "winner_hit":           bool(item.get("winner_hit", False)),
            "top2_hit":             bool(item.get("top2_hit", False)),
            "top3_hit":             bool(item.get("top3_hit", False)),
            "score":                _to_numeric(item.get("score")),
            "winner_odds":          _to_numeric(item.get("winner_odds")),
            "model_version":        item.get("model_version", "baseline_v1"),
            "used_stored_snapshot": bool(item.get("used_stored_snapshot", False)),
            "created_at":           item.get("created_at", _now()),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_numeric(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
