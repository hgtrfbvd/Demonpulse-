"""
migrations.py - DemonPulse database schema migrations.
Run once at startup via init_db() or directly.
"""

import os
import sqlite3
import logging

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    track TEXT NOT NULL,
    code TEXT NOT NULL,
    state TEXT,
    country TEXT,
    status TEXT DEFAULT 'scheduled',
    race_count INTEGER DEFAULT 0,
    venue_name TEXT,
    raw_source TEXT DEFAULT 'oddspro',
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS races (
    race_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    date TEXT NOT NULL,
    track TEXT NOT NULL,
    race_num INTEGER NOT NULL,
    code TEXT NOT NULL,
    race_name TEXT,
    distance INTEGER,
    grade TEXT,
    condition TEXT,
    jump_time TEXT,
    status TEXT DEFAULT 'scheduled',
    result_official INTEGER DEFAULT 0,
    source TEXT DEFAULT 'oddspro',
    fetched_at TEXT NOT NULL,
    blocked INTEGER DEFAULT 0,
    block_reason TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE INDEX IF NOT EXISTS idx_races_date ON races(date);
CREATE INDEX IF NOT EXISTS idx_races_meeting ON races(meeting_id);
CREATE INDEX IF NOT EXISTS idx_races_jump ON races(jump_time);
CREATE INDEX IF NOT EXISTS idx_races_status ON races(status);

CREATE TABLE IF NOT EXISTS runners (
    runner_id TEXT PRIMARY KEY,
    race_id TEXT NOT NULL,
    number INTEGER,
    box_num INTEGER,
    barrier INTEGER,
    name TEXT NOT NULL,
    trainer TEXT,
    jockey TEXT,
    driver TEXT,
    weight REAL,
    scratched INTEGER DEFAULT 0,
    win_odds REAL,
    place_odds REAL,
    source TEXT DEFAULT 'oddspro',
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (race_id) REFERENCES races(race_id)
);

CREATE INDEX IF NOT EXISTS idx_runners_race ON runners(race_id);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    race_id TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    is_provisional INTEGER DEFAULT 0,
    captured_at TEXT NOT NULL,
    FOREIGN KEY (race_id) REFERENCES races(race_id)
);

CREATE INDEX IF NOT EXISTS idx_odds_race ON odds_snapshots(race_id);
CREATE INDEX IF NOT EXISTS idx_odds_source ON odds_snapshots(source);

CREATE TABLE IF NOT EXISTS race_results (
    result_id TEXT PRIMARY KEY,
    race_id TEXT NOT NULL UNIQUE,
    positions TEXT NOT NULL,
    dividends TEXT,
    is_official INTEGER DEFAULT 0,
    provisional_source TEXT,
    confirmed_at TEXT,
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (race_id) REFERENCES races(race_id)
);

CREATE TABLE IF NOT EXISTS blocked_races (
    race_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    blocked_at TEXT NOT NULL,
    resolved INTEGER DEFAULT 0,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS provisional_odds (
    race_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_payloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_entity ON raw_payloads(entity_type, entity_id);
"""


def run_migrations(db_path: str | None = None) -> None:
    if db_path is None:
        db_path = os.environ.get("DATABASE_PATH", "./demonpulse.db")
    log.info(f"Running migrations on {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        log.info("Migrations complete")
    finally:
        conn.close()
