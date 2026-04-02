"""
data_engine.py - Data fetch, parse, normalise, store
Responsibilities: fetch only. No scoring logic here.
Feature coverage: A1-A4, B5-B8, G33-G37, I44-I48
"""

import re
import time
import logging
import hashlib
from datetime import date, datetime
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ----------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------
BASE_URL = "https://www.thedogs.com.au"
RACECARDS_URL = f"{BASE_URL}/racing/racecards"
SCRATCHINGS_URL = f"{BASE_URL}/racing/scratchings"

PLAYWRIGHT_TIMEOUT_MS = 60000
PLAYWRIGHT_WAIT_MS = 2500
STATIC_TIMEOUT_SECONDS = 20
RATE_LIMIT_PER_MINUTE = 30

RACE_PATH_RE = re.compile(
    r"^/racing/([^/]+)/(\d{4}-\d{2}-\d{2})(?:/(\d+)(?:/([^/?#]+))?)?"
)

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
# ----------------------------------------------------------------
def make_race_uid(race_date, code, track, race_num):
    return f"{race_date}_{code}_{track}_{race_num}"


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
# LOGGING HELPERS
# ----------------------------------------------------------------
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


def _domain_from_url(url):
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


def _absolute_url(href):
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"{BASE_URL}{href}"
    return f"{BASE_URL}/{href.lstrip('/')}"


# ----------------------------------------------------------------
# FETCH - with rate limiting and fallback
# ----------------------------------------------------------------
def fetch_page(url, use_playwright=False, wait_ms=PLAYWRIGHT_WAIT_MS):
    from cache import check_rate_limit

    domain = _domain_from_url(url)
    if not check_rate_limit(domain, max_per_minute=RATE_LIMIT_PER_MINUTE):
        log.warning(f"Rate limited on {domain}, skipping {url}")
        log_source_call(url, "rate_limit", "SKIPPED", source=domain)
        return ""

    start = time.time()

    # TheDogs is JS-heavy / often blocks static fetch, so Playwright is primary
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
                rows=len(result),
            )
            return result

    # Static fallback remains for non-blocked pages / resilience
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
            rows=len(result),
        )
        mark_source_healthy(domain)
        return result

    log_source_call(
        url,
        "fetch",
        "FAILED",
        source=domain,
        duration_ms=duration_ms,
        error_message="Both playwright and static fetch returned empty content",
    )
    mark_source_failed(domain)
    return ""


def _page_type(url):
    url = (url or "").lower()
    if "racecards" in url:
        return "racecards"
    if "scratchings" in url:
        return "scratchings"
    if "expert-form" in url:
        return "expert_form"
    return "meeting_or_result"


def _content_markers_for_url(url):
    kind = _page_type(url)

    if kind == "racecards":
        return [
            "/racing/",
            "racecards",
            "Next To Jump",
            "Today",
            "meeting",
        ]

    if kind == "scratchings":
        return [
            "scratchings",
            "Scratching",
            "Late Scratching",
            "<table",
            "<tr",
        ]

    if kind == "expert_form":
        return [
            "expert-form",
            "Expert Form",
            "Grade",
            "m",
            "<tr",
        ]

    return [
        "/racing/",
        "Race",
        "R1",
        "R2",
        "Form",
        "<tr",
    ]


def _has_meaningful_content(url, html):
    if not html:
        return False

    lower = html.lower()
    markers = _content_markers_for_url(url)

    hits = 0
    for marker in markers:
        if marker.lower() in lower:
            hits += 1

    # Strong signal: race links in HTML
    racing_link_hits = lower.count("/racing/")

    # Block / shell detection
    blocked_signals = [
        "403 forbidden",
        "access denied",
        "request blocked",
        "cf-error",
        "captcha",
        "attention required",
    ]
    blocked = any(sig in lower for sig in blocked_signals)

    if blocked:
        return False

    if racing_link_hits >= 3:
        return True

    return hits >= 2


def _log_fetch_diagnostics(url, html, prefix="FETCH"):
    try:
        preview = (html or "")[:1200].replace("\n", " ").replace("\r", " ")
        log.info(f"{prefix} DIAG url={url}")
        log.info(f"{prefix} DIAG html_len={len(html or '')}")
        log.info(f"{prefix} DIAG racing_refs={(html or '').lower().count('/racing/')}")
        log.info(f"{prefix} DIAG preview={preview}")
    except Exception:
        pass


