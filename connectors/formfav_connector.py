from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote as url_quote

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
    paceScenario: str = ""


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
    speedMap: dict[str, Any] | None = None
    decorators: list[Any] | None = None
    classProfile: str = ""
    raceClassFit: float | None = None

    def __post_init__(self):
        if self.stats_json is None:
            self.stats_json = {}
        if self.speedMap is None:
            self.speedMap = {}
        if self.decorators is None:
            self.decorators = []


class FormFavConnector:
    source_name = "formfav"
    supported_codes = ("GALLOPS", "HORSE", "HARNESS", "GREYHOUND")

    # Maps canonical DemonPulse codes → FormFav race_code param values
    RACE_CODE_MAP = {
        "GALLOPS": "gallops",
        "HORSE": "gallops",       # legacy alias
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
        clean_code = (code or "GALLOPS").upper()
        return f"{race_date}_{clean_code}_{clean_track}_{race_num}"

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any] | None:
        """Make an authenticated GET request; returns parsed JSON or None on error."""
        if not self.api_key:
            return None
        response = requests.get(
            f"{BASE_URL}{path}",
            params=params or {},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # /v1/form  — race form with runner details and stats
    # ------------------------------------------------------------------

    def _request_form(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str,
    ) -> dict[str, Any] | None:
        race_code = self.RACE_CODE_MAP.get(code.upper(), "gallops")
        params = {
            "date": target_date,
            "track": track,
            "race": race_num,
            "race_code": race_code,
            "country": self.country,
        }
        return self._get("/v1/form", params)

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

        # Normalise to canonical DemonPulse code (GALLOPS not HORSE)
        normalized_code = code.upper()
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
            paceScenario=payload.get("paceScenario") or "",
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

            speed_map = runner.get("speedMap") or stats.get("speedMap") or {}
            decorators = runner.get("decorators") or stats.get("decorators") or []
            class_profile = runner.get("classProfile") or stats.get("classProfile") or ""
            race_class_fit_raw = runner.get("raceClassFit")
            if race_class_fit_raw is None:
                race_class_fit_raw = stats.get("raceClassFit")
            try:
                race_class_fit = float(race_class_fit_raw) if race_class_fit_raw is not None else None
            except (TypeError, ValueError):
                race_class_fit = None

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
                    speedMap=speed_map,
                    decorators=decorators,
                    classProfile=class_profile,
                    raceClassFit=race_class_fit,
                    source_confidence="api",
                )
            )

        return race, runners

    # ------------------------------------------------------------------
    # /v1/form/meetings  — list meetings for a date
    # ------------------------------------------------------------------

    def fetch_meetings_list(
        self,
        target_date: str | None = None,
        *,
        race_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        GET /v1/form/meetings?date=YYYY-MM-DD[&race_code=gallops|harness|greyhounds]

        Returns a list of meeting dicts from FormFav. Each item includes
        track slug, race_code, and race_numbers available on the date.
        """
        params: dict[str, Any] = {}
        if target_date:
            params["date"] = target_date
        if race_code:
            params["race_code"] = race_code
        try:
            payload = self._get("/v1/form/meetings", params)
        except Exception:
            return []
        if not payload:
            return []
        # FormFav returns {"meetings": [...]} or a list directly
        if isinstance(payload, list):
            return payload
        return payload.get("meetings") or []

    # ------------------------------------------------------------------
    # /v1/predictions  — win/place probabilities per runner
    # ------------------------------------------------------------------

    def fetch_predictions(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str = "GALLOPS",
    ) -> list[dict[str, Any]]:
        """
        GET /v1/predictions?date=...&track=...&race=N&race_code=...

        Returns list of PredictionRunner objects with winProb and placeProb.
        """
        race_code = self.RACE_CODE_MAP.get(code.upper(), "gallops")
        params = {
            "date": target_date,
            "track": track,
            "race": race_num,
            "race_code": race_code,
            "country": self.country,
        }
        try:
            payload = self._get("/v1/predictions", params)
        except Exception:
            return []
        if not payload:
            return []
        # FormFav returns {"runners": [...]} or a list
        if isinstance(payload, list):
            return payload
        return payload.get("runners") or []

    # ------------------------------------------------------------------
    # /v1/stats/track-bias/{track}  — barrier/box bias
    # ------------------------------------------------------------------

    def fetch_track_bias(
        self,
        track: str,
        *,
        race_code: str = "gallops",
        distance: int | None = None,
    ) -> dict[str, Any]:
        """
        GET /v1/stats/track-bias/{track}

        Returns TrackBiasResponse with barrier/box statistics.
        """
        params: dict[str, Any] = {"race_code": race_code}
        if distance:
            params["distance"] = distance
        try:
            payload = self._get(f"/v1/stats/track-bias/{track}", params)
        except Exception:
            return {}
        return payload or {}

    # ------------------------------------------------------------------
    # /v1/stats/jockey/{name}  — jockey statistics
    # ------------------------------------------------------------------

    def fetch_jockey_stats(self, jockey_name: str) -> dict[str, Any]:
        """
        GET /v1/stats/jockey/{jockey_name}

        Returns JockeyStatsResponse with career/recent statistics.
        """
        try:
            payload = self._get(f"/v1/stats/jockey/{url_quote(jockey_name)}")
        except Exception:
            return {}
        return payload or {}

    def search_jockeys(self, query: str) -> list[dict[str, Any]]:
        """GET /v1/stats/jockey/search?q=..."""
        try:
            payload = self._get("/v1/stats/jockey/search", {"q": query})
        except Exception:
            return []
        if not payload:
            return []
        if isinstance(payload, list):
            return payload
        return payload.get("results") or []

    # ------------------------------------------------------------------
    # /v1/stats/trainer/{name}  — trainer statistics
    # ------------------------------------------------------------------

    def fetch_trainer_stats(self, trainer_name: str) -> dict[str, Any]:
        """
        GET /v1/stats/trainer/{trainer_name}

        Returns TrainerStatsResponse with career/recent statistics.
        """
        try:
            payload = self._get(f"/v1/stats/trainer/{url_quote(trainer_name)}")
        except Exception:
            return {}
        return payload or {}

    def search_trainers(self, query: str) -> list[dict[str, Any]]:
        """GET /v1/stats/trainer/search?q=..."""
        try:
            payload = self._get("/v1/stats/trainer/search", {"q": query})
        except Exception:
            return []
        if not payload:
            return []
        if isinstance(payload, list):
            return payload
        return payload.get("results") or []

    # ------------------------------------------------------------------
    # /v1/stats/runner/{id}  — runner career profile
    # ------------------------------------------------------------------

    def fetch_runner_stats(self, runner_id: str) -> dict[str, Any]:
        """
        GET /v1/stats/runner/{runner_id}

        Returns RunnerStatsResponse with full career profile including
        trackStats, conditionStats, distanceStats, weightProfile, sireProfile.
        """
        try:
            payload = self._get(f"/v1/stats/runner/{url_quote(runner_id)}")
        except Exception:
            return {}
        return payload or {}

    def search_runners(self, query: str) -> list[dict[str, Any]]:
        """GET /v1/stats/runner/search?q=..."""
        try:
            payload = self._get("/v1/stats/runner/search", {"q": query})
        except Exception:
            return []
        if not payload:
            return []
        if isinstance(payload, list):
            return payload
        return payload.get("results") or []

    # ------------------------------------------------------------------
    # High-level enrichment fetch — all useful data for one race
    # ------------------------------------------------------------------

    def fetch_full_race_enrichment(
        self,
        *,
        target_date: str,
        track: str,
        race_num: int,
        code: str = "GALLOPS",
    ) -> dict[str, Any]:
        """
        Fetch all available FormFav enrichment for a single race:
          1. Race form (/v1/form) — runner form, stats, conditions, speed map
          2. Predictions (/v1/predictions) — win/place probabilities
          3. Track bias (/v1/stats/track-bias/{track}) — barrier/box context

        Returns a dict with:
          race_uid, date, track, race_num, code,
          race_form:     raw form payload (runners, conditions, speedMap, …)
          predictions:   list of {runner_name, winProb, placeProb, …}
          track_bias:    TrackBiasResponse dict
          runner_enrichment: per-runner dict merging form stats + predictions
        """
        normalized_code = code.upper()
        if normalized_code == "HORSE":
            normalized_code = "GALLOPS"

        clean_track = track.strip().lower().replace(" ", "-")

        result: dict[str, Any] = {
            "date": target_date,
            "track": clean_track,
            "race_num": race_num,
            "code": normalized_code,
            "race_form": {},
            "predictions": [],
            "track_bias": {},
            "runner_enrichment": {},
        }

        # 1. Form data
        try:
            form_payload = self._request_form(
                target_date=target_date,
                track=clean_track,
                race_num=race_num,
                code=normalized_code,
            )
            if form_payload:
                result["race_form"] = form_payload
                race_number = int(form_payload.get("raceNumber") or race_num)
                result["race_num"] = race_number
        except Exception:
            form_payload = None

        # 2. Predictions
        try:
            preds = self.fetch_predictions(
                target_date=target_date,
                track=clean_track,
                race_num=race_num,
                code=normalized_code,
            )
            result["predictions"] = preds
        except Exception:
            preds = []

        # 3. Track bias
        try:
            ff_race_code = self.RACE_CODE_MAP.get(normalized_code, "gallops")
            result["track_bias"] = self.fetch_track_bias(
                clean_track, race_code=ff_race_code
            )
        except Exception:
            pass

        # 4. Build per-runner enrichment dict (keyed by runner name)
        runner_enrichment: dict[str, dict[str, Any]] = {}
        if form_payload:
            for runner in (form_payload.get("runners") or []):
                name = runner.get("name") or ""
                if not name:
                    continue
                runner_enrichment[name] = {
                    "barrier": runner.get("barrier"),
                    "number": runner.get("number"),
                    "weight": runner.get("weight"),
                    "trainer": runner.get("trainer"),
                    "jockey": runner.get("jockey"),
                    "driver": runner.get("driver"),
                    "form_trend": runner.get("formTrend") or runner.get("form_trend") or {},
                    "stats": runner.get("stats") or {},
                    "run_style": runner.get("runningStyle") or runner.get("run_style"),
                    "trial_form": runner.get("trialForm") or {},
                    "class_fit": runner.get("classFit") or {},
                    "decorator": runner.get("decorator") or {},
                    "win_prob": None,
                    "place_prob": None,
                }

        for pred in preds:
            name = pred.get("name") or pred.get("runner_name") or ""
            if not name:
                continue
            if name not in runner_enrichment:
                runner_enrichment[name] = {
                    "win_prob": None, "place_prob": None,
                    "stats": {}, "form_trend": {},
                }
            runner_enrichment[name]["win_prob"] = pred.get("winProb") or pred.get("win_prob")
            runner_enrichment[name]["place_prob"] = pred.get("placeProb") or pred.get("place_prob")

        result["runner_enrichment"] = runner_enrichment

        # Derive race_uid using canonical key
        race_number = result["race_num"]
        race_uid = self._make_race_uid(target_date, normalized_code, clean_track, race_number)
        result["race_uid"] = race_uid

        return result

    # ------------------------------------------------------------------
    # Legacy interface (kept for backward compatibility)
    # ------------------------------------------------------------------

    def fetch_meetings(self, target_date: str | None = None) -> list[MeetingRecord]:
        """
        Uses /v1/form/meetings if the API key is set; returns MeetingRecord list.
        Falls back to empty list when disabled or on error.
        """
        if not self.is_enabled():
            return []
        raw = self.fetch_meetings_list(target_date)
        meetings: list[MeetingRecord] = []
        for item in raw:
            track = (item.get("track") or "").strip().lower().replace(" ", "-")
            rc_raw = (item.get("raceCode") or item.get("race_code") or "gallops").lower()
            # Map FormFav race code back to canonical
            code_map_rev = {"gallops": "GALLOPS", "harness": "HARNESS", "greyhounds": "GREYHOUND"}
            code = code_map_rev.get(rc_raw, "GALLOPS")
            if track:
                meetings.append(
                    MeetingRecord(
                        code=code,
                        source=self.source_name,
                        track=track,
                        meeting_date=target_date or "",
                        extra={
                            "race_numbers": item.get("raceNumbers") or item.get("race_numbers") or [],
                            "raw": item,
                        },
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
