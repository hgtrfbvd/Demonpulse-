"""
connectors/oddspro_connector.py - OddsPro PRIMARY connector
============================================================
OddsPro is the authoritative source of record for DemonPulse.

Documented base URL: https://oddspro.com.au  (set as ODDSPRO_BASE_URL)

Endpoints used (full documented paths):
  GET /api/external/meetings          - daily bootstrap (list today's meetings)
  GET /api/external/meeting/:meetingId - meeting refresh + races + runners
  GET /api/external/race/:raceId      - single race refresh + runners
  GET /api/external/results           - day-level result sweep
  GET /api/external/tracks            - optional track support only
  GET /api/races/:id/results          - single-race official results (NOT under /external)

Standard response shape for external endpoints:
  {"data": [...], "meta": {...}}

Authentication:
  Public endpoints do NOT require an API key.
  API key is optional and only needed for higher rate limits.

Config (env vars):
  ODDSPRO_BASE_URL   - root URL of the OddsPro API (e.g. https://oddspro.com.au)
  ODDSPRO_API_KEY    - authentication key for OddsPro (optional)
  ODDSPRO_TIMEOUT    - request timeout in seconds (default 30)
  ODDSPRO_COUNTRY    - country filter (default "au")
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DATA RECORDS (shared with data_engine / board_builder)
# ---------------------------------------------------------------------------

@dataclass
class MeetingRecord:
    meeting_id: str
    code: str
    source: str
    track: str
    meeting_date: str
    state: str = ""
    country: str = "au"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RaceRecord:
    race_uid: str
    oddspro_race_id: str
    date: str
    track: str
    race_num: int
    code: str
    source: str = "oddspro"
    state: str = ""
    race_name: str = ""
    distance: str = ""
    grade: str = ""
    jump_time: str | None = None
    status: str = "upcoming"
    source_url: str = ""
    time_status: str = "PARTIAL"
    condition: str = ""
    prize_money: str = ""
    blocked: bool = False
    block_code: str = ""


@dataclass
class RunnerRecord:
    race_uid: str
    oddspro_race_id: str
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
    source_confidence: str = "official"
    stats_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class RaceResult:
    race_uid: str
    oddspro_race_id: str
    date: str
    track: str
    race_num: int
    code: str
    winner: str = ""
    winner_number: int | None = None
    win_price: float | None = None
    place_2: str = ""
    place_3: str = ""
    margin: float | None = None
    winning_time: float | None = None
    source: str = "oddspro"


# ---------------------------------------------------------------------------
# CONNECTOR
# ---------------------------------------------------------------------------

class OddsProConnector:
    """Primary data connector for DemonPulse. OddsPro is authoritative."""

    source_name = "oddspro"

    def __init__(self):
        self.base_url = os.getenv("ODDSPRO_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("ODDSPRO_API_KEY", "").strip()
        self.timeout = int(os.getenv("ODDSPRO_TIMEOUT", "30"))
        self.country = os.getenv("ODDSPRO_COUNTRY", "au").strip().lower()

    def is_enabled(self) -> bool:
        """
        OddsPro is configured if the base URL is set.
        API key is optional — public endpoint mode works without a key.
        """
        return bool(self.base_url)

    def is_public_mode(self) -> bool:
        """Return True when operating without an API key (public endpoint mode)."""
        return self.is_enabled() and not bool(self.api_key)

    def healthcheck(self) -> dict[str, Any]:
        if not self.is_enabled():
            return {
                "ok": False,
                "source": self.source_name,
                "reason": "ODDSPRO_BASE_URL not set",
            }
        try:
            resp = self._get("/api/external/tracks", params={"country": self.country})
            return {
                "ok": True,
                "source": self.source_name,
                "status_code": resp.status_code,
                "base_url": self.base_url,
                "oddspro_public_mode": self.is_public_mode(),
                "oddspro_api_key_present": bool(self.api_key),
            }
        except Exception as e:
            log.error(f"OddsPro healthcheck failed: {e}")
            return {"ok": False, "source": self.source_name, "error": "OddsPro connectivity check failed"}

    # -----------------------------------------------------------------------
    # PRIMARY ENDPOINTS
    # -----------------------------------------------------------------------

    def fetch_meetings(self, target_date: str | None = None) -> list[MeetingRecord]:
        """
        GET /api/external/meetings
        Daily bootstrap — list all meetings for the given date.

        Response shape: {"data": [...], "meta": {...}}

        Raises requests.exceptions.HTTPError on non-2xx responses so callers
        can map specific HTTP status codes to diagnostic error codes.
        Raises ValueError on JSON parse failure.
        """
        params: dict[str, Any] = {"country": self.country}
        if target_date:
            params["date"] = target_date

        resp = self._get("/api/external/meetings", params=params)
        try:
            payload = resp.json()
        except ValueError as e:
            log.error(f"OddsPro fetch_meetings: JSON parse failed: {e}")
            raise

        meetings: list[MeetingRecord] = []
        # Documented response shape: {"data": [...], "meta": {...}}
        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("meetings") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []

        for item in items:
            mid = str(item.get("id") or item.get("meetingId") or "")
            if not mid:
                continue
            meetings.append(
                MeetingRecord(
                    meeting_id=mid,
                    code=self._normalise_code(item.get("type") or item.get("code") or "HORSE"),
                    source=self.source_name,
                    track=self._clean_track(item.get("track") or item.get("venue") or ""),
                    meeting_date=str(item.get("date") or target_date or ""),
                    state=str(item.get("state") or item.get("region") or ""),
                    country=str(item.get("country") or self.country),
                    extra={"raw": item},
                )
            )

        log.info(f"OddsPro fetch_meetings: {len(meetings)} meetings for {target_date}")
        return meetings

    def fetch_meeting(self, meeting_id: str) -> MeetingRecord | None:
        """
        GET /api/external/meeting/:meetingId
        Refresh a single meeting record.

        Response shape: {"data": {...}, "meta": {...}}
        """
        try:
            resp = self._get(f"/api/external/meeting/{meeting_id}")
            payload = resp.json()
        except Exception as e:
            log.error(f"OddsPro fetch_meeting({meeting_id}) failed: {e}")
            return None

        if not payload:
            return None

        # Documented response shape: {"data": {...}, "meta": {...}}
        item = (
            payload.get("data")
            or payload.get("meeting")
            or payload
        )
        if isinstance(item, list):
            item = item[0] if item else {}
        return MeetingRecord(
            meeting_id=meeting_id,
            code=self._normalise_code(item.get("type") or item.get("code") or "HORSE"),
            source=self.source_name,
            track=self._clean_track(item.get("track") or item.get("venue") or ""),
            meeting_date=str(item.get("date") or ""),
            state=str(item.get("state") or ""),
            country=str(item.get("country") or self.country),
            extra={"raw": item},
        )

    def fetch_meeting_races(self, meeting: MeetingRecord) -> list[RaceRecord]:
        """
        GET /api/external/meeting/:meetingId
        Returns all races for a meeting.

        Response shape: {"data": {..., "races": [...]}, "meta": {...}}
        """
        try:
            resp = self._get(f"/api/external/meeting/{meeting.meeting_id}")
            payload = resp.json()
        except Exception as e:
            log.error(f"OddsPro fetch_meeting_races({meeting.meeting_id}) failed: {e}")
            return []

        # Documented response shape: {"data": {...}, "meta": {...}}
        raw = (
            payload.get("data")
            or payload.get("meeting")
            or payload
        )
        if isinstance(raw, list):
            races_raw = raw
        else:
            races_raw = raw.get("races") or []

        races: list[RaceRecord] = []
        for item in races_raw:
            race = self._parse_race(item, meeting)
            if race:
                races.append(race)

        return sorted(races, key=lambda r: r.race_num)

    def fetch_meeting_races_with_runners(
        self, meeting: MeetingRecord
    ) -> tuple[list[RaceRecord], list[RunnerRecord]]:
        """
        GET /api/external/meeting/:meetingId
        Returns all races AND runners for a meeting in a single request.
        Used by full_sweep (bootstrap) when races are not already embedded
        in the /meetings response.

        Response shape: {"data": {..., "races": [...]}, "meta": {...}}
        """
        try:
            resp = self._get(f"/api/external/meeting/{meeting.meeting_id}")
            payload = resp.json()
        except Exception as e:
            log.error(f"OddsPro fetch_meeting_races_with_runners({meeting.meeting_id}) failed: {e}")
            return [], []

        # Documented response shape: {"data": {...}, "meta": {...}}
        raw = (
            payload.get("data")
            or payload.get("meeting")
            or payload
        )
        if isinstance(raw, list):
            races_raw = raw
        else:
            races_raw = raw.get("races") or []

        races: list[RaceRecord] = []
        all_runners: list[RunnerRecord] = []
        for item in races_raw:
            race = self._parse_race(item, meeting)
            if race:
                races.append(race)
                runners = self._parse_runners(item, race)
                all_runners.extend(runners)

        return sorted(races, key=lambda r: r.race_num), all_runners

    def parse_meeting_races_with_runners(
        self, meeting: MeetingRecord, raw_meeting: dict
    ) -> tuple[list[RaceRecord], list[RunnerRecord]]:
        """
        Parse races and runners from a raw meeting dict that is already in memory
        (e.g. embedded inside the /api/external/meetings response).
        No HTTP request is made.  Used by full_sweep() to avoid a redundant
        /api/external/meeting/:id call when races are already present.
        """
        if isinstance(raw_meeting, dict):
            races_raw = raw_meeting.get("races") or []
        elif isinstance(raw_meeting, list):
            races_raw = raw_meeting
        else:
            races_raw = []

        races: list[RaceRecord] = []
        all_runners: list[RunnerRecord] = []
        for item in races_raw:
            race = self._parse_race(item, meeting)
            if race:
                races.append(race)
                runners = self._parse_runners(item, race)
                all_runners.extend(runners)

        return sorted(races, key=lambda r: r.race_num), all_runners


    def fetch_race(self, race_id: str, meeting: MeetingRecord | None = None) -> RaceRecord | None:
        """
        GET /api/external/race/:raceId
        Refresh a single race record.

        Response shape: {"data": {...}, "meta": {...}}
        """
        try:
            resp = self._get(f"/api/external/race/{race_id}")
            payload = resp.json()
        except Exception as e:
            log.error(f"OddsPro fetch_race({race_id}) failed: {e}")
            return None

        # Documented response shape: {"data": {...}, "meta": {...}}
        item = (
            payload.get("data")
            or payload.get("race")
            or payload
        )
        if isinstance(item, list):
            item = item[0] if item else {}
        return self._parse_race(item, meeting)

    def fetch_race_with_runners(
        self, race_id: str, meeting: MeetingRecord | None = None
    ) -> tuple[RaceRecord | None, list[RunnerRecord]]:
        """
        GET /api/external/race/:raceId
        Returns the race and its runners.

        Response shape: {"data": {...}, "meta": {...}}
        """
        try:
            resp = self._get(f"/api/external/race/{race_id}")
            payload = resp.json()
        except Exception as e:
            log.error(f"OddsPro fetch_race_with_runners({race_id}) failed: {e}")
            return None, []

        # Documented response shape: {"data": {...}, "meta": {...}}
        item = (
            payload.get("data")
            or payload.get("race")
            or payload
        )
        if isinstance(item, list):
            item = item[0] if item else {}
        race = self._parse_race(item, meeting)
        if not race:
            return None, []

        runners = self._parse_runners(item, race)
        return race, runners

    def fetch_results(self, target_date: str | None = None) -> list[RaceResult]:
        """
        GET /api/external/results
        Day-level result sweep. Returns settled race results.

        Response shape: {"data": [...], "meta": {...}}
        """
        params: dict[str, Any] = {"country": self.country}
        if target_date:
            params["date"] = target_date

        try:
            resp = self._get("/api/external/results", params=params)
            payload = resp.json()
        except Exception as e:
            log.error(f"OddsPro fetch_results failed: {e}")
            return []

        # Documented response shape: {"data": [...], "meta": {...}}
        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("results") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        results: list[RaceResult] = []
        for item in items:
            result = self._parse_result(item)
            if result:
                results.append(result)

        log.info(f"OddsPro fetch_results: {len(results)} results for {target_date}")
        return results

    def fetch_race_result(self, race_id: str) -> RaceResult | None:
        """
        GET /api/races/:id/results
        Single-race official result confirmation.
        This endpoint is NOT under /api/external — it has its own path.

        Response shape: {"data": {...}} or raw result object.
        """
        try:
            resp = self._get(f"/api/races/{race_id}/results")
            payload = resp.json()
        except Exception as e:
            log.error(f"OddsPro fetch_race_result({race_id}) failed: {e}")
            return None

        # Unwrap data/result wrapper if present
        item = (
            payload.get("data")
            or payload.get("result")
            or payload
        )
        if isinstance(item, list):
            item = item[0] if item else {}
        return self._parse_result(item)

    def fetch_tracks(self) -> list[dict[str, Any]]:
        """
        GET /api/external/tracks
        Optional track support — metadata only.
        """
        try:
            resp = self._get("/api/external/tracks", params={"country": self.country})
            payload = resp.json()
            # Documented response shape: {"data": [...], "meta": {...}}
            if isinstance(payload, dict):
                return payload.get("data") or payload.get("tracks") or []
            return payload if isinstance(payload, list) else []
        except Exception as e:
            log.warning(f"OddsPro fetch_tracks failed (non-critical): {e}")
            return []

    # -----------------------------------------------------------------------
    # INTERNAL HELPERS
    # -----------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        if not self.base_url:
            raise RuntimeError("ODDSPRO_BASE_URL is not configured")
        url = f"{self.base_url}{path}"
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def _make_race_uid(self, race_date: str, code: str, track: str, race_num: int) -> str:
        clean_track = (track or "").strip().lower().replace(" ", "-")
        clean_code = (code or "HORSE").upper()
        return f"{race_date}_{clean_code}_{clean_track}_{race_num}"

    def _clean_track(self, track: str) -> str:
        return (track or "").strip().lower().replace(" ", "-")

    def _normalise_code(self, raw: str) -> str:
        mapping = {
            "gallops": "HORSE",
            "thoroughbred": "HORSE",
            "horse": "HORSE",
            "harness": "HARNESS",
            "trot": "HARNESS",
            "greyhound": "GREYHOUND",
            "dogs": "GREYHOUND",
        }
        key = (raw or "").strip().lower()
        return mapping.get(key, (raw or "HORSE").upper())

    def _parse_race(self, item: dict, meeting: MeetingRecord | None) -> RaceRecord | None:
        race_id = str(item.get("id") or item.get("raceId") or "")
        race_num_raw = item.get("raceNumber") or item.get("race_number") or item.get("number")
        try:
            race_num = int(race_num_raw)
        except (TypeError, ValueError):
            return None

        race_date = str(item.get("date") or (meeting.meeting_date if meeting else "") or "")
        track = self._clean_track(item.get("track") or item.get("venue") or (meeting.track if meeting else ""))
        code = self._normalise_code(
            item.get("type") or item.get("code") or (meeting.code if meeting else "HORSE")
        )

        race_uid = self._make_race_uid(race_date, code, track, race_num)

        jump_time = item.get("jumpTime") or item.get("jump_time") or item.get("startTime")
        status_raw = (item.get("status") or "upcoming").lower()
        status = self._normalise_status(status_raw)

        return RaceRecord(
            race_uid=race_uid,
            oddspro_race_id=race_id,
            date=race_date,
            track=track,
            race_num=race_num,
            code=code,
            source=self.source_name,
            state=str(item.get("state") or (meeting.state if meeting else "") or ""),
            race_name=str(item.get("raceName") or item.get("name") or ""),
            distance=str(item.get("distance") or ""),
            grade=str(item.get("grade") or item.get("raceClass") or ""),
            jump_time=str(jump_time) if jump_time else None,
            status=status,
            source_url=str(item.get("url") or ""),
            time_status="VERIFIED" if jump_time else "PARTIAL",
            condition=str(item.get("condition") or item.get("trackCondition") or ""),
            prize_money=str(item.get("prizeMoney") or item.get("prize_money") or ""),
        )

    def _parse_runners(self, item: dict, race: RaceRecord) -> list[RunnerRecord]:
        runners_raw = item.get("runners") or item.get("starters") or []
        runners: list[RunnerRecord] = []

        for r in runners_raw:
            number = r.get("number") or r.get("saddleCloth")
            try:
                number = int(number) if number is not None else None
            except (TypeError, ValueError):
                number = None

            box_num = r.get("boxNumber") or r.get("box_num")
            try:
                box_num = int(box_num) if box_num is not None else None
            except (TypeError, ValueError):
                box_num = None

            weight_raw = r.get("weight")
            try:
                weight = float(weight_raw) if weight_raw is not None else None
            except (TypeError, ValueError):
                weight = None

            price_raw = r.get("price") or r.get("sp") or r.get("winPrice")
            try:
                price = float(price_raw) if price_raw is not None else None
            except (TypeError, ValueError):
                price = None

            scratched_raw = r.get("scratched") or r.get("isScratched") or False
            scratched = bool(scratched_raw)

            runners.append(
                RunnerRecord(
                    race_uid=race.race_uid,
                    oddspro_race_id=race.oddspro_race_id,
                    box_num=box_num if race.code == "GREYHOUND" else None,
                    name=str(r.get("name") or r.get("horseName") or r.get("dogName") or ""),
                    number=number,
                    barrier=r.get("barrier") or r.get("barrierDraw"),
                    trainer=str(r.get("trainer") or ""),
                    jockey=str(r.get("jockey") or ""),
                    driver=str(r.get("driver") or ""),
                    owner=str(r.get("owner") or ""),
                    weight=weight,
                    best_time=str(r.get("bestTime") or r.get("best_time") or "") or None,
                    career=str(r.get("career") or "") or None,
                    price=price,
                    rating=r.get("rating"),
                    run_style=str(r.get("runStyle") or r.get("run_style") or "") or None,
                    early_speed=str(r.get("earlySpeed") or r.get("early_speed") or "") or None,
                    scratched=scratched,
                    scratch_timing="official" if scratched else None,
                    source_confidence="official",
                    stats_json=r.get("stats") or {},
                )
            )

        return runners

    def _parse_result(self, item: dict) -> RaceResult | None:
        race_id = str(item.get("raceId") or item.get("id") or "")
        race_num_raw = item.get("raceNumber") or item.get("race_number") or item.get("number")
        try:
            race_num = int(race_num_raw)
        except (TypeError, ValueError):
            return None

        race_date = str(item.get("date") or "")
        track = self._clean_track(item.get("track") or item.get("venue") or "")
        code = self._normalise_code(item.get("type") or item.get("code") or "HORSE")
        race_uid = self._make_race_uid(race_date, code, track, race_num)

        win_price_raw = item.get("winPrice") or item.get("win_price")
        try:
            win_price = float(win_price_raw) if win_price_raw is not None else None
        except (TypeError, ValueError):
            win_price = None

        margin_raw = item.get("margin")
        try:
            margin = float(margin_raw) if margin_raw is not None else None
        except (TypeError, ValueError):
            margin = None

        time_raw = item.get("winningTime") or item.get("winning_time")
        try:
            winning_time = float(time_raw) if time_raw is not None else None
        except (TypeError, ValueError):
            winning_time = None

        return RaceResult(
            race_uid=race_uid,
            oddspro_race_id=race_id,
            date=race_date,
            track=track,
            race_num=race_num,
            code=code,
            winner=str(item.get("winner") or item.get("winnerName") or ""),
            winner_number=item.get("winnerNumber"),
            win_price=win_price,
            place_2=str(item.get("place2") or item.get("second") or ""),
            place_3=str(item.get("place3") or item.get("third") or ""),
            margin=margin,
            winning_time=winning_time,
            source=self.source_name,
        )

    @staticmethod
    def _normalise_status(raw: str) -> str:
        mapping = {
            "open": "open",
            "active": "open",
            "live": "open",
            "upcoming": "upcoming",
            "scheduled": "upcoming",
            "final": "final",
            "closed": "final",
            "result": "final",
            "resulted": "final",
            "abandoned": "abandoned",
            "interim": "interim",
            "paying": "paying",
        }
        return mapping.get(raw.lower(), "upcoming")