def _fetch_playwright(url, wait_ms=PLAYWRIGHT_WAIT_MS):
    domain = _domain_from_url(url)
    start = time.time()

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 1200},
                locale="en-AU",
            )

            page.set_extra_http_headers(
                {
                    "Accept-Language": "en-AU,en;q=0.9",
                    "Referer": BASE_URL,
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                }
            )

            response = page.goto(
                url,
                timeout=PLAYWRIGHT_TIMEOUT_MS,
                wait_until="domcontentloaded",
            )

            # Initial settle
            page.wait_for_timeout(wait_ms)

            # Try a few stronger readiness checks without hard-failing
            selectors = [
                "a[href*='/racing/']",
                "table",
                "tr",
                "[href*='/racing/']",
                "body",
            ]

            for selector in selectors:
                try:
                    page.wait_for_selector(selector, timeout=4000)
                    break
                except Exception:
                    continue

            # Additional settle after selector hit
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            page.wait_for_timeout(1500)

            content = page.content()

            # If DOM still looks thin, try a controlled scroll + wait once more
            if not _has_meaningful_content(url, content):
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_timeout(1200)
                    content = page.content()
                except Exception:
                    pass

            browser.close()

            if _has_meaningful_content(url, content):
                mark_source_healthy(domain)
                return content

            duration_ms = int((time.time() - start) * 1000)
            _log_fetch_diagnostics(url, content, prefix="PLAYWRIGHT_EMPTY")
            log_source_call(
                url,
                "playwright",
                "FAILED",
                source=domain,
                response_code=response.status if response else None,
                error_message="Playwright returned HTML but no meaningful race content markers were found",
                duration_ms=duration_ms,
                rows=len(content or ""),
            )
            mark_source_failed(domain)
            return ""

    except ImportError:
        log.warning("Playwright not available")
        return ""

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        log.error(f"Playwright failed for {url}: {e}")
        log_source_call(
            url,
            "playwright",
            "FAILED",
            source=domain,
            error_message=str(e),
            duration_ms=duration_ms,
        )
        mark_source_failed(domain)
        return ""


def _fetch_static(url):
    domain = _domain_from_url(url)
    start = time.time()

    try:
        import requests

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-AU,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": BASE_URL,
        }

        r = requests.get(url, headers=headers, timeout=STATIC_TIMEOUT_SECONDS)
        r.raise_for_status()

        html = r.text or ""
        if _has_meaningful_content(url, html):
            mark_source_healthy(domain)
            return html

        duration_ms = int((time.time() - start) * 1000)
        _log_fetch_diagnostics(url, html, prefix="STATIC_EMPTY")
        log_source_call(
            url,
            "requests",
            "FAILED",
            source=domain,
            response_code=getattr(r, "status_code", None),
            error_message="Static fetch returned HTML but no meaningful race content markers were found",
            duration_ms=duration_ms,
            rows=len(html),
        )
        mark_source_failed(domain)
        return ""

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        log.error(f"Static fetch failed for {url}: {e}")
        log_source_call(
            url,
            "requests",
            "FAILED",
            source=domain,
            error_message=str(e),
            duration_ms=duration_ms,
        )
        mark_source_failed(domain)
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
# EXTRACTION HELPERS
# ----------------------------------------------------------------
def _extract_racing_links(soup):
    links = []
    if not soup:
        return links

    for tag in soup.find_all("a", href=True):
        href = (tag.get("href") or "").strip()
        text = tag.get_text(" ", strip=True)
        if "/racing/" not in href:
            continue
        links.append(
            {
                "href": href,
                "abs_url": _absolute_url(href),
                "text": text,
            }
        )
    return links


def _parse_racing_path(href):
    if not href:
        return None

    parsed = urlparse(href)
    path = parsed.path or href
    m = RACE_PATH_RE.match(path)
    if not m:
        return None

    track = m.group(1)
    race_date = m.group(2)
    race_num = m.group(3)
    race_name = m.group(4)

    return {
        "track": track,
        "date": race_date,
        "race_num": int(race_num) if race_num and race_num.isdigit() else None,
        "race_name": race_name or "",
    }


def _looks_like_completed_link(text):
    clean = (text or "").strip()
    if not clean:
        return False
    if clean in {"1", "2", "3", "1st", "2nd", "3rd"}:
        return True
    return False


