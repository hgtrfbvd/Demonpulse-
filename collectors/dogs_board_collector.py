"""
collectors/dogs_board_collector.py
===================================
Browser-based collector for the thedogs.com.au race guide.

Launches a headless Playwright browser, navigates to the main dogs race
guide page, waits for JS-rendered content, and extracts all visible
meetings/races for the day.

Source:
    https://www.thedogs.com.au/racing/{date}?trial=false

Returns:
    list[DogsBoardEntry]  — all races found for the day, sorted by race_time

Error handling:
    - On load failure: saves screenshot + HTML, returns empty list
    - On selector timeout: retries up to MAX_RETRIES times
    - Never injects fake/stale rows

Logging prefix: [DOGS_BOARD]
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from models.dogs_race_packet import DogsBoardEntry

log = logging.getLogger(__name__)

_AEST = ZoneInfo("Australia/Sydney")
_BOARD_BASE_URL = "https://www.thedogs.com.au/racing"
_SCREENSHOT_DIR = os.environ.get("DOGS_SCREENSHOT_DIR", "/tmp/demonpulse_dogs")
_MAX_RETRIES = int(os.environ.get("DOGS_BOARD_MAX_RETRIES", "3"))
_PAGE_TIMEOUT_MS = int(os.environ.get("DOGS_PAGE_TIMEOUT_MS", "30000"))
_WAIT_SELECTOR = "main"  # wait for main content to render

# State slug patterns visible on the schedule page
_STATE_PATTERNS: dict[str, list[str]] = {
    "QLD": ["queensland", "qld"],
    "NSW": ["new south wales", "nsw"],
    "VIC": ["victoria", "vic"],
    "SA": ["south australia", "sa"],
    "WA": ["western australia", "wa"],
    "TAS": ["tasmania", "tas"],
    "NT": ["northern territory", "nt"],
    "ACT": ["act"],
    "NZ": ["new zealand", "nz"],
}


def _ensure_screenshot_dir() -> None:
    os.makedirs(_SCREENSHOT_DIR, exist_ok=True)


def _save_failure_artifacts(
    page,  # Playwright Page
    prefix: str,
) -> tuple[str | None, str | None]:
    """Save screenshot + HTML on failure. Returns (screenshot_path, html_path)."""
    _ensure_screenshot_dir()
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    shot_path = os.path.join(_SCREENSHOT_DIR, f"{prefix}_fail_{ts}.png")
    html_path = os.path.join(_SCREENSHOT_DIR, f"{prefix}_fail_{ts}.html")
    try:
        page.screenshot(path=shot_path, full_page=True)
        log.info(f"[DOGS_BOARD] failure screenshot saved: {shot_path}")
    except Exception as exc:
        log.warning(f"[DOGS_BOARD] screenshot save failed: {exc}")
        shot_path = None
    try:
        html = page.content()
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        log.info(f"[DOGS_BOARD] failure HTML saved: {html_path}")
    except Exception as exc:
        log.warning(f"[DOGS_BOARD] HTML save failed: {exc}")
        html_path = None
    return shot_path, html_path


def _infer_state(text: str) -> str | None:
    """Infer Australian/NZ state from visible text."""
    lower = text.lower()
    for code, patterns in _STATE_PATTERNS.items():
        if any(p in lower for p in patterns):
            return code
    return None


def _parse_time(text: str) -> str | None:
    """Extract HH:MM from a time string like '2:15pm' or '14:15'."""
    text = text.strip()
    m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", text, re.IGNORECASE)
    if not m:
        return None
    hour, minute, period = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if period == "pm" and hour != 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def collect_board(date_slug: str) -> list[DogsBoardEntry]:
    """
    Open the thedogs.com.au day guide page and collect all races.

    Args:
        date_slug: ISO date string, e.g. "2026-04-10"

    Returns:
        Sorted list of DogsBoardEntry objects (ascending race_time).
        Returns empty list on failure — never raises.
    """
    url = f"{_BOARD_BASE_URL}/{date_slug}?trial=false"
    log.info(f"[DOGS_BOARD] collecting board date={date_slug} url={url}")

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("[DOGS_BOARD] playwright not installed — cannot collect board")
        return []

    entries: list[DogsBoardEntry] = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(_PAGE_TIMEOUT_MS)

            attempt = 0
            loaded = False
            while attempt < _MAX_RETRIES and not loaded:
                attempt += 1
                try:
                    log.info(f"[DOGS_BOARD] navigate attempt={attempt} url={url}")
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_selector(_WAIT_SELECTOR, timeout=_PAGE_TIMEOUT_MS)
                    loaded = True
                except PWTimeout:
                    log.warning(
                        f"[DOGS_BOARD] page load timeout attempt={attempt}/{_MAX_RETRIES}"
                    )
                except Exception as exc:
                    log.warning(
                        f"[DOGS_BOARD] page load error attempt={attempt}/{_MAX_RETRIES}: {exc}"
                    )

            if not loaded:
                log.error(
                    f"[DOGS_BOARD] all {_MAX_RETRIES} load attempts failed — "
                    f"saving failure artifacts date={date_slug}"
                )
                _save_failure_artifacts(page, "board")
                context.close()
                browser.close()
                return []

            # Take a board screenshot for audit/debug
            _ensure_screenshot_dir()
            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            board_shot = os.path.join(_SCREENSHOT_DIR, f"board_{date_slug}_{ts}.png")
            try:
                page.screenshot(path=board_shot, full_page=True)
                log.info(f"[DOGS_BOARD] board screenshot saved: {board_shot}")
            except Exception:
                board_shot = None

            html = page.content()
            entries = _extract_board_entries(html, date_slug, url)

            if not entries:
                log.warning(
                    f"[DOGS_BOARD] DOM extraction returned 0 entries — "
                    f"saving fallback artifacts date={date_slug}"
                )
                _save_failure_artifacts(page, "board_empty")

            context.close()
            browser.close()

    except Exception as exc:
        log.error(f"[DOGS_BOARD] collect_board failed: {exc}", exc_info=True)
        return []

    # Sort by race_time ascending
    def _sort_key(e: DogsBoardEntry) -> str:
        return f"{e.date or ''}_{e.race_time or '99:99'}_{e.race_number or 99:04d}"

    entries.sort(key=_sort_key)
    log.info(
        f"[DOGS_BOARD] board collected date={date_slug} entries={len(entries)} "
        f"source=thedogs.com.au"
    )
    return entries


def _extract_board_entries(html: str, date_slug: str, page_url: str) -> list[DogsBoardEntry]:
    """
    Parse rendered page HTML and extract board entries.
    Uses BeautifulSoup on the JS-rendered DOM.

    Attempts multiple CSS selector strategies to handle site layout changes.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.error("[DOGS_BOARD] beautifulsoup4 not installed")
        return []

    soup = BeautifulSoup(html, "lxml")
    entries: list[DogsBoardEntry] = []

    # Strategy 1: look for race-card / meeting structures typical of thedogs.com.au
    # The site groups races under meeting headers with individual race links.
    current_track: str | None = None
    current_state: str | None = None

    # Meeting headers — look for elements that identify a venue/meeting
    for el in soup.select(
        "h2, h3, [class*='meeting'], [class*='venue'], [class*='track-name']"
    ):
        text = el.get_text(strip=True)
        if not text:
            continue
        # Heuristic: headings with "Racing" or "Park" or "Track" are likely venue names
        if any(kw in text for kw in ["Racing", "Park", "Track", "Raceway", "Fields"]):
            current_track = text
            current_state = _infer_state(text)
            continue

    # Strategy 2: find all race-row links and group by meeting
    race_link_els = soup.select(
        "a[href*='/racing/'], [class*='race-row'], [class*='race-card']"
    )

    for el in race_link_els:
        href = el.get("href", "") or ""
        text = el.get_text(separator=" ", strip=True)

        # Parse the URL pattern: /racing/{slug}/{date}/{race_num}
        m = re.search(
            r"/racing/([^/]+)/(\d{4}-\d{2}-\d{2})/(\d+)",
            href,
        )
        if not m:
            continue

        slug, link_date, race_num_str = m.group(1), m.group(2), m.group(3)
        race_num = int(race_num_str)

        # Build canonical race link
        race_link = f"https://www.thedogs.com.au{href}" if href.startswith("/") else href

        # Try to find a time in the element text
        race_time = _parse_time(text)

        # Try to infer state from URL slug or surrounding text
        state = _infer_state(slug) or _infer_state(text) or current_state

        # Build track name from slug
        track = slug.replace("-", " ").title()

        # Look for race status hints (e.g. 'Open', 'Resulted', 'Closed')
        race_status: str | None = None
        status_el = el.find(class_=re.compile(r"status|result|badge", re.I))
        if status_el:
            race_status = status_el.get_text(strip=True) or None

        entry = DogsBoardEntry(
            track_name=track,
            state=state,
            date=link_date,
            race_number=race_num,
            race_time=race_time,
            race_status=race_status,
            race_link=race_link,
            collection_status="queued",
        )
        # Avoid duplicates
        key = (track, link_date, race_num)
        if not any(
            (e.track_name, e.date, e.race_number) == key for e in entries
        ):
            entries.append(entry)

    if not entries:
        log.warning(
            f"[DOGS_BOARD] link-based extraction found 0 races for {date_slug} — "
            f"page may be empty or structure changed"
        )

    return entries
