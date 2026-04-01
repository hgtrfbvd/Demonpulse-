"""
simulation/race_code_modules/thoroughbred_module.py
Full thoroughbred horse race lifecycle simulation.
Key mechanics: tempo, barrier draw, traffic management, sectional stamina.
"""
from __future__ import annotations
import random
from ..models import RunnerProfile, RaceMeta, RacePattern, PhaseEvent
from .base_module import BaseRaceModule


class ThoroughbredModule(BaseRaceModule):
    """
    Thoroughbred-specific simulation.

    Critical factors:
    - Barrier draw matters but is NOT destiny (horse ability can overcome)
    - Tempo dictates race shape — hot pace favours closers
    - Traffic management is crucial for midfield runners
    - Distance specialty: sprinters suffer beyond their range
    - Track condition modifies stamina curves
    - Wide barriers cost extra ground (real physical penalty)
    """

    # Track condition multipliers for stamina cost
    _CONDITION_STAMINA_COST: dict[str, float] = {
        "FIRM":   0.85,
        "GOOD":   1.00,
        "SOFT":   1.20,
        "HEAVY":  1.45,
        "SLOW":   1.35,
        "DEAD":   1.10,
    }

    # Wide runners lose ground in first 400m
    _BARRIER_GROUND_FRACTION: dict[int, float] = {}  # computed dynamically

    def __init__(self, race_meta: RaceMeta):
        super().__init__(race_meta)
        # Use race_meta.condition directly (RaceMeta field), fall back to extra dict for
        # backwards-compat if someone passes condition via extra instead
        self._condition = (race_meta.condition or
                           race_meta.extra.get("condition", "GOOD")).upper()
        self._stam_cost = self._CONDITION_STAMINA_COST.get(self._condition, 1.0)
        self._field_size = race_meta.field_size
        # Distance ranges — aligned with race_shape_engine sprint/staying thresholds
        self._is_sprint  = race_meta.distance_m <= 1200
        self._is_staying = race_meta.distance_m >= 2000

    # ─────────────────────────────────────────────────────────────
    # GATE ADVANTAGE
    # ─────────────────────────────────────────────────────────────

    def _gate_advantage(self, runner: RunnerProfile) -> float:
        """
        Thoroughbred barrier advantage.
        Inner barriers allow a direct line; outer barriers must angle across.
        """
        penalty = self.crash_engine.barrier_penalty(runner.barrier_or_box, self._field_size)
        # For leaders trying to cross to the fence from wide, bigger cost
        if runner.race_pattern in (RacePattern.LEADER, RacePattern.STALKER):
            penalty *= 1.4
        return max(0.65, 1.0 - penalty)

    # ─────────────────────────────────────────────────────────────
    # POSITIONING PATTERN BONUS
    # ─────────────────────────────────────────────────────────────

    def _pattern_bonus(
        self,
        runner: RunnerProfile,
        current_scores: dict[str, float],
    ) -> float:
        """
        Horses in their ideal position mid-race are efficient.
        Leaders up front: can dictate; Stalkers just off the pace: ideal.
        Chasers: depend on tempo opening a path.
        """
        if runner.race_pattern == RacePattern.LEADER:
            # Leaders benefit from a clear run, but use more energy
            return 0.70
        elif runner.race_pattern == RacePattern.STALKER:
            # Sweet spot: sitting just off the pace
            return 0.80
        elif runner.race_pattern == RacePattern.MIDFIELD:
            return 0.65
        elif runner.race_pattern == RacePattern.CHASER:
            # Depends on race opening up
            return 0.55
        elif runner.race_pattern == RacePattern.WIDE:
            # Wide out throughout — extra work
            return 0.45
        return 0.60

    # ─────────────────────────────────────────────────────────────
    # THOROUGHBRED PHASE OVERRIDES
    # ─────────────────────────────────────────────────────────────

    def _phase_start(self, runners: list[RunnerProfile]) -> dict[str, float]:
        """
        Thoroughbred start: barrier break + gate advantage.
        Leaders from wide barriers can still lead by angling early.
        """
        scores = {}
        for r in runners:
            gate_mult  = self._gate_advantage(r)
            break_base = r.start_consistency * 0.6 + r.early_speed_score / 10.0 * 0.4
            noise      = self._noise(0.09)
            # Leaders who WANT to lead get extra commitment bonus
            intent_bonus = 0.06 if r.race_pattern == RacePattern.LEADER else 0.0
            scores[r.runner_id] = max(0.0, min(1.0, break_base * gate_mult + intent_bonus + noise))
        return scores

    def _phase_positioning(self, runners, current_scores, stamina):
        """
        Thoroughbred positioning: the jostling phase.
        Wide draws must expend energy to cross to rail.
        Traffic is created in mid-field.
        Returns updated stamina dict (copy, not mutation of input).
        """
        scores    = {}
        events    = []
        new_stam  = dict(stamina)   # C-04: copy before mutation
        for r in runners:
            base    = r.tactical_position_score / 10.0
            suit    = r.track_distance_suitability
            pattern = self._pattern_bonus(r, current_scores)
            # Barrier penalty on stamina (running wide costs real ground)
            barrier_stam_cost = self.crash_engine.barrier_penalty(r.barrier_or_box, self._field_size) * 0.5
            new_stam[r.runner_id] = max(0.05, new_stam.get(r.runner_id, 0.8) - barrier_stam_cost)
            noise = self._noise(self.NOISE_SIGMA)
            scores[r.runner_id] = max(0.0, min(1.0,
                base * 0.40 + suit * 0.25 + pattern * 0.35 + noise
            ))
        return scores, events, new_stam

    def _phase_pressure(self, runners, current_scores, stamina, pace_type):
        """
        Thoroughbred pressure: track condition amplifies stamina cost.
        """
        scores, events, new_stam = super()._phase_pressure(
            runners, current_scores, stamina, pace_type
        )
        # Apply condition multiplier to stamina cost
        for r in runners:
            extra_cost = (self._stam_cost - 1.0) * 0.12
            new_stam[r.runner_id] = max(0.05, new_stam[r.runner_id] - extra_cost)
        return scores, events, new_stam

    def _phase_late(self, runners, current_scores, stamina, pace_type):
        """
        Thoroughbred late run: distance specialty matters enormously.
        Sprinters fade beyond their range; stayers keep running.
        """
        scores, collapse = super()._phase_late(runners, current_scores, stamina, pace_type)

        for r in runners:
            specialty = r.extra.get("distance_specialty", "MIDDLE")
            stam = stamina.get(r.runner_id, 0.5)

            if self._is_staying:
                if specialty == "STAYING":
                    scores[r.runner_id] = min(1.0, scores[r.runner_id] * 1.12)
                elif specialty == "SPRINT":
                    # Sprint specialists fade in staying races
                    scores[r.runner_id] = max(0.0, scores[r.runner_id] * 0.80)

            elif self._is_sprint:
                if specialty == "SPRINT":
                    scores[r.runner_id] = min(1.0, scores[r.runner_id] * 1.08)

        return scores, collapse

    def _phase_finish(self, runners, current_scores, stamina):
        """
        Thoroughbred finish: tactical position + final sprint.
        Horses in pockets get a late run bonus if a gap opens.
        """
        scores, events = super()._phase_finish(runners, current_scores, stamina)

        # Simulate gap opening for trapped horses
        sorted_r = sorted(runners, key=lambda r: current_scores.get(r.runner_id, 0), reverse=True)
        for i, r in enumerate(sorted_r):
            if r.race_pattern in (RacePattern.CHASER, RacePattern.MIDFIELD):
                gap_probability = 0.35
                if random.random() < gap_probability:
                    # Gap opens — late run bonus
                    gap_value = random.uniform(0.03, 0.12) * r.late_strength / 10.0
                    scores[r.runner_id] = min(1.0, scores[r.runner_id] + gap_value)
                    if gap_value > 0.06:
                        events.append(PhaseEvent("FINISH", r.runner_id, "late_run", gap_value))

        return scores, events
