"""
data_engine.py - Data fetch, parse, normalise, store
Responsibilities: fetch only. No scoring logic here.
Feature coverage: A1-A4, B5-B8, G33-G37, I44-I48
"""
import time
import logging
import hashlib
from datetime import date, datetime

log = logging.getLogger(__name__)

# State offsets to AEST Brisbane (UTC+10)
STATE_OFFSETS = {
    "NSW": -1,
    "VIC": -1,
    "TAS": -1,
    "SA": -0.5,
    "WA": 2,
    "QLD": 0,
    "NZ": -2,
}

# Race lifecycle states (feature 36)
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

# ----------------------------------------------------------------
# CANONICAL RACE UID (feature 2)
# Format: date_code_track_racenum  e.g. 2026-04-01_GREYHOUND_horsham_9
# ----------------------------------------------------------------
def make_race_uid(race_date, code, track, race_num):
    return f"{race_date}_{code}_{track}_{race_num}"
# FETCH - with rate limiting and fallback
# ----------------------------------------------------------------
def fetch_page(url, use_playwright=False, wait_ms=2000):
    from cache import check_rate_limit

    domain = url.split("/")[2] if "/" in url else url
    if not check_rate_limit(domain, max_per_minute=30):
        log.warning(f"Rate limited on {domain}, skipping {url}")
        log_source_call(url, "rate_limit", "SKIPPED", source=domain)
        return ""

    start = time.time()

    if use_playwright:
        result = _fetch_playwright(url, wait_ms)
        if result:
            duration_ms = int((time.time() - start) * 1000)
            log_source_call(
                url,
                "playwright",
                "OK",
                source=domain,
                response_code=200,
                duration_ms=duration_ms,
            )
            return result

    result = _fetch_static(url)
    duration_ms = int((time.time() - start) * 1000)
    if result:
        log_source_call(
            url,
            "requests",
            "OK",
            source=domain,
            response_code=200,
            duration_ms=duration_ms,
        )
    return result


def _fetch_playwright(url, wait_ms=2000):
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )

            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 800},
            )

            page.goto(url, timeout=60000, wait_until="networkidle")
            page.wait_for_selector("a[href*='/racing/']", timeout=10000)

            content = page.content()

            print("=== HTML LENGTH ===", len(content))
            print(content[:2000])

            browser.close()
            return content

    except ImportError:
        log.warning("Playwright not available")
        return ""

    except Exception as e:
        log.error(f"Playwright failed for {url}: {e}")
        log_source_call(
            url,
            "playwright",
            "FAILED",
            source=(url.split("/")[2] if "/" in url else None),
            error_message=str(e),
        )
        return ""


def _fetch_static(url):
    try:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-AU,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.error(f"Static fetch failed for {url}: {e}")
        log_source_call(
            url,
            "requests",
            "FAILED",
            source=(url.split("/")[2] if "/" in url else None),
            error_message=str(e),
        )
        return ""


def parse_html(html):
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser")
    except Exception as e:
        log.error(f"HTML parse failed: {e}")
        return None


# ----------------------------------------------------------------
# TIMEZONE
# ----------------------------------------------------------------
def to_aest(time_str, state):
    if not time_str or ":" not in time_str:
        return time_str
    try:
        parts = time_str.strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        offset = STATE_OFFSETS.get(state, 0)
        total = h * 60 + m + int(offset * 60)
        total = total % (24 * 60)
        return f"{total // 60:02d}:{total % 60:02d}"
    except Exception:
        return time_str


# ----------------------------------------------------------------
# STATE DETECTION
# ----------------------------------------------------------------
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


def detect_state(track):
    return TRACK_STATES.get((track or "").lower(), "QLD")


