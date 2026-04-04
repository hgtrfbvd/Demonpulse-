"""
models.py - DemonPulse core dataclasses.
"""

from dataclasses import dataclass, field


@dataclass
class Meeting:
    meeting_id: str
    date: str           # YYYY-MM-DD
    track: str
    code: str           # HORSE | HARNESS | GREYHOUND
    state: str
    country: str
    status: str         # scheduled | active | completed | abandoned
    race_count: int
    venue_name: str
    raw_source: str     # "oddspro"
    fetched_at: str     # ISO datetime


@dataclass
class Race:
    race_id: str
    meeting_id: str
    date: str
    track: str
    race_num: int
    code: str
    race_name: str
    distance: int
    grade: str
    condition: str
    jump_time: str | None       # ISO datetime string
    status: str                 # scheduled | open | near_jump | closed | settled | abandoned
    result_official: bool
    source: str                 # "oddspro"
    fetched_at: str
    blocked: bool
    block_reason: str | None


@dataclass
class Runner:
    runner_id: str
    race_id: str
    number: int | None
    box_num: int | None
    barrier: int | None
    name: str
    trainer: str
    jockey: str
    driver: str
    weight: float | None
    scratched: bool
    win_odds: float | None
    place_odds: float | None
    source: str
    fetched_at: str


@dataclass
class OddsSnapshot:
    snapshot_id: str    # auto uuid
    race_id: str
    source: str         # "oddspro" | "formfav"
    payload: str        # JSON string
    is_provisional: bool
    captured_at: str


@dataclass
class RaceResult:
    result_id: str
    race_id: str
    positions: str      # JSON: {"1": "runner_name", "2": "..."}
    dividends: str      # JSON: {"WIN": 5.40, ...}
    is_official: bool   # True only if confirmed by OddsPro
    provisional_source: str | None  # "formfav" if provisional
    confirmed_at: str | None
    fetched_at: str


@dataclass
class BlockedRace:
    race_id: str
    reason: str
    blocked_at: str
    resolved: bool
    resolved_at: str | None
