"""
data_engine.py — Unified multi-code data engine
Responsibilities: fetch, normalise, store, refresh.
No scoring logic belongs here.

Supports:
- GREYHOUND
- HORSE
- HARNESS

Design:
- connector-driven
- one internal schema for all codes
- source health tracking
- lifecycle-safe storage
- board/UI should always display track / race / time, never raw race_uid
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

log = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================
SUPPORTED_CODES = ("GREYHOUND", "HORSE", "HARNESS")

LIFECYCLE = [
    "fetched",
    "normalized",
    "scored",
    "packet_built",
    "ai_reviewed",
    "bet_logged",
    "result_captured",
    "learned",
]

DEFAULT_LIMITS = {
    "full_sweep_pause_seconds": 0.25,
    "rolling_refresh_pause_seconds": 0.15,
    "board_limit": 50,
}

DEFAULT_HEALTHY = {
    "status": "UNKNOWN",
    "consecutive_fails": 0,
    "last_ok": None,
    "last_fail": None,
}


# ============================================================
# DB HELPERS
# ============================================================
def _db():
    from db import get_db
    return get_db()


def _T(name: str) -> str:
    from db import T
    return T(name)


def _safe_query(fn, default=None):
    from db import safe_query
    return safe_query(fn, default)


# ============================================================
# SOURCE HEALTH
# ============================================================
_source_health: dict[str, dict[str, Any]] = {}


def mark_source_healthy(source: str):
    prev = _source_health.get(source, DEFAULT_HEALTHY.copy())
    _source_health[source] = {
        "status": "HEALTHY",
        "consecutive_fails": 0,
        "last_ok": time.time(),
        "last_fail": prev.get("last_fail"),
    }


def mark_source_failed(source: str):
    prev = _source_health.get(source, DEFAULT_HEALTHY.copy())
    fails = int(prev.get("consecutive_fails", 0)) + 1
    _source_health[source] = {
        "status": "DEGRADED" if fails >= 3 else "WARNING",
        "consecutive_fails": fails,
        "last_ok": prev.get("last_ok"),
        "last_fail": time.time(),
    }


def get_source_health() -> dict[str, dict[str, Any]]:
    return dict(_source_health)


# ============================================================
# NORMALISED MODELS
# ============================================================
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

    completeness_score: int = 0
    completeness_quality: str = "LOW"
    race_hash: str = ""
    lifecycle_state: str = "fetched"

    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        suffix = f" • {self.jump_time}" if self.jump_time else ""
        return f"{self.track} R{self.race_num}{suffix}"


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
    stats_json: dict[str, Any] = field(default_factory=dict)


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


# ============================================================
# COMMON HELPERS
# ============================================================
def normalise_code(code: str | None) -> str:
    raw = str(code or "GREYHOUND").upper()
    if raw == "THOROUGHBRED":
        return "HORSE"
    return raw


def make_race_uid(race_date: str, code: str, track: str, race_num: int) -> str:
    clean_track = str(track or "").strip().lower().replace(" ", "-")
    clean_code = normalise_code(code)
    return f"{race_date}_{clean_code}_{clean_track}_{race_num}"


def make_runner_hash(*parts: Any) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.md5(joined.encode()).hexdigest()[:12]


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def display_track(track: str | None) -> str:
    raw = str(track or "").strip().replace("-", " ")
    return " ".join(p.capitalize() for p in raw.split())


def coerce_float(v: Any) -> float | None:
    try:
        if v in (None, "", "—"):
            return None
        return float(v)
    except Exception:
        return None


def score_completeness(race: RaceRecord, runners: list[RunnerRecord]) -> dict[str, Any]:
    checks = {
        "jump_time": race.jump_time is not None,
        "grade": bool(race.grade),
        "distance": bool(race.distance),
        "runners_present": len(runners) >= 4,
        "best_times": sum(1 for r in runners if r.best_time) >= max(1, len(runners) // 2),
        "trainers": sum(1 for r in runners if r.trainer) >= max(1, len(runners) // 2),
        "career": sum(1 for r in runners if r.career) >= max(1, len(runners) // 2),
        "no_all_scratched": sum(1 for r in runners if not r.scratched) >= 4,
    }

    score = sum(1 for v in checks.values() if v)
    pct = round(score / len(checks) * 100)

    if pct >= 75:
        quality = "HIGH"
    elif pct >= 50:
        quality = "MODERATE"
    else:
        quality = "LOW"

    return {"score": pct, "quality": quality, "checks": checks}


def compute_race_hash(race: RaceRecord, runners: list[RunnerRecord], scratch_snapshot: list[int] | None = None) -> str:
    runner_bits = []
    for r in sorted(runners, key=lambda x: (x.box_num or 99, x.name or "")):
        runner_bits.append(
            f"{r.box_num}-{r.name}-{r.best_time}-{r.trainer}-{r.raw_hash}"
        )

    scratch_bits = [str(s) for s in sorted(scratch_snapshot or [])]

    key = "|".join(
        [
            race.race_uid,
            race.track,
            str(race.race_num),
            race.code,
            race.distance,
            str(race.jump_time or ""),
            ",".join(runner_bits),
            ",".join(scratch_bits),
        ]
    )
    return hashlib.md5(key.encode()).hexdigest()[:12]


def log_source_call(
    url: str,
    method: str,
    status: str,
    rows: int = 0,
    grv: bool = False,
    source: str | None = None,
    response_code: int | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
):
    try:
        payload = {
            "date": date.today().isoformat(),
            "source": source,
            "url": url,
            "method": method,
            "status": status,
            "response_code": response_code,
            "grv_detected": grv,
            "rows_returned": rows,
            "error_message": error_message,
            "duration_ms": duration_ms,
            "created_at": now_iso(),
        }
        _db().table(_T("source_log")).insert(payload).execute()
    except Exception:
        pass


# ============================================================
# CONNECTOR BASE
# ============================================================
class BaseConnector:
    source_name = "base"
    supported_codes: tuple[str, ...] = ()

    def is_enabled(self) -> bool:
        return True

    def fetch_meetings(self, target_date: str) -> list[MeetingRecord]:
        return []

    def fetch_meeting_races(self, meeting: MeetingRecord) -> list[RaceRecord]:
        return []

    def fetch_race_detail(
        self,
        race: RaceRecord,
        scratchings: dict[str, list[int]] | None = None,
    ) -> tuple[RaceRecord, list[RunnerRecord]]:
        return race, []

    def fetch_scratchings(self, target_date: str) -> dict[str, list[int]]:
        return {}

    def fetch_result(self, race: RaceRecord) -> ResultRecord | None:
        return None


# ============================================================
# CONNECTOR REGISTRY
# ============================================================
_CONNECTORS: list[BaseConnector] = []


def register_connector(connector: BaseConnector):
    if connector and connector not in _CONNECTORS:
        _CONNECTORS.append(connector)


def _load_optional_connectors():
    global _CONNECTORS
    if _CONNECTORS:
        return

    optional_modules = [
        ("connectors.thedogs_connector", "TheDogsConnector"),
        ("connectors.racenet_connector", "RacenetConnector"),
        ("connectors.harness_connector", "HarnessConnector"),
        ("connectors.pdf_connector", "PdfConnector"),
    ]

    loaded_any = False

    for module_name, class_name in optional_modules:
        try:
            module = __import__(module_name, fromlist=[class_name])
            cls = getattr(module, class_name)
            instance = cls()
            register_connector(instance)
            loaded_any = True
            log.info(f"Connector loaded: {module_name}.{class_name}")
        except Exception as e:
            log.debug(f"Connector not loaded: {module_name}.{class_name}: {e}")

    if not loaded_any:
        log.warning("No external connectors loaded. Data engine will run with empty connector registry.")


def get_connectors_for_code(code: str) -> list[BaseConnector]:
    _load_optional_connectors()
    clean_code = normalise_code(code)
    return [
        c for c in _CONNECTORS
        if c.is_enabled() and (clean_code in getattr(c, "supported_codes", ()))
    ]


def get_all_connectors() -> list[BaseConnector]:
    _load_optional_connectors()
    return [c for c in _CONNECTORS if c.is_enabled()]


# ============================================================
# STORAGE
# ============================================================
def upsert_race(race: RaceRecord) -> str | None:
    try:
        payload = {
            "race_uid": race.race_uid,
            "date": race.date,
            "track": race.track,
            "state": race.state,
            "race_num": race.race_num,
            "race_name": race.race_name,
            "code": race.code,
            "distance": race.distance,
            "grade": race.grade,
            "jump_time": race.jump_time,
            "time_status": race.time_status,
            "status": race.status,
            "source_url": race.source_url,
            "expert_form_url": race.expert_form_url,
            "completeness_score": race.completeness_score,
            "completeness_quality": race.completeness_quality,
            "race_hash": race.race_hash,
            "lifecycle_state": race.lifecycle_state,
            "last_verified_at": now_iso() if race.time_status == "VERIFIED" else None,
            "fetched_at": now_iso(),
        }

        res = _db().table(_T("today_races")).upsert(payload, on_conflict="race_uid").execute()

        if getattr(res, "data", None):
            return res.data[0]["id"]

        row = (
            _db().table(_T("today_races"))
            .select("id")
            .eq("race_uid", race.race_uid)
            .limit(1)
            .execute()
            .data
            or []
        )
        return row[0]["id"] if row else None
    except Exception as e:
        log.error(f"Upsert race failed {race.race_uid}: {e}")
        return None


def upsert_runners(race_id: str | None, race_uid: str, runners: list[RunnerRecord]):
    if not race_id or not runners:
        return

    try:
        _db().table(_T("today_runners")).delete().eq("race_uid", race_uid).execute()

        payload = []
        for r in runners:
            payload.append(
                {
                    "race_id": race_id,
                    "race_uid": race_uid,
                    "date": date.today().isoformat(),
                    "box_num": r.box_num,
                    "name": r.name,
                    "runner_name": r.name,
                    "trainer": r.trainer,
                    "owner": r.owner,
                    "weight": r.weight,
                    "best_time": r.best_time,
                    "career": r.career,
                    "price": r.price,
                    "rating": r.rating,
                    "run_style": r.run_style,
                    "early_speed": r.early_speed,
                    "scratched": bool(r.scratched),
                    "scratch_timing": r.scratch_timing,
                    "raw_hash": r.raw_hash,
                    "source_confidence": r.source_confidence,
                }
            )

        _db().table(_T("today_runners")).insert(payload).execute()
    except Exception as e:
        log.error(f"Upsert runners failed {race_uid}: {e}")


def update_lifecycle(race_uid: str, state: str):
    if state not in LIFECYCLE:
        return

    try:
        _db().table(_T("today_races")).update(
            {
                "lifecycle_state": state,
                f"{state}_at": now_iso(),
            }
        ).eq("race_uid", race_uid).execute()
    except Exception as e:
        log.error(f"Lifecycle update failed {race_uid} -> {state}: {e}")


def save_result(result: ResultRecord):
    if not result or not result.race_uid:
        return

    try:
        payload = {
            "race_uid": result.race_uid,
            "date": result.date,
            "track": result.track,
            "race_num": result.race_num,
            "code": result.code,
            "winner": result.winner,
            "winner_box": result.winner_box,
            "win_price": result.win_price,
            "place_2": result.place_2,
            "place_3": result.place_3,
            "margin": result.margin,
            "winning_time": result.winning_time,
            "source": result.source,
            "recorded_at": now_iso(),
        }

        _db().table(_T("results_log")).upsert(payload, on_conflict="race_uid").execute()
        _db().table(_T("today_races")).update(
            {
                "status": "completed",
                "lifecycle_state": "result_captured",
                "result_captured_at": now_iso(),
            }
        ).eq("race_uid", result.race_uid).execute()

        log.info(f"Result saved: {result.track} R{result.race_num} winner={result.winner}")
    except Exception as e:
        log.error(f"Save result failed {result.race_uid}: {e}")


def auto_settle_bets(result: ResultRecord):
    if not result or not result.winner:
        return

    try:
        pending = (
            _db().table(_T("bet_log"))
            .select("*")
            .eq("race_uid", result.race_uid)
            .eq("result", "PENDING")
            .execute()
            .data
            or []
        )

        winner = (result.winner or "").strip().lower()

        for bet in pending:
            runner = (bet.get("runner") or "").strip().lower()
            is_win = runner == winner
            res = "WIN" if is_win else "LOSS"
            stake = float(bet.get("stake") or 0)
            odds = float(bet.get("odds") or 0)
            pl = round(stake * (odds - 1), 2) if is_win else round(-stake, 2)

            _db().table(_T("bet_log")).update(
                {
                    "result": res,
                    "pl": pl,
                    "error_tag": None if is_win else "VARIANCE",
                    "settled_at": now_iso(),
                }
            ).eq("id", bet["id"]).execute()

            log.info(f"Auto-settled: {bet.get('runner')} {res} PL={pl}")
    except Exception as e:
        log.error(f"Auto-settle failed {result.race_uid}: {e}")


# ============================================================
# BOARD / READ HELPERS
# ============================================================
def get_next_race(anchor_time: str | None = None, code: str | None = None):
    try:
        query = (
            _db().table(_T("today_races"))
            .select("*")
            .eq("date", date.today().isoformat())
            .eq("status", "upcoming")
            .order("jump_time")
        )

        if code and normalise_code(code) != "ALL":
            query = query.eq("code", normalise_code(code))

        races = query.execute().data or []
        if not races:
            return None

        valid_races = [r for r in races if r.get("jump_time")]
        scan_pool = valid_races if valid_races else races

        if anchor_time:
            for race in scan_pool:
                jump_time = race.get("jump_time")
                if jump_time and jump_time > anchor_time:
                    return race

        return scan_pool[0]
    except Exception as e:
        log.error(f"get_next_race failed: {e}")
        return None


def get_board(limit: int = 10, code: str | None = None):
    try:
        query = (
            _db().table(_T("today_races"))
            .select("*")
            .eq("date", date.today().isoformat())
            .eq("status", "upcoming")
            .order("jump_time")
            .limit(limit)
        )

        if code and normalise_code(code) != "ALL":
            query = query.eq("code", normalise_code(code))

        rows = query.execute().data or []
        return rows
    except Exception as e:
        log.error(f"get_board failed: {e}")
        return []


def get_race_with_runners(track: str, race_num: int, race_date: str | None = None, code: str | None = None):
    try:
        target_date = race_date or date.today().isoformat()
        query = (
            _db().table(_T("today_races"))
            .select("*")
            .eq("date", target_date)
            .eq("track", str(track or "").strip().lower())
            .eq("race_num", race_num)
            .limit(1)
        )

        if code and normalise_code(code) != "ALL":
            query = query.eq("code", normalise_code(code))

        races = query.execute().data or []
        if not races:
            return None, []

        race = races[0]
        runners = (
            _db().table(_T("today_runners"))
            .select("*")
            .eq("race_uid", race["race_uid"])
            .order("box_num")
            .execute()
            .data
            or []
        )
        return race, runners
    except Exception as e:
        log.error(f"get_race_with_runners failed: {e}")
        return None, []


# ============================================================
# CONNECTOR ORCHESTRATION
# ============================================================
def fetch_meetings_for_code(code: str, target_date: str | None = None) -> list[MeetingRecord]:
    target_date = target_date or date.today().isoformat()
    meetings: list[MeetingRecord] = []

    for connector in get_connectors_for_code(code):
        source = getattr(connector, "source_name", connector.__class__.__name__)
        try:
            rows = connector.fetch_meetings(target_date) or []
            if rows:
                meetings.extend(rows)
                mark_source_healthy(source)
                log.info(f"{source}: fetched {len(rows)} meeting(s) for {code}")
            else:
                log.warning(f"{source}: no meetings returned for {code}")
        except Exception as e:
            mark_source_failed(source)
            log.error(f"{source}: fetch_meetings failed for {code}: {e}")

    deduped = {}
    for m in meetings:
        key = (m.code, m.track, m.meeting_date)
        deduped[key] = m
    return list(deduped.values())


def fetch_scratchings_for_code(code: str, target_date: str | None = None) -> dict[str, list[int]]:
    target_date = target_date or date.today().isoformat()
    merged: dict[str, list[int]] = {}

    for connector in get_connectors_for_code(code):
        source = getattr(connector, "source_name", connector.__class__.__name__)
        try:
            data = connector.fetch_scratchings(target_date) or {}
            if data:
                for race_uid, boxes in data.items():
                    merged.setdefault(race_uid, [])
                    merged[race_uid].extend(boxes or [])
                mark_source_healthy(source)
        except Exception as e:
            mark_source_failed(source)
            log.error(f"{source}: fetch_scratchings failed for {code}: {e}")

    for race_uid in merged:
        merged[race_uid] = sorted(set(int(x) for x in merged[race_uid] if str(x).isdigit()))

    return merged


def fetch_meeting_races(meeting: MeetingRecord) -> list[RaceRecord]:
    connectors = [c for c in get_connectors_for_code(meeting.code) if c.source_name == meeting.source]
    if not connectors:
        connectors = get_connectors_for_code(meeting.code)

    for connector in connectors:
        source = getattr(connector, "source_name", connector.__class__.__name__)
        try:
            races = connector.fetch_meeting_races(meeting) or []
            if races:
                mark_source_healthy(source)
                return races
        except Exception as e:
            mark_source_failed(source)
            log.error(f"{source}: fetch_meeting_races failed {meeting.track}: {e}")

    return []


def fetch_race_detail(race: RaceRecord, scratchings: dict[str, list[int]] | None = None) -> tuple[RaceRecord, list[RunnerRecord]]:
    connectors = [c for c in get_connectors_for_code(race.code) if c.source_name == race.source]
    if not connectors:
        connectors = get_connectors_for_code(race.code)

    for connector in connectors:
        source = getattr(connector, "source_name", connector.__class__.__name__)
        try:
            enriched_race, runners = connector.fetch_race_detail(race, scratchings=scratchings or {})
            if runners:
                mark_source_healthy(source)
                return enriched_race, runners
        except Exception as e:
            mark_source_failed(source)
            log.error(f"{source}: fetch_race_detail failed {race.race_uid}: {e}")

    return race, []


def fetch_result_for_race(race: RaceRecord) -> ResultRecord | None:
    connectors = [c for c in get_connectors_for_code(race.code) if c.source_name == race.source]
    if not connectors:
        connectors = get_connectors_for_code(race.code)

    for connector in connectors:
        source = getattr(connector, "source_name", connector.__class__.__name__)
        try:
            result = connector.fetch_result(race)
            if result:
                mark_source_healthy(source)
                return result
        except Exception as e:
            mark_source_failed(source)
            log.error(f"{source}: fetch_result failed {race.race_uid}: {e}")

    return None


# ============================================================
# FULL SWEEP
# ============================================================
def full_sweep(codes: list[str] | None = None) -> dict[str, Any]:
    start = time.time()
    codes = [normalise_code(c) for c in (codes or list(SUPPORTED_CODES))]

    log.info("=== UNIFIED FULL SWEEP START ===")

    total_meetings = 0
    total_races = 0
    total_runners = 0
    processed_upcoming = 0
    processed_completed = 0
    failed_races = 0
    failed_meetings = 0

    target_date = date.today().isoformat()

    for code in codes:
        scratchings = fetch_scratchings_for_code(code, target_date=target_date)
        meetings = fetch_meetings_for_code(code, target_date=target_date)

        if not meetings:
            log.warning(f"{code}: no meetings found")
            continue

        total_meetings += len(meetings)

        for meeting in meetings:
            try:
                races = fetch_meeting_races(meeting)
                if not races:
                    failed_meetings += 1
                    log.warning(f"{code}: no races extracted for meeting {meeting.track}")
                    continue

                for race in races:
                    try:
                        if race.status == "upcoming":
                            race, runners = fetch_race_detail(race, scratchings=scratchings)
                            completeness = score_completeness(race, runners)
                            race.completeness_score = completeness["score"]
                            race.completeness_quality = completeness["quality"]
                            race.race_hash = compute_race_hash(
                                race,
                                runners,
                                scratchings.get(race.race_uid, []),
                            )

                            race_id = upsert_race(race)
                            if race_id:
                                update_lifecycle(race.race_uid, "fetched")
                                upsert_runners(race_id, race.race_uid, runners)
                                total_runners += len(runners)
                                processed_upcoming += 1

                        else:
                            upsert_race(race)
                            result = fetch_result_for_race(race)
                            if result:
                                save_result(result)
                                auto_settle_bets(result)
                            processed_completed += 1

                        total_races += 1
                        time.sleep(DEFAULT_LIMITS["full_sweep_pause_seconds"])

                    except Exception as e:
                        failed_races += 1
                        log.error(f"{code}: race processing failed {race.race_uid}: {e}")

            except Exception as e:
                failed_meetings += 1
                log.error(f"{code}: meeting processing failed {meeting.track}: {e}")

    elapsed = round(time.time() - start, 1)
    summary = {
        "ok": True,
        "codes": codes,
        "meetings": total_meetings,
        "races": total_races,
        "runners": total_runners,
        "processed_upcoming": processed_upcoming,
        "processed_completed": processed_completed,
        "failed_races": failed_races,
        "failed_meetings": failed_meetings,
        "elapsed": elapsed,
    }

    log.info(f"=== UNIFIED FULL SWEEP COMPLETE: {summary} ===")
    return summary


# ============================================================
# ROLLING REFRESH
# ============================================================
def rolling_refresh(codes: list[str] | None = None) -> dict[str, Any]:
    codes = [normalise_code(c) for c in (codes or list(SUPPORTED_CODES))]
    log.info(f"Rolling refresh start for codes={codes}")

    results_captured = 0
    late_scratches_applied = 0
    upcoming_checked = 0
    failed = 0

    try:
        query = (
            _db().table(_T("today_races"))
            .select("*")
            .eq("date", date.today().isoformat())
            .eq("status", "upcoming")
            .order("jump_time")
        )
        rows = query.execute().data or []
    except Exception as e:
        log.error(f"rolling_refresh load failed: {e}")
        return {"ok": False, "error": "load_failed"}

    upcoming = [r for r in rows if normalise_code(r.get("code")) in codes]
    if not upcoming:
        return {
            "ok": True,
            "results_captured": 0,
            "late_scratches_applied": 0,
            "upcoming_checked": 0,
            "warning": "no_upcoming_races",
        }

    scratch_by_code: dict[str, dict[str, list[int]]] = {}
    for code in codes:
        scratch_by_code[code] = fetch_scratchings_for_code(code)

    for row in upcoming:
        try:
            upcoming_checked += 1
            code = normalise_code(row.get("code"))
            race = RaceRecord(
                race_uid=row["race_uid"],
                date=row.get("date") or date.today().isoformat(),
                track=row.get("track") or "",
                race_num=row.get("race_num") or 0,
                code=code,
                source=row.get("source") or "",
                state=row.get("state") or "",
                race_name=row.get("race_name") or "",
                distance=row.get("distance") or "",
                grade=row.get("grade") or "",
                jump_time=row.get("jump_time"),
                status=row.get("status") or "upcoming",
                source_url=row.get("source_url") or "",
                expert_form_url=row.get("expert_form_url") or "",
            )

            result = fetch_result_for_race(race)
            if result:
                save_result(result)
                auto_settle_bets(result)
                results_captured += 1
                time.sleep(DEFAULT_LIMITS["rolling_refresh_pause_seconds"])
                continue

            new_scratches = scratch_by_code.get(code, {}).get(race.race_uid, [])
            if new_scratches:
                _db().table(_T("today_runners")).update(
                    {"scratched": True, "scratch_timing": "late"}
                ).eq("race_uid", race.race_uid).in_("box_num", new_scratches).execute()

                try:
                    from cache import cache_clear
                    cache_clear(race.race_uid)
                except Exception:
                    pass

                late_scratches_applied += 1
                log.info(f"{race.race_uid}: applied late scratches {new_scratches}")

            time.sleep(DEFAULT_LIMITS["rolling_refresh_pause_seconds"])

        except Exception as e:
            failed += 1
            log.error(f"rolling_refresh error {row.get('race_uid')}: {e}")

    summary = {
        "ok": True,
        "results_captured": results_captured,
        "late_scratches_applied": late_scratches_applied,
        "upcoming_checked": upcoming_checked,
        "failed": failed,
    }
    log.info(f"Rolling refresh summary: {summary}")
    return summary


if __name__ == "__main__":
    full_sweep()
