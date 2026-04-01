"""
simulation/filters/value_filters.py
Value filters — identify overlays and hidden runners where market
is mispriced relative to simulation edge.
"""
from __future__ import annotations
from ..models import FilterContext, FilterResult
from .base_filter import BaseFilter


class SimVsMarketFilter(BaseFilter):
    """
    Primary value filter: sim win% vs market implied probability.
    Positive edge = potential overlay.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        min_edge    = self._threshold("min_edge", 0.04)     # 4% positive edge required
        strong_edge = self._threshold("strong_edge", 0.10)  # 10% = big overlay

        top = ctx.aggregated.top_runner
        if not top or not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No top runner for value assessment")

        edge = top.sim_edge   # sim_win_pct/100 - market_implied_prob
        if edge >= strong_edge:
            return self._result(
                triggered=True, confidence_delta=+0.12,
                reason=f"Strong overlay: sim={top.win_pct:.1f}% vs market={ctx.top_runner.market_implied_prob*100:.1f}% (+{edge*100:.1f}% edge)",
                details={"sim_win_pct": top.win_pct, "market_pct": round(ctx.top_runner.market_implied_prob*100,1), "edge": round(edge,4)},
            )
        elif edge >= min_edge:
            return self._result(
                triggered=True, confidence_delta=+0.06,
                reason=f"Positive overlay: +{edge*100:.1f}% sim edge",
                details={"edge": round(edge, 4)},
            )
        elif edge < -min_edge:
            # Market says this runner is better than sims think
            return self._result(
                triggered=True, confidence_delta=-0.07,
                reason=f"Underlay: sim={top.win_pct:.1f}% but market implied={ctx.top_runner.market_implied_prob*100:.1f}% — market overrates runner",
                details={"edge": round(edge, 4)},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Sim and market roughly aligned (edge: {edge*100:+.1f}%)")


class HiddenValueFilter(BaseFilter):
    """
    Detect hidden value runners: flagged by aggregator as is_hidden_value.
    Boosts confidence and may suggest a secondary bet.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        hidden_runners = [s for s in ctx.aggregated.runners if s.is_hidden_value]
        if not hidden_runners:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No hidden value runners detected")

        best = hidden_runners[0]  # highest win_pct among hidden runners
        return self._result(
            triggered=True, confidence_delta=+0.05,
            reason=f"Hidden value detected: {best.name} sim={best.win_pct:.1f}% at market odds",
            details={"runner": best.name, "win_pct": best.win_pct, "sim_edge": best.sim_edge},
        )


class FalseFavouriteFilter(BaseFilter):
    """
    Detects if the market favourite is a false favourite
    (market overweights it vs simulation). Reduces confidence.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        false_favs = [s for s in ctx.aggregated.runners if s.is_false_favourite]
        if not false_favs:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No false favourite detected")

        ff = false_favs[0]
        delta = self._threshold("false_fav_delta", -0.09)
        return self._result(
            triggered=True, confidence_delta=delta,
            reason=f"False favourite: {ff.name} is market-fav but sim shows only {ff.win_pct:.1f}% wins",
            details={"runner": ff.name, "win_pct": ff.win_pct},
        )


class UnderBetFilter(BaseFilter):
    """
    Detects runners where betting interest is low but sim performance is strong.
    W-02: Guards against double-counting with VL001 (SimVsMarketFilter).
    If VL001 already fired for a meaningful positive edge (>=4%), VL004 
    is suppressed to prevent stacking two value boosts on the same condition.
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        top = ctx.aggregated.top_runner
        if not top or not ctx.top_runner:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason="No top runner")

        # W-02: suppress if VL001 (sim vs market edge) already covers this value
        min_edge_for_vl001 = self._threshold("min_edge", 0.04)
        if top.sim_edge >= min_edge_for_vl001:
            return self._result(triggered=False, confidence_delta=0.0,
                                reason=f"VL001 already captures positive edge ({top.sim_edge*100:+.1f}%) — VL004 suppressed to avoid double-count")

        # Underbet: large odds (market not interested) but strong sim performance
        min_odds     = self._threshold("min_odds_for_underbet", 6.0)
        min_win_pct  = self._threshold("min_win_pct_underbet", 20.0)

        if ctx.top_runner.market_odds >= min_odds and top.win_pct >= min_win_pct:
            return self._result(
                triggered=True, confidence_delta=+0.08,
                reason=f"Underbet runner: {top.name} at ${ctx.top_runner.market_odds} with {top.win_pct:.1f}% sim win rate",
                details={"odds": ctx.top_runner.market_odds, "win_pct": top.win_pct},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason="No underbet opportunity")


class CleanRunValueFilter(BaseFilter):
    """
    Top runner has very low interference in simulations (clean run advantage
    not reflected in odds — market underestimates their chance).
    """
    def evaluate(self, ctx: FilterContext) -> FilterResult:
        if not ctx.top_runner or not ctx.aggregated.top_runner:
            return self._result(triggered=False, confidence_delta=0.0, reason="No top runner")

        # Check top runner's interference events in sims
        top_id = ctx.aggregated.top_runner.runner_id
        if not ctx.aggregated.raw_sims:
            return self._result(triggered=False, confidence_delta=0.0, reason="No raw sim data")

        clean_sims = sum(
            1 for s in ctx.aggregated.raw_sims
            if not any(e.runner_id == top_id and e.event_type == "interference"
                       for e in s.events)
        )
        clean_rate = clean_sims / len(ctx.aggregated.raw_sims)
        threshold  = self._threshold("clean_run_rate_threshold", 0.80)

        if clean_rate >= threshold and ctx.top_runner.market_odds >= 4.0:
            return self._result(
                triggered=True, confidence_delta=+0.06,
                reason=f"{ctx.top_runner.name} had clean runs in {clean_rate:.0%} of sims — value",
                details={"clean_rate": round(clean_rate, 3)},
            )
        return self._result(triggered=False, confidence_delta=0.0,
                            reason=f"Clean run rate ({clean_rate:.0%}) or odds not sufficient for value")