# ----------------------------------------------------------------
# FETCH MEETINGS
# ----------------------------------------------------------------
def fetch_meetings():
    log.info("Fetching racecards...")
    html = fetch_page(RACECARDS_URL, use_playwright=True, wait_ms=3000)
    soup = parse_html(html)
    if not soup:
        log.error("Racecards failed")
        return []

    today = date.today().isoformat()
    links = _extract_racing_links(soup)

    meetings = []
    seen_tracks = set()
    candidate_dates = {}
    candidate_tracks = {}

    log.info(f"Racecards link scan found {len(links)} /racing/ links")

    for item in links:
        parsed = _parse_racing_path(item["href"])
        if not parsed:
            continue

        track = (parsed.get("track") or "").strip().lower()
        race_date = parsed.get("date")

        if not track or not race_date:
            continue

        candidate_dates[race_date] = candidate_dates.get(race_date, 0) + 1
        candidate_tracks[track] = candidate_tracks.get(track, 0) + 1

        if race_date != today:
            continue

        if track in seen_tracks:
            continue

        seen_tracks.add(track)
        meetings.append(
            {
                "track": track,
                "date": race_date,
                "state": detect_state(track),
                "url": f"{BASE_URL}/racing/{track}/{race_date}?trial=false",
            }
        )

    if meetings:
        log.info(f"Found {len(meetings)} meetings for today={today}")
        return meetings

    # Fallback 1:
    # If page has race links but none match today's date, log strongest seen dates
    if candidate_dates:
        sorted_dates = sorted(candidate_dates.items(), key=lambda x: x[1], reverse=True)
        log.warning(f"No meetings matched today={today}. Top dates seen: {sorted_dates[:5]}")

    # Fallback 2:
    # Look for track/date patterns anywhere in raw HTML, not just in anchor hrefs
    html_matches = re.findall(
        r"/racing/([a-z0-9\-]+)/(\d{4}-\d{2}-\d{2})",
        html or "",
        flags=re.IGNORECASE,
    )

    for track, race_date in html_matches:
        track = (track or "").strip().lower()
        if race_date != today:
            continue
        if not track or track in seen_tracks:
            continue

        seen_tracks.add(track)
        meetings.append(
            {
                "track": track,
                "date": race_date,
                "state": detect_state(track),
                "url": f"{BASE_URL}/racing/{track}/{race_date}?trial=false",
            }
        )

    if meetings:
        log.info(f"Fallback HTML scan found {len(meetings)} meetings for today={today}")
        return meetings

    # Fallback 3:
    # If we still have nothing, dump diagnostics so we stop guessing
    try:
        page_text = soup.get_text(" ", strip=True)[:1500]
        log.warning(f"Racecards diagnostics: today={today}, candidate_tracks={list(candidate_tracks)[:20]}")
        log.warning(f"Racecards diagnostics text preview: {page_text}")
    except Exception:
        pass

    log.error("Racecards parsed but no meetings were extracted")
    return []

# ----------------------------------------------------------------
# FETCH SCRATCHINGS
# ----------------------------------------------------------------
def fetch_scratchings():
    log.info("Fetching scratchings...")
    html = fetch_page(SCRATCHINGS_URL, use_playwright=True, wait_ms=3000)
    soup = parse_html(html)
    if not soup:
        log.warning("Scratchings page failed")
        return {}

    scratchings = {}
    today = date.today().isoformat()
    page_text = soup.get_text(" ", strip=True)

    # ------------------------------------------------------------
    # PRIMARY TABLE PARSE
    # ------------------------------------------------------------
    parsed_rows = 0

    for row in soup.select("tr"):
        cells = row.select("td")
        if len(cells) < 3:
            continue

        try:
            cell_texts = [c.get_text(" ", strip=True) for c in cells]

            track_raw = (cell_texts[0] or "").strip().lower()
            rnum_raw = (cell_texts[1] or "").strip()
            boxes_raw = (cell_texts[2] or "").strip()

            if not track_raw or not rnum_raw or not boxes_raw:
                continue

            track = track_raw.replace(" ", "-")
            rnum_match = re.search(r"\b([0-9]{1,2})\b", rnum_raw)
            if not rnum_match:
                continue

            race_num = int(rnum_match.group(1))
            if race_num < 1 or race_num > 20:
                continue

            box_nums = []
            for b in re.findall(r"\b([0-9]{1,2})\b", boxes_raw):
                try:
                    box_num = int(b)
                    if 1 <= box_num <= 12:
                        box_nums.append(box_num)
                except Exception:
                    continue

            if not box_nums:
                continue

            uid = make_race_uid(today, "GREYHOUND", track, race_num)
            scratchings.setdefault(uid, [])

            for box_num in box_nums:
                if box_num not in scratchings[uid]:
                    scratchings[uid].append(box_num)

            parsed_rows += 1

        except Exception:
            continue

    # ------------------------------------------------------------
    # FALLBACK TEXT PARSE
    # ------------------------------------------------------------
    if not scratchings:
        text_lines = [ln.strip() for ln in soup.get_text("\n", strip=True).splitlines() if ln.strip()]

        current_track = None
        current_race = None

        for line in text_lines:
            line_lower = line.lower().strip()

            # Track guess
            if line_lower:
                guessed_track = line_lower.replace(" ", "-")
                if guessed_track in TRACK_STATES:
                    current_track = guessed_track
                    current_race = None
                    continue

            # Race guess
            race_match = re.search(r"\bR(?:ace)?\s*([0-9]{1,2})\b", line, flags=re.IGNORECASE)
            if race_match:
                try:
                    rn = int(race_match.group(1))
                    if 1 <= rn <= 20:
                        current_race = rn
                        continue
                except Exception:
                    pass

            # Scratch box guess
            if current_track and current_race:
                if "scratch" in line_lower or "scratched" in line_lower:
                    nums = []
                    for b in re.findall(r"\b([0-9]{1,2})\b", line):
                        try:
                            bn = int(b)
                            if 1 <= bn <= 12:
                                nums.append(bn)
                        except Exception:
                            continue

                    if nums:
                        uid = make_race_uid(today, "GREYHOUND", current_track, current_race)
                        scratchings.setdefault(uid, [])
                        for bn in nums:
                            if bn not in scratchings[uid]:
                                scratchings[uid].append(bn)

    # Sort values for stable output
    for uid in scratchings:
        scratchings[uid] = sorted(set(scratchings[uid]))

    if scratchings:
        total_boxes = sum(len(v) for v in scratchings.values())
        log.info(
            f"Scratchings loaded for {len(scratchings)} races, "
            f"{total_boxes} scratched boxes, parsed_rows={parsed_rows}"
        )
        return scratchings

    preview = page_text[:1500]
    log.warning("Scratchings parsed but none extracted")
    log.warning(f"Scratchings preview: {preview}")
    return {}


