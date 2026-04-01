"""
simulation/race_code_modules/harness_module.py
Full harness racing lifecycle simulation.
Key mechanics: parked energy bleed, positional map, trail tactics, reinsman effect.
"""
from __future__ import annotations
import random
from ..models import RunnerProfile, RaceMeta, RacePattern, PhaseEvent
from .base_module import BaseRaceModule


class HarnessModule(BaseRaceModule):
    """
    Harness racing simulation.

    Critical factors:
    - PARKED (3-wide no cover) = heavy energy drain
    - TRAIL (cover behind leaders) = energy saving, kick later
    - On-pace from front row = ideal for pace setters
    - Back row = must navigate traffic, burn energy to reach position
    - Energy model is more explicit — harness races are tactical, not explosive
    - Reinsman skill can override natural ability (low RNG but real effect)
    - Gait faults can strike: horse breaks stride and must restart
    """

    # Positional energy costs per lap (per unit of race)
    _ENERGY_COST_PARKED    = 0.22
    _ENERGY_COST_TRAIL     = 0.06
    _ENERGY_COST_ON_PACE   = 0.12
    _ENERGY_COST_BACK_ROW  = 0.14

    # Gait break probability (if horse overextends early)
    _BASE_GAIT_BREAK_PROB = 0.04

    def __init__(self, race_meta: RaceMeta):
        super().__init__(race_meta)
        self._field_size = race_meta.field_size
        # track_width stored for future use; no longer read from race_meta.extra
        # (race_meta.extra still available for callers who need it)

    # ─────────────────────────────────────────────────────────────
    # GATE ADVANTAGE
    # ─────────────────────────────────────────────────────────────

    def _gate_advantage(self, runner: RunnerProfile) -> float:
        """
        Harness: front-row barriers (1–4 in most fields) = direct to position.
        Back-row runners must spend extra to find their spot.
        """
        n = self._field_size
        row_break = n // 2  # first row vs second row
        if runner.barrier_or_box <= row_break:
            # Front row: minor variation by position
            pos_frac = (runner.barrier_or_box - 1) / max(row_break - 1, 1)
            return 1.10 - pos_frac * 0.15   # 1.10 → 0.95 across front row
        else:
            # Back row: significant disadvantage
            back_pos  = runner.barrier_or_box - row_break
            back_frac = back_pos / max(n - row_break, 1)
            return 0.78 - back_frac * 0.12   # 0.78 → 0.66 across back row

    # ─────────────────────────────────────────────────────────────
    # POSITIONING PATTERN BONUS
    # ─────────────────────────────────────────────────────────────

    def _pattern_bonus(
        self,
        runner: RunnerProfile,
        current_scores: dict[str, float],
    ) -> float:
        """
        Harness positional map:
        - On pace from front row = clear, low energy cost
        - Trail (behind leaders) = energy saving, big kick late
        - Parked = wide exposure, heavy cost
        - Back trailer = must settle for last
        """
        if runner.race_pattern == RacePattern.LEADER:
            return 0.80  # dictates race — efficient if unopposed
        elif runner.race_pattern == RacePattern.TRAILER:
            return 0.75  # best second position, saves energy
        elif runner.race_pattern == RacePattern.PARKED:
            return 0.40  # working hard — may still win but costly
        elif runner.race_pattern == RacePattern.STALKER:
            return 0.65  # sitting 2-3 wide but covered
        elif runner.race_pattern == RacePattern.CHASER:
            return 0.60  # chasing from the back row
        return 0.55

    # ─────────────────────────────────────────────────────────────
    # HARNESS PHASE OVERRIDES
    # ─────────────────────────────────────────────────────────────

    def _phase_start(self, runners: list[RunnerProfile]) -> dict[str, float]:
        """
        Harness start: mobile gate break.
        Front row get immediate positional advantage.
        Back row must accelerate strongly to avoid being boxed in.
        """
        scores = {}
        for r in runners:
            gate  = self._gate_advantage(r)
            speed = r.early_speed_score / 10.0
            cons  = r.start_consistency
            # Reinsman skill modifier
            reinsman = r.extra.get("reinsman_skill", 0.5)
            noise = self._noise(0.08)
            scores[r.runner_id] = max(0.0, min(1.0,
                (speed * 0.45 + cons * 0.30 + reinsman * 0.25) * gate + noise
            ))
        return scores

    def _phase_positioning(self, runners, current_scores, stamina):
        """
        Harness positioning: horses find their tactical position.
        Parked horses pay the energy price here.
        Returns (scores, events, new_stamina) — C-04: copy, not mutation.
        """
        scores   = {}
        events   = []
        new_stam = dict(stamina)   # C-04: copy before mutation

        for r in runners:
            base     = r.tactical_position_score / 10.0
            pattern  = self._pattern_bonus(r, current_scores)
            reinsman = r.extra.get("reinsman_skill", 0.5)

            # Apply positional energy costs
            if r.race_pattern == RacePattern.PARKED:
                stam_cost = self._ENERGY_COST_PARKED * 0.6
                events.append(PhaseEvent("POSITIONING", r.runner_id, "parked_cost", stam_cost))
            elif r.race_pattern == RacePattern.TRAILER:
                stam_cost = self._ENERGY_COST_TRAIL * 0.6
            elif r.race_pattern in (RacePattern.LEADER,):
                stam_cost = self._ENERGY_COST_ON_PACE * 0.6
            else:
                stam_cost = self._ENERGY_COST_BACK_ROW * 0.6

            new_stam[r.runner_id] = max(0.05, new_stam.get(r.runner_id, 0.8) - stam_cost)
            noise = self._noise(self.NOISE_SIGMA)
            scores[r.runner_id] = max(0.0, min(1.0,
                base * 0.35 + pattern * 0.40 + reinsman * 0.25 + noise
            ))

        return scores, events, new_stam

    def _phase_pressure(self, runners, current_scores, stamina, pace_type):
        """
        Harness pressure: PARKED runners suffer most.
        Also check for gait breaks when energy depleted.
        """
        scores, events, new_stam = super()._phase_pressure(
            runners, current_scores, stamina, pace_type
        )

        for r in runners:
            # Additional cost for parked runners in pressure phase
            if r.race_pattern == RacePattern.PARKED:
                extra = self._ENERGY_COST_PARKED * 0.5
                new_stam[r.runner_id] = max(0.04, new_stam[r.runner_id] - extra)

            # Gait break check: over-taxed horses may break stride
            remaining_stam = new_stam[r.runner_id]
            if remaining_stam < 0.25 and pace_type in ("FAST","HOT"):
                break_prob = self._BASE_GAIT_BREAK_PROB * (1.0 - remaining_stam * 3)
                if random.random() < break_prob:
                    events.append(PhaseEvent("PRESSURE", r.runner_id, "gait_break", 0.35))
                    # W-07: only penalise phase score — do NOT mutate current_scores
                    # (that would double-count the penalty in the accumulator)
                    scores[r.runner_id] = max(0.0, scores[r.runner_id] - 0.30)
                    # Extra stamina drain from the break
                    new_stam[r.runner_id] = max(0.02, new_stam[r.runner_id] - 0.15)

        return scores, events, new_stam

    def _phase_late(self, runners, current_scores, stamina, pace_type):
        """
        Harness late run: trailers get the big kick if energy preserved.
        Parked runners may still win if pace was honest.
        """
        scores, collapse = super()._phase_late(runners, current_scores, stamina, pace_type)

        for r in runners:
            stam = stamina.get(r.runner_id, 0.5)
            if r.race_pattern == RacePattern.TRAILER and stam > 0.50:
                # Well-covered trailer has fresh legs late
                kick_bonus = (stam - 0.40) * 0.30 * r.late_strength / 10.0
                scores[r.runner_id] = min(1.0, scores[r.runner_id] + kick_bonus)
            elif r.race_pattern == RacePattern.PARKED and stam < 0.30:
                # Parked horse with no legs left
                fade_penalty = (0.30 - stam) * 0.40
                scores[r.runner_id] = max(0.0, scores[r.runner_id] - fade_penalty)

        return scores, collapse
