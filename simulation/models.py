"""
simulation/models.py
Shared dataclasses for the DemonPulse V9 Monte Carlo simulation engine.
All inter-module data exchange goes through these types.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ─────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────

class RaceCode(str, Enum):
    GREYHOUND    = "greyhound"
    THOROUGHBRED = "thoroughbred"
    HARNESS      = "harness"


# CF-15: Translation table from canonical DB/config race codes (VALID_RACE_CODES in
# supabase_config.py) to simulation RaceCode values.
# supabase_config uses "GALLOPS" for thoroughbred horse races.
RACE_CODE_ALIASES: dict[str, "RaceCode"] = {
    # Canonical simulation values (pass-through)
    "greyhound":    RaceCode.GREYHOUND,
    "thoroughbred": RaceCode.THOROUGHBRED,
    "harness":      RaceCode.HARNESS,
    # DB / config uppercase values
    "GREYHOUND":    RaceCode.GREYHOUND,
    "GALLOPS":      RaceCode.THOROUGHBRED,   # "GALLOPS" is the DB name for thoroughbreds
    "HARNESS":      RaceCode.HARNESS,
    # Alternate spellings encountered in connector payloads
    "THOROUGHBRED": RaceCode.THOROUGHBRED,
}


def normalize_race_code(raw: str) -> "RaceCode":
    """
    Translate any known race-code string to a canonical RaceCode enum.

    Accepts both DB-layer codes (GALLOPS, GREYHOUND, HARNESS) and
    simulation-layer codes (thoroughbred, greyhound, harness).

    Raises ValueError for unknown values so callers cannot silently
    use a wrong code.
    """
    if raw is None:
        raise ValueError("Race code must not be None")
    code = RACE_CODE_ALIASES.get(raw)
    if code is None:
        code = RACE_CODE_ALIASES.get(raw.upper())
    if code is None:
        raise ValueError(
            f"Unknown race code '{raw}'. "
            f"Valid values: GREYHOUND, GALLOPS (thoroughbred), HARNESS"
        )
    return code

class RacePattern(str, Enum):
    LEADER   = "LEADER"
    STALKER  = "STALKER"
    MIDFIELD = "MIDFIELD"
    CHASER   = "CHASER"
    WIDE     = "WIDE"
    RAILER   = "RAILER"     # greyhound-specific
    PARKED   = "PARKED"     # harness-specific
    TRAILER  = "TRAILER"    # harness-specific

class ChaosRating(str, Enum):
    LOW      = "LOW"
    MODERATE = "MODERATE"
    HIGH     = "HIGH"
    EXTREME  = "EXTREME"

class ConfidenceRating(str, Enum):
    HIGH     = "HIGH"
    SOLID    = "SOLID"
    MODERATE = "MODERATE"
    LOW      = "LOW"

class Decision(str, Enum):
    BET       = "BET"
    SMALL_BET = "SMALL_BET"
    SAVE_BET  = "SAVE_BET"
    CAUTION   = "CAUTION"
    PASS      = "PASS"

class FilterMode(str, Enum):
    HARD          = "hard"
    SOFT          = "soft"
    INFORMATIONAL = "informational"


# ─────────────────────────────────────────────────────────────────
# RUNNER / RACE INPUT
# ─────────────────────────────────────────────────────────────────

@dataclass
class RunnerProfile:
    """Universal runner model — all race codes."""
    runner_id:                str
    name:                     str
    barrier_or_box:           int            # box 1-8 (dogs) or barrier 1-N (horses/harness)

    # Speed & pace attributes (0–10 scale)
    early_speed_score:        float = 5.0
    start_consistency:        float = 0.7    # 0–1 probability of clean start
    tactical_position_score:  float = 5.0
    mid_race_strength:        float = 5.0
    late_strength:            float = 5.0
    stamina_score:            float = 5.0

    # Race shape / style
    race_pattern:             RacePattern = RacePattern.MIDFIELD
    track_distance_suitability: float = 0.75  # 0–1

    # Risk / reliability
    pressure_risk_score:      float = 0.3    # 0–1 (higher = more risk under pressure)
    confidence_factor:        float = 0.7    # 0–1 (form reliability)

    # Market
    market_odds:              float = 5.0
    scratched:                bool  = False

    # Code-specific extras (populated by modules as needed)
    extra: dict = field(default_factory=dict)

    @property
    def market_implied_prob(self) -> float:
        """Market-implied win probability from odds."""
        if self.market_odds <= 1.0:
            return 0.99
        return 1.0 / self.market_odds


@dataclass
class RaceMeta:
    """Race context passed into every simulation."""
    race_uid:    str
    track:       str
    race_code:   RaceCode
    distance_m:  int           # in metres
    grade:       str  = ""
    condition:   str  = "GOOD" # track condition: FIRM/GOOD/DEAD/SOFT/HEAVY/SLOW
    field_size:  int  = 8
    n_sims:      int  = 200    # number of simulations to run
    extra:       dict = field(default_factory=dict)   # code-specific extras


# ─────────────────────────────────────────────────────────────────
# PER-SIMULATION OUTPUT
# ─────────────────────────────────────────────────────────────────

@dataclass
class PhaseEvent:
    """A notable event that occurred during a simulation phase."""
    phase:       str
    runner_id:   str
    event_type:  str    # e.g. "interference", "fade", "surge", "blocked"
    severity:    float  # 0–1 impact on race outcome


@dataclass
class SingleSimResult:
    """Output of one full race simulation."""
    finish_order:        list[str]           # runner_ids in finish order
    phase_scores:        dict[str, float]    # runner_id → cumulative score
    events:              list[PhaseEvent]    # notable race events
    pace_type:           str                 # SLOW / MODERATE / FAST / HOT
    leader_at_turn:      str | None          # runner_id leading at mid-race
    winner_pattern:      RacePattern         # how the winner ran
    interference_count:  int
    collapse_occurred:   bool                # pace collapse in final stages


# ─────────────────────────────────────────────────────────────────
# AGGREGATED RESULTS
# ─────────────────────────────────────────────────────────────────

@dataclass
class RunnerStats:
    """Per-runner statistics after aggregating N simulations."""
    runner_id:          str
    name:               str
    barrier_or_box:     int
    win_count:          int   = 0
    place_count:        int   = 0    # top 3
    total_finish_pos:   float = 0.0
    n_sims:             int   = 0

    # Derived (computed by aggregator)
    win_pct:            float = 0.0
    place_pct:          float = 0.0
    avg_finish:         float = 0.0
    sim_edge:           float = 0.0  # sim_win_pct – market_implied_prob

    # Flags
    is_false_favourite: bool  = False
    is_hidden_value:    bool  = False
    is_vulnerable:      bool  = False
    is_best_map:        bool  = False


@dataclass
class AggregatedResult:
    """Full aggregated output from N simulations."""
    race_uid:           str
    race_code:          RaceCode
    n_sims:             int
    runners:            list[RunnerStats]

    # Race-level stats
    chaos_rating:       ChaosRating       = ChaosRating.MODERATE
    confidence_rating:  ConfidenceRating  = ConfidenceRating.MODERATE
    pace_type:          str               = "MODERATE"
    collapse_risk:      str               = "LOW"
    interference_rate:  float             = 0.0    # average events per sim

    most_common_scenario: str             = ""
    leader_frequency:     dict[str, float] = field(default_factory=dict)  # runner_id → %

    # Top selections (by win_pct)
    top_runner:         RunnerStats | None = None
    second_runner:      RunnerStats | None = None

    raw_sims:           list[SingleSimResult] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# FILTER SYSTEM
# ─────────────────────────────────────────────────────────────────

@dataclass
class FilterConfig:
    """Configuration for one filter — loaded from JSON or DB."""
    filter_id:   str
    name:        str
    enabled:     bool
    mode:        FilterMode
    weight:      float              # 0–1, only used for soft filters
    threshold:   dict[str, Any]     # filter-specific thresholds
    applies_to:  list[str]          # ["greyhound","horse","harness","all"]
    description: str
    filter_class: str               # fully-qualified class name, e.g. "filters.hard_block_filters.ExtremeChaosFilter"

    def is_applicable(self, race_code: RaceCode) -> bool:
        return "all" in self.applies_to or race_code.value in self.applies_to


@dataclass
class FilterResult:
    """Result of evaluating one filter."""
    filter_id:         str
    filter_name:       str
    mode:              FilterMode
    triggered:         bool
    confidence_delta:  float   # positive = boost, negative = reduction
    reason:            str
    details:           dict[str, Any] = field(default_factory=dict)


@dataclass
class FilterContext:
    """Everything a filter needs to make its decision."""
    aggregated:  AggregatedResult
    race_meta:   RaceMeta
    runners:     list[RunnerProfile]
    top_runner:  RunnerProfile | None


@dataclass
class FilterDecision:
    """Final output of the full filter pipeline."""
    decision:          Decision
    confidence_score:  float              # final 0–1 confidence
    triggered_filters: list[FilterResult]
    passed_filters:    list[FilterResult]
    hard_blocked_by:   FilterResult | None
    reasoning:         list[str]          # human-readable reasoning chain
    top_runner_id:     str | None
    top_runner_name:   str | None


# ─────────────────────────────────────────────────────────────────
# EXPERT GUIDE OUTPUT
# ─────────────────────────────────────────────────────────────────

@dataclass
class ExpertGuide:
    """Complete expert guide output — the final deliverable."""
    race_uid:          str
    race_code:         RaceCode
    track:             str
    decision:          Decision
    confidence_score:  float
    chaos_rating:      ChaosRating
    confidence_rating: ConfidenceRating

    # Sections
    simulation_summary:     str
    projected_race_run:     str
    race_shape_insights:    str
    runner_impact_notes:    list[dict]   # [{runner, note, flag}]
    filter_results_panel:   FilterDecision
    final_decision_note:    str

    # Raw data
    aggregated:             AggregatedResult
