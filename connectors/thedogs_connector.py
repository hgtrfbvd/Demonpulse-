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

TRACK_STATES = {
    "casino": "NSW",
    "gosford": "NSW",
    "wentworth-park": "NSW",
    "bulli": "NSW",
    "maitland": "NSW",
    "gunnedah": "NSW",
    "grafton": "NSW",
    "richmond": "NSW",
    "wagga": "NSW",
    "taree": "NSW",
    "dubbo": "NSW",
    "nowra": "NSW",
    "goulburn": "NSW",
    "temora": "NSW",
    "the-gardens": "NSW",
    "broken-hill": "NSW",
    "horsham": "VIC",
    "bendigo": "VIC",
    "ballarat": "VIC",
    "sandown": "VIC",
    "meadows": "VIC",
    "shepparton": "VIC",
    "warragul": "VIC",
    "sale": "VIC",
    "geelong": "VIC",
    "traralgon": "VIC",
    "cranbourne": "VIC",
    "warrnambool": "VIC",
    "ladbrokes-q1-lakeside": "QLD",
    "ladbrokes-q-straight": "QLD",
    "ladbrokes-q2-parklands": "QLD",
    "townsville": "QLD",
    "capalaba": "QLD",
    "rockhampton": "QLD",
    "angle-park": "SA",
    "mount-gambier": "SA",
    "murray-bridge-straight": "SA",
    "gawler": "SA",
    "cannington": "WA",
    "mandurah": "WA",
    "launceston": "TAS",
    "hobart": "TAS",
}

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