# ----------------------------------------------------------------
# FETCH RACE LIST FOR MEETING
# ----------------------------------------------------------------
def fetch_meeting_races(meeting):
    track = (meeting.get("track") or "").strip().lower()
    state = meeting.get("state", "QLD")
    today = date.today().isoformat()

    html = fetch_page(meeting["url"], use_playwright=True, wait_ms=2500)
    soup = parse_html(html)
    if not soup:
        log.error(f"{track}: meeting page failed")
        return []

    links = _extract_racing_links(soup)
    races = []
    seen = set()
    candidate_nums = []

    log.info(f"{track}: meeting page scan found {len(links)} /racing/ links")

    for item in links:
        parsed = _parse_racing_path(item["href"])
        if not parsed:
            continue

        parsed_track = (parsed.get("track") or "").strip().lower()
        parsed_date = parsed.get("date")
        race_num = parsed.get("race_num")

        if parsed_track != track:
            continue
        if parsed_date != today:
            continue
        if race_num is None:
            continue

        candidate_nums.append(race_num)

        if race_num in seen:
            continue
        seen.add(race_num)

        race_name = (parsed.get("race_name") or "").strip()
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
                "status": "completed" if _looks_like_completed_link(item["text"]) else "upcoming",
                "expert_form_url": f"{BASE_URL}/racing/{track}/{today}/{race_num}/{race_name}/expert-form",
                "url": f"{BASE_URL}/racing/{track}/{today}/{race_num}/{race_name}?trial=false",
            }
        )

    if races:
        races.sort(key=lambda x: x["race_num"])
        log.info(f"{track}: extracted {len(races)} races from anchor scan")
        return races

    # Fallback 1:
    # scan raw HTML for direct race paths for this track/date
    html_matches = re.findall(
        rf"/racing/{re.escape(track)}/{today}/(\d+)(?:/([^\"'<>?#]+))?",
        html or "",
        flags=re.IGNORECASE,
    )

    for race_num_raw, race_name_raw in html_matches:
        if not race_num_raw.isdigit():
            continue

        race_num = int(race_num_raw)
        if race_num in seen:
            continue
        seen.add(race_num)

        race_name = (race_name_raw or "").strip("/")
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
                "status": "upcoming",
                "expert_form_url": f"{BASE_URL}/racing/{track}/{today}/{race_num}/{race_name}/expert-form",
                "url": f"{BASE_URL}/racing/{track}/{today}/{race_num}/{race_name}?trial=false",
            }
        )

    if races:
        races.sort(key=lambda x: x["race_num"])
        log.info(f"{track}: extracted {len(races)} races from raw HTML fallback")
        return races

    # Fallback 2:
    # infer race numbers from page text like R1 / Race 1 if link structure is weak
    page_text = soup.get_text(" ", strip=True)
    text_nums = set()

    for match in re.findall(r"\bR(?:ace)?\s*([0-9]{1,2})\b", page_text, flags=re.IGNORECASE):
        try:
            rn = int(match)
            if 1 <= rn <= 20:
                text_nums.add(rn)
        except Exception:
            continue

    for race_num in sorted(text_nums):
        if race_num in seen:
            continue
        seen.add(race_num)

        race_uid = make_race_uid(today, "GREYHOUND", track, race_num)

        races.append(
            {
                "race_uid": race_uid,
                "track": track,
                "state": state,
                "date": today,
                "race_num": race_num,
                "race_name": "",
                "code": "GREYHOUND",
                "status": "upcoming",
                "expert_form_url": f"{BASE_URL}/racing/{track}/{today}/{race_num}/expert-form",
                "url": f"{BASE_URL}/racing/{track}/{today}/{race_num}?trial=false",
            }
        )

    if races:
        races.sort(key=lambda x: x["race_num"])
        log.info(f"{track}: inferred {len(races)} races from text fallback")
        return races

    # Diagnostics so we stop guessing when this fails
    try:
        preview = page_text[:1500]
        log.warning(f"{track}: no races extracted for today={today}")
        log.warning(f"{track}: candidate anchor race nums={sorted(set(candidate_nums))[:20]}")
        log.warning(f"{track}: page text preview={preview}")
    except Exception:
        pass

    log.error(f"{track}: meeting page parsed but no races extracted")
    return []

