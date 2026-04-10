"""
services/dogs_capture_service.py
==================================
On-demand single-race refresh for greyhound races.

Used by api/race_routes.py live_race() endpoint to refresh stale race data.
Replaces the old ClaudeScraper.fetch_single_race() for GREYHOUND races.

One consistent source: thedogs.com.au
No Claude, no mixed APIs.

Logging prefix: [DOGS_CAPTURE_SVC]
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)


def refresh_race(race_uid: str, race: dict) -> dict | None:
    """
    Re-capture and re-parse a single greyhound race from thedogs.com.au.

    Args:
        race_uid:  Unique race identifier
        race:      Stored race dict from database (must have track, race_num, date)

    Returns:
        Normalised race dict ready for _store_race(), or None on failure.
        Never raises.
    """
    track = race.get("track") or ""
    race_num = int(race.get("race_num") or 0)
    race_date = race.get("date") or datetime.utcnow().date().isoformat()

    # Build the race link from track slug and date
    slug = track.lower().replace(" ", "-")
    race_link = f"https://www.thedogs.com.au/racing/{slug}/{race_date}/{race_num}"

    log.info(
        f"[DOGS_CAPTURE_SVC] refreshing race "
        f"race_uid={race_uid} track={track} R{race_num} date={race_date}"
    )

    try:
        from collectors.dogs_race_capturer import capture_race
        from parsers.dogs_source_parser import parse_race_page, normalise_for_db
        from features import compute_greyhound_derived

        capture = capture_race(
            race_link=race_link,
            track_name=track,
            race_number=race_num,
            date_slug=race_date,
        )

        if not capture.get("ok"):
            log.warning(
                f"[DOGS_CAPTURE_SVC] capture failed race_uid={race_uid} "
                f"error={capture.get('error')}"
            )
            return None

        raw = parse_race_page(
            html=capture["html"],
            source_url=capture["source_url"],
            date_slug=race_date,
            board_entry={
                "track_name": track,
                "state": race.get("state"),
                "date": race_date,
                "race_number": race_num,
                "race_time": None,
            },
        )

        if raw is None:
            log.warning(f"[DOGS_CAPTURE_SVC] parse returned None race_uid={race_uid}")
            return None

        try:
            raw["derived"] = compute_greyhound_derived(raw)
        except Exception as exc:
            log.warning(f"[DOGS_CAPTURE_SVC] derived compute failed: {exc}")
            raw["derived"] = {}

        race_dict = normalise_for_db(raw, race_date)
        race_dict["raw_json"]["_screenshot_path"] = capture.get("screenshot_path")
        race_dict["raw_json"]["_html_path"] = capture.get("html_path")
        race_dict["raw_json"]["_refreshed_at"] = datetime.utcnow().isoformat()

        log.info(
            f"[DOGS_CAPTURE_SVC] refresh ok race_uid={race_uid} source=thedogs_browser"
        )
        return race_dict

    except Exception as exc:
        log.error(
            f"[DOGS_CAPTURE_SVC] refresh_race raised: {exc}",
            exc_info=True,
        )
        return None
