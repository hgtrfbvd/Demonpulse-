"""
parsers/dogs_source_parser.py
==============================
Parse rendered HTML from thedogs.com.au race pages into our internal
DogsRacePacket schema.

This parser operates ONLY on HTML captured by dogs_race_capturer.
It never calls external APIs. Missing fields are stored as None.

The output schema matches what pipeline.py's _store_race() expects.
Derived stats (early_speed_rating, finish_strength_rating, etc.) are
computed by features.compute_greyhound_derived() after raw extraction.

Logging prefix: [DOGS_PARSER]
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _text(el) -> str:
    """Get stripped text from a BS4 element, or ''."""
    if el is None:
        return ""
    return el.get_text(separator=" ", strip=True)


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_time(text: str) -> str | None:
    """Extract HH:MM from a time string."""
    m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", text.strip(), re.IGNORECASE)
    if not m:
        return None
    hour, minute, period = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if period == "pm" and hour != 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _parse_distance(text: str) -> int | None:
    """Extract numeric distance in metres from e.g. '380m' or '380'."""
    m = re.search(r"(\d{3,4})\s*m?", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_pct(text: str) -> float | None:
    """Extract percentage float from e.g. '33.3%'."""
    m = re.search(r"([\d.]+)\s*%", text)
    return _safe_float(m.group(1)) if m else None


def _infer_state_from_text(text: str) -> str | None:
    mapping = {
        "QLD": ["queensland", "qld"],
        "NSW": ["new south wales", "nsw"],
        "VIC": ["victoria", "vic"],
        "SA": ["south australia", "sa"],
        "WA": ["western australia", "wa"],
        "TAS": ["tasmania", "tas"],
        "NT": ["northern territory", "nt"],
        "ACT": ["act"],
        "NZ": ["new zealand", "nz"],
    }
    lower = text.lower()
    for code, patterns in mapping.items():
        if any(p in lower for p in patterns):
            return code
    return None


# ---------------------------------------------------------------------------
# MAIN PARSER
# ---------------------------------------------------------------------------

def parse_race_page(
    html: str,
    source_url: str,
    date_slug: str,
    board_entry: dict | None = None,
) -> dict | None:
    """
    Parse a rendered thedogs.com.au race expert-form page.

    Args:
        html:        Rendered HTML from dogs_race_capturer
        source_url:  URL that was captured (for schema + error tracking)
        date_slug:   ISO date string e.g. "2026-04-10"
        board_entry: Optional DogsBoardEntry.to_dict() to seed known fields

    Returns:
        Raw race dict suitable for pipeline._store_race(), or None on total failure.
        Partial parse is preferred over total failure: missing fields set to None.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.error("[DOGS_PARSER] beautifulsoup4 not installed")
        return None

    if not html:
        log.error("[DOGS_PARSER] empty HTML passed to parser")
        return None

    errors: list[str] = []
    soup = BeautifulSoup(html, "lxml")
    board = board_entry or {}

    # ------------------------------------------------------------------
    # RACE-LEVEL FIELDS
    # ------------------------------------------------------------------

    # Track name — from board entry or page title/heading
    track_name: str | None = board.get("track_name")
    if not track_name:
        for sel in ["h1", "h2", "[class*='track-name']", "[class*='venue-name']"]:
            el = soup.select_one(sel)
            if el:
                track_name = _text(el) or None
                break

    # State
    state: str | None = board.get("state")
    if not state:
        full_text = soup.get_text(separator=" ")
        state = _infer_state_from_text(full_text)

    # Race number
    race_number: int | None = board.get("race_number")
    if not race_number:
        for sel in ["[class*='race-number']", "[class*='race-num']"]:
            el = soup.select_one(sel)
            if el:
                race_number = _safe_int(_text(el))
                if race_number:
                    break
        if not race_number:
            # Try parsing from URL: /racing/{slug}/{date}/{num}
            m = re.search(r"/racing/[^/]+/\d{4}-\d{2}-\d{2}/(\d+)", source_url)
            if m:
                race_number = _safe_int(m.group(1))

    # Race time
    race_time: str | None = board.get("race_time")
    if not race_time:
        for sel in ["[class*='race-time']", "[class*='jump-time']", "time"]:
            el = soup.select_one(sel)
            if el:
                race_time = _parse_time(_text(el))
                if race_time:
                    break

    # Distance
    distance_m: int | None = None
    for sel in ["[class*='distance']", "[class*='dist']"]:
        el = soup.select_one(sel)
        if el:
            distance_m = _parse_distance(_text(el))
            if distance_m:
                break
    if not distance_m:
        m = re.search(r"\b(\d{3,4})m\b", soup.get_text())
        if m:
            distance_m = _safe_int(m.group(1))

    # Grade
    grade: str | None = None
    for sel in ["[class*='grade']", "[class*='class']"]:
        el = soup.select_one(sel)
        if el:
            t = _text(el)
            if t and len(t) < 40:
                grade = t
                break

    # Race type
    race_type: str | None = None
    for sel in ["[class*='race-type']", "[class*='race-name']"]:
        el = soup.select_one(sel)
        if el:
            t = _text(el)
            if t and len(t) < 60:
                race_type = t
                break

    # Track condition
    track_condition: str | None = None
    full_text = soup.get_text(separator=" ")
    for cond in ["Good", "Slow", "Heavy", "Firm", "Soft", "Synthetic"]:
        if cond.lower() in full_text.lower():
            track_condition = cond
            break

    # Weather
    weather: str | None = None
    for sel in ["[class*='weather']"]:
        el = soup.select_one(sel)
        if el:
            weather = _text(el) or None

    # Prize money
    prize_money: str | None = None
    m_prize = re.search(r"\$[\d,]+", soup.get_text())
    if m_prize:
        prize_money = m_prize.group(0)

    # First bend distance
    first_bend_distance: int | None = None
    m_bend = re.search(r"first\s+bend[^:]*:\s*(\d+)\s*m", full_text, re.IGNORECASE)
    if m_bend:
        first_bend_distance = _safe_int(m_bend.group(1))

    # ------------------------------------------------------------------
    # RUNNERS
    # ------------------------------------------------------------------
    runners = _extract_runners(soup, errors)

    # ------------------------------------------------------------------
    # ASSEMBLE RAW DICT
    # ------------------------------------------------------------------
    raw: dict = {
        "track_name": track_name,
        "state": state,
        "date": date_slug,
        "race_number": race_number,
        "race_time": race_time,
        "distance_m": distance_m,
        "grade": grade,
        "race_type": race_type,
        "track_condition": track_condition,
        "weather": weather,
        "prize_money": prize_money,
        "first_bend_distance": first_bend_distance,
        "runners": runners,
        "_source_url": source_url,
        "_parse_errors": errors,
    }

    if not track_name:
        errors.append("track_name_missing")
    if not race_number:
        errors.append("race_number_missing")
    if not runners:
        errors.append("runners_empty")

    if errors:
        log.warning(
            f"[DOGS_PARSER] partial parse errors={errors} "
            f"track={track_name!r} R{race_number} url={source_url}"
        )
    else:
        log.info(
            f"[DOGS_PARSER] parse ok "
            f"track={track_name} R{race_number} runners={len(runners)} "
            f"source=thedogs.com.au"
        )

    return raw


