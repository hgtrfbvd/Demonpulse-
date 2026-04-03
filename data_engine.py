# FILE: core/data_engine.py

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from connectors.racenet_connector import RacenetConnector
from connectors.thedogs_connector import TheDogsConnector

log = logging.getLogger(__name__)

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


def _db():
    from db import get_db
    return get_db()


def _T(name: str) -> str:
    from db import T
    return T(name)


_source_health: dict[str, dict[str, Any]] = {}


def mark_source_healthy(source: str):
    prev = _source_health.get(source, {})
    _source_health[source] = {
        "status": "HEALTHY",
        "consecutive_fails": 0,
        "last_ok": time.time(),
        "last_fail": prev.get("last_fail"),
    }


def mark_source_failed(source: str):
    prev = _source_health.get(source, {})
    fails = int(prev.get("consecutive_fails", 0)) + 1
    _source_health[source] = {
        "status": "DEGRADED" if fails >= 3 else "WARNING",
        "consecutive_fails": fails,
        "last_ok": prev.get("last_ok"),
        "last_fail": time.time(),
    }


def get_source_health() -> dict[str, dict[str, Any]]:
    return dict(_source_health)


class BaseConnector:
    source_name = "base"
    supported_codes: tuple[str, ...] = ()

    def is_enabled(self) -> bool:
        return True

    def fetch_meetings(self, target_date: str | None = None):
        return []

    def fetch_meeting_races(self, meeting):
        return []

    def fetch_race_detail(self, race, scratchings=None):
        return race, []

    def fetch_scratchings(self, target_date: str | None = None):
        return {}

    def fetch_result(self, race):
        return None


_CONNECTORS: list[BaseConnector] = []


def register_connector(connector: BaseConnector):
    if connector and connector not in _CONNECTORS:
        _CONNECTORS.append(connector)


def load_default_connectors():
    if _CONNECTORS:
        return
    register_connector(TheDogsConnector())
    register_connector(RacenetConnector())
    log.info("Default connectors loaded: %s", [c.source_name for c in _CONNECTORS])


def get_connectors_for_code(code: str) -> list[BaseConnector]:
    load_default_connectors()
    code = normalise_code(code)
    return [
        c for c in _CONNECTORS
        if c.is_enabled() and code in getattr(c, "supported_codes", ())
    ]


def get_all_connectors() -> list[BaseConnector]:
    load_default_connectors()
    return [c for c in _CONNECTORS if c.is_enabled()]


