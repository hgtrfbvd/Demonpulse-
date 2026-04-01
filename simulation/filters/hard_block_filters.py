"""
simulation/filters/hard_block_filters.py
Hard-block filters. Any triggered filter forces an immediate PASS decision.
These are evaluated FIRST in the pipeline.
"""
from __future__ import annotations
from ..models import FilterContext, ChaosRating, ConfidenceRating, FilterResult
from .base_filter import BaseFilter


class ExtremeChaosFilter(BaseFilter):
    """
    Block if chaos rating is EXTREME (or above configured threshold).
    No bet should be placed in structurally broken races.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        threshold = self._threshold("chaos_rating", "EXTREME")
        chaos_levels = [c.value for c in ChaosRating]
        threshold_idx = chaos_levels.index(threshold) if threshold in chaos_levels else 3

        actual_idx = chaos_levels.index(ctx.aggregated.chaos_rating.value)
        triggered  = actual_idx >= threshold_idx

        return self._result(
            triggered=triggered,
            reason=f"Chaos rating {ctx.aggregated.chaos_rating.value} reaches block threshold {threshold}",
            details={"chaos": ctx.aggregated.chaos_rating.value, "threshold": threshold},
        )


class MinConfidenceFilter(BaseFilter):
    """
    Block if simulation confidence rating is below minimum.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        min_conf = self._threshold("min_confidence_rating", "LOW")
        conf_levels = {
            ConfidenceRating.HIGH.value: 4,
            ConfidenceRating.SOLID.value: 3,
            ConfidenceRating.MODERATE.value: 2,
            ConfidenceRating.LOW.value: 1,
        }
        actual_level = conf_levels.get(ctx.aggregated.confidence_rating.value, 1)
        min_level    = conf_levels.get(min_conf, 1)
        triggered    = actual_level < min_level

        return self._result(
            triggered=triggered,
            reason=f"Confidence {ctx.aggregated.confidence_rating.value} below minimum {min_conf}",
            details={"confidence": ctx.aggregated.confidence_rating.value, "min_required": min_conf},
        )


class InvalidDataFilter(BaseFilter):
    """
    Block if critical runner data is missing or invalid.
    Fires when top runner has no market odds, or field size is too small.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        min_runners = self._threshold("min_runners", 3)
        active = [r for r in ctx.runners if not r.scratched]

        if len(active) < min_runners:
            return self._result(
                triggered=True,
                reason=f"Field too small: {len(active)} runners (minimum {min_runners})",
                details={"field_size": len(active), "min_required": min_runners},
            )

        # Check for runners with invalid market odds
        invalid_odds = [r for r in active if r.market_odds <= 1.0 or r.market_odds > 500]
        if len(invalid_odds) >= len(active) // 2:
            return self._result(
                triggered=True,
                reason=f"{len(invalid_odds)} runners have invalid market odds",
                details={"invalid_count": len(invalid_odds)},
            )

        # Check for zero simulation results
        if ctx.aggregated.n_sims < 10:
            return self._result(
                triggered=True,
                reason=f"Insufficient simulation data: {ctx.aggregated.n_sims} runs",
                details={"n_sims": ctx.aggregated.n_sims},
            )

        return self._result(triggered=False, reason="Data validation passed")


class StructuralRaceFilter(BaseFilter):
    """
    Block if the race has structural integrity issues:
    - All runners have same race pattern (no differentiation)
    - Top sim winner is scratched
    - All win percentages are equal (no separation)
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        active = [r for r in ctx.runners if not r.scratched]
        if not active or not ctx.aggregated.runners:
            return self._result(triggered=False, reason="No active runners to evaluate")

        win_pcts = [s.win_pct for s in ctx.aggregated.runners]
        max_win  = max(win_pcts) if win_pcts else 0

        # No separation at all (all runners within 2% of each other)
        min_threshold = self._threshold("min_separation_pct", 2.0)
        if max_win > 0:
            spread = max(win_pcts) - min(win_pcts)
            if spread < min_threshold:
                return self._result(
                    triggered=True,
                    reason=f"No meaningful separation in sim results (spread: {spread:.1f}%)",
                    details={"spread_pct": round(spread, 2)},
                )

        # Top runner is scratched
        top = ctx.aggregated.top_runner
        if top:
            top_profile = next((r for r in ctx.runners if r.runner_id == top.runner_id), None)
            if top_profile and top_profile.scratched:
                return self._result(
                    triggered=True,
                    reason=f"Top simulation runner {top.name} is scratched",
                    details={"runner": top.name},
                )

        return self._result(triggered=False, reason="Race structure acceptable")


class MarketSuspensionFilter(BaseFilter):
    """
    Block if market appears suspended or unreliable (all runners at same odds,
    or total implied probability far outside normal range).
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        active = [r for r in ctx.runners if not r.scratched]
        if not active:
            return self._result(triggered=False, reason="No runners")

        total_implied = sum(r.market_implied_prob for r in active)

        # Normal range: 1.05–1.40 (overround)
        min_implied = self._threshold("min_total_implied", 0.90)
        max_implied = self._threshold("max_total_implied", 1.60)

        if not (min_implied <= total_implied <= max_implied):
            return self._result(
                triggered=True,
                reason=f"Market appears unreliable: total implied = {total_implied:.2f}",
                details={"total_implied": round(total_implied, 3)},
            )

        # All runners at suspiciously similar odds
        odds_list = sorted(r.market_odds for r in active)
        if len(odds_list) >= 3:
            spread = odds_list[-1] - odds_list[0]
            if spread < 0.5:
                return self._result(
                    triggered=True,
                    reason=f"Market odds suspiciously clustered (spread: {spread:.2f})",
                    details={"odds_spread": round(spread, 2)},
                )

        return self._result(triggered=False, reason="Market data looks valid")
