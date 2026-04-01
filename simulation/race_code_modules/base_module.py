"""
simulation/race_code_modules/base_module.py
Abstract base class for greyhound, thoroughbred, and harness modules.
Each module implements the full 7-phase race lifecycle.
"""
from __future__ import annotations
import random
import math
from abc import ABC, abstractmethod
from ..models import RunnerProfile, RaceMeta, SingleSimResult, PhaseEvent, RacePattern
from ..crash_map_engine import CrashMapEngine


class BaseRaceModule(ABC):
    """
    Abstract race simulator. Subclasses implement code-specific logic
    while sharing the common phase chain skeleton.

    PHASE CHAIN:  START → EARLY → POSITIONING → PRESSURE → MID → LATE → FINISH
    """

    # Phase weights: must sum to 1.0
    PHASE_WEIGHTS = {
        "START":       0.12,
        "EARLY":       0.22,
        "POSITIONING": 0.15,
        "PRESSURE":    0.15,
        "MID":         0.15,
        "LATE":        0.13,
        "FINISH":      0.08,
    }

    # Bounded noise: ±this fraction of base score
    NOISE_BOUND = 0.22
    NOISE_SIGMA = 0.07   # Gaussian std dev before bounding

    def __init__(self, race_meta: RaceMeta):
        self.race_meta    = race_meta
        self.crash_engine = CrashMapEngine(
            race_meta.race_code,
            race_meta.track,
            race_meta.distance_m,
        )

    # ─────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────

    def simulate_race(self, runners: list[RunnerProfile]) -> SingleSimResult:
        """
        Run one complete race simulation.
        Returns finish order, per-phase scores, and events.
        """
        active = [r for r in runners if not r.scratched]
        if not active:
            return self._empty_result(runners)

        # Initialise cumulative score for each runner
        scores: dict[str, float] = {r.runner_id: 0.0 for r in active}
        # Stamina budget: starts at 1.0, depleted by pressure/pace
        stamina: dict[str, float] = {r.runner_id: r.stamina_score / 10.0 for r in active}
        all_events: list[PhaseEvent] = []
        leader_at_turn: str | None = None
        pace_type = "MODERATE"
        collapse_occurred = False

        # ── PHASE 1: START ────────────────────────────────────────
        start_scores = self._phase_start(active)
        scores = self._accumulate(scores, start_scores, self.PHASE_WEIGHTS["START"])

        # ── PHASE 2: EARLY ────────────────────────────────────────
        early_scores, early_events = self._phase_early(active, scores)
        scores = self._accumulate(scores, early_scores, self.PHASE_WEIGHTS["EARLY"])
        all_events.extend(early_events)

        # Apply early interference penalties immediately
        for ev in early_events:
            if ev.event_type in ("interference", "blocked", "traffic", "gate_back"):
                scores[ev.runner_id] = max(0.0, scores[ev.runner_id] - ev.severity)
                stamina[ev.runner_id] = max(0.0, stamina[ev.runner_id] - ev.severity * 0.3)

        # ── PHASE 3: POSITIONING ─────────────────────────────────
        # Returns (scores, events, updated_stamina) — stamina updated as copy
        _pos_result = self._phase_positioning(active, scores, stamina)
        if len(_pos_result) == 3:
            pos_scores, pos_events, stamina = _pos_result
        else:
            pos_scores, pos_events = _pos_result   # base class 2-tuple fallback
        scores = self._accumulate(scores, pos_scores, self.PHASE_WEIGHTS["POSITIONING"])
        all_events.extend(pos_events)

        # Identify pace type after positioning
        leader_at_turn = max(active, key=lambda r: scores.get(r.runner_id, 0)).runner_id
        pace_type = self._classify_pace(active, scores)

        # ── PHASE 4: PRESSURE ────────────────────────────────────
        pres_scores, pres_events, stamina = self._phase_pressure(
            active, scores, stamina, pace_type
        )
        scores = self._accumulate(scores, pres_scores, self.PHASE_WEIGHTS["PRESSURE"])
        all_events.extend(pres_events)

        # ── PHASE 5: MID ─────────────────────────────────────────
        mid_events = self.crash_engine.compute_mid_race_events(active, scores)
        all_events.extend(mid_events)
        for ev in mid_events:
            if ev.event_type in ("interference", "traffic", "blocked", "parked_energy_cost"):
                scores[ev.runner_id] = max(0.0, scores[ev.runner_id] - ev.severity)
                stamina[ev.runner_id] = max(0.0, stamina[ev.runner_id] - ev.severity * 0.4)

        mid_scores = self._phase_mid(active, scores, stamina)
        scores = self._accumulate(scores, mid_scores, self.PHASE_WEIGHTS["MID"])

        # ── PHASE 6: LATE ────────────────────────────────────────
        late_scores, collapse = self._phase_late(active, scores, stamina, pace_type)
        scores = self._accumulate(scores, late_scores, self.PHASE_WEIGHTS["LATE"])
        if collapse:
            collapse_occurred = True
            all_events.append(PhaseEvent("LATE", leader_at_turn or "", "pace_collapse", 0.3))

        # ── PHASE 7: FINISH ──────────────────────────────────────
        finish_scores, finish_events = self._phase_finish(active, scores, stamina)
        scores = self._accumulate(scores, finish_scores, self.PHASE_WEIGHTS["FINISH"])
        all_events.extend(finish_events)

        # ── DETERMINE FINISH ORDER ────────────────────────────────
        finish_order = sorted(active, key=lambda r: scores[r.runner_id], reverse=True)
        finish_ids   = [r.runner_id for r in finish_order]
        winner       = finish_order[0] if finish_order else active[0]

        return SingleSimResult(
            finish_order=finish_ids,
            phase_scores=scores,
            events=all_events,
            pace_type=pace_type,
            leader_at_turn=leader_at_turn,
            winner_pattern=winner.race_pattern,
            interference_count=sum(1 for e in all_events
                                   if e.event_type in ("interference","blocked","traffic")),
            collapse_occurred=collapse_occurred,
        )

    # ─────────────────────────────────────────────────────────────
    # PHASE IMPLEMENTATIONS (overridable by subclasses)
    # ─────────────────────────────────────────────────────────────

    def _phase_start(self, runners: list[RunnerProfile]) -> dict[str, float]:
        """
        START: break reaction, gate consistency.
        Returns per-runner phase score (0–1).
        """
        scores = {}
        for r in runners:
            base  = r.start_consistency
            gate  = self._gate_advantage(r)
            noise = self._noise(0.10)
            scores[r.runner_id] = max(0.0, min(1.0, base * gate + noise))
        return scores

    def _phase_early(
        self,
        runners: list[RunnerProfile],
        current_scores: dict[str, float],
    ) -> tuple[dict[str, float], list[PhaseEvent]]:
        """
        EARLY: early speed advantage, initial positioning.
        Subclasses should call crash_engine.compute_first_phase_events() here.
        """
        scores = {}
        for r in runners:
            base  = r.early_speed_score / 10.0
            noise = self._noise(self.NOISE_SIGMA)
            scores[r.runner_id] = max(0.0, min(1.0, base + noise))

        events = self.crash_engine.compute_first_phase_events(runners, current_scores)
        return scores, events

    def _phase_positioning(
        self,
        runners: list[RunnerProfile],
        current_scores: dict[str, float],
        stamina: dict[str, float],
    ) -> tuple:
        """
        POSITIONING: runners settle into their race pattern.
        Track/distance suitability applies here.
        Returns (scores, events, updated_stamina) — C-04 consistent 3-tuple.
        Subclasses that modify stamina in this phase return updated copy.
        """
        scores = {}
        events = []
        for r in runners:
            base    = r.tactical_position_score / 10.0
            suit    = r.track_distance_suitability
            pattern = self._pattern_bonus(r, current_scores)
            noise   = self._noise(self.NOISE_SIGMA)
            scores[r.runner_id] = max(0.0, min(1.0, (base * 0.5 + suit * 0.3 + pattern * 0.2) + noise))
        return scores, events, dict(stamina)   # return copy — no modification in base

    def _phase_pressure(
        self,
        runners: list[RunnerProfile],
        current_scores: dict[str, float],
        stamina: dict[str, float],
        pace_type: str,
    ) -> tuple[dict[str, float], list[PhaseEvent], dict[str, float]]:
        """
        PRESSURE: sustained pace takes a toll on stamina.
        High-pressure runners lose stamina; strong runners hold better.
        """
        scores   = {}
        events   = []
        new_stam = dict(stamina)
        pace_factor = {"SLOW": 0.05, "MODERATE": 0.10, "FAST": 0.18, "HOT": 0.28}[pace_type]

        for r in runners:
            stam = stamina[r.runner_id]
            # Pressure resistance = mid_race_strength and low pressure_risk
            resistance = (r.mid_race_strength / 10.0) * (1.0 - r.pressure_risk_score * 0.5)
            stam_cost  = pace_factor * (1.0 - resistance * 0.7)
            new_stam[r.runner_id] = max(0.05, stam - stam_cost)

            # Phase score: how well they handle pressure
            scores[r.runner_id] = max(0.0, min(1.0, resistance * stam + self._noise(self.NOISE_SIGMA)))

        return scores, events, new_stam

    def _phase_mid(
        self,
        runners: list[RunnerProfile],
        current_scores: dict[str, float],
        stamina: dict[str, float],
    ) -> dict[str, float]:
        """MID: mid_race_strength × remaining stamina."""
        scores = {}
        for r in runners:
            stam  = stamina[r.runner_id]
            base  = r.mid_race_strength / 10.0
            noise = self._noise(self.NOISE_SIGMA)
            scores[r.runner_id] = max(0.0, min(1.0, base * stam + noise))
        return scores

    def _phase_late(
        self,
        runners: list[RunnerProfile],
        current_scores: dict[str, float],
        stamina: dict[str, float],
        pace_type: str,
    ) -> tuple[dict[str, float], bool]:
        """
        LATE: late strength × remaining stamina.
        Pace collapse can occur if leaders ran too hard.
        """
        scores = {}
        collapse = False

        # Check if leaders have very low stamina
        sorted_r = sorted(runners, key=lambda r: current_scores.get(r.runner_id, 0), reverse=True)
        leaders  = sorted_r[:2]
        avg_lead_stam = sum(stamina.get(r.runner_id, 0.5) for r in leaders) / max(len(leaders), 1)

        if pace_type in ("HOT", "FAST") and avg_lead_stam < 0.35:
            collapse = True

        for r in runners:
            stam    = stamina[r.runner_id]
            base    = r.late_strength / 10.0
            # Closers get a boost from pace collapse
            closer_bonus = 0.0
            if collapse and r.race_pattern in (RacePattern.CHASER, RacePattern.WIDE,
                                                RacePattern.TRAILER, RacePattern.MIDFIELD):
                closer_bonus = random.uniform(0.05, 0.15)
            # Leaders penalised by low stamina
            leader_fade = 0.0
            if collapse and r.race_pattern in (RacePattern.LEADER, RacePattern.RAILER,
                                                RacePattern.PARKED) and stam < 0.30:
                leader_fade = random.uniform(0.08, 0.25)

            noise = self._noise(self.NOISE_SIGMA)
            scores[r.runner_id] = max(0.0, min(1.0,
                base * stam + closer_bonus - leader_fade + noise
            ))
        return scores, collapse

    def _phase_finish(
        self,
        runners: list[RunnerProfile],
        current_scores: dict[str, float],
        stamina: dict[str, float],
    ) -> tuple[dict[str, float], list[PhaseEvent]]:
        """FINISH: final sprint. confidence_factor adds reliability."""
        scores = {}
        events = []
        for r in runners:
            stam  = stamina[r.runner_id]
            base  = (r.late_strength / 10.0) * 0.6 + (r.confidence_factor) * 0.4
            noise = self._noise(self.NOISE_SIGMA * 0.7)  # tighter noise at finish
            scores[r.runner_id] = max(0.0, min(1.0, base * (0.6 + stam * 0.4) + noise))
        return scores, events

    # ─────────────────────────────────────────────────────────────
    # SUBCLASS HOOKS (override for code-specific behaviour)
    # ─────────────────────────────────────────────────────────────

    @abstractmethod
    def _gate_advantage(self, runner: RunnerProfile) -> float:
        """Box/barrier advantage at start. Return multiplier (0.7–1.3)."""

    @abstractmethod
    def _pattern_bonus(
        self,
        runner: RunnerProfile,
        current_scores: dict[str, float],
    ) -> float:
        """Pattern-specific positioning bonus. Return 0–1."""

    # ─────────────────────────────────────────────────────────────
    # UTILITIES
    # ─────────────────────────────────────────────────────────────

    def _noise(self, sigma: float) -> float:
        """Bounded Gaussian noise. Realistic only — no extreme swings."""
        raw = random.gauss(0, sigma)
        return max(-self.NOISE_BOUND, min(self.NOISE_BOUND, raw))

    @staticmethod
    def _accumulate(
        total: dict[str, float],
        phase: dict[str, float],
        weight: float,
    ) -> dict[str, float]:
        return {rid: total.get(rid, 0) + phase.get(rid, 0) * weight
                for rid in total}

    @staticmethod
    def _classify_pace(
        runners: list[RunnerProfile],
        scores: dict[str, float],
    ) -> str:
        leaders  = [r for r in runners if r.race_pattern in
                    (RacePattern.LEADER, RacePattern.RAILER, RacePattern.PARKED)]
        if len(leaders) >= 4: return "HOT"
        if len(leaders) >= 3: return "FAST"
        fast_runners = [r for r in runners if r.early_speed_score >= 7.5]
        if len(fast_runners) >= 3: return "FAST"
        if len(leaders) >= 2 or len(fast_runners) >= 2: return "MODERATE"
        return "SLOW"

    @staticmethod
    def _empty_result(runners):
        return SingleSimResult(
            finish_order=[r.runner_id for r in runners],
            phase_scores={r.runner_id: 0.0 for r in runners},
            events=[],
            pace_type="MODERATE",
            leader_at_turn=None,
            winner_pattern=RacePattern.MIDFIELD,
            interference_count=0,
            collapse_occurred=False,
        )
