"""
collectors/dogs_upcoming_selector.py
=====================================
Selects the next upcoming greyhound race from the day board.

Rules:
  - Uses current AEST time
  - Chooses earliest race where race_time is still upcoming
  - Skips races already completed / captured (unless refresh=True)
  - Returns None (board idle) when no upcoming races remain

The selector does NOT call external APIs. It operates purely on the
DogsBoardEntry objects already collected by dogs_board_collector.

Logging prefix: [DOGS_SELECTOR]
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from models.dogs_race_packet import DogsBoardEntry

log = logging.getLogger(__name__)

_AEST = ZoneInfo("Australia/Sydney")

# Collection statuses that are considered "done" (skip unless refresh=True)
_DONE_STATUSES = frozenset({"completed", "analysed"})
# Page-level race statuses that mean the race is over
_OVER_RACE_STATUSES = frozenset({"resulted", "closed", "final"})


def select_next_upcoming(
    board: list[DogsBoardEntry],
    date_slug: str,
    *,
    refresh: bool = False,
) -> DogsBoardEntry | None:
    """
    Return the next upcoming race from the board.

    Args:
        board:      Sorted board entries (output of dogs_board_collector.collect_board)
        date_slug:  ISO date string the board was built for
        refresh:    If True, re-select even if already captured/analysed

    Returns:
        The earliest DogsBoardEntry that qualifies, or None if board is idle.
    """
    now_aest = datetime.now(_AEST)
    now_time = now_aest.time()

    log.info(
        f"[DOGS_SELECTOR] selecting next upcoming race "
        f"date={date_slug} now_aest={now_aest.strftime('%H:%M:%S')} "
        f"board_size={len(board)} refresh={refresh}"
    )

    candidates: list[DogsBoardEntry] = []

    for entry in board:
        # Skip entries from a different date
        if entry.date and entry.date != date_slug:
            continue

        # Skip if race is already done on the site
        rs = (entry.race_status or "").lower()
        if any(over in rs for over in _OVER_RACE_STATUSES):
            log.debug(
                f"[DOGS_SELECTOR] skip race_status={entry.race_status!r} "
                f"track={entry.track_name} R{entry.race_number}"
            )
            continue

        # Skip pipeline-completed races unless refresh requested
        if not refresh and entry.collection_status in _DONE_STATUSES:
            log.debug(
                f"[DOGS_SELECTOR] skip collection_status={entry.collection_status} "
                f"track={entry.track_name} R{entry.race_number}"
            )
            continue

        # Parse race_time to determine if it's still upcoming
        if entry.race_time:
            try:
                h, m = map(int, entry.race_time.split(":"))
                race_t = dtime(hour=h, minute=m)
                if race_t < now_time:
                    log.debug(
                        f"[DOGS_SELECTOR] skip past race_time={entry.race_time} "
                        f"track={entry.track_name} R{entry.race_number}"
                    )
                    continue
            except (ValueError, AttributeError):
                # No parseable time — include and let it through
                pass

        candidates.append(entry)

    if not candidates:
        log.info(
            f"[DOGS_SELECTOR] no upcoming races on board — board idle date={date_slug}"
        )
        return None

    # Pick the earliest candidate (board is already sorted by time)
    selected = candidates[0]
    log.info(
        f"[DOGS_SELECTOR] selected next race "
        f"track={selected.track_name} R{selected.race_number} "
        f"time={selected.race_time} link={selected.race_link}"
    )
    return selected
