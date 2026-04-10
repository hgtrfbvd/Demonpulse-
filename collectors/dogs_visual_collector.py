"""
collectors/dogs_visual_collector.py
=====================================
AUTHORITATIVE Dogs visual pipeline — Playwright-only, no scraping/API.

Pipeline flow:
  1.  Playwright launches (headless + stealth)
  2.  Open TheDogs main race guide board
  3.  Wait for full JS render
  4.  Capture board.png (full page)
  5.  Detect & navigate to next upcoming race via UI click
  6.  Ensure expert form is visible
  7.  Capture: header.png, expert_form.png, box_history.png, results.png
  8.  Store screenshots under /data/YYYY-MM-DD/TRACK_RACE/
  9.  Run LOCAL OCR (Tesseract + EasyOCR hybrid) → structured JSON
  10. Save race.json locally
  11. Upsert structured data to Supabase dogs_races table

ALL components read from the same stored structured data.
No re-fetching, no alternate sources, no external LLMs.

Logging prefix: [DOGS_VISUAL]
"""
from __future__ import annotations

import json
import logging
import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
_BASE_URL = "https://www.thedogs.com.au"
_RACE_GUIDE_PATH = "/racing/racecards"
_DATA_ROOT = Path(os.environ.get("DOGS_DATA_ROOT", "data"))
_HEADLESS = os.environ.get("DOGS_HEADLESS", "true").lower() != "false"
_PAGE_TIMEOUT_MS = int(os.environ.get("DOGS_PAGE_TIMEOUT_MS", "30000"))
_RENDER_PAUSE_MS = int(os.environ.get("DOGS_RENDER_PAUSE_MS", "3000"))
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)

# Selectors tried in order to find the "next upcoming race" link on the board
_RACE_NAV_SELECTORS = [
    "a[href*='/racing/'][href*='/20']",   # direct race links with date in URL
    "[class*='race-card'] a",
    "[class*='race-row'] a",
    "a[href*='/racing/']",
]

# Tab labels tried for expert form / box history / results
_EXPERT_FORM_LABELS = ["Expert Form", "Expert", "Form Guide", "Form"]
_BOX_HISTORY_LABELS = ["Box History", "Box Stats", "History"]
_RESULTS_LABELS = ["Results", "Result", "Finishing Order"]


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# MAIN CLASS
# ──────────────────────────────────────────────────────────────────────────────