# ----------------------------------------------------------------
# FETCH MEETINGS
# ----------------------------------------------------------------
def fetch_meeting_races(meeting):
    track = meeting["track"]
    state = meeting["state"]
    today = date.today().isoformat()

    html = fetch_page(meeting["url"], use_playwright=True, wait_ms=2500)
    soup = parse_html(html)
    if not soup:
        return []

    races = []
    seen = set()

    for link in soup.find_all("a"):
        href = link.get("href", "")
        if not href or "/racing/" not in href:
            continue

        parts = [p for p in href.strip("/").split("/") if p]
        if len(parts) < 4 or parts[0] != "racing" or parts[1] != track:
            continue
        if not parts[3].isdigit():
            continue

        race_num = int(parts[3])
        if race_num in seen:
            continue
        seen.add(race_num)

        race_name = parts[4] if len(parts) > 4 else ""
        cell_text = link.get_text(strip=True)
        has_result = any(c.isdigit() for c in cell_text) and len(cell_text) < 10
        race_uid = make_race_uid(today, "GREYHOUND", track, race_num)

        races.append(
            {
                "race_uid": race_uid,
                "track": track,
                "state": state,
                "date": today,
                "race_num": race_num,
                "race_name": race_name,
                "code": "GREYHOUND",
                "status": "completed" if has_result else "upcoming",
                "expert_form_url": f"https://www.thedogs.com.au/racing/{track}/{today}/{race_num}/{race_name}/expert-form",
                "url": f"https://www.thedogs.com.au/racing/{track}/{today}/{race_num}/{race_name}?trial=false",
            }
        )

    races.sort(key=lambda x: x["race_num"])
    return races

# ----------------------------------------------------------------
# FETCH SCRATCHINGS
# ----------------------------------------------------------------
def fetch_scratchings():
    log.info("Fetching scratchings...")
    html = fetch_page(
        "https://www.thedogs.com.au/racing/scratchings",
        use_playwright=True,
        wait_ms=3000,
    )
    soup = parse_html(html)
    if not soup:
        return {}

    scratchings = {}
    today = date.today().isoformat()

    for row in soup.select("tr"):
        cells = row.select("td")
        if len(cells) < 3:
            continue
        try:
            track = cells[0].get_text(strip=True).lower().replace(" ", "-")
            rnum = cells[1].get_text(strip=True)
            boxes = cells[2].get_text(strip=True)

            if not rnum.isdigit():
                continue

            uid = make_race_uid(today, "GREYHOUND", track, int(rnum))
            scratchings.setdefault(uid, [])

            for b in boxes.split(","):
                b = b.strip()
                if b.isdigit():
                    scratchings[uid].append(int(b))
        except Exception:
            continue

    log.info(f"Scratchings loaded for {len(scratchings)} races")
    return scratchings


# ----------------------------------------------------------------
# FETCH RACE LIST FOR MEETING
# ----------------------------------------------------------------
def fetch_meeting_races(meeting):
    track = meeting["track"]
    state = meeting["state"]
    today = date.today().isoformat()

    html = fetch_page(meeting["url"], use_playwright=True, wait_ms=2500)
    soup = parse_html(html)
    if not soup:
        return []

    races = []
    seen = set()

    for link in soup.select("a[href*='/racing/']"):
        href = link.get("href", "")
        parts = [p for p in href.strip("/").split("/") if p]

        if len(parts) < 4 or parts[0] != "racing" or parts[1] != track:
            continue
        if not parts[3].isdigit():
            continue

        race_num = int(parts[3])
        if race_num in seen:
            continue
        seen.add(race_num)

        race_name = parts[4] if len(parts) > 4 else ""
        cell_text = link.get_text(strip=True)
        has_result = any(c.isdigit() for c in cell_text) and len(cell_text) < 10
        race_uid = make_race_uid(today, "GREYHOUND", track, race_num)

        races.append(
            {
                "race_uid": race_uid,
                "track": track,
                "state": state,
                "date": today,
                "race_num": race_num,
                "race_name": race_name,
                "code": "GREYHOUND",
                "status": "completed" if has_result else "upcoming",
                "expert_form_url": f"https://www.thedogs.com.au/racing/{track}/{today}/{race_num}/{race_name}/expert-form",
                "url": f"https://www.thedogs.com.au/racing/{track}/{today}/{race_num}/{race_name}?trial=false",
            }
        )

    races.sort(key=lambda x: x["race_num"])
    return races


