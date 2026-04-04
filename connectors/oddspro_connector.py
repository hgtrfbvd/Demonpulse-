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

Supported payload shapes for meetings endpoint:
  A. {"data": [...], ...}        - data is a list of meetings
  B. {"data": {...}, ...}        - data is a single meeting dict (wrapped into list)
  C. [...]                       - bare list of meetings
  D. {"meetings": [...], ...}    - meetings key holds list

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
# PAYLOAD NORMALISATION HELPERS
# ---------------------------------------------------------------------------

def normalize_meetings_payload(payload: Any) -> list:
    """
    Normalise an OddsPro /meetings response into a flat list of meeting dicts.

    Supported shapes:
      A. {"data": [...], ...}     -> return payload["data"]
      B. {"data": {...}, ...}     -> wrap single meeting in list: [payload["data"]]
      C. [...]                    -> return payload directly
      D. {"meetings": [...], ...} -> return payload["meetings"]

    Raises ValueError with shape diagnostics if none of the above match.
    """
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Single meeting object — wrap in list so downstream code is uniform
            meetings_inner = data.get("meetings")
            if isinstance(meetings_inner, list):
                return meetings_inner
            return [data]
        meetings = payload.get("meetings")
        if isinstance(meetings, list):
            return meetings

    raise ValueError(
        f"Cannot normalize meetings payload: "
        f"type={type(payload).__name__}, "
        f"keys={list(payload.keys()) if isinstance(payload, dict) else 'N/A'}"
    )


def _truncate_sample(item: Any, max_str_len: int = 120) -> Any:
    """
    Return a shallow, string-truncated copy of *item* suitable for diagnostic
    logging.  Only one level deep — nested dicts/lists are summarised by type
    and length so that no large payloads are accidentally stored or returned.
    """
    if not isinstance(item, dict):
        s = str(item)
        return s[:max_str_len] + "…" if len(s) > max_str_len else s
    result: dict[str, Any] = {}
    for k, v in item.items():
        if isinstance(v, list):
            result[k] = f"[list len={len(v)}]"
        elif isinstance(v, dict):
            result[k] = f"{{dict keys={list(v.keys())}}}"
        else:
            s = str(v)
            result[k] = s[:max_str_len] + "…" if len(s) > max_str_len else s
    return result


# ---------------------------------------------------------------------------
# PARSE ERROR — carries structured diagnostics for callers
# ---------------------------------------------------------------------------

