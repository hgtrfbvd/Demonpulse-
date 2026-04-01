"""
simulation/core_simulation_engine.py
The main entry point for the DemonPulse Monte Carlo simulation engine.

Flow:
  1. Select race-code module (greyhound / thoroughbred / harness)
  2. Pre-analyse race shape
  3. Run N simulations (100–500)
  4. Aggregate results
  5. Pass through filter engine
  6. Generate expert guide

Usage:
    engine = SimulationEngine()
    guide  = engine.run(race_meta, runners)
    # guide.decision, guide.simulation_summary, guide.filter_results_panel, etc.
"""
from __future__ import annotations
import logging
import time
from .models import (
    RunnerProfile, RaceMeta, RaceCode,
    AggregatedResult, ExpertGuide,
)
from .race_code_modules.greyhound_module    import GreyhoundModule
from .race_code_modules.thoroughbred_module import ThoroughbredModule
from .race_code_modules.harness_module      import HarnessModule
from .race_shape_engine       import RaceShapeEngine
from .simulation_aggregator   import SimulationAggregator
from .filter_engine           import FilterEngine
from .expert_guide_integration import ExpertGuideGenerator

log = logging.getLogger(__name__)

_MODULE_MAP = {
    RaceCode.GREYHOUND:    GreyhoundModule,
    RaceCode.THOROUGHBRED: ThoroughbredModule,
    RaceCode.HARNESS:      HarnessModule,
}


class SimulationEngine:
    """
    Universal race simulation engine.

    Instantiate once (per process) and call run() for each race.
    FilterEngine is stateful (loaded configs) but thread-safe for reads.
    """

    def __init__(
        self,
        filter_config_path: str | None = None,
        filter_config_dicts: list[dict] | None = None,
    ):
        self.filter_engine = FilterEngine(
            config_path=filter_config_path,
            config_dicts=filter_config_dicts,
        )
        self.aggregator = SimulationAggregator()
        self.guide_gen  = ExpertGuideGenerator()

    # ─────────────────────────────────────────────────────────────
    # MAIN RUN
    # ─────────────────────────────────────────────────────────────

    def run(
        self,
        race_meta: RaceMeta,
        runners: list[RunnerProfile],
    ) -> ExpertGuide:
        """
        Full pipeline: simulate → aggregate → filter → expert guide.
        Returns a complete ExpertGuide object.
        """
        t0 = time.perf_counter()
        active = [r for r in runners if not r.scratched]
        if not active:
            log.warning(f"No active runners for {race_meta.race_uid}")
            return self.guide_gen.empty_guide(race_meta)

        # ── 1. PRE-RACE SHAPE ANALYSIS ────────────────────────────
        shape_engine = RaceShapeEngine(race_meta.race_code, race_meta.distance_m)
        race_shape   = shape_engine.analyse(active)

        # ── 2. SELECT MODULE ──────────────────────────────────────
        module_cls = _MODULE_MAP.get(race_meta.race_code)
        if not module_cls:
            raise ValueError(f"Unknown race code: {race_meta.race_code}")
        module = module_cls(race_meta)

        # ── 3. RUN SIMULATIONS ────────────────────────────────────
        n_sims = max(100, min(500, race_meta.n_sims))
        sims   = []
        for _ in range(n_sims):
            try:
                sims.append(module.simulate_race(active))
            except Exception as e:
                log.error(f"Simulation failed: {e}")

        if not sims:
            log.error(f"All {n_sims} simulations failed for {race_meta.race_uid}")
            return self.guide_gen.empty_guide(race_meta)

        # ── 4. AGGREGATE ──────────────────────────────────────────
        aggregated: AggregatedResult = self.aggregator.aggregate(race_meta, active, sims)

        # ── 5. FILTER PIPELINE ────────────────────────────────────
        filter_decision = self.filter_engine.run(aggregated, race_meta, active)

        # ── 6. EXPERT GUIDE ───────────────────────────────────────
        guide = self.guide_gen.generate(
            race_meta, active, aggregated, race_shape, filter_decision
        )

        elapsed = time.perf_counter() - t0
        log.info(
            f"{race_meta.race_uid} | {n_sims} sims | {elapsed*1000:.0f}ms | "
            f"decision={filter_decision.decision.value} | "
            f"confidence={filter_decision.confidence_score:.3f}"
        )
        return guide

    def run_aggregated_only(
        self,
        race_meta: RaceMeta,
        runners: list[RunnerProfile],
    ) -> AggregatedResult:
        """
        Run simulation + aggregation only (no filter pipeline or guide).
        Useful for: backtesting, batch processing, standalone analysis.
        """
        active = [r for r in runners if not r.scratched]
        module_cls = _MODULE_MAP.get(race_meta.race_code)
        if not module_cls:
            raise ValueError(f"Unknown race code: {race_meta.race_code}")
        module = module_cls(race_meta)
        n_sims = max(100, min(500, race_meta.n_sims))
        sims   = [module.simulate_race(active) for _ in range(n_sims)]
        return self.aggregator.aggregate(race_meta, active, sims)

    # ─────────────────────────────────────────────────────────────
    # FILTER MANAGEMENT (pass-through to FilterEngine)
    # ─────────────────────────────────────────────────────────────

    def add_filter(self, config_dict: dict) -> bool:
        return self.filter_engine.add_filter(config_dict)

    def remove_filter(self, filter_id: str) -> bool:
        return self.filter_engine.remove_filter(filter_id)

    def toggle_filter(self, filter_id: str, enabled: bool) -> bool:
        return self.filter_engine.toggle_filter(filter_id, enabled)

    def update_threshold(self, filter_id: str, key: str, value) -> bool:
        return self.filter_engine.update_threshold(filter_id, key, value)

    def update_weight(self, filter_id: str, weight: float) -> bool:
        return self.filter_engine.update_weight(filter_id, weight)

    def list_filters(self) -> list[dict]:
        return self.filter_engine.list_filters()
