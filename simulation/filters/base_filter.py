"""
simulation/filters/base_filter.py
Abstract base for every filter. All filters receive a FilterContext
and return a FilterResult. Filters must NOT mutate shared state.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from ..models import FilterConfig, FilterResult, FilterContext, FilterMode, RaceCode


class BaseFilter(ABC):
    """
    Abstract filter. Instantiated once per FilterConfig entry.
    Filters are stateless — evaluate() is pure.
    """

    def __init__(self, config: FilterConfig):
        self.config = config

    @property
    def filter_id(self) -> str:
        return self.config.filter_id

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def mode(self) -> FilterMode:
        return self.config.mode

    @property
    def weight(self) -> float:
        return self.config.weight

    def is_applicable(self, race_code: RaceCode) -> bool:
        """Return True if this filter applies to the given race code."""
        applies = self.config.applies_to
        return "all" in applies or race_code.value in applies

    @abstractmethod
    def evaluate(self, context: FilterContext) -> FilterResult:
        """
        Evaluate the filter against the simulation context.

        Hard filters: return triggered=True to force PASS.
        Soft filters: return confidence_delta (positive or negative).
        Informational: never affects decision, just provides data.
        """

    def _result(
        self,
        triggered: bool,
        reason: str,
        confidence_delta: float = 0.0,
        details: dict | None = None,
    ) -> FilterResult:
        """Helper to build a FilterResult."""
        return FilterResult(
            filter_id=self.filter_id,
            filter_name=self.name,
            mode=self.mode,
            triggered=triggered,
            confidence_delta=confidence_delta if not triggered or self.mode != FilterMode.HARD else -1.0,
            reason=reason,
            details=details or {},
        )

    def _threshold(self, key: str, default=None):
        """Safe threshold lookup from config."""
        return self.config.threshold.get(key, default)