class OddsProParseError(ValueError):
    """
    Raised when the OddsPro response cannot be parsed into the expected structure.
    Carries parse_stage, response_type, response_keys, first_item_keys,
    exception_message and sample_payload so callers can return structured
    diagnostics without needing to re-parse the error message string.
    """

    def __init__(
        self,
        message: str,
        parse_stage: str,
        response_keys: list[str],
        first_item_keys: list[str],
        response_type: str = "",
        exception_message: str = "",
        sample_payload: Any = None,
    ):
        super().__init__(message)
        self.parse_stage = parse_stage
        self.response_keys = response_keys
        self.first_item_keys = first_item_keys
        self.response_type = response_type or ""
        self.exception_message = exception_message or message
        self.sample_payload = sample_payload


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

        Supported response shapes (all handled):
          A. {"data": [...], ...}     - data is a list of meetings
          B. {"data": {...}, ...}     - data is a single meeting dict
          C. [...]                    - bare list of meetings
          D. {"meetings": [...], ...} - meetings key holds list

        Raises requests.exceptions.HTTPError on non-2xx responses so callers
        can map specific HTTP status codes to diagnostic error codes.
        Raises OddsProParseError (subclass of ValueError) on JSON parse failure,
        carrying full diagnostics: parse_stage, response_type, response_keys,
        first_item_keys, exception_message, sample_payload.
        """
        params: dict[str, Any] = {"country": self.country}
        if target_date:
            params["date"] = target_date

        resp = self._get("/api/external/meetings", params=params)
        status_code = resp.status_code

        try:
            payload = resp.json()
        except ValueError as e:
            log.error(f"OddsPro fetch_meetings: JSON parse failed (HTTP {status_code}): {e}")
            raise OddsProParseError(
                f"JSON decode error: {e}",
                parse_stage="root",
                response_keys=[],
                first_item_keys=[],
                response_type="invalid_json",
                exception_message=str(e),
                sample_payload=None,
            ) from e

        response_type = type(payload).__name__
        top_keys: list[str] = list(payload.keys()) if isinstance(payload, dict) else []
        first_item_keys: list[str] = []
        sample_payload: Any = None

        try:
            items = normalize_meetings_payload(payload)

            if items and isinstance(items[0], dict):
                first_item_keys = list(items[0].keys())
                sample_payload = _truncate_sample(items[0])

            meetings: list[MeetingRecord] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("id") or item.get("meetingId") or "")
                if not mid:
                    continue
                meetings.append(
                    MeetingRecord(
                        meeting_id=mid,
                        code=self._normalise_code(
                            item.get("type") or item.get("code") or item.get("raceType") or "HORSE"
                        ),
                        source=self.source_name,
                        track=self._clean_track(
                            item.get("track") or item.get("meetingTrack")
                            or item.get("venue") or item.get("name") or ""
                        ),
                        meeting_date=str(item.get("date") or target_date or ""),
                        state=str(
                            item.get("location") or item.get("state") or item.get("region") or ""
                        ),
                        country=str(item.get("country") or self.country),
                        extra={"raw": item},
                    )
                )
        except OddsProParseError:
            raise
        except ValueError as e:
            log.error(f"OddsPro fetch_meetings: normalize error (HTTP {status_code}): {e}")
            raise OddsProParseError(
                f"meetings normalize error: {e}",
                parse_stage="root",
                response_keys=top_keys,
                first_item_keys=first_item_keys,
                response_type=response_type,
                exception_message=str(e),
                sample_payload=sample_payload,
            ) from e
        except Exception as e:
            log.error(f"OddsPro fetch_meetings: parse error (HTTP {status_code}): {e}")
            raise OddsProParseError(
                f"meetings parse error: {e}",
                parse_stage="meetings",
                response_keys=top_keys,
                first_item_keys=first_item_keys,
                response_type=response_type,
                exception_message=str(e),
                sample_payload=sample_payload,
            ) from e

        log.info(f"OddsPro fetch_meetings: {len(meetings)} meetings for {target_date} (HTTP {status_code})")
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
        Also accepts: races / events / meetingsRaces as the race list key.
        """
        try:
            resp = self._get(f"/api/external/meeting/{meeting.meeting_id}")
            payload = resp.json()
        except Exception as e:
            log.error(f"OddsPro fetch_meeting_races({meeting.meeting_id}) failed: {e}")
            return []

        raw = (
            payload.get("data")
            or payload.get("meeting")
            or payload
        )
        races_raw = self._extract_races_list(raw)

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
        Also accepts: races / events / meetingsRaces as the race list key.
        """
        try:
            resp = self._get(f"/api/external/meeting/{meeting.meeting_id}")
            payload = resp.json()
        except Exception as e:
            log.error(f"OddsPro fetch_meeting_races_with_runners({meeting.meeting_id}) failed: {e}")
            return [], []

        raw = (
            payload.get("data")
            or payload.get("meeting")
            or payload
        )
        races_raw = self._extract_races_list(raw)

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
        Accepts: races / events / meetingsRaces as the race list key.
        """
        races_raw = self._extract_races_list(raw_meeting)

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

    def _extract_races_list(self, raw: Any) -> list:
        """
        Extract the list of race dicts from a meeting payload or raw meeting dict.
        Supports the following keys for the race list:
          - races          (primary documented key)
          - events         (alternate)
          - meetingsRaces  (alternate)
        If raw is already a list, returns it directly.
        """
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            return (
                raw.get("races")
                or raw.get("events")
                or raw.get("meetingsRaces")
                or []
            )
        return []

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
        track = self._clean_track(
            item.get("track") or item.get("meetingTrack")
            or item.get("venue") or (meeting.track if meeting else "")
        )
        code = self._normalise_code(
            item.get("type") or item.get("code") or item.get("raceType")
            or (meeting.code if meeting else "HORSE")
        )

        race_uid = self._make_race_uid(race_date, code, track, race_num)

        jump_time = (
            item.get("jumpTime") or item.get("jump_time")
            or item.get("startTime") or item.get("advertisedStart")
        )
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
        # Accepts: runners / field / entries / starters
        runners_raw = (
            item.get("runners") or item.get("field")
            or item.get("entries") or item.get("starters") or []
        )
        runners: list[RunnerRecord] = []

        for r in runners_raw:
            if not isinstance(r, dict):
                continue
            # Documented aliases: runnerNumber | number | saddleCloth
            number = r.get("runnerNumber") or r.get("number") or r.get("saddleCloth")
            try:
                number = int(number) if number is not None else None
            except (TypeError, ValueError):
                number = None

            # Documented aliases: boxNumber | box | box_num (for greyhound box draw)
            box_num = r.get("boxNumber") or r.get("box") or r.get("box_num")
            try:
                box_num = int(box_num) if box_num is not None else None
            except (TypeError, ValueError):
                box_num = None

            # barrier / barrierDraw for gallops/harness; box/boxNumber used above for greyhounds
            barrier_raw = r.get("barrier") or r.get("barrierDraw")

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
                    # Documented aliases: runnerName | name | horseName | dogName
                    name=str(
                        r.get("runnerName") or r.get("name")
                        or r.get("horseName") or r.get("dogName") or ""
                    ),
                    number=number,
                    barrier=barrier_raw,
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
