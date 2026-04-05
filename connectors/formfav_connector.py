from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "https://api.formfav.com"


@dataclass
class MeetingRecord:
    code: str
    source: str
    track: str
    meeting_date: str
    state: str = ""
    url: str = ""
    extra: dict[str, Any] | None = None

    def __post_init__(self):
        if self.extra is None:
            self.extra = {}


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
    condition: str = ""


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


class FormFavConnector:
    source_name = "formfav"
    # GALLOPS is the canonical code for thoroughbred (OddsPro normalises T → GALLOPS).
    # Keep HORSE in the list for backward compatibility with any legacy callers.
    supported_codes = ("GALLOPS", "HORSE", "HARNESS", "GREYHOUND")

    RACE_CODE_MAP = {
        "GALLOPS": "gallops",   # canonical OddsPro code for thoroughbred
        "HORSE": "gallops",     # legacy alias — treated identically to GALLOPS
        "HARNESS": "harness",
        "GREYHOUND": "greyhounds",
    }

    def __init__(self):
        self.api_key = os.getenv("FORMFAV_API_KEY", "").strip()
        self.country = os.getenv("FORMFAV_COUNTRY", "au").strip().lower() or "au"
        self.timeout = int(os.getenv("FORMFAV_TIMEOUT", "30"))

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def healthcheck(self) -> dict[str, Any]:
        return {
            "ok": self.is_enabled(),
            "source": self.source_name,
            "base_url": BASE_URL,
            "has_api_key": bool(self.api_key),
        }

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    def _make_race_uid(self, race_date: str, code: str, track: str, race_num: int) -> str:
        clean_track = (track or "").strip().lower().replace(" ", "-")
        # Canonicalise: HORSE → GALLOPS so UIDs match OddsPro-sourced race UIDs.
        clean_code = (code or "GALLOPS").upper()
        if clean_code == "HORSE":
            clean_code = "GALLOPS"
        return f"{race_date}_{clean_code}_{clean_track}_{race_num}"

    def _request_form(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str,
    ) -> dict[str, Any] | None:
        if not self.api_key:
            return None

        race_code = self.RACE_CODE_MAP.get(code.upper(), "gallops")
        params = {
            "date": target_date,
            "track": track,
            "race": race_num,
            "race_code": race_code,
            "country": self.country,
        }

        response = requests.get(
            f"{BASE_URL}/v1/form",
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch_race_form(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str = "GALLOPS",
    ) -> tuple[RaceRecord, list[RunnerRecord]]:
        payload = self._request_form(
            target_date=target_date,
            track=track,
            race_num=race_num,
            code=code,
        )
        if not payload:
            raise RuntimeError("No FormFav payload returned")

        normalized_code = code.upper()
        # Canonicalise legacy HORSE → GALLOPS so UIDs match OddsPro-sourced race UIDs.
        if normalized_code == "HORSE":
            normalized_code = "GALLOPS"
        track_name = (payload.get("track") or track).strip().lower().replace(" ", "-")
        race_number = int(payload.get("raceNumber") or race_num)
        race_uid = self._make_race_uid(target_date, normalized_code, track_name, race_number)

        race = RaceRecord(
            race_uid=race_uid,
            date=payload.get("date") or target_date,
            track=track_name,
            race_num=race_number,
            code=normalized_code,
            source=self.source_name,
            race_name=payload.get("raceName") or "",
            distance=payload.get("distance") or "",
            grade=payload.get("raceClass") or "",
            condition=payload.get("condition") or "",
            source_url=f"{BASE_URL}/v1/form",
            expert_form_url=f"{BASE_URL}/v1/form",
            time_status="PARTIAL",
        )

        runners: list[RunnerRecord] = []
        for runner in payload.get("runners", []):
            number = runner.get("number")
            barrier = runner.get("barrier")
            stats = runner.get("stats") or {}
            overall = stats.get("overall") or {}

            runners.append(
                RunnerRecord(
                    race_uid=race_uid,
                    # For greyhounds use box number; for gallops/harness use runner number
                    # so the (race_uid, box_num) upsert key is never NULL.
                    box_num=number if normalized_code == "GREYHOUND" else number,
                    name=runner.get("name") or "",
                    number=number,
                    barrier=barrier,
                    trainer=runner.get("trainer") or "",
                    jockey=runner.get("jockey") or "",
                    driver=runner.get("driver") or "",
                    weight=runner.get("weight"),
                    career=str(overall) if overall else None,
                    stats_json=stats,
                    source_confidence="api",
                )
            )

        return race, runners

    def fetch_meetings(self, target_date: str | None = None) -> list[MeetingRecord]:
        """
        FormFav docs provided here confirm /v1/form race-form requests, but not a public
        meeting-list endpoint. So this connector does not invent one.
        Use fetch_race_form directly, or pass known race_numbers into MeetingRecord.extra
        before calling fetch_meeting_races.
        """
        return []

    def fetch_meeting_races(self, meeting: MeetingRecord) -> list[RaceRecord]:
        race_numbers = (meeting.extra or {}).get("race_numbers") or []
        races: list[RaceRecord] = []

        for race_num in race_numbers:
            try:
                race, _ = self.fetch_race_form(
                    target_date=meeting.meeting_date,
                    track=meeting.track,
                    race_num=int(race_num),
                    code=meeting.code,
                )
                races.append(race)
            except Exception:
                continue

        return races

    def fetch_race_detail(
        self,
        race: RaceRecord,
        scratchings: dict[str, list[int]] | None = None,
    ) -> tuple[RaceRecord, list[RunnerRecord]]:
        fresh_race, runners = self.fetch_race_form(
            target_date=race.date,
            track=race.track,
            race_num=race.race_num,
            code=race.code,
        )

        scratched = set((scratchings or {}).get(fresh_race.race_uid, []))
        if scratched:
            for r in runners:
                runner_num = r.number if r.number is not None else r.box_num
                if runner_num in scratched:
                    r.scratched = True
                    r.scratch_timing = "late"

        return fresh_race, runners

    def fetch_scratchings(self, target_date: str | None = None) -> dict[str, list[int]]:
        return {}

    def fetch_result(self, race: RaceRecord):
        return None