@dataclass
class ResultRecord:
    race_uid: str
    track: str
    race_num: int
    date: str
    code: str
    winner: str | None = None
    winner_box: int | None = None
    win_price: float | None = None
    place_2: str | None = None
    place_3: str | None = None
    margin: float | None = None
    winning_time: str | None = None
    source: str = ""


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

    def is_enabled(self) -> bool:
        return True

    def healthcheck(self) -> dict[str, Any]:
        result = self.browser.fetch_page(
            RACECARDS_URL,
            wait_selectors=self.default_wait_selectors,
            save_debug=True,
            debug_prefix="thedogs_healthcheck",
        )
        return result.to_dict()

    def fetch_meetings(self, target_date: str | None = None) -> list[MeetingRecord]:
        if not target_date:
            return []

        result = self.browser.fetch_page(
            RACECARDS_URL,
            wait_selectors=self.default_wait_selectors,
            save_debug=True,
            debug_prefix=f"thedogs_meetings_{target_date}",
        )

        if not result.ok:
            log.warning("TheDogs meetings blocked/failed: %s", result.reason)
            return []

        soup = self._parse_html(result.html)
        if not soup:
            return []

        meetings: list[MeetingRecord] = []
        seen: set[tuple[str, str]] = set()

        for item in self._extract_racing_links(soup):
            parsed = self._parse_racing_path(item["href"])
            if not parsed:
                continue
            if parsed["date"] != target_date:
                continue

            track = parsed["track"]
            key = (track, target_date)
            if key in seen:
                continue
            seen.add(key)

            meetings.append(
                MeetingRecord(
                    code="GREYHOUND",
                    source=self.source_name,
                    track=track,
                    meeting_date=target_date,
                    state=self._detect_state(track),
                    url=f"{BASE_URL}/racing/{track}/{target_date}?trial=false",
                )
            )

        return meetings

    def fetch_meeting_races(self, meeting: MeetingRecord) -> list[RaceRecord]:
        result = self.browser.fetch_page(
            meeting.url,
            wait_selectors=self.default_wait_selectors,
            save_debug=True,
            debug_prefix=f"thedogs_races_{meeting.track}_{meeting.meeting_date}",
        )

        if not result.ok:
            log.warning("TheDogs meeting page failed %s: %s", meeting.track, result.reason)
            return []

        soup = self._parse_html(result.html)
        if not soup:
            return []

        races: list[RaceRecord] = []
        seen: set[int] = set()

        for item in self._extract_racing_links(soup):
            parsed = self._parse_racing_path(item["href"])
            if not parsed:
                continue
            if parsed["track"] != meeting.track:
                continue
            if parsed["date"] != meeting.meeting_date:
                continue
            if parsed["race_num"] is None:
                continue

            race_num = parsed["race_num"]
            if race_num in seen:
                continue
            seen.add(race_num)

            race_name = parsed["race_name"] or ""
            race_uid = self._make_race_uid(meeting.meeting_date, "GREYHOUND", meeting.track, race_num)

            race_url = f"{BASE_URL}/racing/{meeting.track}/{meeting.meeting_date}/{race_num}"
            if race_name:
                race_url += f"/{race_name}"
            race_url += "?trial=false"

            expert_url = f"{BASE_URL}/racing/{meeting.track}/{meeting.meeting_date}/{race_num}"
            if race_name:
                expert_url += f"/{race_name}"
            expert_url += "/expert-form"

            races.append(
                RaceRecord(
                    race_uid=race_uid,
                    date=meeting.meeting_date,
                    track=meeting.track,
                    race_num=race_num,
                    code="GREYHOUND",
                    source=self.source_name,
                    state=meeting.state,
                    race_name=race_name,
                    status="completed" if self._looks_like_completed_link(item["text"]) else "upcoming",
                    source_url=race_url,
                    expert_form_url=expert_url,
                )
            )

        return sorted(races, key=lambda x: x.race_num)

    def fetch_race_detail(
        self,
        race: RaceRecord,
        scratchings: dict[str, list[int]] | None = None,
    ) -> tuple[RaceRecord, list[RunnerRecord]]:
        url = race.expert_form_url or race.source_url
        result = self.browser.fetch_page(
            url,
            wait_selectors=["body", "table", "tr"],
            save_debug=True,
            debug_prefix=f"thedogs_race_{race.track}_{race.race_num}",
        )

        if not result.ok:
            log.warning("TheDogs race detail failed %s: %s", race.race_uid, result.reason)
            return race, []

        soup = self._parse_html(result.html)
        if not soup:
            return race, []

        page_text = soup.get_text(" ", strip=True)

        jump_time = None
        grade = ""
        distance = ""

        time_match = re.search(r"\b(\d{1,2}:\d{2})\b", page_text)
        if time_match:
            jump_time = time_match.group(1)

        dist_match = re.search(r"\b(\d{3,4}m)\b", page_text.lower())
        if dist_match:
            distance = dist_match.group(1)

        grade_match = re.search(r"\b(grade\s*[0-9a-z+\- ]+)\b", page_text, flags=re.IGNORECASE)
        if grade_match:
            grade = grade_match.group(1)[:80]

        scratched_boxes = set((scratchings or {}).get(race.race_uid, []))
        runners: list[RunnerRecord] = []
        seen_boxes: set[int] = set()

        for row in soup.select("tr"):
            cells = row.select("td")
            if len(cells) < 2:
                continue

            values = [c.get_text(" ", strip=True) for c in cells]
            if not values[0].isdigit():
                continue

            box_num = int(values[0])
            if not (1 <= box_num <= 12):
                continue
            if box_num in seen_boxes:
                continue

            name = values[1].strip()
            if len(name) < 2:
                continue

            trainer = values[2].strip() if len(values) > 2 else ""
            best_time = None
            weight = None
            career = None

            for value in values[3:]:
                if "." in value:
                    try:
                        num = float(value)
                        if 20 < num < 35 and best_time is None:
                            best_time = value
                        elif 20 < num < 45 and weight is None:
                            weight = num
                    except ValueError:
                        pass

                if career is None and ((":" in value and "-" in value) or value.count("-") >= 2):
                    career = value[:40]

            runners.append(
                RunnerRecord(
                    race_uid=race.race_uid,
                    box_num=box_num,
                    name=name,
                    trainer=trainer,
                    weight=weight,
                    best_time=best_time,
                    career=career,
                    scratched=box_num in scratched_boxes,
                    scratch_timing="late" if box_num in scratched_boxes else None,
                    raw_hash=self._make_hash(box_num, name, trainer, best_time, career),
                )
            )
            seen_boxes.add(box_num)

        race.jump_time = jump_time
        race.grade = grade
        race.distance = distance
        race.time_status = "VERIFIED" if jump_time else "PARTIAL"

        return race, runners

    def fetch_scratchings(self, target_date: str | None = None) -> dict[str, list[int]]:
        if not target_date:
            return {}

        result = self.browser.fetch_page(
            SCRATCHINGS_URL,
            wait_selectors=["body", "table", "tr"],
            save_debug=True,
            debug_prefix=f"thedogs_scratchings_{target_date}",
        )

        if not result.ok:
            log.warning("TheDogs scratchings failed: %s", result.reason)
            return {}

        soup = self._parse_html(result.html)
        if not soup:
            return {}

        output: dict[str, list[int]] = {}

        for row in soup.select("tr"):
            cells = row.select("td")
            if len(cells) < 3:
                continue

            values = [c.get_text(" ", strip=True) for c in cells]
            track = values[0].strip().lower().replace(" ", "-")
            race_match = re.search(r"\b([0-9]{1,2})\b", values[1])
            if not race_match:
                continue

            race_num = int(race_match.group(1))
            boxes = []
            for box in re.findall(r"\b([0-9]{1,2})\b", values[2]):
                box_num = int(box)
                if 1 <= box_num <= 12:
                    boxes.append(box_num)

            if not boxes:
                continue

            race_uid = self._make_race_uid(target_date, "GREYHOUND", track, race_num)
            output.setdefault(race_uid, [])
            output[race_uid].extend(boxes)

        for key in output:
            output[key] = sorted(set(output[key]))

        return output

    def fetch_result(self, race: RaceRecord) -> ResultRecord | None:
        result = self.browser.fetch_page(
            race.source_url,
            wait_selectors=["body", "table", "tr"],
            save_debug=True,
            debug_prefix=f"thedogs_result_{race.track}_{race.race_num}",
        )

        if not result.ok:
            log.warning("TheDogs result fetch failed %s: %s", race.race_uid, result.reason)
            return None

        soup = self._parse_html(result.html)
        if not soup:
            return None

        positions: dict[str, dict[str, Any]] = {}
        page_text = soup.get_text(" ", strip=True)

        for row in soup.select("tr"):
            cells = row.select("td")
            if len(cells) < 2:
                continue

            values = [c.get_text(" ", strip=True) for c in cells]
            pos = values[0].strip()
            if pos not in {"1st", "2nd", "3rd", "1", "2", "3"}:
                continue

            norm = pos.lower().replace("st", "").replace("nd", "").replace("rd", "")
            if norm in positions:
                continue

            name = values[1].strip()
            if len(name) < 2:
                continue

            win_price = None
            winning_time = None

            for value in values[2:]:
                if value.startswith("$"):
                    try:
                        win_price = float(value.replace("$", "").replace(",", ""))
                    except ValueError:
                        pass
                if "." in value:
                    try:
                        maybe_time = float(value)
                        if 20 < maybe_time < 35:
                            winning_time = value
                    except ValueError:
                        pass

            positions[norm] = {
                "name": name,
                "price": win_price,
                "time": winning_time,
            }

        if not positions:
            lines = [x.strip() for x in soup.get_text("\n", strip=True).splitlines() if x.strip()]
            for line in lines:
                m = re.match(r"^(1st|2nd|3rd|1|2|3)[\.\-\s]+(.+)$", line, flags=re.IGNORECASE)
                if not m:
                    continue
                norm = m.group(1).lower().replace("st", "").replace("nd", "").replace("rd", "")
                if norm in positions:
                    continue
                positions[norm] = {"name": m.group(2).strip(), "price": None, "time": None}

        if "1" not in positions:
            log.warning("No winner parsed for %s", race.race_uid)
            log.debug("Result text preview: %s", page_text[:1000])
            return None

        return ResultRecord(
            race_uid=race.race_uid,
            track=race.track,
            race_num=race.race_num,
            date=race.date,
            code="GREYHOUND",
            winner=positions.get("1", {}).get("name"),
            win_price=positions.get("1", {}).get("price"),
            winning_time=positions.get("1", {}).get("time"),
            place_2=positions.get("2", {}).get("name"),
            place_3=positions.get("3", {}).get("name"),
            source=self.source_name,
        )

    def _parse_html(self, html: str) -> BeautifulSoup | None:
        if not html:
            return None
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception:
            return None

    def _extract_racing_links(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        for tag in soup.find_all("a", href=True):
            href = (tag.get("href") or "").strip()
            if "/racing/" not in href:
                continue
            links.append({
                "href": href,
                "text": tag.get_text(" ", strip=True),
            })
        return links

    def _parse_racing_path(self, href: str) -> dict[str, Any] | None:
        path = urlparse(href).path or href
        match = RACE_PATH_RE.match(path)
        if not match:
            return None

        track = match.group(1)
        race_date = match.group(2)
        race_num = match.group(3)
        race_name = match.group(4)

        return {
            "track": track,
            "date": race_date,
            "race_num": int(race_num) if race_num and race_num.isdigit() else None,
            "race_name": race_name or "",
        }

    def _looks_like_completed_link(self, text: str) -> bool:
        return text.strip() in {"1", "2", "3", "1st", "2nd", "3rd"}

    def _detect_state(self, track: str) -> str:
        return TRACK_STATES.get((track or "").lower(), "QLD")

    def _make_race_uid(self, race_date: str, code: str, track: str, race_num: int) -> str:
        return f"{race_date}_{code}_{track}_{race_num}"

    def _make_hash(self, *parts: Any) -> str:
        raw = "|".join("" if p is None else str(p) for p in parts)
        return hashlib.md5(raw.encode()).hexdigest()[:12]

def debug_racecards_fetch(self) -> dict[str, Any]:
    result = self.browser.fetch_page(
        RACECARDS_URL,
        wait_selectors=self.default_wait_selectors,
        save_debug=True,
        debug_prefix="thedogs_debug_racecards",
    )
    return result.to_dict()


def debug_scratchings_fetch(self) -> dict[str, Any]:
    result = self.browser.fetch_page(
        SCRATCHINGS_URL,
        wait_selectors=["body", "table", "tr"],
        save_debug=True,
        debug_prefix="thedogs_debug_scratchings",
    )
    return result.to_dict()
