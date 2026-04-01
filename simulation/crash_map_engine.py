"""
simulation/crash_map_engine.py
Models collision, interference, and positional conflict probabilities.

Greyhounds:  first bend crash clusters, wide runner squeeze, rail pressure.
Thoroughbreds: barrier-to-rail distance, traffic pockets, wide sweeping.
Harness:       parked energy penalty, blocking from trail.
"""
from __future__ import annotations
import random
import math
from .models import RunnerProfile, RaceCode, RacePattern, PhaseEvent


class CrashMapEngine:
    """
    Computes interference events for a given snapshot of runner positions.
    Called once per simulation per phase where contact is possible.
    """

    # Box profiles: STRONG=1.0, NEUTRAL=0.5, WEAK=0.2, AVOID=0.0
    _GH_BOX_PROFILES: dict[int, float] = {
        1: 1.0, 2: 0.90, 3: 0.60, 4: 0.55,
        5: 0.45, 6: 0.35, 7: 0.20, 8: 0.10,
    }

    # Tracks with known wide-box bias (inner tracks favour rails)
    _WIDE_BOX_TRACKS: set[str] = {"horsham", "bendigo", "sandown"}

    def __init__(self, race_code: RaceCode, track: str = "", distance_m: int = 400):
        self.race_code  = race_code
        self.track      = track.lower()
        self.distance_m = distance_m

    # ─────────────────────────────────────────────────────────────
    # PUBLIC
    # ─────────────────────────────────────────────────────────────

    def compute_first_phase_events(
        self,
        runners: list[RunnerProfile],
        positions: dict[str, float],   # runner_id → current score (higher = more forward)
    ) -> list[PhaseEvent]:
        """
        Compute interference events at the first major conflict point
        (first bend for dogs, initial jostle for horses, gate break for harness).
        """
        if self.race_code == RaceCode.GREYHOUND:
            return self._greyhound_bend_events(runners, positions)
        elif self.race_code == RaceCode.THOROUGHBRED:
            return self._thoroughbred_barrier_events(runners, positions)
        elif self.race_code == RaceCode.HARNESS:
            return self._harness_gate_events(runners, positions)
        return []

    def compute_mid_race_events(
        self,
        runners: list[RunnerProfile],
        positions: dict[str, float],
    ) -> list[PhaseEvent]:
        """
        Mid-race interference: traffic for horses, second bend for dogs,
        parked energy bleed for harness.
        """
        if self.race_code == RaceCode.GREYHOUND:
            return self._greyhound_mid_events(runners, positions)
        elif self.race_code == RaceCode.THOROUGHBRED:
            return self._thoroughbred_traffic_events(runners, positions)
        elif self.race_code == RaceCode.HARNESS:
            return self._harness_parked_events(runners, positions)
        return []

    def box_advantage(self, box: int) -> float:
        """Return normalised box advantage score (greyhounds)."""
        if self.track in self._WIDE_BOX_TRACKS:
            # Wide box tracks slightly penalise outer boxes more
            profile = self._GH_BOX_PROFILES.get(box, 0.5)
            if box >= 6:
                profile *= 0.85
            return profile
        return self._GH_BOX_PROFILES.get(box, 0.5)

    def barrier_penalty(self, barrier: int, field_size: int) -> float:
        """
        Return energy/position penalty for a wide barrier draw (thoroughbreds).
        Outer barriers must travel further to find a rail position.
        Returns a value 0–0.25 (0=no penalty, 0.25=heavy penalty).
        """
        if field_size <= 0:
            return 0.0
        wide_fraction = (barrier - 1) / max(field_size - 1, 1)
        # Non-linear: first 30% = negligible, outer 20% = significant
        if wide_fraction < 0.3:
            return wide_fraction * 0.05
        elif wide_fraction < 0.7:
            return 0.015 + (wide_fraction - 0.3) * 0.10
        else:
            return 0.055 + (wide_fraction - 0.7) * 0.25 * 1.5

    # ─────────────────────────────────────────────────────────────
    # GREYHOUND EVENTS
    # ─────────────────────────────────────────────────────────────

    def _greyhound_bend_events(
        self,
        runners: list[RunnerProfile],
        positions: dict[str, float],
    ) -> list[PhaseEvent]:
        events = []
        active = [r for r in runners if not r.scratched]

        # Leaders (highest score) entering bend are at most risk from crowding
        sorted_r = sorted(active, key=lambda r: positions.get(r.runner_id, 0), reverse=True)

        for i, runner in enumerate(sorted_r):
            # Probability of interference at first bend
            # Inner box = lower base risk but crowded; outer = wider travel
            box       = runner.barrier_or_box
            base_risk = 0.08 if box in (1,2) else 0.12 if box in (3,4,5) else 0.09
            # Increase risk when fast runners from multiple boxes are converging
            leaders_ahead = sum(1 for r in sorted_r[:4]
                                if r.early_speed_score >= 7.0 and r.runner_id != runner.runner_id)
            convergence_risk = leaders_ahead * 0.025

            # Start inconsistency raises risk
            consistency_risk = (1.0 - runner.start_consistency) * 0.10

            total_risk = min(0.55, base_risk + convergence_risk + consistency_risk)

            if random.random() < total_risk:
                severity = random.uniform(0.05, 0.30)
                events.append(PhaseEvent(
                    phase="EARLY",
                    runner_id=runner.runner_id,
                    event_type="interference",
                    severity=severity,
                ))
        return events

    def _greyhound_mid_events(
        self,
        runners: list[RunnerProfile],
        positions: dict[str, float],
    ) -> list[PhaseEvent]:
        events = []
        active = [r for r in runners if not r.scratched]
        # Tightly bunched field (small spread) has higher mid-race interference
        scores = [positions.get(r.runner_id, 0) for r in active]
        spread = max(scores) - min(scores) if scores else 1.0
        bunch_risk = max(0, 0.30 - spread * 0.15)

        for runner in active:
            if runner.race_pattern in (RacePattern.WIDE,):
                risk = bunch_risk * 0.5
            elif runner.race_pattern == RacePattern.RAILER:
                risk = bunch_risk * 0.8
            else:
                risk = bunch_risk * 0.6

            if random.random() < risk:
                events.append(PhaseEvent(
                    phase="MID",
                    runner_id=runner.runner_id,
                    event_type="interference",
                    severity=random.uniform(0.03, 0.15),
                ))
        return events

    # ─────────────────────────────────────────────────────────────
    # THOROUGHBRED EVENTS
    # ─────────────────────────────────────────────────────────────

    def _thoroughbred_barrier_events(
        self,
        runners: list[RunnerProfile],
        positions: dict[str, float],
    ) -> list[PhaseEvent]:
        events = []
        active     = [r for r in runners if not r.scratched]
        field_size = len(active)
        leaders    = [r for r in active if r.race_pattern in (RacePattern.LEADER, RacePattern.STALKER)]

        for runner in active:
            penalty = self.barrier_penalty(runner.barrier_or_box, field_size)
            # Leaders from wide barriers take big risks pushing for position
            if runner.race_pattern in (RacePattern.LEADER, RacePattern.STALKER):
                push_risk = penalty * 1.8
            else:
                push_risk = penalty * 0.8

            # Heavy traffic when >3 horses all want to be on pace
            if len(leaders) >= 4:
                push_risk += 0.06

            if random.random() < min(0.50, push_risk):
                events.append(PhaseEvent(
                    phase="EARLY",
                    runner_id=runner.runner_id,
                    event_type="blocked" if runner.barrier_or_box > field_size // 2 else "traffic",
                    severity=random.uniform(0.05, 0.25),
                ))
        return events

    def _thoroughbred_traffic_events(
        self,
        runners: list[RunnerProfile],
        positions: dict[str, float],
    ) -> list[PhaseEvent]:
        events = []
        active = [r for r in runners if not r.scratched]
        # Sort by position mid-race
        sorted_r = sorted(active, key=lambda r: positions.get(r.runner_id, 0), reverse=True)

        for i, runner in enumerate(sorted_r):
            if runner.race_pattern in (RacePattern.CHASER, RacePattern.MIDFIELD):
                # Chasers from mid-pack: risk of getting trapped
                rank_fraction = i / max(len(sorted_r) - 1, 1)
                trap_risk = rank_fraction * 0.20 * runner.pressure_risk_score
                if random.random() < trap_risk:
                    events.append(PhaseEvent(
                        phase="MID",
                        runner_id=runner.runner_id,
                        event_type="traffic",
                        severity=random.uniform(0.04, 0.18),
                    ))
        return events

    # ─────────────────────────────────────────────────────────────
    # HARNESS EVENTS
    # ─────────────────────────────────────────────────────────────

    def _harness_gate_events(
        self,
        runners: list[RunnerProfile],
        positions: dict[str, float],
    ) -> list[PhaseEvent]:
        events = []
        active = [r for r in runners if not r.scratched]

        for runner in active:
            # Back-row barriers take longer to find their line
            if runner.barrier_or_box > len(active) // 2:
                gate_risk = 0.12 + (runner.barrier_or_box - len(active) // 2) * 0.03
                if random.random() < min(0.35, gate_risk):
                    events.append(PhaseEvent(
                        phase="EARLY",
                        runner_id=runner.runner_id,
                        event_type="gate_back",
                        severity=random.uniform(0.04, 0.12),
                    ))
        return events

    def _harness_parked_events(
        self,
        runners: list[RunnerProfile],
        positions: dict[str, float],
    ) -> list[PhaseEvent]:
        """Parked runners bleed energy; trailing runners face a blocking risk."""
        events = []
        active = [r for r in runners if not r.scratched]

        for runner in active:
            if runner.race_pattern == RacePattern.PARKED:
                # Parked = exposed to wind + energy cost regardless of outcome
                events.append(PhaseEvent(
                    phase="PRESSURE",
                    runner_id=runner.runner_id,
                    event_type="parked_energy_cost",
                    severity=random.uniform(0.08, 0.22),
                ))
            elif runner.race_pattern == RacePattern.TRAILER:
                # Trailing horses may get blocked when leaders angle out
                block_risk = 0.10 * runner.pressure_risk_score
                if random.random() < block_risk:
                    events.append(PhaseEvent(
                        phase="MID",
                        runner_id=runner.runner_id,
                        event_type="blocked",
                        severity=random.uniform(0.04, 0.14),
                    ))
        return events