def _extract_runners(soup, errors: list[str]) -> list[dict]:
    """
    Extract runner data from the race expert-form page.
    Returns list of runner dicts. Stores None for missing fields.
    """
    runners: list[dict] = []

    # Selector strategy: look for runner/dog row containers
    runner_els = soup.select(
        "[class*='runner'], [class*='form-guide__runner'], "
        "[class*='race-card__runner'], tr[class*='dog']"
    )

    if not runner_els:
        # Fallback: any table rows with a box number in first cell
        runner_els = [
            tr for tr in soup.select("table tr")
            if re.match(r"^\s*\d+\s*$", tr.select_one("td").get_text() if tr.select_one("td") else "")
        ]

    for el in runner_els:
        text = _text(el)
        if not text:
            continue

        runner: dict = {}

        # Box number
        box_el = el.select_one(
            "[class*='box'], [class*='barrier'], [class*='number'], td:first-child"
        )
        runner["box"] = _safe_int(_text(box_el)) if box_el else None
        if not runner["box"]:
            # Try first number in the row text
            m = re.match(r"\s*(\d{1,2})\s", text)
            runner["box"] = _safe_int(m.group(1)) if m else None

        # Runner name
        name_el = el.select_one(
            "[class*='name'], [class*='dog-name'], [class*='runner-name']"
        )
        runner["name"] = _text(name_el) if name_el else None
        if not runner["name"]:
            # Heuristic: second capital word group
            words = [w for w in re.split(r"\s+", text) if w and w[0].isupper()]
            runner["name"] = " ".join(words[:3]) if words else None

        # Trainer
        trainer_el = el.select_one("[class*='trainer']")
        runner["trainer"] = _text(trainer_el) if trainer_el else None

        # Weight
        wt_el = el.select_one("[class*='weight']")
        runner["weight"] = _safe_float(_text(wt_el).replace("kg", "")) if wt_el else None

        # Scratched
        runner["scratched"] = bool(
            el.select_one("[class*='scratch'], [class*='scr']")
        ) or "SCR" in text.upper() or "SCRATCHED" in text.upper()

        # Last form string (e.g. "17" or "1215")
        form_el = el.select_one("[class*='form'], [class*='last4'], [class*='recent']")
        last4 = _text(form_el) if form_el else None
        if last4 and re.match(r"^[\dXx\.\-]{1,8}$", last4):
            runner["last4"] = last4
        else:
            runner["last4"] = None

        # Last start position
        runner["last_start_position"] = _safe_int(last4[0]) if last4 and last4[0].isdigit() else None

        # Times — look for numeric values in range 20–40 (greyhound race times)
        time_vals = re.findall(r"\b(2\d\.\d{2}|3\d\.\d{2})\b", text)
        runner["last_start_time"] = time_vals[0] if time_vals else None
        runner["best_time_distance_match"] = time_vals[1] if len(time_vals) > 1 else time_vals[0] if time_vals else None

        # Split time (typically 6–9 seconds)
        split_vals = re.findall(r"\b([6-9]\.\d{2})\b", text)
        runner["split_time"] = split_vals[0] if split_vals else None

        # Career stats pattern: e.g. "12: 3-2-1"
        career_m = re.search(r"(\d+)\s*:\s*(\d+)\s*[-–]\s*(\d+)\s*[-–]\s*(\d+)", text)
        if career_m:
            runner["career_starts"] = _safe_int(career_m.group(1))
            runner["career_wins"] = _safe_int(career_m.group(2))
            runner["career_places"] = _safe_int(career_m.group(3))
        else:
            runner["career_starts"] = None
            runner["career_wins"] = None
            runner["career_places"] = None

        # Win/place percentages
        pct_vals = re.findall(r"([\d.]+)\s*%", text)
        runner["win_pct"] = _safe_float(pct_vals[0]) if pct_vals else None
        runner["place_pct"] = _safe_float(pct_vals[1]) if len(pct_vals) > 1 else None

        # Prize money
        pm_m = re.search(r"\$([\d,]+)", text)
        runner["prize_money_career"] = pm_m.group(0) if pm_m else None

        # Odds (e.g. "$5.00" or "5.00")
        odds_el = el.select_one("[class*='odds'], [class*='price'], [class*='win-price']")
        runner["odds"] = _text(odds_el) if odds_el else None

        if runner.get("box") or runner.get("name"):
            runners.append(runner)

    return runners