# ----------------------------------------------------------------
# FETCH EXPERT FORM
# ----------------------------------------------------------------
def fetch_expert_form(race, scratchings):
    html = fetch_page(race["expert_form_url"], use_playwright=True, wait_ms=2000)
    soup = parse_html(html)
    if not soup:
        return None, []

    jump_time = None
    for tag in soup.find_all(["span", "div", "p", "td"]):
        text = tag.get_text(strip=True)
        if len(text) <= 8 and ":" in text:
            parts = text.split(":")
            if len(parts) == 2 and parts[0].isdigit() and parts[1][:2].isdigit():
                jump_time = to_aest(text[:5], race["state"])
                break

    grade = ""
    distance = ""
    for tag in soup.select("h1, h2, h3, .race-title, .race-info"):
        text = tag.get_text(strip=True)
        if "grade" in text.lower():
            grade = text[:60]
        for word in text.split():
            if word.endswith("m") and word[:-1].isdigit():
                distance = word
                break

    scratched_boxes = scratchings.get(race["race_uid"], [])
    runners_raw = []

    for row in soup.select("tr"):
        cells = row.select("td")
        if len(cells) < 2:
            continue

        try:
            box_text = cells[0].get_text(strip=True)
            if not box_text.isdigit():
                continue

            box_num = int(box_text)
            name = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            if not name or len(name) < 2:
                continue

            trainer = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            best_time = None
            weight = None
            career = None

            for cell in cells[3:]:
                text = cell.get_text(strip=True)

                if "." in text:
                    try:
                        val = float(text)
                        if 20 < val < 35 and best_time is None:
                            best_time = text
                        elif 20 < val < 45 and weight is None:
                            weight = val
                    except ValueError:
                        pass

                if (":" in text and "-" in text) or text.count("-") >= 2:
                    career = text[:40]

            raw_hash = hashlib.md5(
                f"{name}{trainer}{best_time}{career}".encode()
            ).hexdigest()[:8]

            runners_raw.append(
                {
                    "race_uid": race["race_uid"],
                    "race_id": None,
                    "box_num": box_num,
                    "name": name,
                    "trainer": trainer,
                    "weight": weight,
                    "best_time": best_time,
                    "career": career,
                    "price": None,
                    "rating": None,
                    "scratched": box_num in scratched_boxes,
                    "scratch_timing": "late" if box_num in scratched_boxes else None,
                    "run_style": None,
                    "early_speed": None,
                    "raw_hash": raw_hash,
                    "source_confidence": "official",
                }
            )
        except Exception:
            continue

    form_meta = {
        "jump_time": jump_time,
        "grade": grade,
        "distance": distance,
        "time_status": "VERIFIED" if jump_time else "PARTIAL",
    }
    return form_meta, runners_raw


# ----------------------------------------------------------------
# FETCH RESULT
# ----------------------------------------------------------------
def fetch_result(race):
    html = fetch_page(race["url"], use_playwright=True, wait_ms=1500)
    soup = parse_html(html)
    if not soup:
        return None

    positions = []
    for row in soup.select("tr"):
        cells = row.select("td")
        if len(cells) < 3:
            continue

        try:
            pos = cells[0].get_text(strip=True)
            if pos not in ["1st", "2nd", "3rd", "1", "2", "3"]:
                continue

            name = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            price = None
            win_time = None

            for cell in cells:
                text = cell.get_text(strip=True)

                if text.startswith("$"):
                    try:
                        price = float(text.replace("$", ""))
                    except ValueError:
                        pass

                if "." in text:
                    try:
                        val = float(text)
                        if 20 < val < 35:
                            win_time = text
                    except ValueError:
                        pass

            positions.append({"pos": pos, "name": name, "price": price, "time": win_time})
        except Exception:
            continue

    if not positions:
        return None

    return {
        "race_uid": race["race_uid"],
        "track": race["track"],
        "race_num": race["race_num"],
        "date": date.today().isoformat(),
        "code": "GREYHOUND",
        "winner": positions[0]["name"] if positions else None,
        "win_price": positions[0]["price"] if positions else None,
        "winning_time": positions[0]["time"] if positions else None,
        "place_2": positions[1]["name"] if len(positions) > 1 else None,
        "place_3": positions[2]["name"] if len(positions) > 2 else None,
    }