def normalise_code(code: str | None) -> str:
    raw = str(code or "GREYHOUND").upper()
    if raw == "THOROUGHBRED":
        return "HORSE"
    return raw


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def score_completeness(race, runners) -> dict[str, Any]:
    checks = {
        "jump_time": race.jump_time is not None,
        "grade": bool(race.grade),
        "distance": bool(race.distance),
        "runners_present": len(runners) >= 4,
        "best_times": sum(1 for r in runners if getattr(r, "best_time", None)) >= max(1, len(runners) // 2),
        "trainers": sum(1 for r in runners if getattr(r, "trainer", None)) >= max(1, len(runners) // 2),
        "career": sum(1 for r in runners if getattr(r, "career", None)) >= max(1, len(runners) // 2),
        "no_all_scratched": sum(1 for r in runners if not getattr(r, "scratched", False)) >= 4,
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


def compute_race_hash(race, runners, scratch_snapshot=None) -> str:
    import hashlib
    runner_bits = []
    for r in sorted(runners, key=lambda x: ((getattr(x, "box_num", None) or 99), getattr(x, "name", ""))):
        runner_bits.append(
            f"{getattr(r, 'box_num', '')}-{getattr(r, 'name', '')}-{getattr(r, 'best_time', '')}-{getattr(r, 'trainer', '')}-{getattr(r, 'raw_hash', '')}"
        )
    scratch_bits = [str(s) for s in sorted(scratch_snapshot or [])]
    key = "|".join([
        getattr(race, "race_uid", ""),
        getattr(race, "track", ""),
        str(getattr(race, "race_num", "")),
        getattr(race, "code", ""),
        getattr(race, "distance", ""),
        str(getattr(race, "jump_time", "") or ""),
        ",".join(runner_bits),
        ",".join(scratch_bits),
    ])
    return hashlib.md5(key.encode()).hexdigest()[:12]


def upsert_race(race) -> str | None:
    try:
        payload = {
            "race_uid": race.race_uid,
            "date": race.date,
            "track": race.track,
            "state": getattr(race, "state", ""),
            "race_num": race.race_num,
            "race_name": getattr(race, "race_name", ""),
            "code": race.code,
            "distance": getattr(race, "distance", ""),
            "grade": getattr(race, "grade", ""),
            "jump_time": getattr(race, "jump_time", None),
            "time_status": getattr(race, "time_status", "PARTIAL"),
            "status": getattr(race, "status", "upcoming"),
            "source_url": getattr(race, "source_url", ""),
            "expert_form_url": getattr(race, "expert_form_url", ""),
            "completeness_score": getattr(race, "completeness_score", 0),
            "completeness_quality": getattr(race, "completeness_quality", "LOW"),
            "race_hash": getattr(race, "race_hash", ""),
            "lifecycle_state": getattr(race, "lifecycle_state", "fetched"),
            "last_verified_at": now_iso() if getattr(race, "time_status", "PARTIAL") == "VERIFIED" else None,
            "fetched_at": now_iso(),
            "source": getattr(race, "source", ""),
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
        log.error("Upsert race failed %s: %s", getattr(race, "race_uid", "unknown"), e)
        return None


def upsert_runners(race_id: str | None, race_uid: str, runners):
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
                    "box_num": getattr(r, "box_num", None),
                    "name": getattr(r, "name", None),
                    "runner_name": getattr(r, "name", None),
                    "trainer": getattr(r, "trainer", ""),
                    "owner": getattr(r, "owner", ""),
                    "weight": getattr(r, "weight", None),
                    "best_time": getattr(r, "best_time", None),
                    "career": getattr(r, "career", None),
                    "price": getattr(r, "price", None),
                    "rating": getattr(r, "rating", None),
                    "run_style": getattr(r, "run_style", None),
                    "early_speed": getattr(r, "early_speed", None),
                    "scratched": bool(getattr(r, "scratched", False)),
                    "scratch_timing": getattr(r, "scratch_timing", None),
                    "raw_hash": getattr(r, "raw_hash", ""),
                    "source_confidence": getattr(r, "source_confidence", "official"),
                }
            )

        _db().table(_T("today_runners")).insert(payload).execute()
    except Exception as e:
        log.error("Upsert runners failed %s: %s", race_uid, e)


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
        log.error("Lifecycle update failed %s -> %s: %s", race_uid, state, e)


def save_result(result):
    if not result or not getattr(result, "race_uid", None):
        return

    try:
        payload = {
            "race_uid": result.race_uid,
            "date": result.date,
            "track": result.track,
            "race_num": result.race_num,
            "code": result.code,
            "winner": result.winner,
            "winner_box": getattr(result, "winner_box", None),
            "win_price": getattr(result, "win_price", None),
            "place_2": getattr(result, "place_2", None),
            "place_3": getattr(result, "place_3", None),
            "margin": getattr(result, "margin", None),
            "winning_time": getattr(result, "winning_time", None),
            "source": getattr(result, "source", ""),
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

        log.info("Result saved: %s R%s winner=%s", result.track, result.race_num, result.winner)
    except Exception as e:
        log.error("Save result failed %s: %s", getattr(result, "race_uid", "unknown"), e)


def auto_settle_bets(result):
    if not result or not getattr(result, "winner", None):
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
    except Exception as e:
        log.error("Auto-settle failed %s: %s", getattr(result, "race_uid", "unknown"), e)


def fetch_meetings_for_code(code: str, target_date: str | None = None):
    target_date = target_date or date.today().isoformat()
    meetings = []

    for connector in get_connectors_for_code(code):
        source = getattr(connector, "source_name", connector.__class__.__name__)
        try:
            rows = connector.fetch_meetings(target_date) or []
            if rows:
                meetings.extend(rows)
                mark_source_healthy(source)
                log.info("%s: fetched %s meeting(s) for %s", source, len(rows), code)
            else:
                log.warning("%s: no meetings returned for %s", source, code)
        except Exception as e:
            mark_source_failed(source)
            log.error("%s: fetch_meetings failed for %s: %s", source, code, e)

    deduped = {}
    for m in meetings:
        key = (m.code, m.track, m.meeting_date)
        deduped[key] = m
    return list(deduped.values())


def fetch_scratchings_for_code(code: str, target_date: str | None = None):
    target_date = target_date or date.today().isoformat()
    merged = {}

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
            log.error("%s: fetch_scratchings failed for %s: %s", source, code, e)

    for race_uid in merged:
        merged[race_uid] = sorted(set(int(x) for x in merged[race_uid] if str(x).isdigit()))

    return merged


def fetch_meeting_races(meeting):
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
            log.error("%s: fetch_meeting_races failed %s: %s", source, meeting.track, e)

    return []


def fetch_race_detail(race, scratchings=None):
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
            log.error("%s: fetch_race_detail failed %s: %s", source, race.race_uid, e)

    return race, []


def fetch_result_for_race(race):
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
            log.error("%s: fetch_result failed %s: %s", source, race.race_uid, e)

    return None


def full_sweep(codes: list[str] | None = None) -> dict[str, Any]:
    start = time.time()
    codes = [normalise_code(c) for c in (codes or list(SUPPORTED_CODES))]
    load_default_connectors()

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
            log.warning("%s: no meetings found", code)
            continue

        total_meetings += len(meetings)

        for meeting in meetings:
            try:
                races = fetch_meeting_races(meeting)
                if not races:
                    failed_meetings += 1
                    continue

                for race in races:
                    try:
                        if getattr(race, "status", "upcoming") == "upcoming":
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
                        log.error("%s: race processing failed %s: %s", code, getattr(race, "race_uid", "unknown"), e)

            except Exception as e:
                failed_meetings += 1
                log.error("%s: meeting processing failed %s: %s", code, getattr(meeting, "track", "unknown"), e)

    elapsed = round(time.time() - start, 1)
    return {
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


def rolling_refresh(codes: list[str] | None = None) -> dict[str, Any]:
    codes = [normalise_code(c) for c in (codes or list(SUPPORTED_CODES))]
    load_default_connectors()

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
        log.error("rolling_refresh load failed: %s", e)
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

    scratch_by_code = {}
    for code in codes:
        scratch_by_code[code] = fetch_scratchings_for_code(code)

    for row in upcoming:
        try:
            upcoming_checked += 1
            code = normalise_code(row.get("code"))

            if code == "GREYHOUND":
                race_obj = TheDogsConnector.__annotations__ if False else None  # no-op for lint quietness

            class TempRace:
                pass

            race = TempRace()
            race.race_uid = row["race_uid"]
            race.date = row.get("date") or date.today().isoformat()
            race.track = row.get("track") or ""
            race.race_num = row.get("race_num") or 0
            race.code = code
            race.source = row.get("source") or ""
            race.state = row.get("state") or ""
            race.race_name = row.get("race_name") or ""
            race.distance = row.get("distance") or ""
            race.grade = row.get("grade") or ""
            race.jump_time = row.get("jump_time")
            race.status = row.get("status") or "upcoming"
            race.source_url = row.get("source_url") or ""
            race.expert_form_url = row.get("expert_form_url") or ""

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
                late_scratches_applied += 1

            time.sleep(DEFAULT_LIMITS["rolling_refresh_pause_seconds"])
        except Exception as e:
            failed += 1
            log.error("rolling_refresh error %s: %s", row.get("race_uid"), e)

    return {
        "ok": True,
        "results_captured": results_captured,
        "late_scratches_applied": late_scratches_applied,
        "upcoming_checked": upcoming_checked,
        "failed": failed,
    }


def get_board(limit: int = 10, code: str | None = None):
    try:
        load_default_connectors()
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

        return query.execute().data or []
    except Exception as e:
        log.error("get_board failed: %s", e)
        return []


def get_next_race(anchor_time: str | None = None, code: str | None = None):
    try:
        load_default_connectors()
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
        log.error("get_next_race failed: %s", e)
        return None


def get_race_with_runners(track: str, race_num: int, race_date: str | None = None, code: str | None = None):
    try:
        load_default_connectors()
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
        log.error("get_race_with_runners failed: %s", e)
        return None, []


if __name__ == "__main__":
    print(full_sweep())
