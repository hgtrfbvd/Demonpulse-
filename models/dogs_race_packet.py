"""
models/dogs_race_packet.py
==========================
Internal schema for a collected greyhound race packet.

This is the single authoritative data shape used by:
  - dogs_source_parser.py  (producer)
  - pipeline.py            (storage)
  - dashboard_dogs.py      (display)
  - simulation/greyhound_module.py (betting engine consumer)

All fields sourced from the same browser-rendered page.
No cross-source enrichment. Missing fields are stored as None.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PacketStatus(str, Enum):
    CAPTURED = "CAPTURED"
    EXTRACTED = "EXTRACTED"
    ANALYSED = "ANALYSED"
    SETTLED = "SETTLED"


@dataclass
class DogsRunnerPacket:
    """Per-runner data extracted from the race page."""
    box: int | None = None
    runner_name: str | None = None
    trainer: str | None = None
    weight: float | None = None
    scratched: bool = False

    # Performance
    last_start_position: int | None = None
    last_start_time: str | None = None
    best_time_distance_match: str | None = None
    avg_time_last_3: str | None = None
    split_time: str | None = None
    last4: str | None = None

    # Career stats
    career_starts: int | None = None
    career_wins: int | None = None
    career_places: int | None = None
    win_pct: float | None = None
    place_pct: float | None = None
    prize_money_career: str | None = None

    # Derived ratings (computed by features.py)
    early_speed_rating: int | None = None
    finish_strength_rating: float | None = None
    consistency_rating: float | None = None

    # Track / box / class
    box_win_percent: float | None = None
    track_distance_record: str | None = None
    class_level: str | None = None

    # Market
    odds: str | None = None
    market_rank: int | None = None

    def to_dict(self) -> dict:
        return {
            "box": self.box,
            "name": self.runner_name,
            "trainer": self.trainer,
            "weight": self.weight,
            "scratched": self.scratched,
            "last_start_position": self.last_start_position,
            "last_start_time": self.last_start_time,
            "best_time_distance_match": self.best_time_distance_match,
            "avg_time_last_3": self.avg_time_last_3,
            "split_time": self.split_time,
            "last4": self.last4,
            "career_starts": self.career_starts,
            "career_wins": self.career_wins,
            "career_places": self.career_places,
            "win_pct": self.win_pct,
            "place_pct": self.place_pct,
            "prize_money_career": self.prize_money_career,
            "early_speed_rating": self.early_speed_rating,
            "finish_strength_rating": self.finish_strength_rating,
            "consistency_rating": self.consistency_rating,
            "box_win_percent": self.box_win_percent,
            "track_distance_record": self.track_distance_record,
            "class_level": self.class_level,
            "odds": self.odds,
            "market_rank": self.market_rank,
        }


@dataclass
class DogsRacePacket:
    """
    Complete greyhound race packet from a single browser session.
    Everything in this packet comes from the same source page.
    """
    # Source tracking
    source_name: str = "thedogs.com.au"
    source_url: str | None = None
    board_capture_timestamp: str | None = None
    race_capture_timestamp: str | None = None
    board_screenshot_path: str | None = None
    race_screenshot_path: str | None = None
    raw_html_path: str | None = None
    extraction_status: str = "pending"  # pending|captured|parsed|failed
    parse_errors: list[str] = field(default_factory=list)

    # Race identity key (set after parsing)
    race_uid: str | None = None

    # Race-level fields
    track_name: str | None = None
    state: str | None = None
    date: str | None = None
    race_number: int | None = None
    race_time: str | None = None
    distance_m: int | None = None
    grade: str | None = None
    race_type: str | None = None
    track_condition: str | None = None
    weather: str | None = None
    prize_money: str | None = None

    # Race shape inputs (derived)
    num_leaders: int | None = None
    num_mid_pack: int | None = None
    num_backmarkers: int | None = None
    tempo_rating: float | None = None
    collision_risk_score: float | None = None
    first_bend_distance: int | None = None

    # Runners
    runners: list[DogsRunnerPacket] = field(default_factory=list)

    # Status lifecycle
    status: str = "CAPTURED"  # CAPTURED | EXTRACTED | ANALYSED | SETTLED

    # Screenshot paths keyed by name
    screenshots: dict[str, str] = field(default_factory=dict)  # board, header, expert_form, box_history, results

    # Structured extracted data from OCR/extraction service
    extracted_data: dict[str, Any] = field(default_factory=dict)
    # Should contain: runners[], form_lines[], times/splits, box_history_metrics, derived_features

    # V7 analysis engine output
    engine_output: dict[str, Any] = field(default_factory=dict)
    # Should contain: tempo (FAST|MODERATE|SLOW), primary, secondary, confidence, notes

    # Monte Carlo simulation output
    simulation_output: dict[str, Any] = field(default_factory=dict)
    # Should contain: win_probabilities, top3_probabilities, most_likely_scenario, chaos_rating, lead_at_first_bend_pct

    # Race result (post-race)
    result: dict[str, Any] = field(default_factory=dict)
    # Should contain: finishing_order, margins, official_time

    # Learning engine output
    learning: dict[str, Any] = field(default_factory=dict)
    # Should contain: error_tags, adjustments, notes

    # Raw fragments (for debug/fallback)
    raw_fragments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "source_url": self.source_url,
            "board_capture_timestamp": self.board_capture_timestamp,
            "race_capture_timestamp": self.race_capture_timestamp,
            "board_screenshot_path": self.board_screenshot_path,
            "race_screenshot_path": self.race_screenshot_path,
            "raw_html_path": self.raw_html_path,
            "extraction_status": self.extraction_status,
            "parse_errors": self.parse_errors,
            "race_uid": self.race_uid,
            "track_name": self.track_name,
            "state": self.state,
            "date": self.date,
            "race_number": self.race_number,
            "race_time": self.race_time,
            "distance_m": self.distance_m,
            "grade": self.grade,
            "race_type": self.race_type,
            "track_condition": self.track_condition,
            "weather": self.weather,
            "prize_money": self.prize_money,
            "num_leaders": self.num_leaders,
            "num_mid_pack": self.num_mid_pack,
            "num_backmarkers": self.num_backmarkers,
            "tempo_rating": self.tempo_rating,
            "collision_risk_score": self.collision_risk_score,
            "first_bend_distance": self.first_bend_distance,
            "runners": [r.to_dict() for r in self.runners],
            "status": self.status,
            "screenshots": self.screenshots,
            "extracted_data": self.extracted_data,
            "engine_output": self.engine_output,
            "simulation_output": self.simulation_output,
            "result": self.result,
            "learning": self.learning,
            "raw_fragments": self.raw_fragments,
        }


@dataclass
class DogsBoardEntry:
    """A single entry on the day's race board."""
    track_name: str
    state: str | None = None
    date: str | None = None
    race_number: int | None = None
    race_time: str | None = None
    race_status: str | None = None  # e.g. 'Open', 'Closed', 'Resulted'
    race_link: str | None = None
    # Pipeline status tracking
    collection_status: str = "queued"  # queued|processing|captured|analysed|completed|failed

    def to_dict(self) -> dict:
        return {
            "track_name": self.track_name,
            "state": self.state,
            "date": self.date,
            "race_number": self.race_number,
            "race_time": self.race_time,
            "race_status": self.race_status,
            "race_link": self.race_link,
            "collection_status": self.collection_status,
        }