# ---------------------------------------------------------------------------
# NORMALISE FOR STORAGE
# ---------------------------------------------------------------------------

def normalise_for_db(raw: dict, today: str) -> dict:
    """
    Convert a raw parsed dict (from parse_race_page) into the DB today_races schema.
    This replaces the old _normalise_greyhound_race() in pipeline.py.

    Args:
        raw:   Output of parse_race_page()
        today: ISO date string fallback

    Returns:
        Dict suitable for database.upsert_race() + _runners key for upsert_runners
    """
    track = (raw.get("track_name") or raw.get("track") or "").strip()
    race_num = int(raw.get("race_number") or raw.get("race_num") or 0)
    race_date = raw.get("date") or today

    norm_track = track.lower().replace(" ", "_")
    race_uid = f"{race_date}_GREYHOUND_{norm_track}_{race_num}"

    jump_time: str | None = None
    rt = raw.get("race_time")
    if rt:
        try:
            jump_time = f"{race_date}T{rt}:00+10:00"
        except Exception:
            pass

    runners_raw = raw.get("runners", [])

    return {
        "race_uid": race_uid,
        "date": race_date,
        "track": track,
        "state": raw.get("state") or "",
        "country": "au",
        "race_num": race_num,
        "code": "GREYHOUND",
        "distance": str(raw.get("distance_m") or ""),
        "grade": raw.get("grade") or "",
        "race_name": raw.get("race_type") or "",
        "jump_time": jump_time,
        "prize_money": str(raw.get("prize_money") or ""),
        "condition": raw.get("track_condition") or "",
        "status": "upcoming",
        "source": "thedogs_browser",
        "runner_count": len([r for r in runners_raw if not r.get("scratched")]),
        "derived_json": raw.get("derived"),
        "raw_json": {k: v for k, v in raw.items() if k not in ("runners", "_runners")},
        # Passed through for _store_race
        "_runners": runners_raw,
        "_race_uid": race_uid,
    }
