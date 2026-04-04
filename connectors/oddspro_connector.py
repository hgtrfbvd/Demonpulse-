"""
connectors/oddspro_connector.py - OddsPro primary data connector

Endpoints consumed:
  GET /api/external/meetings                  - daily meeting discovery
  GET /api/external/meeting/:meetingId        - meeting detail + race list
  GET /api/external/race/:raceId              - race detail + runners + odds
  GET /api/external/results                   - today's results
  GET /api/races/:raceId/results              - single race result
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# DATA RECORDS
# ─────────────────────────────────────────────────────────────────
@dataclass
class MeetingRecord:
    meeting_id: str
    code: str          # HORSE | HARNESS | GREYHOUND
    track: str
    meeting_date: str  # YYYY-MM-DD
    state: str = ""
    country: str = "AU"
    source: str = "oddspro"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RaceRecord:
    race_id: str
    race_uid: str      # local stable key: date_code_track_racenum
    meeting_id: str
    date: str
    track: str
    race_num: int
    code: str
    race_name: str = ""
    distance: str = ""
    grade: str = ""
    condition: str = ""
    status: str = "upcoming"
    jump_time: str | None = None   # ISO-8601 or HH:MM
    source: str = "oddspro"


@dataclass
class RunnerRecord:
    race_id: str
    race_uid: str
    runner_id: str
    name: str
    number: int | None = None
    box_num: int | None = None
    barrier: int | None = None
    trainer: str = ""
    jockey: str = ""
    driver: str = ""
    weight: float | None = None
    price: float | None = None
    scratched: bool = False
    scratch_timing: str | None = None
    stats_json: dict[str, Any] = field(default_factory=dict)
    source: str = "oddspro"


@dataclass
class ResultRecord:
    race_id: str
    race_uid: str
    positions: list[dict[str, Any]] = field(default_factory=list)
    dividends: dict[str, Any] = field(default_factory=dict)
    source: str = "oddspro"


# ─────────────────────────────────────────────────────────────────
# CONNECTOR
# ─────────────────────────────────────────────────────────────────
class OddsProConnector:
    source_name = "oddspro"

    def __init__(self):
        self.base_url = os.getenv("ODDSPRO_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("ODDSPRO_API_KEY", "").strip()
        self.timeout = int(os.getenv("ODDSPRO_TIMEOUT", "30"))

    # ── CAPABILITY ────────────────────────────────────────────────
    def is_enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    def healthcheck(self) -> dict[str, Any]:
        return {
            "ok": self.is_enabled(),
            "source": self.source_name,
            "base_url": self.base_url,
            "has_api_key": bool(self.api_key),
        }

    # ── HTTP ──────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("OddsPro connector not enabled (missing ODDSPRO_BASE_URL or ODDSPRO_API_KEY)")
        url = f"{self.base_url}{path}"
        resp = requests.get(url, headers=self._headers(), params=params or {}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ── HELPERS ───────────────────────────────────────────────────
    @staticmethod
    def _race_uid(race_date: str, code: str, track: str, race_num: int) -> str:
        clean = (track or "").strip().lower().replace(" ", "-")
        return f"{race_date}_{code.upper()}_{clean}_{race_num}"

    @staticmethod
    def _normalise_code(raw: str) -> str:
        mapping = {
            "gallops": "HORSE",
            "horse": "HORSE",
            "thoroughbred": "HORSE",
            "harness": "HARNESS",
            "greyhound": "GREYHOUND",
            "greyhounds": "GREYHOUND",
            "dogs": "GREYHOUND",
        }
        return mapping.get((raw or "").strip().lower(), (raw or "HORSE").upper())

    # ── MEETINGS DISCOVERY ────────────────────────────────────────
    def fetch_meetings(self, target_date: str | None = None) -> list[MeetingRecord]:
        """
        GET /api/external/meetings
        Returns all meetings for today (or target_date if provided).
        """
        params: dict[str, str] = {}
        if target_date:
            params["date"] = target_date

        payload = self._get("/api/external/meetings", params)
        raw_meetings = payload.get("meetings") or payload.get("data") or []
        if isinstance(payload, list):
            raw_meetings = payload

        meetings: list[MeetingRecord] = []
        for m in raw_meetings:
            mid = str(m.get("meetingId") or m.get("id") or "")
            if not mid:
                continue
            meetings.append(MeetingRecord(
                meeting_id=mid,
                code=self._normalise_code(m.get("raceType") or m.get("code") or ""),
                track=(m.get("venue") or m.get("track") or "").strip().lower().replace(" ", "-"),
                meeting_date=str(m.get("date") or target_date or ""),
                state=m.get("state") or m.get("stateCode") or "",
                country=m.get("country") or "AU",
                extra=m,
            ))
        return meetings

    # ── MEETING DETAIL ────────────────────────────────────────────
    def fetch_meeting(self, meeting_id: str) -> tuple[MeetingRecord | None, list[RaceRecord]]:
        """
        GET /api/external/meeting/:meetingId
        Returns meeting metadata and its race list.
        """
        payload = self._get(f"/api/external/meeting/{meeting_id}")
        m = payload.get("meeting") or payload

        meeting = MeetingRecord(
            meeting_id=meeting_id,
            code=self._normalise_code(m.get("raceType") or m.get("code") or ""),
            track=(m.get("venue") or m.get("track") or "").strip().lower().replace(" ", "-"),
            meeting_date=str(m.get("date") or ""),
            state=m.get("state") or m.get("stateCode") or "",
            country=m.get("country") or "AU",
            extra=m,
        )

        races: list[RaceRecord] = []
        for r in (payload.get("races") or m.get("races") or []):
            rid = str(r.get("raceId") or r.get("id") or "")
            rnum = int(r.get("raceNumber") or r.get("number") or 0)
            races.append(RaceRecord(
                race_id=rid,
                race_uid=self._race_uid(meeting.meeting_date, meeting.code, meeting.track, rnum),
                meeting_id=meeting_id,
                date=meeting.meeting_date,
                track=meeting.track,
                race_num=rnum,
                code=meeting.code,
                race_name=r.get("raceName") or r.get("name") or "",
                distance=str(r.get("distance") or ""),
                grade=r.get("raceClass") or r.get("grade") or "",
                condition=r.get("trackCondition") or r.get("condition") or "",
                status=r.get("status") or "upcoming",
                jump_time=r.get("startTime") or r.get("jumpTime") or r.get("scheduledTime"),
            ))
        return meeting, races

    # ── RACE DETAIL ───────────────────────────────────────────────
    def fetch_race(self, race_id: str) -> tuple[RaceRecord | None, list[RunnerRecord]]:
        """
        GET /api/external/race/:raceId
        Returns race metadata and runner list with current odds.
        """
        payload = self._get(f"/api/external/race/{race_id}")
        r = payload.get("race") or payload

        mid = str(r.get("meetingId") or "")
        rnum = int(r.get("raceNumber") or r.get("number") or 0)
        rdate = str(r.get("date") or "")
        code = self._normalise_code(r.get("raceType") or r.get("code") or "")
        track = (r.get("venue") or r.get("track") or "").strip().lower().replace(" ", "-")

        race = RaceRecord(
            race_id=race_id,
            race_uid=self._race_uid(rdate, code, track, rnum),
            meeting_id=mid,
            date=rdate,
            track=track,
            race_num=rnum,
            code=code,
            race_name=r.get("raceName") or r.get("name") or "",
            distance=str(r.get("distance") or ""),
            grade=r.get("raceClass") or r.get("grade") or "",
            condition=r.get("trackCondition") or r.get("condition") or "",
            status=r.get("status") or "upcoming",
            jump_time=r.get("startTime") or r.get("jumpTime") or r.get("scheduledTime"),
        )

        runners: list[RunnerRecord] = []
        for rn in (payload.get("runners") or r.get("runners") or []):
            rnid = str(rn.get("runnerId") or rn.get("id") or "")
            num = rn.get("number") or rn.get("runnerNumber")
            box = rn.get("boxNumber") or (num if code == "GREYHOUND" else None)
            runners.append(RunnerRecord(
                race_id=race_id,
                race_uid=race.race_uid,
                runner_id=rnid,
                name=rn.get("name") or rn.get("runnerName") or "",
                number=int(num) if num is not None else None,
                box_num=int(box) if box is not None else None,
                barrier=rn.get("barrier") or rn.get("barrierNumber"),
                trainer=rn.get("trainer") or rn.get("trainerName") or "",
                jockey=rn.get("jockey") or rn.get("jockeyName") or "",
                driver=rn.get("driver") or rn.get("driverName") or "",
                weight=rn.get("weight"),
                price=rn.get("fixedWin") or rn.get("winPrice") or rn.get("price"),
                scratched=bool(rn.get("scratched") or rn.get("isScratched")),
                scratch_timing=rn.get("scratchTime") or rn.get("scratchedAt"),
                stats_json=rn.get("stats") or {},
            ))
        return race, runners

    # ── RESULTS ───────────────────────────────────────────────────
    def fetch_results(self, target_date: str | None = None) -> list[ResultRecord]:
        """
        GET /api/external/results
        Returns all settled results for today (or target_date).
        """
        params: dict[str, str] = {}
        if target_date:
            params["date"] = target_date

        payload = self._get("/api/external/results", params)
        raw = payload.get("results") or payload.get("data") or []
        if isinstance(payload, list):
            raw = payload

        results: list[ResultRecord] = []
        for res in raw:
            rid = str(res.get("raceId") or res.get("id") or "")
            results.append(ResultRecord(
                race_id=rid,
                race_uid=str(res.get("raceUid") or ""),
                positions=res.get("positions") or res.get("finishOrder") or [],
                dividends=res.get("dividends") or res.get("exotics") or {},
            ))
        return results

    def fetch_race_result(self, race_id: str) -> ResultRecord | None:
        """
        GET /api/races/:raceId/results
        Returns the settled result for a single race.
        """
        try:
            payload = self._get(f"/api/races/{race_id}/results")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise
        r = payload.get("result") or payload
        return ResultRecord(
            race_id=race_id,
            race_uid=str(r.get("raceUid") or ""),
            positions=r.get("positions") or r.get("finishOrder") or [],
            dividends=r.get("dividends") or r.get("exotics") or {},
        )
