# FILE: connectors/racenet_connector.py

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://www.racenet.com.au"
FORM_GUIDE_URL = f"{BASE_URL}/form-guide"

TIMEOUT = 20


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
    distance: str = ""
    grade: str = ""
    jump_time: str | None = None
    status: str = "upcoming"
    source_url: str = ""
    expert_form_url: str = ""
    time_status: str = "PARTIAL"


@dataclass
class RunnerRecord:
    race_uid: str
    box_num: int | None
    name: str
    number: int | None = None
    barrier: int | None = None
    trainer: str = ""
    jockey: str = ""
    driver: str = ""
    owner: str = ""
    weight: float | None = None
    best_time: str | None = None
    career: str | None = None
    price: float | None = None
    rating: float | None = None
    run_style: str | None = None
    early_speed: str | None = None
    scratched: bool = False
    scratch_timing: str | None = None
    raw_hash: str = ""
    source_confidence: str = "official"
    stats_json: dict[str, Any] | None = None

    def __post_init__(self):
        if self.stats_json is None:
            self.stats_json = {}


class RacenetConnector:
    source_name = "racenet"
    supported_codes = ("HORSE",)

    def is_enabled(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        try:
            html = self._fetch(FORM_GUIDE_URL)
            return {
                "ok": bool(html),
                "source": self.source_name,
                "url": FORM_GUIDE_URL,
                "html_len": len(html or ""),
            }
        except Exception as e:
            return {"ok": False, "source": self.source_name, "error": str(e)}

    def fetch_meetings(self, target_date: str | None = None) -> list[MeetingRecord]:
        html = self._fetch(FORM_GUIDE_URL)
        soup = self._parse_html(html)
        if not soup:
            return []

        meetings: list[MeetingRecord] = []
        seen: set[tuple[str, str]] = set()

        for tag in soup.find_all("a", href=True):
            href = tag.get("href", "").strip()
            if "/form-guide/" not in href:
                continue

            text = tag.get_text(" ", strip=True)
            parsed = self._parse_meeting_link(href)
            if not parsed:
                continue

            meeting_date = parsed["date"] or target_date or ""
            track = parsed["track"]
            if not meeting_date or not track:
                continue

            key = (track, meeting_date)
            if key in seen:
                continue
            seen.add(key)

            meetings.append(
                MeetingRecord(
                    code="HORSE",
                    source=self.source_name,
                    track=track,
                    meeting_date=meeting_date,
                    state=parsed.get("state", ""),
                    url=urljoin(BASE_URL, href),
                )
            )

        return meetings

    def fetch_meeting_races(self, meeting: MeetingRecord) -> list[RaceRecord]:
        html = self._fetch(meeting.url)
        soup = self._parse_html(html)
        if not soup:
            return []

        races: list[RaceRecord] = []
        seen: set[int] = set()

        for tag in soup.find_all("a", href=True):
            href = tag.get("href", "").strip()
            if "/form-guide/" not in href:
                continue

            parsed = self._parse_race_link(href)
            if not parsed:
                continue
            if parsed["track"] != meeting.track:
                continue
            if meeting.meeting_date and parsed["date"] and parsed["date"] != meeting.meeting_date:
                continue

            race_num = parsed["race_num"]
            if race_num is None or race_num in seen:
                continue
            seen.add(race_num)

            race_uid = self._make_race_uid(meeting.meeting_date or parsed["date"], "HORSE", meeting.track, race_num)

            races.append(
                RaceRecord(
                    race_uid=race_uid,
                    date=meeting.meeting_date or parsed["date"],
                    track=meeting.track,
                    race_num=race_num,
                    code="HORSE",
                    source=self.source_name,
                    state=meeting.state,
                    race_name=parsed.get("race_name", ""),
                    source_url=urljoin(BASE_URL, href),
                    expert_form_url=urljoin(BASE_URL, href),
                    status="upcoming",
                )
            )

        return sorted(races, key=lambda x: x.race_num)

    def fetch_race_detail(
        self,
        race: RaceRecord,
        scratchings: dict[str, list[int]] | None = None,
    ) -> tuple[RaceRecord, list[RunnerRecord]]:
        html = self._fetch(race.source_url)
        soup = self._parse_html(html)
        if not soup:
            return race, []

        text = soup.get_text(" ", strip=True)

        dist_match = re.search(r"\b(\d{3,4}m)\b", text.lower())
        if dist_match:
            race.distance = dist_match.group(1)

        time_match = re.search(r"\b(\d{1,2}:\d{2})\b", text)
        if time_match:
            race.jump_time = time_match.group(1)
            race.time_status = "VERIFIED"

        grade_match = re.search(r"\b(class|benchmark|maiden|group|listed)[^|,\n]{0,40}", text, flags=re.IGNORECASE)
        if grade_match:
            race.grade = grade_match.group(0).strip()

        runners: list[RunnerRecord] = []

        rows = soup.find_all("tr")
        seen_numbers: set[int] = set()

        for row in rows:
            cells = row.find_all(["td", "th"])
            values = [c.get_text(" ", strip=True) for c in cells]
            if len(values) < 3:
                continue

            number = None
            barrier = None
            weight = None
            odds = None
            trainer = ""
            jockey = ""
            name = ""

            for idx, value in enumerate(values):
                if idx == 0 and value.isdigit():
                    number = int(value)
                if not name and idx > 0 and len(value) > 2 and not value.isdigit():
                    if value.lower() not in {"wt", "barrier", "trainer", "jockey", "odds"}:
                        name = value

                if barrier is None and re.fullmatch(r"[0-9]{1,2}", value):
                    barrier = int(value)

                if weight is None:
                    m = re.search(r"\b([4-6][0-9](?:\.[0-9])?)\b", value)
                    if m:
                        try:
                            weight = float(m.group(1))
                        except ValueError:
                            pass

                if odds is None and "$" in value:
                    try:
                        odds = float(value.replace("$", "").strip())
                    except ValueError:
                        pass

            if len(values) >= 4:
                trainer = values[-2] if len(values[-2]) < 40 else ""
                jockey = values[-1] if len(values[-1]) < 40 else ""

            if number is None or not name:
                continue
            if number in seen_numbers:
                continue
            seen_numbers.add(number)

            runners.append(
                RunnerRecord(
                    race_uid=race.race_uid,
                    box_num=None,
                    name=name,
                    number=number,
                    barrier=barrier,
                    trainer=trainer,
                    jockey=jockey,
                    weight=weight,
                    price=odds,
                    raw_hash=self._make_hash(number, name, barrier, trainer, jockey, weight, odds),
                )
            )

        return race, runners

    def fetch_scratchings(self, target_date: str | None = None) -> dict[str, list[int]]:
        return {}

    def fetch_result(self, race: RaceRecord):
        return None

    def _fetch(self, url: str) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-AU,en;q=0.9",
            "Referer": BASE_URL,
        }
        response = requests.get(url, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        return response.text or ""

    def _parse_html(self, html: str) -> BeautifulSoup | None:
        if not html:
            return None
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception:
            return None

    def _parse_meeting_link(self, href: str) -> dict[str, Any] | None:
        path = urlparse(href).path
        m = re.search(r"/form-guide/[^/]+/([^/]+)/([^/]+)/(\d{4}-\d{2}-\d{2})", path)
        if not m:
            return None
        return {
            "state": m.group(1).upper(),
            "track": m.group(2).lower(),
            "date": m.group(3),
        }

    def _parse_race_link(self, href: str) -> dict[str, Any] | None:
        path = urlparse(href).path
        m = re.search(
            r"/form-guide/[^/]+/([^/]+)/([^/]+)/(\d{4}-\d{2}-\d{2})/race-([0-9]+)(?:/([^/?#]+))?",
            path,
        )
        if not m:
            return None
        return {
            "state": m.group(1).upper(),
            "track": m.group(2).lower(),
            "date": m.group(3),
            "race_num": int(m.group(4)),
            "race_name": m.group(5) or "",
        }

    def _make_race_uid(self, race_date: str, code: str, track: str, race_num: int) -> str:
        return f"{race_date}_{code}_{track}_{race_num}"

    def _make_hash(self, *parts: Any) -> str:
        import hashlib
        raw = "|".join("" if p is None else str(p) for p in parts)
        return hashlib.md5(raw.encode()).hexdigest()[:12]