# ----------------------------------------------------------------
# FETCH EXPERT FORM
# ----------------------------------------------------------------
def fetch_expert_form(race, scratchings):
    html = fetch_page(race["expert_form_url"], use_playwright=True, wait_ms=2000)
    soup = parse_html(html)
    if not soup:
        log.error(f"{race.get('race_uid')}: expert form failed")
        return None, []

    page_text = soup.get_text(" ", strip=True)

    # ------------------------------------------------------------
    # META EXTRACTION
    # ------------------------------------------------------------
    jump_time = None
    grade = ""
    distance = ""

    # Prefer explicit short time tokens first
    time_candidates = re.findall(r"\b(\d{1,2}:\d{2})\b", page_text)
    for candidate in time_candidates:
        jump_time = to_aest(candidate, race["state"])
        if jump_time:
            break

    # Grade / distance from headings and info blocks first
    for tag in soup.select("h1, h2, h3, .race-title, .race-info, .event-info, .meeting-info"):
        text = tag.get_text(" ", strip=True)

        if not grade and "grade" in text.lower():
            grade = text[:80]

        if not distance:
            dm = re.search(r"\b(\d{3,4}m)\b", text.lower())
            if dm:
                distance = dm.group(1)

    # Fallback to whole page text
    if not grade:
        gm = re.search(r"\b(grade\s*[0-9a-z+\- ]+)\b", page_text, flags=re.IGNORECASE)
        if gm:
            grade = gm.group(1)[:80]

    if not distance:
        dm = re.search(r"\b(\d{3,4}m)\b", page_text.lower())
        if dm:
            distance = dm.group(1)

    scratched_boxes = set(scratchings.get(race["race_uid"], []))
    runners_raw = []
    seen_boxes = set()

    # ------------------------------------------------------------
    # PRIMARY TABLE PARSE
    # ------------------------------------------------------------
    for row in soup.select("tr"):
        cells = row.select("td")
        if len(cells) < 2:
            continue

        try:
            cell_texts = [c.get_text(" ", strip=True) for c in cells]
            box_text = (cell_texts[0] or "").strip()

            if not box_text.isdigit():
                continue

            box_num = int(box_text)
            if box_num < 1 or box_num > 12:
                continue

            if box_num in seen_boxes:
                continue

            name = (cell_texts[1] or "").strip()
            if not name or len(name) < 2:
                continue

            trainer = cell_texts[2].strip() if len(cell_texts) > 2 else ""
            best_time = None
            weight = None
            career = None

            for text in cell_texts[3:]:
                if not text:
                    continue

                if "." in text:
                    try:
                        val = float(text)
                        if 20 < val < 35 and best_time is None:
                            best_time = text
                        elif 20 < val < 45 and weight is None:
                            weight = val
                    except ValueError:
                        pass

                if career is None and ((":" in text and "-" in text) or text.count("-") >= 2):
                    career = text[:40]

            raw_hash = hashlib.md5(
                f"{box_num}|{name}|{trainer}|{best_time}|{career}".encode()
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
            seen_boxes.add(box_num)

        except Exception:
            continue

    # ------------------------------------------------------------
    # FALLBACK TEXT PARSE
    # Handles pages where rows are not in clean <tr><td> tables
    # ------------------------------------------------------------
    if len(runners_raw) < 4:
        text_lines = [ln.strip() for ln in soup.get_text("\n", strip=True).splitlines() if ln.strip()]

        i = 0
        while i < len(text_lines):
            line = text_lines[i]

            # Box + runner name patterns like:
            # "1 Runner Name"
            # "1. Runner Name"
            # "1 - Runner Name"
            m = re.match(r"^([1-9]|1[0-2])[\.\-\s]+(.+)$", line)
            if not m:
                i += 1
                continue

            try:
                box_num = int(m.group(1))
                if box_num in seen_boxes:
                    i += 1
                    continue

                name = (m.group(2) or "").strip()
                if len(name) < 2:
                    i += 1
                    continue

                trainer = ""
                best_time = None
                weight = None
                career = None

                # Look ahead a few lines for useful fields
                lookahead = text_lines[i + 1:i + 6]
                for txt in lookahead:
                    if not trainer and len(txt) <= 40 and not txt.isdigit():
                        # weak trainer heuristic only if it isn't another boxed runner
                        if not re.match(r"^([1-9]|1[0-2])[\.\-\s]+", txt):
                            trainer = txt

                    if "." in txt:
                        try:
                            val = float(txt)
                            if 20 < val < 35 and best_time is None:
                                best_time = txt
                            elif 20 < val < 45 and weight is None:
                                weight = val
                        except ValueError:
                            pass

                    if career is None and ((":" in txt and "-" in txt) or txt.count("-") >= 2):
                        career = txt[:40]

                raw_hash = hashlib.md5(
                    f"{box_num}|{name}|{trainer}|{best_time}|{career}".encode()
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
                seen_boxes.add(box_num)
            except Exception:
                pass

            i += 1

    runners_raw.sort(key=lambda r: r.get("box_num") or 99)

    # Diagnostics when parse is weak
    if len(runners_raw) < 4:
        preview = page_text[:1500]
        log.warning(f"{race.get('race_uid')}: weak expert form parse, runners={len(runners_raw)}")
        log.warning(f"{race.get('race_uid')}: expert form text preview={preview}")

    form_meta = {
        "jump_time": jump_time,
        "grade": grade,
        "distance": distance,
        "time_status": "VERIFIED" if jump_time else "PARTIAL",
    }

    log.info(
        f"{race.get('race_uid')}: expert form extracted "
        f"jump_time={jump_time}, grade={grade}, distance={distance}, runners={len(runners_raw)}"
    )

    return form_meta, runners_raw

# ----------------------------------------------------------------
# FETCH RESULT
# ----------------------------------------------------------------
def fetch_result(race):
    html = fetch_page(race["url"], use_playwright=True, wait_ms=1500)
    soup = parse_html(html)
    if not soup:
        log.error(f"{race.get('race_uid')}: result fetch failed")
        return None

    positions = []
    seen_pos = set()
    page_text = soup.get_text(" ", strip=True)

    # ------------------------------------------------------------
    # PRIMARY TABLE PARSE
    # ------------------------------------------------------------
    for row in soup.select("tr"):
        cells = row.select("td")
        if len(cells) < 2:
            continue

        try:
            cell_texts = [c.get_text(" ", strip=True) for c in cells]
            pos = (cell_texts[0] or "").strip()

            if pos not in {"1st", "2nd", "3rd", "1", "2", "3"}:
                continue

            norm_pos = pos.replace("st", "").replace("nd", "").replace("rd", "")
            if norm_pos in seen_pos:
                continue

            name = (cell_texts[1] or "").strip()
            if not name or len(name) < 2:
                continue

            price = None
            win_time = None

            for text in cell_texts[2:]:
                if not text:
                    continue

                if text.startswith("$"):
                    try:
                        price = float(text.replace("$", "").replace(",", ""))
                    except ValueError:
                        pass

                if "." in text:
                    try:
                        val = float(text)
                        if 20 < val < 35:
                            win_time = text
                    except ValueError:
                        pass

            positions.append(
                {
                    "pos": norm_pos,
                    "name": name,
                    "price": price,
                    "time": win_time,
                }
            )
            seen_pos.add(norm_pos)

        except Exception:
            continue

    # ------------------------------------------------------------
    # FALLBACK TEXT PARSE
    # ------------------------------------------------------------
    if not positions:
        text_lines = [ln.strip() for ln in soup.get_text("\n", strip=True).splitlines() if ln.strip()]

        for line in text_lines:
            m = re.match(r"^(1st|2nd|3rd|1|2|3)[\.\-\s]+(.+)$", line, flags=re.IGNORECASE)
            if not m:
                continue

            pos_raw = m.group(1)
            norm_pos = pos_raw.lower().replace("st", "").replace("nd", "").replace("rd", "")
            if norm_pos in seen_pos:
                continue

            remainder = (m.group(2) or "").strip()
            if len(remainder) < 2:
                continue

            # Try to strip trailing price/time tokens if embedded in same line
            name = remainder
            price = None
            win_time = None

            price_match = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", remainder)
            if price_match:
                try:
                    price = float(price_match.group(1))
                except ValueError:
                    pass

            time_match = re.search(r"\b([2-3][0-9]\.[0-9]{1,3})\b", remainder)
            if time_match:
                win_time = time_match.group(1)

            # remove obvious trailing price/time fragments from name
            name = re.sub(r"\$[0-9]+(?:\.[0-9]+)?", "", name).strip()
            name = re.sub(r"\b[2-3][0-9]\.[0-9]{1,3}\b", "", name).strip(" -|")

            positions.append(
                {
                    "pos": norm_pos,
                    "name": name,
                    "price": price,
                    "time": win_time,
                }
            )
            seen_pos.add(norm_pos)

            if len(positions) >= 3:
                break

    # Sort into 1 / 2 / 3 order
    positions.sort(key=lambda x: int(x["pos"]) if str(x.get("pos", "")).isdigit() else 99)

    if not positions:
        preview = page_text[:1500]
        log.warning(f"{race.get('race_uid')}: no result positions parsed")
        log.warning(f"{race.get('race_uid')}: result text preview={preview}")
        return None

    result = {
        "race_uid": race["race_uid"],
        "track": race["track"],
        "race_num": race["race_num"],
        "date": date.today().isoformat(),
        "code": "GREYHOUND",
        "winner": positions[0]["name"] if len(positions) > 0 else None,
        "win_price": positions[0]["price"] if len(positions) > 0 else None,
        "winning_time": positions[0]["time"] if len(positions) > 0 else None,
        "place_2": positions[1]["name"] if len(positions) > 1 else None,
        "place_3": positions[2]["name"] if len(positions) > 2 else None,
    }

    log.info(
        f"{race.get('race_uid')}: result extracted "
        f"winner={result.get('winner')}, place_2={result.get('place_2')}, place_3={result.get('place_3')}"
    )

    return result

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
                time.sleep(0.4)
            except Exception as e:
                log.error(f"Race processing failed {race.get('race_uid')}: {e}")
                mark_source_failed("thedogs.com.au")

    elapsed = round(time.time() - start, 1)
    log.info(f"=== SWEEP COMPLETE: {total_races} races, {total_runners} runners in {elapsed}s ===")
    return {"ok": True, "races": total_races, "runners": total_runners, "elapsed": # ----------------------------------------------------------------
# FULL SWEEP
# ----------------------------------------------------------------
def full_sweep():
    log.info("=== FULL SWEEP START ===")
    start = time.time()

    meetings = fetch_meetings()
    if not meetings:
        log.warning("No meetings found")
        return {
            "ok": True,
            "races": 0,
            "runners": 0,
            "meetings": 0,
            "elapsed": 0,
            "warning": "No meetings found",
        }

    scratchings = fetch_scratchings()
    total_meetings = len(meetings)
    total_races = 0
    total_runners = 0
    processed_upcoming = 0
    processed_completed = 0
    failed_races = 0
    failed_meetings = 0

    for meeting in meetings:
        track = meeting.get("track", "unknown")
        try:
            log.info(f"Processing meeting: {track} {meeting.get('date')} {meeting.get('state')}")
            races = fetch_meeting_races(meeting)

            if not races:
                log.warning(f"{track}: no races extracted from meeting page")
                failed_meetings += 1
                continue

            log.info(f"{track}: {len(races)} races found")

            for race in races:
                try:
                    race_uid = race.get("race_uid")
                    race_status = race.get("status", "upcoming")

                    if race_status == "upcoming":
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
                        if race_id:
                            update_lifecycle(race_uid, "fetched")

                        if race_id and runners:
                            upsert_runners(race_id, race["race_uid"], runners)
                            total_runners += len(runners)

                        processed_upcoming += 1

                        log.info(
                            f"{race_uid}: upcoming processed | "
                            f"runners={len(runners)} | "
                            f"jump_time={race.get('jump_time')} | "
                            f"distance={race.get('distance')} | "
                            f"grade={race.get('grade')} | "
                            f"quality={race.get('completeness_quality')} "
                            f"({race.get('completeness_score')})"
                        )

                    else:
                        result = fetch_result(race)
                        if result:
                            save_result(result)
                            auto_settle_bets(result)
                        else:
                            log.warning(f"{race_uid}: marked completed but no result extracted")

                        upsert_race(race)
                        processed_completed += 1

                        log.info(f"{race_uid}: completed race processed")

                    total_races += 1
                    time.sleep(0.4)

                except Exception as e:
                    failed_races += 1
                    log.error(f"Race processing failed {race.get('race_uid')}: {e}")
                    mark_source_failed("thedogs.com.au")

        except Exception as e:
            failed_meetings += 1
            log.error(f"Meeting processing failed {track}: {e}")
            mark_source_failed("thedogs.com.au")

    elapsed = round(time.time() - start, 1)

    summary = {
        "ok": True,
        "meetings": total_meetings,
        "races": total_races,
        "runners": total_runners,
        "processed_upcoming": processed_upcoming,
        "processed_completed": processed_completed,
        "failed_races": failed_races,
        "failed_meetings": failed_meetings,
        "elapsed": elapsed,
    }

    log.info(f"=== SWEEP COMPLETE: {summary} ===")
    return summary# ----------------------------------------------------------------
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

    if not upcoming:
        log.info("Rolling refresh: no upcoming races in DB")
        return {
            "ok": True,
            "results_captured": 0,
            "late_scratches_applied": 0,
            "upcoming_checked": 0,
            "warning": "no_upcoming_races",
        }

    results_captured = 0
    late_scratches_applied = 0
    checked = 0
    failed = 0

    for race in upcoming:
        try:
            checked += 1

            race_obj = {
                "race_uid": race["race_uid"],
                "track": race["track"],
                "state": race.get("state", "QLD"),
                "date": date.today().isoformat(),
                "race_num": race["race_num"],
                "race_name": race.get("race_name", ""),
                "url": race.get(
                    "source_url",
                    f"{BASE_URL}/racing/{race['track']}/{date.today().isoformat()}/{race['race_num']}/?trial=false",
                ),
                "expert_form_url": race.get(
                    "expert_form_url",
                    f"{BASE_URL}/racing/{race['track']}/{date.today().isoformat()}/{race['race_num']}/expert-form",
                ),
            }

            # ----------------------------------------------------
            # RESULT CHECK FIRST
            # ----------------------------------------------------
            result = fetch_result(race_obj)
            if result:
                save_result(result)
                auto_settle_bets(result)
                results_captured += 1
                log.info(
                    f"{race_obj['race_uid']}: result captured during refresh "
                    f"winner={result.get('winner')}"
                )
                time.sleep(0.3)
                continue

            # ----------------------------------------------------
            # LATE SCRATCHINGS
            # ----------------------------------------------------
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
                    late_scratches_applied += 1

                    log.info(
                        f"{race['race_uid']}: late scratches applied "
                        f"track={race['track']} R{race['race_num']} boxes={new_scratches}"
                    )
                except Exception as e:
                    log.error(f"Late scratching update failed {race.get('race_uid')}: {e}")

            time.sleep(0.3)

        except Exception as e:
            failed += 1
            log.error(f"Rolling refresh error {race.get('race_uid')}: {e}")

    summary = {
        "ok": True,
        "results_captured": results_captured,
        "late_scratches_applied": late_scratches_applied,
        "upcoming_checked": checked,
        "failed": failed,
    }

    log.info(f"Rolling refresh summary: {summary}")
    return summary
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
            log.info("get_next_race: no upcoming races found")
            return None

        # Prefer valid jump_time rows first
        valid_races = [r for r in races if r.get("jump_time")]
        scan_pool = valid_races if valid_races else races

        if anchor_time:
            for race in scan_pool:
                jump_time = race.get("jump_time")
                if jump_time and jump_time > anchor_time:
                    log.info(
                        f"get_next_race: selected {race.get('track')} R{race.get('race_num')} "
                        f"jump_time={jump_time} anchor={anchor_time}"
                    )
                    return race

        chosen = scan_pool[0]
        log.info(
            f"get_next_race: fallback selected {chosen.get('track')} R{chosen.get('race_num')} "
            f"jump_time={chosen.get('jump_time')}"
        )
        return chosen

    except Exception as e:
        log.error(f"Get next race failed: {e}")
        return None


def get_board(limit=10):
    try:
        from db import get_db, T

        db = get_db()
        rows = (
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

        log.info(f"get_board: returning {len(rows)} races")
        return rows

    except Exception as e:
        log.error(f"Get board failed: {e}")
        return []


def get_race_with_runners(track, race_num):
    try:
        from db import get_db, T

        db = get_db()
        track = (track or "").strip().lower()

        races = (
            db.table(T("today_races"))
            .select("*")
            .eq("date", date.today().isoformat())
            .eq("track", track)
            .eq("race_num", race_num)
            .limit(1)
            .execute()
            .data
            or []
        )

        if not races:
            log.warning(f"get_race_with_runners: no race found for {track} R{race_num}")
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

        log.info(
            f"get_race_with_runners: {track} R{race_num} "
            f"race_uid={race.get('race_uid')} runners={len(runners)}"
        )

        return race, runners

    except Exception as e:
        log.error(f"Get race with runners failed: {e}")
        return None, []
