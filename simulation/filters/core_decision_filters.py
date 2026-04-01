"""
simulation/filters/core_decision_filters.py
Core decision filters — the primary scoring layer.
These are soft filters that adjust confidence up or down.
"""
from __future__ import annotations
from ..models import FilterContext, RacePattern, RaceCode, FilterResult
from .base_filter import BaseFilter


class SimWinRateFilter(BaseFilter):
    """
    Core filter: top runner's simulation win rate vs threshold.
    Boosts confidence if win rate is strong; reduces if weak.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        min_win_pct = self._threshold("min_win_pct", 22.0)
        strong_pct  = self._threshold("strong_win_pct", 35.0)

        top = ctx.aggregated.top_runner
        if not top:
            return self._result(triggered=True, confidence_delta=-0.15,
                                reason="No clear simulation winner identified")

        if top.win_pct >= strong_pct:
            return self._result(
                triggered=True, confidence_delta=+0.12,
                reason=f"{top.name} wins {top.win_pct:.1f}% — strong simulation dominance",
                details={"win_pct": top.win_pct},
            )
        elif top.win_pct >= min_win_pct:
            return self._result(
                triggered=False, confidence_delta=0.0,
                reason=f"{top.name} wins {top.win_pct:.1f}% — meets minimum threshold",
                details={"win_pct": top.win_pct},
            )
        else:
            return self._result(
                triggered=True, confidence_delta=-0.12,
                reason=f"{top.name} wins only {top.win_pct:.1f}% — below threshold ({min_win_pct}%)",
                details={"win_pct": top.win_pct, "threshold": min_win_pct},
            )


class EarlySpeedFilter(BaseFilter):
    """
    Core filter: does the top runner have early speed or map advantage?
    Important for all codes but especially greyhounds.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        min_speed = self._threshold("min_early_speed_score", 6.0)

        if not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No top runner to evaluate")

        speed = ctx.top_runner.early_speed_score
        if speed >= min_speed:
            delta = min(0.08, (speed - min_speed) * 0.025)
            return self._result(
                triggered=True, confidence_delta=delta,
                reason=f"{ctx.top_runner.name} has strong early speed ({speed:.1f}/10)",
                details={"early_speed": speed},
            )
        else:
            return self._result(
                triggered=True, confidence_delta=-0.05,
                reason=f"{ctx.top_runner.name} has below-average early speed ({speed:.1f}/10)",
                details={"early_speed": speed},
            )


class MapAdvantageFilter(BaseFilter):
    """
    Core filter: does the top runner have a positional map advantage?
    Checks race_pattern vs projected race shape.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        if not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No top runner to evaluate")

        pace = ctx.aggregated.pace_type
        collapse = ctx.aggregated.collapse_risk
        pattern  = ctx.top_runner.race_pattern

        # Leader in slow/moderate pace: structurally advantaged
        if pattern in (RacePattern.LEADER, RacePattern.RAILER) and pace in ("SLOW", "MODERATE"):
            return self._result(
                triggered=True, confidence_delta=+0.07,
                reason=f"{ctx.top_runner.name} leads in {pace} pace — ideal map",
            )
        # Chaser in hot pace with collapse risk: very advantaged
        elif pattern in (RacePattern.CHASER, RacePattern.TRAILER) and collapse == "HIGH":
            return self._result(
                triggered=True, confidence_delta=+0.09,
                reason=f"{ctx.top_runner.name} chasing in {pace} pace with {collapse} collapse risk — ideal",
            )
        # Leader in hot pace: race will burn them out
        elif pattern in (RacePattern.LEADER, RacePattern.PARKED) and pace == "HOT":
            return self._result(
                triggered=True, confidence_delta=-0.08,
                reason=f"{ctx.top_runner.name} is a leader in HOT pace — structural disadvantage",
            )
        # Wide runner: always some penalty
        elif pattern == RacePattern.WIDE:
            return self._result(
                triggered=True, confidence_delta=-0.06,
                reason=f"{ctx.top_runner.name} is a wide runner — extra work against",
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Map position neutral for {ctx.top_runner.name}")


class TrackDistanceSuitabilityFilter(BaseFilter):
    """
    Core filter: is the top runner well-suited to this track/distance?
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        min_suit  = self._threshold("min_suitability", 0.60)
        high_suit = self._threshold("high_suitability", 0.80)

        if not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No top runner to evaluate")

        suit = ctx.top_runner.track_distance_suitability
        if suit >= high_suit:
            return self._result(
                triggered=True, confidence_delta=+0.06,
                reason=f"{ctx.top_runner.name} is highly suited to track/distance ({suit:.0%})",
                details={"suitability": suit},
            )
        elif suit < min_suit:
            return self._result(
                triggered=True, confidence_delta=-0.08,
                reason=f"{ctx.top_runner.name} has poor track/distance suitability ({suit:.0%})",
                details={"suitability": suit},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Track/distance suitability acceptable ({suit:.0%})")


class RaceShapeFilter(BaseFilter):
    """
    Core filter: does the projected race shape favour our selection?
    Cross-checks pace type against runner pattern.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        if not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No top runner to evaluate")

        pace        = ctx.aggregated.pace_type
        collapse    = ctx.aggregated.collapse_risk
        pattern     = ctx.top_runner.race_pattern
        runner_name = ctx.top_runner.name

        # Ideal: stalker in fast pace (sits off and finishes over)
        if (pattern == RacePattern.STALKER and pace in ("FAST","HOT") and collapse != "HIGH"):
            return self._result(
                triggered=True, confidence_delta=+0.05,
                reason=f"{runner_name} stalking in {pace} pace — ideal shape",
            )
        # Ideal: trailer in harness with HIGH collapse
        if (ctx.race_meta.race_code == RaceCode.HARNESS and
                pattern == RacePattern.TRAILER and collapse == "HIGH"):
            return self._result(
                triggered=True, confidence_delta=+0.08,
                reason=f"{runner_name} trailing in HOT harness pace — perfect energy conservation",
            )
        # Poor: parked in harness with HOT pace (energy drained)
        if (ctx.race_meta.race_code == RaceCode.HARNESS and
                pattern == RacePattern.PARKED and pace == "HOT"):
            return self._result(
                triggered=True, confidence_delta=-0.10,
                reason=f"{runner_name} is PARKED in HOT harness pace — significant energy drain",
            )

        return self._result(triggered=False, confidence_delta=0.0,
                            reason="Race shape neutral")


class PlaceRateFilter(BaseFilter):
    """
    Core filter: top runner's place rate (top 3 frequency).
    Useful for win/place betting assessment.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        min_place_pct  = self._threshold("min_place_pct", 45.0)
        high_place_pct = self._threshold("high_place_pct", 65.0)

        top = ctx.aggregated.top_runner
        if not top:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No top runner data")

        if top.place_pct >= high_place_pct:
            return self._result(
                triggered=True, confidence_delta=+0.05,
                reason=f"{top.name} places in {top.place_pct:.1f}% of simulations — very reliable",
                details={"place_pct": top.place_pct},
            )
        elif top.place_pct < min_place_pct:
            return self._result(
                triggered=True, confidence_delta=-0.06,
                reason=f"{top.name} places in only {top.place_pct:.1f}% of simulations",
                details={"place_pct": top.place_pct},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Place rate {top.place_pct:.1f}% acceptable")
