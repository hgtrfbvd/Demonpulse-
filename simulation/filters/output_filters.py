"""
simulation/filters/output_filters.py
Output filters translate a final confidence score into a betting decision.
These always run last in the pipeline.
"""
from __future__ import annotations
from ..models import FilterContext, Decision, ChaosRating, FilterResult
from .base_filter import BaseFilter


class OutputDecisionFilter(BaseFilter):
    """
    Maps final confidence score to BET / SMALL_BET / SAVE_BET / CAUTION / PASS.

    Thresholds (configurable):
      BET:       confidence >= 0.65 AND chaos not EXTREME
      SMALL_BET: confidence >= 0.50
      SAVE_BET:  confidence >= 0.42 AND hidden_value detected
      CAUTION:   confidence >= 0.35
      PASS:      everything else
    """

    def evaluate(self, ctx: FilterContext) -> FilterResult:
        # This filter is special: it doesn't have a triggered/not-triggered binary.
        # It always returns triggered=True (it always produces a decision).
        # The decision is conveyed in details["decision"].
        # The FilterEngine reads this.
        return self._result(
            triggered=True,
            confidence_delta=0.0,
            reason="Output decision filter — always evaluates",
        )

    def make_decision(self, confidence: float, ctx: FilterContext) -> Decision:
        """
        Compute the final Decision from confidence and context.
        Called by FilterEngine after all other filters have run.
        """
        chaos         = ctx.aggregated.chaos_rating
        hidden_value  = any(s.is_hidden_value for s in ctx.aggregated.runners)

        # Chaos override: never full BET in extreme chaos
        if chaos == ChaosRating.EXTREME:
            confidence = min(confidence, 0.48)

        # Thresholds
        bet_threshold       = self._threshold("bet_threshold",       0.65)
        small_bet_threshold = self._threshold("small_bet_threshold", 0.50)
        save_bet_threshold  = self._threshold("save_bet_threshold",  0.42)
        caution_threshold   = self._threshold("caution_threshold",   0.35)

        if confidence >= bet_threshold and chaos not in (ChaosRating.EXTREME,):
            return Decision.BET
        elif confidence >= small_bet_threshold:
            return Decision.SMALL_BET
        elif confidence >= save_bet_threshold and hidden_value:
            return Decision.SAVE_BET
        elif confidence >= caution_threshold:
            return Decision.CAUTION
        return Decision.PASS
