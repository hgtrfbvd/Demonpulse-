"""
services/dogs_board_service.py
================================
Orchestrates the full dogs browser collection pipeline for a given date.

Pipeline steps:
  1. Open board page → collect all day's races (dogs_board_collector)
  2. For each race: capture race page → parse → store

Used by pipeline.full_sweep() as the GREYHOUND data path.
Uses ONE consistent source (thedogs.com.au) throughout.
No Claude, no mixed APIs, no cross-source enrichment.

Logging prefix: [DOGS_SERVICE]
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
_AEST = ZoneInfo("Australia/Sydney")


def collect_greyhound_board(today: str) -> list[dict]:
    """
    Collect all greyhound races for today from thedogs.com.au.

    This is the primary entry point called by pipeline.full_sweep().

    Returns:
        List of normalised race dicts ready for pipeline._store_race().
        Includes _runners key for runner upsert.
        Returns empty list on total failure — never raises.
    """
    log.info(f"[DOGS_SERVICE] starting board collection date={today} source=thedogs.com.au")

    from collectors.dogs_board_collector import collect_board
    from collectors.dogs_race_capturer import capture_race
    from parsers.dogs_source_parser import parse_race_page, normalise_for_db
    from features import compute_greyhound_derived

    # Step 1: collect the day board
    board_entries = collect_board(today)
    if not board_entries:
        log.warning(
            f"[DOGS_SERVICE] board collection returned 0 entries date={today} — "
            f"board empty or page unavailable"
        )
        return []

    log.info(f"[DOGS_SERVICE] board collected entries={len(board_entries)} date={today}")

    normalised_races: list[dict] = []
    capture_ts = datetime.utcnow().isoformat()

    for entry in board_entries:
        entry_dict = entry.to_dict()
        track = entry.track_name
        race_num = entry.race_number
        race_link = entry.race_link

        if not race_link:
            log.warning(
                f"[DOGS_SERVICE] skip entry with no race_link "
                f"track={track} R{race_num}"
            )
            continue

        log.info(
            f"[DOGS_SERVICE] capturing race track={track} R{race_num} "
            f"time={entry.race_time} link={race_link}"
        )

        # Step 2: capture the race page
        capture = capture_race(
            race_link=race_link,
            track_name=track,
            race_number=race_num or 0,
            date_slug=today,
        )

        if not capture.get("ok"):
            log.error(
                f"[DOGS_SERVICE] capture failed track={track} R{race_num} "
                f"error={capture.get('error')}"
            )
            continue

        # Step 3: parse the captured HTML
        raw = parse_race_page(
            html=capture["html"],
            source_url=capture["source_url"],
            date_slug=today,
            board_entry=entry_dict,
        )

        if raw is None:
            log.error(
                f"[DOGS_SERVICE] parse returned None track={track} R{race_num}"
            )
            continue

        # Step 4: compute derived features (pure calculation, no external calls)
        try:
            raw["derived"] = compute_greyhound_derived(raw)
        except Exception as exc:
            log.warning(f"[DOGS_SERVICE] derived compute failed track={track} R{race_num}: {exc}")
            raw["derived"] = {}

        # Step 5: normalise to DB schema
        try:
            race_dict = normalise_for_db(raw, today)
        except Exception as exc:
            log.error(
                f"[DOGS_SERVICE] normalise failed track={track} R{race_num}: {exc}",
                exc_info=True,
            )
            continue

        # Attach capture metadata
        race_dict.setdefault("raw_json", {})
        race_dict["raw_json"]["_source_url"] = capture["source_url"]
        race_dict["raw_json"]["_screenshot_path"] = capture.get("screenshot_path")
        race_dict["raw_json"]["_html_path"] = capture.get("html_path")
        race_dict["raw_json"]["_board_capture_ts"] = capture_ts
        race_dict["raw_json"]["_race_capture_ts"] = datetime.utcnow().isoformat()
        race_dict["raw_json"]["_parse_errors"] = raw.get("_parse_errors", [])

        normalised_races.append(race_dict)
        log.info(
            f"[DOGS_SERVICE] race ready for storage track={track} R{race_num} "
            f"source=thedogs_browser"
        )

    log.info(
        f"[DOGS_SERVICE] board collection complete date={today} "
        f"races_collected={len(normalised_races)} board_entries={len(board_entries)}"
    )
    return normalised_races
