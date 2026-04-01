"""
simulation/race_code_modules/greyhound_module.py
Full greyhound race lifecycle simulation.
Key mechanics: box draw dominance, first-bend crash map, railer vs wide dynamics.
"""
from __future__ import annotations
import random
from ..models import RunnerProfile, RaceMeta, RacePattern, PhaseEvent
from .base_module import BaseRaceModule


class GreyhoundModule(BaseRaceModule):
    """
    Greyhound-specific simulation.

    Critical factors:
    - Box 1/2 strongly favoured at most tracks (rail position)
    - First bend is the primary interference zone
    - Race won or lost by 600m at most distances
    - Railers hold the rail; wide runners cover extra ground
    - Short distances (300–380m): start wins races
    - Longer distances (515m+): run style and stamina matter more
    """

    # Box profiles per track type (default = standard oval)
    _BOX_STRENGTH: dict[int, float] = {
        1: 1.25, 2: 1.15, 3: 1.00, 4: 0.95,
        5: 0.90, 6: 0.82, 7: 0.70, 8: 0.58,
    }
    # Wide-box penalty: extra ground covered = stamina cost
    _BOX_GROUND_PENALTY: dict[int, float] = {
        1: 0.00, 2: 0.01, 3: 0.02, 4: 0.03,
        5: 0.04, 6: 0.06, 7: 0.08, 8: 0.10,
    }

    def __init__(self, race_meta: RaceMeta):
        super().__init__(race_meta)
        # Short-course flag: start dominates
        self._is_sprint = race_meta.distance_m <= 380

    # ─────────────────────────────────────────────────────────────
    # GATE ADVANTAGE
    # ─────────────────────────────────────────────────────────────

    def _gate_advantage(self, runner: RunnerProfile) -> float:
        """
        Inner boxes get a cleaner start on the inside.
        Wide boxes have longer travel to the bend.
        """
        box      = runner.barrier_or_box
        strength = self._BOX_STRENGTH.get(box, 0.90)
        # Pattern modifier: railer exploits box 1–3 more
        if runner.race_pattern == RacePattern.RAILER and box <= 3:
            strength *= 1.10
        elif runner.race_pattern == RacePattern.WIDE and box >= 6:
            strength *= 0.88  # wide runner from wide box takes even more ground
        return strength

    # ─────────────────────────────────────────────────────────────
    # POSITIONING PATTERN BONUS
    # ─────────────────────────────────────────────────────────────

    def _pattern_bonus(
        self,
        runner: RunnerProfile,
        current_scores: dict[str, float],
    ) -> float:
        """
        In greyhounds, positioning is about clearing the bend first.
        Leaders and railers from good boxes get a bonus.
        """
        box = runner.barrier_or_box
        if runner.race_pattern in (RacePattern.LEADER, RacePattern.RAILER) and box <= 4:
            return 0.75
        elif runner.race_pattern == RacePattern.RAILER and box <= 6:
            return 0.60
        elif runner.race_pattern == RacePattern.WIDE:
            # Wide runners move out — clear ground but extra distance
            return 0.40
        elif runner.race_pattern == RacePattern.CHASER:
            return 0.50
        return 0.55

    # ─────────────────────────────────────────────────────────────
    # GREYHOUND PHASE OVERRIDES
    # ─────────────────────────────────────────────────────────────

    def _phase_start(self, runners: list[RunnerProfile]) -> dict[str, float]:
        """Greyhound start is purely mechanical — break speed from box."""
        scores = {}
        for r in runners:
            box_adv  = self._gate_advantage(r)
            break_sp = r.start_consistency * r.early_speed_score / 10.0
            noise    = self._noise(0.09)
            scores[r.runner_id] = max(0.0, min(1.0, break_sp * box_adv + noise))
        return scores

    def _phase_early(self, runners, current_scores):
        """
        Greyhound early phase: sprint for the rail / first bend.
        Fast box 1–3 dogs immediately dominate.
        """
        scores = {}
        for r in runners:
            box_adv   = self._BOX_STRENGTH.get(r.barrier_or_box, 0.90)
            speed     = r.early_speed_score / 10.0
            gnd_cost  = self._BOX_GROUND_PENALTY.get(r.barrier_or_box, 0.0)
            noise     = self._noise(self.NOISE_SIGMA)
            # Sprint bonus: start matters most at short courses
            sprint_mult = 1.15 if self._is_sprint else 1.0
            scores[r.runner_id] = max(0.0, min(1.0,
                (speed * box_adv - gnd_cost) * sprint_mult + noise
            ))
        events = self.crash_engine.compute_first_phase_events(runners, current_scores)
        return scores, events

    def _phase_late(self, runners, current_scores, stamina, pace_type):
        """
        Greyhounds: late run in sprints is minimal.
        Longer distances allow more late gains.
        """
        scores, collapse = super()._phase_late(runners, current_scores, stamina, pace_type)
        if self._is_sprint:
            # In sprints, late surge is minor — leaders nearly always hold
            for r in runners:
                if r.race_pattern in (RacePattern.LEADER, RacePattern.RAILER):
                    scores[r.runner_id] = min(1.0, scores[r.runner_id] * 1.10)
                elif r.race_pattern == RacePattern.CHASER:
                    scores[r.runner_id] *= 0.92
        return scores, collapse
