from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger(__name__)

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
    # Extended race-level metadata from FormFav
    weather: str = ""
    start_time: str = ""
    start_time_utc: str = ""
    timezone: str = ""
    abandoned: bool = False
    number_of_runners: int = 0
    pace_scenario: str = ""
    prize_money: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        pass


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
    # Extended runner-level fields from FormFav
    age: str = ""
    claim: str = ""
    form_string: str = ""
    decorators: list[dict[str, Any]] = field(default_factory=list)
    speed_map: dict[str, Any] | None = None
    class_profile: dict[str, Any] | None = None
    race_class_fit: dict[str, Any] | None = None
    stats_track: dict[str, Any] | None = None
    stats_distance: dict[str, Any] | None = None
    stats_condition: dict[str, Any] | None = None
    stats_track_distance: dict[str, Any] | None = None
    # Prediction fields (populated by fetch_race_predictions)
    win_prob: float | None = None
    place_prob: float | None = None
    model_rank: int | None = None
    confidence: str = ""
    model_version: str = ""
    # Missing fields from FormFav API
    last20_starts: str = ""              # last20Starts — 20-start summary
    racing_colours: str = ""             # racingColours — jockey silks
    gear_change: str = ""                # gearChange — equipment changes (VERY valuable)
    # Expanded stats breakdown (currently stored as raw JSONB only)
    stats_overall_starts: int | None = None
    stats_overall_wins: int | None = None
    stats_overall_places: int | None = None
    stats_overall_seconds: int | None = None
    stats_overall_thirds: int | None = None
    stats_overall_win_pct: float | None = None
    stats_overall_place_pct: float | None = None
    stats_first_up: dict | None = None   # firstUp stats
    stats_second_up: dict | None = None  # secondUp stats

    def __post_init__(self):
        if self.stats_json is None:
            self.stats_json = {}


