"""
simulation/filter_engine.py
The modular filter engine. Loads filter configs, instantiates filter classes,
and runs the full pipeline: HARD → CORE → RISK → VALUE → OUTPUT.

Filters can be:
  - Added:   drop new config entry + Python class, no core engine changes
  - Removed: set enabled: false, or delete config entry
  - Adjusted: change threshold/weight in JSON or DB, no code changes
  - Toggled:  enabled: true/false

Config source priority:
  1. Dict passed directly (e.g. from DB row)
  2. JSON file path
  3. Built-in defaults (filter_config.json next to this module)
"""
from __future__ import annotations
import importlib
import json
import logging
import os
from typing import Any

from .models import (
    FilterConfig, FilterContext, FilterDecision, FilterMode,
    RunnerProfile, AggregatedResult, RaceMeta, Decision, RaceCode,
)
from .filters.base_filter import BaseFilter
from .filters.output_filters import OutputDecisionFilter

log = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "filter_config.json")


class FilterEngine:
    """
    Orchestrates the full filter pipeline.

    Usage:
        engine = FilterEngine()                    # loads default config
        engine = FilterEngine(config_path="…")     # custom JSON
        engine = FilterEngine(config_dicts=[…])    # from DB

        decision = engine.run(aggregated, race_meta, runners)
    """

    # Base confidence before soft filters adjust it
    _BASE_CONFIDENCE = 0.50

    def __init__(
        self,
        config_path: str | None = None,
        config_dicts: list[dict] | None = None,
    ):
        """
        Load filter configs and instantiate filter objects.
        config_dicts takes priority over config_path.
        """
        raw_configs = self._load_configs(config_path, config_dicts)
        self._filters: list[BaseFilter] = []
        self._output_filter: OutputDecisionFilter | None = None

        for raw in raw_configs:
            if not raw.get("enabled", True):
                continue
            if raw.get("filter_id", "").startswith("OP"):
                # Output filter loaded separately
                f = self._instantiate(raw)
                if isinstance(f, OutputDecisionFilter):
                    self._output_filter = f
                continue
            f = self._instantiate(raw)
            if f:
                self._filters.append(f)

        # Fallback output filter
        if self._output_filter is None:
            self._output_filter = OutputDecisionFilter(FilterConfig(
                filter_id="OP001", name="Output Decision", enabled=True,
                mode=FilterMode.SOFT, weight=1.0,
                threshold={"bet_threshold":0.65,"small_bet_threshold":0.50,
                           "save_bet_threshold":0.42,"caution_threshold":0.35},
                applies_to=["all"], description="Default output filter",
                filter_class="simulation.filters.output_filters.OutputDecisionFilter",
            ))

        log.info(
            f"FilterEngine: loaded {len(self._filters)} pipeline filters + "
            f"{'1 output filter' if self._output_filter else 'NO output filter (fallback active)'} "
            f"= {len(self._filters) + (1 if self._output_filter else 0)} total"
        )

    # ─────────────────────────────────────────────────────────────
    # MAIN PIPELINE
    # ─────────────────────────────────────────────────────────────

    def run(
        self,
        aggregated: AggregatedResult,
        race_meta: RaceMeta,
        runners: list[RunnerProfile],
    ) -> FilterDecision:
        """
        Execute the full filter pipeline.

        Returns a FilterDecision with:
          - final Decision (BET / SMALL_BET / … / PASS)
          - confidence_score
          - all triggered and passed filters
          - reasoning chain
        """
        active    = [r for r in runners if not r.scratched]
        top_runner = None
        if aggregated.top_runner:
            top_runner = next(
                (r for r in active if r.runner_id == aggregated.top_runner.runner_id),
                None
            )

        ctx = FilterContext(
            aggregated=aggregated,
            race_meta=race_meta,
            runners=active,
            top_runner=top_runner,
        )

        triggered_results = []
        passed_results    = []
        hard_blocked_by   = None
        reasoning         = []
        confidence        = self._BASE_CONFIDENCE

        # ── STEP 1: HARD BLOCK FILTERS ───────────────────────────
        for f in self._hard_filters(race_meta.race_code):
            try:
                result = f.evaluate(ctx)
            except Exception as e:
                log.error(f"Filter {f.filter_id} raised: {e}")
                continue

            if result.triggered:
                hard_blocked_by = result
                triggered_results.append(result)
                reasoning.append(f"BLOCK [{f.filter_id}]: {result.reason}")
                return FilterDecision(
                    decision=Decision.PASS,
                    confidence_score=0.0,
                    triggered_filters=triggered_results,
                    passed_filters=passed_results,
                    hard_blocked_by=hard_blocked_by,
                    reasoning=reasoning,
                    top_runner_id=aggregated.top_runner.runner_id if aggregated.top_runner else None,
                    top_runner_name=aggregated.top_runner.name if aggregated.top_runner else None,
                )
            else:
                passed_results.append(result)

        # ── STEP 2: SOFT + INFORMATIONAL FILTERS ─────────────────
        # W-06: Track positive delta accumulation to prevent over-inflation.
        # Positive soft filter deltas are capped at +0.35 total above base.
        # Negative deltas are uncapped — risk filters can always reduce.
        _MAX_POSITIVE_DELTA = 0.35
        _positive_accumulated = 0.0

        for f in self._soft_filters(race_meta.race_code):
            try:
                result = f.evaluate(ctx)
            except Exception as e:
                log.error(f"Filter {f.filter_id} raised: {e}")
                continue

            if result.triggered:
                triggered_results.append(result)
                if f.mode == FilterMode.SOFT:
                    weighted_delta = result.confidence_delta * f.weight
                    # Apply positive cap: stop accumulating positive boosts after limit
                    if weighted_delta > 0:
                        headroom = _MAX_POSITIVE_DELTA - _positive_accumulated
                        if headroom <= 0:
                            reasoning.append(
                                f"SOFT [{f.filter_id}] capped (positive limit reached): {result.reason[:50]}"
                            )
                            passed_results.append(result)
                            continue
                        weighted_delta = min(weighted_delta, headroom)
                        _positive_accumulated += weighted_delta
                    confidence += weighted_delta
                    sign = "+" if weighted_delta >= 0 else ""
                    reasoning.append(
                        f"SOFT [{f.filter_id}] {sign}{weighted_delta:+.3f}: {result.reason}"
                    )
                else:
                    reasoning.append(f"INFO [{f.filter_id}]: {result.reason}")
            else:
                passed_results.append(result)

        # Clamp confidence to [0, 1]
        confidence = max(0.0, min(1.0, confidence))

        # ── STEP 3: OUTPUT DECISION ───────────────────────────────
        decision = self._output_filter.make_decision(confidence, ctx)
        reasoning.append(
            f"DECISION: {decision.value} (confidence={confidence:.3f})"
        )

        return FilterDecision(
            decision=decision,
            confidence_score=round(confidence, 4),
            triggered_filters=triggered_results,
            passed_filters=passed_results,
            hard_blocked_by=None,
            reasoning=reasoning,
            top_runner_id=aggregated.top_runner.runner_id if aggregated.top_runner else None,
            top_runner_name=aggregated.top_runner.name if aggregated.top_runner else None,
        )

    # ─────────────────────────────────────────────────────────────
    # DYNAMIC FILTER MANAGEMENT
    # ─────────────────────────────────────────────────────────────

    def add_filter(self, config_dict: dict) -> bool:
        """Add a new filter at runtime without restarting."""
        f = self._instantiate(config_dict)
        if not f:
            return False
        # Remove old version of same ID if present
        self._filters = [x for x in self._filters if x.filter_id != f.filter_id]
        self._filters.append(f)
        log.info(f"FilterEngine: added/replaced filter {f.filter_id}")
        return True

    def remove_filter(self, filter_id: str) -> bool:
        """Remove a filter by ID."""
        before = len(self._filters)
        self._filters = [f for f in self._filters if f.filter_id != filter_id]
        removed = len(self._filters) < before
        if removed:
            log.info(f"FilterEngine: removed filter {filter_id}")
        return removed

    def toggle_filter(self, filter_id: str, enabled: bool) -> bool:
        """Enable or disable a filter by ID."""
        for f in self._filters:
            if f.filter_id == filter_id:
                f.config.enabled = enabled
                log.info(f"FilterEngine: {filter_id} {'enabled' if enabled else 'disabled'}")
                return True
        return False

    def update_threshold(self, filter_id: str, key: str, value: Any) -> bool:
        """Update a single threshold value on a live filter."""
        for f in self._filters:
            if f.filter_id == filter_id:
                f.config.threshold[key] = value
                log.info(f"FilterEngine: {filter_id}.{key} = {value}")
                return True
        if self._output_filter and self._output_filter.filter_id == filter_id:
            self._output_filter.config.threshold[key] = value
            return True
        return False

    def update_weight(self, filter_id: str, weight: float) -> bool:
        """Update the weight of a soft filter."""
        for f in self._filters:
            if f.filter_id == filter_id:
                f.config.weight = max(0.0, min(2.0, weight))
                return True
        return False

    def list_filters(self) -> list[dict]:
        """Return a summary of all loaded filters."""
        result = []
        for f in self._filters:
            result.append({
                "filter_id":   f.filter_id,
                "name":        f.name,
                "enabled":     f.enabled,
                "mode":        f.mode.value,
                "weight":      f.weight,
                "applies_to":  f.config.applies_to,
                "thresholds":  f.config.threshold,
            })
        return result

    # ─────────────────────────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────────────────────────

    def _hard_filters(self, race_code: RaceCode) -> list[BaseFilter]:
        return [f for f in self._filters
                if f.enabled and f.mode == FilterMode.HARD and f.is_applicable(race_code)]

    def _soft_filters(self, race_code: RaceCode) -> list[BaseFilter]:
        return [f for f in self._filters
                if f.enabled
                and f.mode in (FilterMode.SOFT, FilterMode.INFORMATIONAL)
                and f.is_applicable(race_code)]

    @staticmethod
    def _instantiate(raw: dict) -> BaseFilter | None:
        """Dynamically load and instantiate a filter class from config."""
        cls_path = raw.get("filter_class", "")
        if not cls_path:
            log.warning(f"Filter {raw.get('filter_id')} has no filter_class")
            return None
        try:
            # Handle both "simulation.filters.X.Y" and "filters.X.Y" forms
            # Always resolve relative to the package root
            module_path, cls_name = cls_path.rsplit(".", 1)
            # If the path doesn't start with "simulation", prepend it
            if not module_path.startswith("simulation"):
                module_path = "simulation." + module_path
            mod = importlib.import_module(module_path)
            cls = getattr(mod, cls_name)

            cfg = FilterConfig(
                filter_id   = raw["filter_id"],
                name        = raw.get("name", raw["filter_id"]),
                enabled     = raw.get("enabled", True),
                mode        = FilterMode(raw.get("mode", "soft")),
                weight      = float(raw.get("weight") or 1.0),
                threshold   = raw.get("threshold", {}),
                applies_to  = raw.get("applies_to", ["all"]),
                description = raw.get("description", ""),
                filter_class= cls_path,
            )
            return cls(cfg)
        except (ImportError, AttributeError, KeyError) as e:
            log.error(f"Cannot load filter {raw.get('filter_id')}: {e}")
            return None

    @staticmethod
    def _load_configs(
        config_path: str | None,
        config_dicts: list[dict] | None,
    ) -> list[dict]:
        if config_dicts is not None:
            return config_dicts

        path = config_path or _DEFAULT_CONFIG_PATH
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
            # Flatten: strip comment-only entries
            filters = [
                f for f in data.get("filters", [])
                if "filter_id" in f
            ]
            log.info(f"Loaded {len(filters)} filter configs from {path}")
            return filters
        except FileNotFoundError:
            log.warning(f"Filter config not found at {path} — using empty config")
            return []
        except json.JSONDecodeError as e:
            log.error(f"Filter config JSON error: {e}")
            return []
