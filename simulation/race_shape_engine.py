"""
simulation/race_shape_engine.py
Analyses runner profiles to classify race tempo, project pace shape,
and flag collapse risk. Feeds into simulation and filters.
"""
from __future__ import annotations
from dataclasses import dataclass
from .models import RunnerProfile, RaceCode, RacePattern


@dataclass
class RaceShape:
    pace_type:        str       # SLOW / MODERATE / FAST / HOT
    tempo_band:       str       # CONTESTED / CLEAR / ONE_PACER
    collapse_risk:    str       # LOW / MODERATE / HIGH
    leader_count:     int
    leader_names:     list[str]
    stalker_count:    int
    chaser_count:     int
    projected_leader: str | None
    pace_score:       float     # 0–10, higher = faster pace
    closer_advantage: bool      # True if late runners have structural advantage
    notes:            list[str] = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


class RaceShapeEngine:
    """
    Analyses a field of runners and returns a RaceShape object.
    Used both before simulation (for context) and after (for validation).
    """

    _LEADER_PATTERNS  = {RacePattern.LEADER}
    _STALKER_PATTERNS = {RacePattern.STALKER, RacePattern.RAILER, RacePattern.PARKED}
    _CHASER_PATTERNS  = {RacePattern.CHASER, RacePattern.MIDFIELD, RacePattern.TRAILER, RacePattern.WIDE}

    # Distance thresholds for PACE SCORE computation
    # Note: ThoroughbredModule uses separate thresholds (1200m/2000m) for DISTANCE SPECIALTY.
    # These are intentionally different concepts:
    #   race_shape_engine: 500m = greyhound sprint; affects pace score curve
    #   thoroughbred_module: 1200m sprint / 2000m staying = distance suitability adjustment
    _SPRINT_THRESHOLD   = 500   # metres (greyhound sprint / short TB sprint)
    _STAYING_THRESHOLD  = 1600  # metres (TB middle vs staying)

    def __init__(self, race_code: RaceCode, distance_m: int):
        self.race_code  = race_code
        self.distance_m = distance_m

    def analyse(self, runners: list[RunnerProfile]) -> RaceShape:
        active = [r for r in runners if not r.scratched]
        if not active:
            return self._empty_shape()

        leaders  = [r for r in active if r.race_pattern in self._LEADER_PATTERNS]
        stalkers = [r for r in active if r.race_pattern in self._STALKER_PATTERNS]
        chasers  = [r for r in active if r.race_pattern in self._CHASER_PATTERNS]

        # Fast runners regardless of declared pattern
        fast_runners = [r for r in active if r.early_speed_score >= 7.5]

        # Effective leader count includes fast runners pushing from good positions
        effective_leaders = len(leaders) + sum(
            1 for r in fast_runners
            if r not in leaders and r.barrier_or_box <= len(active) // 2
        )

        pace_score  = self._compute_pace_score(active, fast_runners)
        pace_type   = self._classify_pace(pace_score, effective_leaders)
        tempo_band  = self._classify_tempo(leaders, stalkers, active)
        collapse_risk = self._compute_collapse_risk(pace_type, effective_leaders, pace_score)
        projected_leader = self._project_leader(active)
        closer_advantage = self._closer_advantage(pace_type, collapse_risk)
        notes = self._build_notes(active, pace_type, collapse_risk, effective_leaders, closer_advantage)

        return RaceShape(
            pace_type=pace_type,
            tempo_band=tempo_band,
            collapse_risk=collapse_risk,
            leader_count=effective_leaders,
            leader_names=[r.name for r in leaders],
            stalker_count=len(stalkers),
            chaser_count=len(chasers),
            projected_leader=projected_leader,
            pace_score=pace_score,
            closer_advantage=closer_advantage,
            notes=notes,
        )

    # ─────────────────────────────────────────────────────────────
    # PACE SCORE
    # ─────────────────────────────────────────────────────────────

    def _compute_pace_score(self, active: list[RunnerProfile], fast: list[RunnerProfile]) -> float:
        """
        Compute a 0–10 pace score for the field.
        High score = fast race; low score = dawdle.
        """
        if not active:
            return 5.0

        # Average early speed of top-3 fastest runners
        top3_speed = sorted([r.early_speed_score for r in active], reverse=True)[:3]
        avg_top_speed = sum(top3_speed) / len(top3_speed) if top3_speed else 5.0

        # Count of runners with pattern LEADER / RAILER
        on_pace_count = sum(1 for r in active if r.race_pattern in
                            (RacePattern.LEADER, RacePattern.RAILER, RacePattern.PARKED))

        # More on-pace runners = more pressure = higher score
        competition_bonus = min(2.0, on_pace_count * 0.4)

        # Distance factor: shorter races run faster relative to stamina
        if self.distance_m <= self._SPRINT_THRESHOLD:
            distance_factor = 1.10
        elif self.distance_m >= self._STAYING_THRESHOLD:
            distance_factor = 0.90
        else:
            distance_factor = 1.0

        return min(10.0, (avg_top_speed + competition_bonus) * distance_factor)

    def _classify_pace(self, pace_score: float, leader_count: int) -> str:
        if pace_score >= 8.5 or leader_count >= 4:
            return "HOT"
        elif pace_score >= 7.0 or leader_count >= 3:
            return "FAST"
        elif pace_score >= 5.0 or leader_count >= 2:
            return "MODERATE"
        else:
            return "SLOW"

    def _classify_tempo(
        self,
        leaders:  list[RunnerProfile],
        stalkers: list[RunnerProfile],
        active:   list[RunnerProfile],
    ) -> str:
        if len(leaders) >= 3:
            return "CONTESTED"
        elif len(leaders) == 1 and len(stalkers) <= 1:
            return "ONE_PACER"
        else:
            return "CLEAR"

    # ─────────────────────────────────────────────────────────────
    # COLLAPSE RISK
    # ─────────────────────────────────────────────────────────────

    def _compute_collapse_risk(self, pace_type: str, leader_count: int, pace_score: float) -> str:
        """
        High pace + many leaders on a long distance = HIGH collapse risk.
        """
        if pace_type == "HOT" and leader_count >= 3:
            return "HIGH"
        elif pace_type == "HOT" and self.distance_m >= 600:
            return "HIGH"
        elif pace_type == "FAST" and leader_count >= 3:
            return "MODERATE"
        elif pace_type == "FAST" and self.distance_m >= 1200:
            return "MODERATE"
        elif pace_type in ("SLOW", "MODERATE") and leader_count <= 1:
            return "LOW"
        else:
            return "LOW"

    # ─────────────────────────────────────────────────────────────
    # LEADERS & CLOSERS
    # ─────────────────────────────────────────────────────────────

    def _project_leader(self, active: list[RunnerProfile]) -> str | None:
        """Identify which runner is most likely to lead."""
        if not active:
            return None
        # Score: early_speed * start_consistency * box advantage factor
        best = max(
            active,
            key=lambda r: (
                r.early_speed_score * r.start_consistency
                * (1.2 if r.race_pattern in (RacePattern.LEADER, RacePattern.RAILER) else 0.8)
                * (1.0 if r.barrier_or_box <= 3 else 0.9)
            )
        )
        return best.name

    def _closer_advantage(self, pace_type: str, collapse_risk: str) -> bool:
        """
        Closers have structural advantage when pace is fast + collapse risk is high.
        """
        return pace_type in ("HOT", "FAST") and collapse_risk in ("HIGH", "MODERATE")

    # ─────────────────────────────────────────────────────────────
    # NOTES
    # ─────────────────────────────────────────────────────────────

    def _build_notes(
        self, active, pace_type, collapse_risk, leader_count, closer_advantage
    ) -> list[str]:
        notes = []
        if pace_type == "HOT":
            notes.append(f"HOT pace expected with {leader_count} on-pace runners — leaders under pressure.")
        elif pace_type == "FAST":
            notes.append(f"FAST pace projected — watch for leader fade late.")
        elif pace_type == "SLOW":
            notes.append("SLOW pace likely — leader may benefit from easy lead.")

        if collapse_risk == "HIGH":
            notes.append("HIGH collapse risk — closers structurally advantaged.")
        if closer_advantage:
            notes.append("Late runners may benefit from pace collapse.")

        # Code-specific notes
        if self.race_code == RaceCode.GREYHOUND:
            railers = [r for r in active if r.race_pattern == RacePattern.RAILER]
            if railers:
                notes.append(f"Box railers: {', '.join(r.name for r in railers)} — box draw critical.")
        elif self.race_code == RaceCode.HARNESS:
            parked = [r for r in active if r.race_pattern == RacePattern.PARKED]
            if parked:
                notes.append(f"Parked runners will bleed energy: {', '.join(r.name for r in parked)}.")

        return notes

    def _empty_shape(self) -> RaceShape:
        return RaceShape(
            pace_type="MODERATE", tempo_band="CLEAR", collapse_risk="LOW",
            leader_count=0, leader_names=[], stalker_count=0, chaser_count=0,
            projected_leader=None, pace_score=5.0, closer_advantage=False,
        )
