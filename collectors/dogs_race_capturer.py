"""
collectors/dogs_race_capturer.py
==================================
Browser-based capturer for a single greyhound race page on thedogs.com.au.

Opens the race's expert form page, waits for content to fully render,
captures rendered HTML and a full-page screenshot.

Navigation path (same-source, no cold deep-linking):
    board link → race detail → expert form tab (if not already on it)

Returns:
    dict with keys:
      html          — full rendered HTML of the page
      screenshot_path — absolute path to saved PNG
      source_url    — the URL that was captured
      ok            — True if capture succeeded

On failure:
    - Saves failure screenshot + HTML
    - Returns {"ok": False, "error": ..., "screenshot_path": ..., "html": ""}

Logging prefix: [DOGS_CAPTURER]
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime

log = logging.getLogger(__name__)

_SCREENSHOT_DIR = os.environ.get("DOGS_SCREENSHOT_DIR", "/tmp/demonpulse_dogs")
_MAX_RETRIES = int(os.environ.get("DOGS_CAPTURER_MAX_RETRIES", "3"))
_PAGE_TIMEOUT_MS = int(os.environ.get("DOGS_PAGE_TIMEOUT_MS", "30000"))
_EXPERT_FORM_SUFFIX = "/expert-form"

# Selector we wait for on a loaded race expert-form page
_RACE_CONTENT_SELECTOR = "main"
_RUNNERS_SELECTOR = (
    "[class*='runner'], [class*='dog'], [class*='form-guide'], "
    "[class*='race-detail']"
)


def _ensure_dir() -> None:
    os.makedirs(_SCREENSHOT_DIR, exist_ok=True)


def _build_expert_form_url(race_link: str) -> str:
    """
    Ensure the URL points to the expert-form page.
    If the link already ends in /expert-form, return as-is.
    """
    url = race_link.rstrip("/")
    if not url.endswith(_EXPERT_FORM_SUFFIX):
        url = url + _EXPERT_FORM_SUFFIX
    return url


def capture_race(
    race_link: str,
    track_name: str,
    race_number: int,
    date_slug: str,
) -> dict:
    """
    Navigate to a race expert-form page and capture rendered content.

    Args:
        race_link:    URL from the board (e.g. /racing/townsville/2026-04-10/1)
        track_name:   For logging and filename
        race_number:  For logging and filename
        date_slug:    ISO date string

    Returns:
        {
            "ok": bool,
            "html": str,
            "screenshot_path": str | None,
            "source_url": str,
            "error": str | None,
        }
    """
    target_url = _build_expert_form_url(race_link)
    # Sanitize track_name for use in filenames — only allow alnum/underscore
    slug = re.sub(r"[^a-z0-9_]", "", re.sub(r"[^a-z0-9]", "_", track_name.lower()))[:40]
    prefix = f"race_{slug}_r{int(race_number) if str(race_number).isdigit() else 0}_{re.sub(r'[^0-9-]', '', date_slug)}"

    log.info(
        f"[DOGS_CAPTURER] capturing race "
        f"track={track_name} R{race_number} date={date_slug} url={target_url}"
    )

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("[DOGS_CAPTURER] playwright not installed")
        return {"ok": False, "html": "", "screenshot_path": None,
                "source_url": target_url, "error": "playwright_not_installed"}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(_PAGE_TIMEOUT_MS)

            loaded = False
            attempt = 0
            while attempt < _MAX_RETRIES and not loaded:
                attempt += 1
                try:
                    log.info(
                        f"[DOGS_CAPTURER] navigate attempt={attempt} url={target_url}"
                    )
                    page.goto(target_url, wait_until="domcontentloaded")
                    page.wait_for_selector(_RACE_CONTENT_SELECTOR,
                                           timeout=_PAGE_TIMEOUT_MS)
                    loaded = True
                except PWTimeout:
                    log.warning(
                        f"[DOGS_CAPTURER] timeout attempt={attempt}/{_MAX_RETRIES}"
                    )
                except Exception as exc:
                    log.warning(
                        f"[DOGS_CAPTURER] load error attempt={attempt}/{_MAX_RETRIES}: {exc}"
                    )

            if not loaded:
                log.error(
                    f"[DOGS_CAPTURER] all {_MAX_RETRIES} load attempts failed "
                    f"track={track_name} R{race_number}"
                )
                _ensure_dir()
                ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                fail_shot = os.path.join(_SCREENSHOT_DIR, f"{prefix}_fail_{ts}.png")
                fail_html = os.path.join(_SCREENSHOT_DIR, f"{prefix}_fail_{ts}.html")
                try:
                    page.screenshot(path=fail_shot, full_page=True)
                except Exception:
                    fail_shot = None
                try:
                    with open(fail_html, "w", encoding="utf-8") as fh:
                        fh.write(page.content())
                except Exception:
                    fail_html = None
                context.close()
                browser.close()
                return {
                    "ok": False,
                    "html": "",
                    "screenshot_path": fail_shot,
                    "html_path": fail_html,
                    "source_url": target_url,
                    "error": "page_load_failed",
                }

            # Try to click expert-form tab if we're not already on it
            try:
                ef_tab = page.query_selector(
                    "[class*='expert'], a[href*='expert-form'], button[class*='tab']"
                )
                if ef_tab:
                    ef_tab.click()
                    page.wait_for_timeout(1500)
            except Exception:
                pass  # Already on expert-form or tab not present

            # Expand runner details if there's a "show more" / expand button
            try:
                for expand_btn in page.query_selector_all(
                    "[class*='expand'], [class*='show-more'], [aria-expanded='false']"
                ):
                    try:
                        expand_btn.click()
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
            except Exception:
                pass

            # Capture rendered HTML
            html = page.content()

            # Save full-page screenshot
            _ensure_dir()
            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            shot_path = os.path.join(_SCREENSHOT_DIR, f"{prefix}_{ts}.png")
            html_path = os.path.join(_SCREENSHOT_DIR, f"{prefix}_{ts}.html")
            try:
                page.screenshot(path=shot_path, full_page=True)
                log.info(f"[DOGS_CAPTURER] screenshot saved: {shot_path}")
            except Exception:
                shot_path = None
            try:
                with open(html_path, "w", encoding="utf-8") as fh:
                    fh.write(html)
                log.info(f"[DOGS_CAPTURER] HTML saved: {html_path}")
            except Exception:
                html_path = None

            context.close()
            browser.close()

            log.info(
                f"[DOGS_CAPTURER] capture ok "
                f"track={track_name} R{race_number} html_len={len(html)}"
            )
            return {
                "ok": True,
                "html": html,
                "html_path": html_path,
                "screenshot_path": shot_path,
                "source_url": target_url,
                "error": None,
            }

    except Exception as exc:
        log.error(
            f"[DOGS_CAPTURER] capture_race raised: {exc}",
            exc_info=True,
        )
        return {
            "ok": False,
            "html": "",
            "screenshot_path": None,
            "html_path": None,
            "source_url": target_url,
            "error": str(exc),
        }