class DogsVisualCollector:
    """
    Production-ready Playwright visual pipeline for TheDogs.com.au.

    Usage:
        collector = DogsVisualCollector()
        result = collector.run_full_pipeline()          # today
        result = collector.run_full_pipeline("2026-04-10")
    """

    def __init__(self) -> None:
        import easyocr  # lazy import — heavy; only needed at OCR time
        self._reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        log.info("[DOGS_VISUAL] EasyOCR reader initialised (cpu)")

    # ──────────────────────────────────────────────────────────────────────────
    # PUBLIC ENTRY POINT
    # ──────────────────────────────────────────────────────────────────────────

    def run_full_pipeline(self, date_str: str | None = None) -> dict:
        """
        Run the full visual pipeline for a given date (defaults to today).

        Returns a summary dict: {"ok": bool, "date": str, "race_dir": str, ...}
        Never raises — saves debug artifacts on failure.
        """
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        day_dir = _DATA_ROOT / date_str
        _ensure_dir(day_dir)

        log.info(f"[DOGS_VISUAL] pipeline start date={date_str} dir={day_dir}")

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            log.error("[DOGS_VISUAL] playwright not installed")
            return {"ok": False, "date": date_str, "error": "playwright_not_installed"}

        try:
            from playwright_stealth import stealth_sync as _stealth_sync
            _apply_stealth = _stealth_sync
        except ImportError:
            log.warning("[DOGS_VISUAL] playwright-stealth not installed — running without stealth")
            _apply_stealth = None

        race_dir: Path | None = None
        structured: dict = {}

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=_HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=_USER_AGENT,
            )
            if _apply_stealth:
                _apply_stealth(context)

            page = context.new_page()
            page.set_default_timeout(_PAGE_TIMEOUT_MS)

            try:
                # ── STEP 2: open board ──────────────────────────────────────
                board_url = f"{_BASE_URL}{_RACE_GUIDE_PATH}"
                log.info(f"[DOGS_VISUAL] opening board url={board_url}")
                page.goto(board_url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=_PAGE_TIMEOUT_MS)
                page.wait_for_timeout(_RENDER_PAUSE_MS)

                # ── STEP 4: capture board ────────────────────────────────────
                board_path = day_dir / "board.png"
                page.screenshot(path=str(board_path), full_page=True)
                log.info(f"[DOGS_VISUAL] board.png saved path={board_path}")

                # ── STEP 5: detect & navigate to next upcoming race ──────────
                race_link, race_slug = self._detect_next_race(page)
                if not race_link:
                    log.warning("[DOGS_VISUAL] no upcoming race link found — using board fallback")
                    structured = self._parse_board_fallback(str(board_path), date_str)
                    json_path = day_dir / "race.json"
                    with open(json_path, "w", encoding="utf-8") as fh:
                        json.dump(structured, fh, indent=2)
                    self._save_to_supabase(structured, date_str)
                    return {
                        "ok": True,
                        "date": date_str,
                        "race_dir": str(day_dir),
                        "source": "board_fallback",
                    }

                log.info(f"[DOGS_VISUAL] navigating to race slug={race_slug}")
                page.click(f'a[href="{race_link}"]', timeout=_PAGE_TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=_PAGE_TIMEOUT_MS)
                page.wait_for_timeout(_RENDER_PAUSE_MS)

                # Derive race directory from slug / URL
                race_dir = self._make_race_dir(day_dir, race_slug, page.url)
                _ensure_dir(race_dir)

                # ── STEP capture: race header ────────────────────────────────
                header_path = race_dir / "header.png"
                page.screenshot(path=str(header_path), full_page=True)
                log.info(f"[DOGS_VISUAL] header.png saved path={header_path}")

                # ── STEP 7: expert form ──────────────────────────────────────
                self._click_tab(page, _EXPERT_FORM_LABELS)
                expert_path = race_dir / "expert_form.png"
                page.screenshot(path=str(expert_path), full_page=True)
                log.info(f"[DOGS_VISUAL] expert_form.png saved path={expert_path}")

                # ── STEP 8: box history ──────────────────────────────────────
                self._click_tab(page, _BOX_HISTORY_LABELS)
                box_path = race_dir / "box_history.png"
                page.screenshot(path=str(box_path), full_page=True)
                log.info(f"[DOGS_VISUAL] box_history.png saved path={box_path}")

                # ── STEP 8: results (best-effort — may not yet exist) ────────
                results_path = race_dir / "results.png"
                try:
                    self._click_tab(page, _RESULTS_LABELS)
                    page.screenshot(path=str(results_path), full_page=True)
                    log.info(f"[DOGS_VISUAL] results.png saved path={results_path}")
                except Exception as exc:
                    log.info(f"[DOGS_VISUAL] results tab not available (pre-race) exc={exc!r}")
                    page.screenshot(path=str(results_path), full_page=True)

                # ── STEP 10: local OCR → structured JSON ─────────────────────
                log.info("[DOGS_VISUAL] running local OCR + parsing")
                structured = self._extract_with_ocr(race_dir)

                json_path = race_dir / "race.json"
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(structured, fh, indent=2)
                log.info(f"[DOGS_VISUAL] race.json saved path={json_path}")

                # ── STEP 11: save to Supabase ─────────────────────────────────
                self._save_to_supabase(structured, date_str)

                log.info(f"[DOGS_VISUAL] pipeline complete date={date_str}")
                return {
                    "ok": True,
                    "date": date_str,
                    "race_dir": str(race_dir),
                    "source": "playwright_ocr",
                }

            except Exception as exc:
                tb = traceback.format_exc()
                log.error(f"[DOGS_VISUAL] pipeline error: {exc}\n{tb}")
                target_dir = race_dir or day_dir
                self._save_debug_artifacts(page, target_dir)
                return {
                    "ok": False,
                    "date": date_str,
                    "error": str(exc),
                    "race_dir": str(target_dir),
                }
            finally:
                browser.close()

    # ──────────────────────────────────────────────────────────────────────────
    # NAVIGATION HELPERS
    # ──────────────────────────────────────────────────────────────────────────

    def _detect_next_race(self, page) -> tuple[str | None, str | None]:
        """
        Scan the loaded board page for the next upcoming race link.
        Returns (href, slug) or (None, None) if nothing found.
        """
        for selector in _RACE_NAV_SELECTORS:
            try:
                els = page.query_selector_all(selector)
                for el in els:
                    href = el.get_attribute("href") or ""
                    m = re.search(r"/racing/([^/]+)/(\d{4}-\d{2}-\d{2})/(\d+)", href)
                    if m:
                        full_href = href if href.startswith("http") else f"{_BASE_URL}{href}"
                        slug = f"{m.group(1)}_R{m.group(3)}"
                        log.info(f"[DOGS_VISUAL] detected race href={full_href} slug={slug}")
                        return full_href, slug
            except Exception as exc:
                log.debug(f"[DOGS_VISUAL] selector {selector!r} failed: {exc}")
        return None, None

    def _click_tab(self, page, labels: list[str]) -> None:
        """Try clicking a tab by exact text label; tries each label in order."""
        last_exc: Exception | None = None
        for label in labels:
            try:
                page.get_by_text(label, exact=True).first.click(timeout=8000)
                page.wait_for_load_state("networkidle", timeout=_PAGE_TIMEOUT_MS)
                page.wait_for_timeout(1500)
                log.debug(f"[DOGS_VISUAL] tab clicked label={label!r}")
                return
            except Exception as exc:
                last_exc = exc
        log.warning(
            f"[DOGS_VISUAL] could not click any tab from {labels} — last error: {last_exc!r}"
        )

    def _make_race_dir(self, day_dir: Path, race_slug: str | None, current_url: str) -> Path:
        """Derive a stable race subdirectory name from slug or current URL."""
        if race_slug:
            safe = re.sub(r"[^A-Za-z0-9_\-]", "_", race_slug)
            return day_dir / safe
        # fallback: extract from URL
        m = re.search(r"/racing/([^/]+)/\d{4}-\d{2}-\d{2}/(\d+)", current_url)
        if m:
            return day_dir / f"{m.group(1)}_R{m.group(2)}"
        return day_dir / "unknown_race"

    def _save_debug_artifacts(self, page, target_dir: Path) -> None:
        """Save failure screenshot + HTML dump to target_dir/debug/."""
        debug_dir = target_dir / "debug"
        _ensure_dir(debug_dir)
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        try:
            page.screenshot(path=str(debug_dir / f"failure_{ts}.png"), full_page=True)
            log.info(f"[DOGS_VISUAL] debug screenshot saved dir={debug_dir}")
        except Exception as exc:
            log.warning(f"[DOGS_VISUAL] debug screenshot failed: {exc}")
        try:
            html = page.content()
            html_path = debug_dir / f"failure_{ts}.html"
            html_path.write_text(html, encoding="utf-8")
            log.info(f"[DOGS_VISUAL] debug HTML saved path={html_path}")
        except Exception as exc:
            log.warning(f"[DOGS_VISUAL] debug HTML save failed: {exc}")

    # ──────────────────────────────────────────────────────────────────────────
    # OCR / EXTRACTION
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_with_ocr(self, race_dir: Path) -> dict:
        """
        Run Tesseract + EasyOCR on all captured screenshots and combine
        into a single structured JSON payload.

        Returns:
            {
                "race_date": str,
                "source": "visual_playwright_ocr",
                "race_metadata": dict,
                "runners": list[dict],
                "form_lines": list[dict],
                "box_history": list[dict],
                "results": list[dict],
                "raw_ocr": dict,
            }
        """
        date_str = race_dir.parent.name  # day_dir.name == YYYY-MM-DD

        raw_ocr: dict[str, list[str]] = {}

        header_path = race_dir / "header.png"
        expert_path = race_dir / "expert_form.png"
        box_path    = race_dir / "box_history.png"
        results_path = race_dir / "results.png"

        header_lines  = self._ocr_image(header_path,  "header")  if header_path.exists()  else []
        expert_lines  = self._ocr_image(expert_path,  "expert")  if expert_path.exists()  else []
        box_lines     = self._ocr_image(box_path,     "box")     if box_path.exists()     else []
        results_lines = self._ocr_image(results_path, "results") if results_path.exists() else []

        raw_ocr["header"]  = header_lines
        raw_ocr["expert"]  = expert_lines
        raw_ocr["box"]     = box_lines
        raw_ocr["results"] = results_lines

        race_metadata = self._parse_race_header(header_lines)
        runners       = self._parse_expert_form(expert_lines)
        box_history   = self._parse_box_history(box_lines)
        results       = self._parse_results(results_lines)

        return {
            "race_date":    date_str,
            "source":       "visual_playwright_ocr",
            "race_metadata": race_metadata,
            "runners":      runners,
            "form_lines":   [r.get("form_line", "") for r in runners if r.get("form_line")],
            "box_history":  box_history,
            "results":      results,
            "raw_ocr":      raw_ocr,
        }

    def _preprocess_image(self, img_path: Path) -> "Image.Image":
        """Preprocess a PIL image for better OCR accuracy."""
        from PIL import Image, ImageFilter, ImageEnhance
        img = Image.open(img_path).convert("L")   # greyscale
        img = img.filter(ImageFilter.SHARPEN)
        img = ImageEnhance.Contrast(img).enhance(2.0)
        return img

    def _ocr_image(self, img_path: Path, label: str) -> list[str]:
        """
        OCR a single image using Tesseract as primary, EasyOCR as fallback.
        Returns a list of non-empty text lines.
        """
        import pytesseract
        from PIL import Image

        lines: list[str] = []

        # ── Tesseract primary ────────────────────────────────────────────────
        try:
            img = self._preprocess_image(img_path)
            raw = pytesseract.image_to_string(img, config="--psm 6 --oem 3")
            tess_lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            if tess_lines:
                log.debug(f"[DOGS_VISUAL] tesseract {label} lines={len(tess_lines)}")
                lines = tess_lines
        except Exception as exc:
            log.warning(f"[DOGS_VISUAL] tesseract failed for {label}: {exc}")

        # ── EasyOCR fallback (when Tesseract yields nothing useful) ──────────
        if len(lines) < 3:
            try:
                easy_results = self._reader.readtext(str(img_path), detail=0, paragraph=True)
                easy_lines = [ln.strip() for ln in easy_results if ln.strip()]
                if easy_lines:
                    log.debug(f"[DOGS_VISUAL] easyocr {label} fallback lines={len(easy_lines)}")
                    lines = easy_lines if len(easy_lines) > len(lines) else lines
            except Exception as exc:
                log.warning(f"[DOGS_VISUAL] easyocr failed for {label}: {exc}")

        return lines

    # ──────────────────────────────────────────────────────────────────────────
    # PARSE HELPERS
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_race_header(self, lines: list[str]) -> dict:
        """
        Extract race metadata from header OCR lines.

        Typical header text (thedogs.com.au):
            Wentworth Park Race 3
            Thursday 10 April 2026  2:15pm AEST
            520m  Grade 5  Prize $4,000
        """
        meta: dict[str, Any] = {
            "track":       None,
            "race_number": None,
            "race_name":   None,
            "date":        None,
            "jump_time":   None,
            "distance_m":  None,
            "grade":       None,
            "prize_money": None,
            "condition":   None,
        }
        if not lines:
            return meta

        combined = " ".join(lines)

        # Track + race number: "Wentworth Park Race 3"
        m = re.search(r"^(.+?)\s+Race\s+(\d+)", lines[0], re.IGNORECASE)
        if m:
            meta["track"] = m.group(1).strip()
            meta["race_number"] = int(m.group(2))
        else:
            m2 = re.search(r"Race\s+(\d+)", combined, re.IGNORECASE)
            if m2:
                meta["race_number"] = int(m2.group(1))

        # Time: e.g. "2:15pm", "14:15"
        m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", combined, re.IGNORECASE)
        if m:
            hour, minute, period = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0
            meta["jump_time"] = f"{hour:02d}:{minute:02d}"

        # Distance: "520m"
        m = re.search(r"(\d{3,4})\s*m\b", combined, re.IGNORECASE)
        if m:
            meta["distance_m"] = int(m.group(1))

        # Grade: "Grade 5" / "G5" / "Free For All"
        m = re.search(r"Grade\s*(\d+|[A-Z\s]+)", combined, re.IGNORECASE)
        if m:
            meta["grade"] = m.group(1).strip()

        # Prize money: "$4,000" / "$4000"
        m = re.search(r"\$\s*([\d,]+)", combined)
        if m:
            meta["prize_money"] = m.group(1).replace(",", "")

        # Race name (second line if it doesn't look like a date/track)
        if len(lines) >= 2:
            meta["race_name"] = lines[1].strip()

        # Track condition: "Good", "Fast", "Slow", "Heavy"
        m = re.search(r"\b(Good|Fast|Slow|Heavy|Wet)\b", combined, re.IGNORECASE)
        if m:
            meta["condition"] = m.group(1).title()

        return meta

    def _parse_expert_form(self, lines: list[str]) -> list[dict]:
        """
        Extract runner records from expert form OCR lines.

        Typical pattern per runner:
            1  Box  DOG NAME         T: Trainer Name
               Best: 29.85  Last 5: 1-2-3-1-2  SP: $3.50
               Form: 1121231

        Returns a list of runner dicts with: box, name, trainer, best_time,
        last_five, sp, form_line, career, splits.
        """
        runners: list[dict] = []
        if not lines:
            return runners

        current: dict | None = None

        for line in lines:
            # Detect start of a new runner: leading box number
            m = re.match(r"^(\d{1,2})\s+(.+)", line)
            if m and len(m.group(2)) > 1:
                # Possibly "1  BOX HEADER" — skip header rows
                if re.match(r"(?i)^box\s+name", m.group(2)):
                    continue
                if current:
                    runners.append(current)
                box_num = int(m.group(1))
                remainder = m.group(2).strip()
                # Trainer inline: "DOG NAME  T: Trainer"
                trainer = None
                tm = re.search(r"[Tt]:\s*(.+)", remainder)
                if tm:
                    trainer = tm.group(1).strip()
                    remainder = remainder[: tm.start()].strip()
                current = {
                    "box":       box_num,
                    "name":      remainder,
                    "trainer":   trainer,
                    "best_time": None,
                    "last_five": None,
                    "sp":        None,
                    "form_line": None,
                    "career":    None,
                    "splits":    [],
                    "scratched": False,
                }
                if re.search(r"(?i)scratch|scr\b", line):
                    current["scratched"] = True
                continue

            if current is None:
                continue

            # Best time: "Best: 29.85" / "29.85"
            m = re.search(r"(?i)best[:\s]+(\d+\.\d+)", line)
            if m:
                current["best_time"] = m.group(1)

            # Last 5 results: "Last 5: 1-2-3-1-2" / "12312"
            m = re.search(r"(?i)last\s*5[:\s]+([1-9X\-\.]+)", line)
            if m:
                current["last_five"] = m.group(1)

            # Starting price: "SP: $3.50" / "$3.50"
            m = re.search(r"(?i)(?:sp[:\s]+)?\$\s*([\d\.]+)", line)
            if m:
                current["sp"] = m.group(1)

            # Form string: "Form: 11212" or standalone digits/letters
            m = re.search(r"(?i)form[:\s]+([1-9X\.]+)", line)
            if m:
                current["form_line"] = m.group(1)

            # Career stats: "Career: 20: 5-3-2" / "20-5-3-2"
            m = re.search(r"(?i)career[:\s]+(\d+)[-:\s]+(\d+)[-:\s]+(\d+)[-:\s]+(\d+)", line)
            if m:
                starts, wins, sec, third = m.group(1), m.group(2), m.group(3), m.group(4)
                current["career"] = f"{starts}:{wins}-{sec}-{third}"

            # Trainer inline (may appear on second detail line)
            if not current.get("trainer"):
                tm = re.search(r"[Tt]:\s*(.+)", line)
                if tm:
                    current["trainer"] = tm.group(1).strip()

            # Split times: e.g. "5m: 5.12  Splits: 5.12/10.24/20.48"
            split_m = re.findall(r"\d+\.\d+", line)
            if split_m and len(split_m) > 1:
                current["splits"].extend(split_m)

        if current:
            runners.append(current)

        return runners

    def _parse_box_history(self, lines: list[str]) -> list[dict]:
        """
        Extract box history statistics from OCR lines.

        Typical columns: Box | Starts | Wins | Places | Win% | Place%
        """
        records: list[dict] = []
        if not lines:
            return records

        for line in lines:
            # Skip header rows
            if re.match(r"(?i)box\s+(start|wins|no\.)", line):
                continue
            # Match: box_num  starts  wins  places  win%  place%
            m = re.match(
                r"(\d{1,2})\s+(\d+)\s+(\d+)\s+(\d+)\s*([\d\.]+%?)\s*([\d\.]+%?)?",
                line,
            )
            if m:
                records.append({
                    "box":      int(m.group(1)),
                    "starts":   int(m.group(2)),
                    "wins":     int(m.group(3)),
                    "places":   int(m.group(4)),
                    "win_pct":  m.group(5).replace("%", ""),
                    "place_pct": (m.group(6) or "").replace("%", "") or None,
                })

        return records

    def _parse_results(self, lines: list[str]) -> list[dict]:
        """
        Extract finishing order from results OCR lines.

        Typical: "1st  Box 3  GREYHOUND NAME  29.85"
        """
        results: list[dict] = []
        if not lines:
            return results

        for line in lines:
            # "1st Box 3 DOG NAME 29.85" or "1  3  DOG NAME  29.85"
            m = re.match(
                r"(?:(\d+)(?:st|nd|rd|th)?\.?\s+)?(?:[Bb]ox\s+)?(\d{1,2})\s+([A-Z][A-Za-z\s'\-]+?)\s+([\d]+\.[\d]+)",
                line,
            )
            if m:
                results.append({
                    "position": int(m.group(1)) if m.group(1) else len(results) + 1,
                    "box":      int(m.group(2)),
                    "name":     m.group(3).strip(),
                    "time":     m.group(4),
                })

        return results

    def _parse_board_fallback(self, board_path: str, date_str: str) -> dict:
        """
        Parse the board screenshot when no specific race could be navigated to.
        Runs OCR on board.png and extracts a list of upcoming races.
        """
        board = Path(board_path)
        if not board.exists():
            return {
                "race_date": date_str,
                "source": "board_fallback_no_image",
                "race_metadata": {},
                "runners": [],
                "form_lines": [],
                "box_history": [],
                "results": [],
                "raw_ocr": {},
            }

        lines = self._ocr_image(board, "board_fallback")
        races: list[dict] = []
        for line in lines:
            m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?\s+(.+)", line, re.IGNORECASE)
            if m:
                races.append({
                    "time": f"{m.group(1)}:{m.group(2)}{m.group(3) or ''}",
                    "info": m.group(4).strip(),
                })

        return {
            "race_date":    date_str,
            "source":       "board_fallback_ocr",
            "race_metadata": {"board_races": races},
            "runners":      [],
            "form_lines":   [],
            "box_history":  [],
            "results":      [],
            "raw_ocr":      {"board": lines},
        }

    # ──────────────────────────────────────────────────────────────────────────
    # SUPABASE PERSISTENCE
    # ──────────────────────────────────────────────────────────────────────────

    def _save_to_supabase(self, data: dict, date_str: str) -> None:
        """
        Upsert structured race data to the dogs_races Supabase table.
        Uses the project's existing db.py / env.py connection so TEST/LIVE
        mode is respected automatically.
        """
        try:
            from db import get_db, T
        except ImportError:
            log.warning("[DOGS_VISUAL] db.py not available — skipping Supabase save")
            return

        try:
            db = get_db()
            table_name = T("dogs_races")

            meta = data.get("race_metadata") or {}
            track = meta.get("track") or ""
            race_num = meta.get("race_number") or 0
            row = {
                "race_date":    date_str,
                "track":        track,
                "race_num":     race_num,
                "race_uid":     f"{date_str}_GREYHOUND_{track.lower().replace(' ', '_')}_{race_num}",
                "source":       data.get("source", "visual_playwright_ocr"),
                "race_metadata": meta,
                "runners":      data.get("runners", []),
                "form_lines":   data.get("form_lines", []),
                "box_history":  data.get("box_history", []),
                "results":      data.get("results", []),
                "raw_ocr":      data.get("raw_ocr", {}),
                "captured_at":  datetime.utcnow().isoformat(),
            }
            db.table(table_name).upsert(row, on_conflict="race_uid").execute()
            log.info(f"[DOGS_VISUAL] saved to Supabase table={table_name} race_uid={row['race_uid']}")
        except Exception as exc:
            log.error(f"[DOGS_VISUAL] Supabase save failed: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# STANDALONE ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    collector = DogsVisualCollector()
    result = collector.run_full_pipeline(date_arg)
    print(json.dumps(result, indent=2))
