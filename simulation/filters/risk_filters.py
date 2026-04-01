"""
simulation/filters/risk_filters.py
Risk filters — reduce confidence or downgrade bet type.
Code-specific risks: interference (dogs), wide/traffic (horses), parked (harness).
"""
from __future__ import annotations
from ..models import FilterContext, RaceCode, RacePattern, ChaosRating, FilterResult
from .base_filter import BaseFilter


class InterferenceRiskFilter(BaseFilter):
    """
    Greyhound: high interference rate in simulations = risky bet.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        if ctx.race_meta.race_code != RaceCode.GREYHOUND:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="Not applicable to this race code")

        threshold = self._threshold("interference_rate_threshold", 1.5)
        rate      = ctx.aggregated.interference_rate
        if rate >= threshold:
            delta = -min(0.15, (rate - threshold) * 0.04)
            return self._result(
                triggered=True, confidence_delta=delta,
                reason=f"High interference rate: {rate:.2f} events/sim (threshold {threshold})",
                details={"interference_rate": rate},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Interference rate acceptable: {rate:.2f}")


class WidePositionRiskFilter(BaseFilter):
    """
    Thoroughbred: top selection is wide-drawn or running wide.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        if ctx.race_meta.race_code != RaceCode.THOROUGHBRED:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="Not applicable to this race code")

        if not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No top runner")

        max_barrier = self._threshold("max_acceptable_barrier_fraction", 0.65)
        field_size  = ctx.race_meta.field_size
        barrier_frac = (ctx.top_runner.barrier_or_box - 1) / max(field_size - 1, 1)

        if barrier_frac > max_barrier or ctx.top_runner.race_pattern == RacePattern.WIDE:
            delta = -0.06 - barrier_frac * 0.05
            return self._result(
                triggered=True, confidence_delta=delta,
                reason=f"{ctx.top_runner.name} is wide-drawn (barrier {ctx.top_runner.barrier_or_box}/{field_size})",
                details={"barrier": ctx.top_runner.barrier_or_box, "field_size": field_size},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Barrier position acceptable")


class ParkedRiskFilter(BaseFilter):
    """
    Harness: runner is expected to be PARKED (wide without cover).
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        if ctx.race_meta.race_code != RaceCode.HARNESS:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="Not applicable to this race code")

        if not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0, reason="No top runner")

        if ctx.top_runner.race_pattern == RacePattern.PARKED:
            parked_penalty = self._threshold("parked_confidence_delta", -0.10)
            return self._result(
                triggered=True, confidence_delta=parked_penalty,
                reason=f"{ctx.top_runner.name} expected to be PARKED — significant energy cost",
                details={"pattern": ctx.top_runner.race_pattern.value},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Runner not parked")


class InconsistencyRiskFilter(BaseFilter):
    """
    Runner has low confidence_factor (inconsistent recent form).
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        min_conf = self._threshold("min_confidence_factor", 0.55)

        if not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0, reason="No top runner")

        cf = ctx.top_runner.confidence_factor
        if cf < min_conf:
            delta = -min(0.12, (min_conf - cf) * 0.25)
            return self._result(
                triggered=True, confidence_delta=delta,
                reason=f"{ctx.top_runner.name} has low form consistency ({cf:.2f} < {min_conf})",
                details={"confidence_factor": cf},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Form consistency acceptable ({cf:.2f})")


class MarketDriftFilter(BaseFilter):
    """
    Informational: runner's market odds have drifted (shortened or firmed
    beyond expected range based on sim results). Indicates market disagreement.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        if not ctx.top_runner or not ctx.aggregated.top_runner:
            return self._result(triggered=False, confidence_delta=0.0, reason="No top runner")

        sim_win_pct  = ctx.aggregated.top_runner.win_pct / 100.0
        market_prob  = ctx.top_runner.market_implied_prob
        drift_threshold = self._threshold("drift_threshold", 0.20)

        if market_prob > 0 and sim_win_pct > 0:
            ratio = abs(sim_win_pct - market_prob) / max(market_prob, sim_win_pct)
            if ratio > drift_threshold:
                direction = "SHORTER" if market_prob > sim_win_pct else "LONGER"
                return self._result(
                    triggered=True, confidence_delta=-0.04,
                    reason=f"Market {direction} than simulation suggests — possible drift ({ratio:.0%} divergence)",
                    details={"sim_prob": round(sim_win_pct, 3), "market_prob": round(market_prob, 3), "drift": round(ratio, 3)},
                )

        return self._result(triggered=False, confidence_delta=0.0,
                            reason="Market and simulation broadly aligned")


class CollapseRiskFilter(BaseFilter):
    """
    High collapse risk reduces confidence in leaders; boosts closers.
    If our top runner is a LEADER and collapse risk is HIGH: downgrade.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        if not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0, reason="No top runner")

        collapse = ctx.aggregated.collapse_risk
        pattern  = ctx.top_runner.race_pattern

        if collapse == "HIGH" and pattern in (RacePattern.LEADER, RacePattern.RAILER, RacePattern.PARKED):
            delta = self._threshold("collapse_leader_delta", -0.08)
            return self._result(
                triggered=True, confidence_delta=delta,
                reason=f"HIGH collapse risk with {ctx.top_runner.name} as pace setter — fade risk elevated",
                details={"collapse_risk": collapse, "pattern": pattern.value},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Collapse risk ({collapse}) vs pattern ({ctx.top_runner.race_pattern.value}) — no concern")