class FormFavConnector:
    source_name = "formfav"
    supported_codes = ("HORSE", "HARNESS", "GREYHOUND")

    RACE_CODE_MAP = {
        "HORSE": "gallops",
        "HARNESS": "harness",
        "GREYHOUND": "greyhounds",
    }

    def __init__(self):
        _raw_key = os.environ.get("FORMFAV_API_KEY")
        if not _raw_key:
            log.error("[FORMFAV] Missing API Key")
        self.api_key = (_raw_key or "").strip()
        self.country = (os.environ.get("FORMFAV_COUNTRY") or "au").strip().lower() or "au"
        self.timeout = int(os.environ.get("FORMFAV_TIMEOUT") or "30")

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
        clean_code = (code or "HORSE").upper()
        return f"{race_date}_{clean_code}_{clean_track}_{race_num}"

    def _request_form(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str,
        country: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.api_key:
            log.error("[FORMFAV] Missing API Key")
            return None

        race_code = self.RACE_CODE_MAP.get(code.upper(), "gallops")
        params = {
            "date": target_date,
            "track": track,
            "race": race_num,
            "race_code": race_code,
            "country": (country or self.country).lower(),
        }
        url = f"{BASE_URL}/v1/form"
        log.info(
            "[FORMFAV] HTTP url=%s params=%s",
            url,
            params,
        )

        response = requests.get(
            url,
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def _request_predictions(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str,
        country: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch win/place probabilities and model metadata from /v1/predictions (Pro tier)."""
        if not self.api_key:
            log.error("[FORMFAV] Missing API Key")
            return None

        race_code = self.RACE_CODE_MAP.get(code.upper(), "gallops")
        params = {
            "date": target_date,
            "track": track,
            "race": race_num,
            "race_code": race_code,
            "country": (country or self.country).lower(),
        }
        url = f"{BASE_URL}/v1/predictions"
        log.info(
            "[FORMFAV] HTTP url=%s params=%s",
            url,
            params,
        )

        try:
            response = requests.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            # Predictions endpoint is Pro-tier; gracefully ignore if unavailable
            return None

    def _request_meetings(self, target_date: str, code: str) -> list[dict[str, Any]]:
        """Fetch list of meetings for a date from /v1/form/meetings."""
        if not self.api_key:
            log.error("[FORMFAV] Missing API Key")
            return []

        race_code = self.RACE_CODE_MAP.get(code.upper(), "gallops")
        params = {
            "date": target_date,
            "race_code": race_code,
            "country": self.country,
        }
        url = f"{BASE_URL}/v1/form/meetings"
        log.info(
            "[FORMFAV] HTTP url=%s params=%s",
            url,
            params,
        )

        try:
            response = requests.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            # API returns {"meetings": [...]} or a list directly
            if isinstance(data, list):
                return data
            return data.get("meetings") or data.get("data") or []
        except Exception:
            return []

    def fetch_race_form(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str = "HORSE",
        country: str | None = None,
    ) -> tuple[RaceRecord, list[RunnerRecord]]:
        payload = self._request_form(
            target_date=target_date,
            track=track,
            race_num=race_num,
            code=code,
            country=country,
        )
        if not payload:
            raise RuntimeError("No FormFav payload returned")

        normalized_code = code.upper()
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
            # Extended metadata
            weather=payload.get("weather") or "",
            start_time=payload.get("startTime") or "",
            start_time_utc=payload.get("startTimeUtc") or "",
            timezone=payload.get("timezone") or "",
            abandoned=bool(payload.get("abandoned", False)),
            number_of_runners=int(payload.get("numberOfRunners") or len(payload.get("runners", []))),
            pace_scenario=payload.get("paceScenario") or "",
            prize_money=payload.get("prizeMoney") or "",
            raw_response=payload,
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
                    box_num=None if normalized_code != "GREYHOUND" else number,
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
                    # Extended runner fields
                    age=str(runner.get("age") or ""),
                    claim=str(runner.get("claim") or ""),
                    scratched=bool(runner.get("scratched", False)),
                    form_string=runner.get("form") or "",
                    decorators=runner.get("decorators") or [],
                    speed_map=runner.get("speedMap") or None,
                    class_profile=runner.get("classProfile") or None,
                    race_class_fit=runner.get("raceClassFit") or None,
                    stats_track=stats.get("track") or None,
                    stats_distance=stats.get("distance") or None,
                    stats_condition=stats.get("condition") or None,
                    stats_track_distance=stats.get("trackDistance") or None,
                    # New fields from FormFav API
                    last20_starts=runner.get("last20Starts") or "",
                    racing_colours=runner.get("racingColours") or "",
                    gear_change=runner.get("gearChange") or "",
                    stats_first_up=stats.get("firstUp") or None,
                    stats_second_up=stats.get("secondUp") or None,
                    stats_overall_starts=overall.get("starts"),
                    stats_overall_wins=overall.get("wins"),
                    stats_overall_places=overall.get("places"),
                    stats_overall_seconds=overall.get("seconds"),
                    stats_overall_thirds=overall.get("thirds"),
                    stats_overall_win_pct=overall.get("winPercent"),
                    stats_overall_place_pct=overall.get("placePercent"),
                )
            )

        return race, runners

    def fetch_race_predictions(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str = "HORSE",
        country: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Fetch prediction data (winProb, placeProb, modelRank, confidence, modelVersion)
        from /v1/predictions for each runner. Returns raw payload or None if unavailable.
        """
        return self._request_predictions(
            target_date=target_date,
            track=track,
            race_num=race_num,
            code=code,
            country=country,
        )

    def fetch_race_form_with_predictions(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str = "HORSE",
        country: str | None = None,
    ) -> tuple[RaceRecord, list[RunnerRecord]]:
        """
        Fetch race form AND predictions in one call, merging prediction fields
        (winProb, placeProb, modelRank, confidence, modelVersion) onto runners.
        Returns (race, runners) with full enrichment.
        """
        race, runners = self.fetch_race_form(
            target_date=target_date,
            track=track,
            race_num=race_num,
            code=code,
            country=country,
        )

        preds_payload = self.fetch_race_predictions(
            target_date=target_date,
            track=track,
            race_num=race_num,
            code=code,
            country=country,
        )

        if preds_payload and preds_payload.get("runners"):
            model_version = preds_payload.get("modelVersion") or ""
            pred_by_num: dict[int, dict[str, Any]] = {}
            for pr in preds_payload["runners"]:
                n = pr.get("number")
                if n is not None:
                    pred_by_num[int(n)] = pr

            for runner in runners:
                runner_num = runner.number if runner.number is not None else runner.box_num
                if runner_num is not None:
                    pr = pred_by_num.get(int(runner_num))
                    if pr:
                        runner.win_prob = pr.get("winProb")
                        runner.place_prob = pr.get("placeProb")
                        runner.model_rank = pr.get("modelRank")
                        runner.confidence = str(pr.get("confidence") or "")
                        runner.model_version = model_version

        return race, runners

    def fetch_meetings(self, target_date: str | None = None) -> list[MeetingRecord]:
        """
        Fetch available meetings for a date from /v1/form/meetings.
        Returns MeetingRecord list with race_numbers in extra.
        """
        if not self.api_key:
            return []

        from datetime import date as _date
        td = target_date or _date.today().isoformat()

        meetings: list[MeetingRecord] = []
        for code in self.supported_codes:
            raw_meetings = self._request_meetings(td, code)
            for m in raw_meetings:
                track = (m.get("track") or m.get("venue") or "").strip().lower().replace(" ", "-")
                if not track:
                    continue
                race_numbers = m.get("raceNumbers") or m.get("race_numbers") or []
                meetings.append(
                    MeetingRecord(
                        code=code,
                        source=self.source_name,
                        track=track,
                        meeting_date=td,
                        state=m.get("state") or "",
                        extra={"race_numbers": race_numbers, "raw": m},
                    )
                )
        return meetings

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
        country: str | None = None,
    ) -> tuple[RaceRecord, list[RunnerRecord]]:
        fresh_race, runners = self.fetch_race_form_with_predictions(
            target_date=race.date,
            track=race.track,
            race_num=race.race_num,
            code=race.code,
            country=country,
        )

        scratched = set((scratchings or {}).get(fresh_race.race_uid, []))
        if scratched:
            for r in runners:
                runner_num = r.number if r.number is not None else r.box_num
                if runner_num in scratched:
                    r.scratched = True
                    r.scratch_timing = "late"

        return fresh_race, runners

    def fetch_all_races_for_date(
        self,
        target_date: str | None = None,
    ) -> list[tuple["RaceRecord", list["RunnerRecord"]]]:
        """
        Fetch ALL races for a date from FormFav across all supported race codes.

        This is the Step-1 FORMFAV fetch in the new 10-step pipeline.
        No country or domestic filtering is applied here — the caller (merge
        engine / full_sweep) applies classification AFTER the full dataset is built.

        Steps:
          1. Fetch meeting list from /v1/form/meetings for each code
          2. For each meeting, fetch race + runner detail via fetch_race_form_with_predictions
          3. Return the flat list of (RaceRecord, [RunnerRecord]) tuples

        Returns an empty list (not an error) when the connector is not enabled.
        Individual race fetch failures are silently skipped (logged at DEBUG).
        """
        if not self.api_key:
            return []

        from datetime import date as _date
        td = target_date or _date.today().isoformat()

        results: list[tuple[RaceRecord, list[RunnerRecord]]] = []
        for code in self.supported_codes:
            raw_meetings = self._request_meetings(td, code)
            for m in raw_meetings:
                track = (m.get("track") or m.get("venue") or "").strip().lower().replace(" ", "-")
                if not track:
                    continue
                race_numbers = m.get("raceNumbers") or m.get("race_numbers") or []
                for race_num in race_numbers:
                    try:
                        race, runners = self.fetch_race_form_with_predictions(
                            target_date=td,
                            track=track,
                            race_num=int(race_num),
                            code=code,
                        )
                        results.append((race, runners))
                    except Exception as exc:
                        log.debug(
                            "[FORMFAV] fetch_all_races_for_date: skipped"
                            " track=%s race=%s code=%s error=%s",
                            track, race_num, code, exc,
                        )
                        continue
        return results


    def fetch_track_bias(self, track: str, race_code: str = "gallops",
                         window: int | None = 90) -> dict | None:
        """GET /v1/stats/track-bias/{track} — barrier/box bias stats (Pro)."""
        if not self.api_key:
            return None
        params: dict = {"race_code": race_code}
        if window:
            params["window"] = window
        try:
            resp = requests.get(
                f"{BASE_URL}/v1/stats/track-bias/{track}",
                params=params, headers=self._headers(), timeout=self.timeout
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.debug(f"[FORMFAV] track-bias fetch failed {track}: {e}")
            return None

    def fetch_jockey_stats(self, jockey_name: str,
                           race_code: str = "gallops") -> dict | None:
        """GET /v1/stats/jockey/{name} — jockey career stats (Pro)."""
        if not self.api_key:
            return None
        from urllib.parse import quote
        params: dict = {"race_code": race_code}
        try:
            resp = requests.get(
                f"{BASE_URL}/v1/stats/jockey/{quote(jockey_name)}",
                params=params, headers=self._headers(), timeout=self.timeout
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.debug(f"[FORMFAV] jockey stats fetch failed {jockey_name}: {e}")
            return None

    def fetch_trainer_stats(self, trainer_name: str,
                            race_code: str = "gallops") -> dict | None:
        """GET /v1/stats/trainer/{name} — trainer career stats (Pro)."""
        if not self.api_key:
            return None
        from urllib.parse import quote
        params: dict = {"race_code": race_code}
        try:
            resp = requests.get(
                f"{BASE_URL}/v1/stats/trainer/{quote(trainer_name)}",
                params=params, headers=self._headers(), timeout=self.timeout
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.debug(f"[FORMFAV] trainer stats fetch failed {trainer_name}: {e}")
            return None

    def fetch_venues(self, race_type: str | None = None,
                     country: str = "au") -> list[dict]:
        """GET /v1/form/venues — get canonical venue/track names (Pro)."""
        if not self.api_key:
            return []
        params: dict = {"country": country}
        if race_type:
            params["raceType"] = race_type
        try:
            resp = requests.get(
                f"{BASE_URL}/v1/form/venues",
                params=params, headers=self._headers(), timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("venues") or []
        except Exception as e:
            log.warning(f"[FORMFAV] fetch_venues failed: {e}")
            return []

    def fetch_scratchings(self, target_date: str | None = None) -> dict[str, list[int]]:
        return {}

    def fetch_result(self, race: RaceRecord):
        return None