# ----------------------------------------------------------------
# DATA COMPLETENESS SCORE (feature 5)
# ----------------------------------------------------------------
def score_completeness(race_meta, runners):
    checks = {
        "jump_time": race_meta.get("jump_time") is not None,
        "grade": bool(race_meta.get("grade")),
        "distance": bool(race_meta.get("distance")),
        "runners_present": len(runners) >= 4,
        "best_times": sum(1 for r in runners if r.get("best_time")) >= max(1, len(runners) // 2),
        "trainers": sum(1 for r in runners if r.get("trainer")) >= max(1, len(runners) // 2),
        "career": sum(1 for r in runners if r.get("career")) >= max(1, len(runners) // 2),
        "no_all_scratched": sum(1 for r in runners if not r.get("scratched")) >= 4,
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


# ----------------------------------------------------------------
# CHANGE DETECTION (feature 37)
# ----------------------------------------------------------------
def compute_race_hash(race_meta, runners, scratchings_snapshot):
    runner_bits = []
    for r in runners or []:
        runner_bits.append(
            f"{r.get('box_num','')}-{r.get('name','')}-{r.get('best_time','')}-{r.get('trainer','')}-{r.get('raw_hash','')}"
        )

    scratch_bits = [str(s) for s in (scratchings_snapshot or [])]

    key = "|".join(
        [
            str(race_meta.get("race_uid", "")),
            str(race_meta.get("track", "")),
            str(race_meta.get("race_num", "")),
            str(race_meta.get("distance", "")),
            str(race_meta.get("jump_time", "")),
            ",".join(sorted(runner_bits)),
            ",".join(sorted(scratch_bits)),
        ]
    )

    return hashlib.md5(key.encode()).hexdigest()[:12]


# ----------------------------------------------------------------
# SUPABASE STORAGE
# ----------------------------------------------------------------
def upsert_race(race_data):
    try:
        from db import get_db, T

        db = get_db()
        payload = {
            "race_uid": race_data["race_uid"],
            "date": race_data["date"],
            "track": race_data["track"],
            "state": race_data.get("state", ""),
            "race_num": race_data["race_num"],
            "race_name": race_data.get("race_name", ""),
            "code": race_data.get("code", "GREYHOUND"),
            "distance": race_data.get("distance", ""),
            "grade": race_data.get("grade", ""),
            "jump_time": race_data.get("jump_time"),
            "time_status": race_data.get("time_status", "PARTIAL"),
            "status": race_data.get("status", "upcoming"),
            "source_url": race_data.get("url", ""),
            "expert_form_url": race_data.get("expert_form_url", ""),
            "completeness_score": race_data.get("completeness_score", 0),
            "completeness_quality": race_data.get("completeness_quality", "LOW"),
            "race_hash": race_data.get("race_hash", ""),
            "lifecycle_state": race_data.get("lifecycle_state", "fetched"),
            "last_verified_at": datetime.utcnow().isoformat()
            if race_data.get("time_status") == "VERIFIED"
            else None,
            "fetched_at": datetime.utcnow().isoformat(),
        }

        res = db.table(T("today_races")).upsert(payload, on_conflict="race_uid").execute()

        if getattr(res, "data", None):
            return res.data[0]["id"]

        row = (
            db.table(T("today_races"))
            .select("id")
            .eq("race_uid", race_data["race_uid"])
            .limit(1)
            .execute()
            .data
            or []
        )
        return row[0]["id"] if row else None
    except Exception as e:
        log.error(f"Upsert race failed {race_data.get('race_uid')}: {e}")
        return None


def upsert_runners(race_id, race_uid, runners):
    if not race_id or not runners:
        return

    try:
        from db import get_db, T

        db = get_db()
        db.table(T("today_runners")).delete().eq("race_uid", race_uid).execute()

        payload = []
        for r in runners:
            payload.append(
                {
                    "race_id": race_id,
                    "race_uid": race_uid,
                    "date": date.today().isoformat(),
                    "box_num": r.get("box_num"),
                    "name": r.get("name"),
                    "runner_name": r.get("name"),
                    "trainer": r.get("trainer"),
                    "weight": r.get("weight"),
                    "best_time": r.get("best_time"),
                    "career": r.get("career"),
                    "price": r.get("price"),
                    "rating": r.get("rating"),
                    "run_style": r.get("run_style"),
                    "early_speed": r.get("early_speed"),
                    "scratched": bool(r.get("scratched", False)),
                    "scratch_timing": r.get("scratch_timing"),
                    "raw_hash": r.get("raw_hash"),
                    "source_confidence": r.get("source_confidence"),
                }
            )

        db.table(T("today_runners")).insert(payload).execute()
    except Exception as e:
        log.error(f"Upsert runners failed {race_uid}: {e}")


def update_lifecycle(race_uid, state):
    """Update race lifecycle state (feature 36)."""
    if state not in LIFECYCLE:
        return

    try:
        from db import get_db, T

        db = get_db()
        db.table(T("today_races")).update(
            {
                "lifecycle_state": state,
                f"{state}_at": datetime.utcnow().isoformat(),
            }
        ).eq("race_uid", race_uid).execute()
    except Exception as e:
        log.error(f"Lifecycle update failed {race_uid} -> {state}: {e}")


def save_result(result):
    if not result:
        return

    try:
        from db import get_db, T

        db = get_db()
        db.table(T("results_log")).upsert(result, on_conflict="race_uid").execute()
        db.table(T("today_races")).update(
            {
                "status": "completed",
                "lifecycle_state": "result_captured",
                "result_captured_at": datetime.utcnow().isoformat(),
            }
        ).eq("race_uid", result["race_uid"]).execute()
        log.info(f"Result: {result['track']} R{result['race_num']} - {result.get('winner')}")
    except Exception as e:
        log.error(f"Save result failed: {e}")


def auto_settle_bets(result):
    """Auto-settle pending bets when result arrives."""
    if not result or not result.get("winner"):
        return

    try:
        from db import get_db, T

        db = get_db()
        pending = (
            db.table(T("bet_log"))
            .select("*")
            .eq("race_uid", result["race_uid"])
            .eq("result", "PENDING")
            .execute()
            .data
            or []
        )

        winner = (result.get("winner") or "").strip().lower()

        for bet in pending:
            runner = (bet.get("runner") or "").strip().lower()
            is_win = runner == winner
            res = "WIN" if is_win else "LOSS"
            stake = float(bet.get("stake") or 0)
            odds = float(bet.get("odds") or 0)
            pl = round(stake * (odds - 1), 2) if is_win else round(-stake, 2)

            db.table(T("bet_log")).update(
                {
                    "result": res,
                    "pl": pl,
                    "error_tag": None if is_win else "VARIANCE",
                    "settled_at": datetime.utcnow().isoformat(),
                }
            ).eq("id", bet["id"]).execute()

            log.info(f"Auto-settled: {bet.get('runner')} {res} PL={pl}")
    except Exception as e:
        log.error(f"Auto-settle failed: {e}")


def log_source_call(
    url,
    method,
    status,
    rows=0,
    grv=False,
    source=None,
    response_code=None,
    error_message=None,
    duration_ms=None,
):
    try:
        from db import get_db, T

        db = get_db()
        db.table(T("source_log")).insert(
            {
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
                "created_at": datetime.utcnow().isoformat(),
            }
        ).execute()
    except Exception:
        pass


# ----------------------------------------------------------------
# SOURCE HEALTH (feature 8)
# ----------------------------------------------------------------
_source_health = {}


def mark_source_healthy(source):
    prev = _source_health.get(source, {})
    _source_health[source] = {
        "status": "HEALTHY",
        "consecutive_fails": 0,
        "last_ok": time.time(),
        "last_fail": prev.get("last_fail"),
    }


def mark_source_failed(source):
    prev = _source_health.get(source, {})
    fails = int(prev.get("consecutive_fails", 0)) + 1
    _source_health[source] = {
        "status": "DEGRADED" if fails >= 3 else "WARNING",
        "consecutive_fails": fails,
        "last_ok": prev.get("last_ok"),
        "last_fail": time.time(),
    }


def get_source_health():
    return dict(_source_health)


# ----------------------------------------------------------------
# FULL SWEEP
# ----------------------------------------------------------------
def full_sweep():
    log.info("=== FULL SWEEP START ===")
    start = time.time()
    meetings = fetch_meetings()

    if not meetings:
        log.warning("No meetings found")
        return {"ok": True, "races": 0, "runners": 0, "elapsed": 0, "warning": "No meetings found"}

    scratchings = fetch_scratchings()
    total_races = 0
    total_runners = 0

    for meeting in meetings:
        log.info(f"Processing {meeting['track']}...")
        races = fetch_meeting_races(meeting)

        for race in races:
            try:
                if race.get("status") == "upcoming":
                    form_meta, runners = fetch_expert_form(race, scratchings)
                    if form_meta:
                        race.update(form_meta)

                    completeness = score_completeness(race, runners)
                    race["completeness_score"] = completeness["score"]
                    race["completeness_quality"] = completeness["quality"]
                    race["race_hash"] = compute_race_hash(
                        race,
                        runners,
                        scratchings.get(race["race_uid"], []),
                    )

                    race_id = upsert_race(race)
                    if race_id and runners:
                        upsert_runners(race_id, race["race_uid"], runners)
                        total_runners += len(runners)
                else:
                    result = fetch_result(race)
                    if result:
                        save_result(result)
                        auto_settle_bets(result)
                    upsert_race(race)

                total_races += 1
                mark_source_healthy("thedogs.com.au")
                time.sleep(0.4)
            except Exception as e:
                log.error(f"Race processing failed {race.get('race_uid')}: {e}")
                mark_source_failed("thedogs.com.au")

    elapsed = round(time.time() - start, 1)
    log.info(f"=== SWEEP COMPLETE: {total_races} races, {total_runners} runners in {elapsed}s ===")
    return {"ok": True, "races": total_races, "runners": total_runners, "elapsed": elapsed}


# ----------------------------------------------------------------
# ROLLING REFRESH
# ----------------------------------------------------------------
def rolling_refresh():
    log.info("Rolling refresh...")
    scratchings = fetch_scratchings()

    try:
        from db import get_db, T

        db = get_db()
        upcoming = (
            db.table(T("today_races"))
            .select("*")
            .eq("date", date.today().isoformat())
            .eq("status", "upcoming")
            .eq("code", "GREYHOUND")
            .execute()
            .data
            or []
        )
    except Exception as e:
        log.error(f"Rolling refresh load failed: {e}")
        return {"ok": False, "error": "load_failed"}

    results_captured = 0

    for race in upcoming:
        try:
            race_obj = {
                "race_uid": race["race_uid"],
                "track": race["track"],
                "state": race.get("state", "QLD"),
                "date": date.today().isoformat(),
                "race_num": race["race_num"],
                "race_name": race.get("race_name", ""),
                "url": race.get(
                    "source_url",
                    f"https://www.thedogs.com.au/racing/{race['track']}/{date.today().isoformat()}/{race['race_num']}/?trial=false",
                ),
                "expert_form_url": race.get(
                    "expert_form_url",
                    f"https://www.thedogs.com.au/racing/{race['track']}/{date.today().isoformat()}/{race['race_num']}/expert-form",
                ),
            }

            result = fetch_result(race_obj)
            if result:
                save_result(result)
                auto_settle_bets(result)
                results_captured += 1
                continue

            new_scratches = scratchings.get(race["race_uid"], [])
            if new_scratches:
                try:
                    db.table(T("today_runners")).update(
                        {
                            "scratched": True,
                            "scratch_timing": "late",
                        }
                    ).eq("race_uid", race["race_uid"]).in_("box_num", new_scratches).execute()

                    from cache import cache_clear

                    cache_clear(race["race_uid"])
                    log.info(
                        f"Late scratch detected: {race['track']} R{race['race_num']} boxes {new_scratches}"
                    )
                except Exception as e:
                    log.error(f"Late scratching update failed {race.get('race_uid')}: {e}")

            time.sleep(0.3)
        except Exception as e:
            log.error(f"Rolling refresh error {race.get('race_uid')}: {e}")

    return {"ok": True, "results_captured": results_captured}


# ----------------------------------------------------------------
# READ HELPERS
# ----------------------------------------------------------------
def get_next_race(anchor_time=None):
    try:
        from db import get_db, T

        db = get_db()
        races = (
            db.table(T("today_races"))
            .select("*")
            .eq("date", date.today().isoformat())
            .eq("status", "upcoming")
            .eq("code", "GREYHOUND")
            .order("jump_time")
            .execute()
            .data
            or []
        )

        if not races:
            return None

        if anchor_time:
            for race in races:
                if race.get("jump_time") and race["jump_time"] > anchor_time:
                    return race

        return races[0]
    except Exception as e:
        log.error(f"Get next race failed: {e}")
        return None


def get_board(limit=10):
    try:
        from db import get_db, T

        db = get_db()
        return (
            db.table(T("today_races"))
            .select("*")
            .eq("date", date.today().isoformat())
            .eq("status", "upcoming")
            .eq("code", "GREYHOUND")
            .order("jump_time")
            .limit(limit)
            .execute()
            .data
            or []
        )
    except Exception as e:
        log.error(f"Get board failed: {e}")
        return []


def get_race_with_runners(track, race_num):
    try:
        from db import get_db, T

        db = get_db()
        races = (
            db.table(T("today_races"))
            .select("*")
            .eq("date", date.today().isoformat())
            .eq("track", track)
            .eq("race_num", race_num)
            .execute()
            .data
            or []
        )

        if not races:
            return None, []

        race = races[0]
        runners = (
            db.table(T("today_runners"))
            .select("*")
            .eq("race_uid", race["race_uid"])
            .eq("scratched", False)
            .order("box_num")
            .execute()
            .data
            or []
        )
        return race, runners
    except Exception as e:
        log.error(f"Get race with runners failed: {e}")
        return None, []


if __name__ == "__main__":
    full_sweep()
