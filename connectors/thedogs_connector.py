from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from connectors.browser_client import BrowserClient

log = logging.getLogger(__name__)

BASE_URL = "https://www.thedogs.com.au"
RACECARDS_URL = f"{BASE_URL}/racing/racecards"
SCRATCHINGS_URL = f"{BASE_URL}/racing/scratchings"

RACE_PATH_RE = re.compile(
    r"^/racing/([^/]+)/(\d{4}-\d{2}-\d{2})(?:/(\d+)(?:/([^/?#]+))?)?"
)


@dataclass
class MeetingRecord:
    code: str
    source: str
    track: str
    meeting_date: str
    state: str = ""
    url: str = ""


@dataclass
class RaceRecord:
    race_uid: str
    date: str
    track: str
    race_num: int
    code: str
    source: str
    state: str = ""
    race_name: str = ""
    jump_time: str | None = None
    status: str = "upcoming"
    source_url: str = ""


class TheDogsConnector:
    source_name = "thedogs"
    supported_codes = ("GREYHOUND",)

    def __init__(self):
        self.browser = BrowserClient()
        self.default_wait_selectors = [
            "body",
            "a[href*='/racing/']",
            "table",
            "tr",
        ]

    # --------------------------------------------------
    # DEBUG (FIXED — INSIDE CLASS)
    # --------------------------------------------------
    def debug_racecards_fetch(self):
        result = self.browser.fetch_page(
            RACECARDS_URL,
            wait_selectors=self.default_wait_selectors,
            save_debug=True,
            debug_prefix="thedogs_debug_racecards",
        )
        return result.to_dict()

    def debug_scratchings_fetch(self):
        result = self.browser.fetch_page(
            SCRATCHINGS_URL,
            wait_selectors=["body", "table", "tr"],
            save_debug=True,
            debug_prefix="thedogs_debug_scratchings",
        )
        return result.to_dict()

    # --------------------------------------------------
    # CORE
    # --------------------------------------------------
    def fetch_meetings(self, target_date: str | None = None):
        if not target_date:
            return []

        result = self.browser.fetch_page(
            RACECARDS_URL,
            wait_selectors=self.default_wait_selectors,
        )

        if not result.ok:
            log.warning("TheDogs blocked: %s", result.reason)
            return []

        soup = BeautifulSoup(result.html, "html.parser")
        meetings = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a.get("href")
            if "/racing/" not in href:
                continue

            parsed = self._parse_path(href)
            if not parsed or parsed["date"] != target_date:
                continue

            key = (parsed["track"], target_date)
            if key in seen:
                continue
            seen.add(key)

            meetings.append(
                MeetingRecord(
                    code="GREYHOUND",
                    source="thedogs",
                    track=parsed["track"],
                    meeting_date=target_date,
                    url=f"{BASE_URL}/racing/{parsed['track']}/{target_date}",
                )
            )

        return meetings

    # --------------------------------------------------
    # HELPERS
    # --------------------------------------------------
    def _parse_path(self, href: str):
        path = urlparse(href).path
        m = RACE_PATH_RE.match(path)
        if not m:
            return None

        return {
            "track": m.group(1),
            "date": m.group(2),
            "race_num": int(m.group(3)) if m.group(3) else None,
        }

    def _make_hash(self, *parts):
        raw = "|".join("" if p is None else str(p) for p in parts)
        return hashlib.md5(raw.encode()).hexdigest()[:12]
