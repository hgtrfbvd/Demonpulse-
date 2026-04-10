"""
modules/dogs_capture/capture_pipeline.py
==========================================
Playwright-based greyhound race capture pipeline module.

Navigates thedogs.com.au, captures structured screenshots, POSTs them
to an extraction service, and builds the extracted_data portion of a
DogsRacePacket.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.base_module import BaseModule

log = logging.getLogger(__name__)

BASE_URL = "https://www.thedogs.com.au"
DATA_ROOT = Path(os.getenv("DOGS_DATA_ROOT", "data/dogs"))
EXTRACTION_SERVICE_URL = os.getenv("EXTRACTION_SERVICE_URL", "")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


class DogsCaptureModule(BaseModule):
    """
    Playwright capture + extraction module.

    Input:  race_uid (optional), race_time (optional), race_url (optional)
    Output: screenshots dict, extracted_data dict, status string
    """

    module_name = "dogs_capture"
    module_type = "capture"
    version = "1.0.0"
    input_requirements = []
    output_keys = ["screenshots", "extracted_data", "status"]

    def process(self, packet: dict[str, Any]) -> dict[str, Any]:
        race_uid = packet.get("race_uid") or datetime.now().strftime("%Y%m%d_%H%M%S")
        race_url = packet.get("source_url") or ""
        date_str = packet.get("date") or datetime.now().strftime("%Y-%m-%d")

        race_dir = DATA_ROOT / date_str / race_uid
        _ensure_dir(race_dir)

        try:
            screenshots = self._capture_screenshots(race_dir, race_url)
            extracted = self._extract_data(screenshots, race_uid, date_str)
            status = "EXTRACTED" if extracted.get("runners") else "CAPTURED"
            return {
                "screenshots": screenshots,
                "extracted_data": extracted,
                "status": status,
            }
        except Exception as e:
            log.error(f"[dogs_capture] process failed for {race_uid}: {e}")
            return {}

    def _capture_screenshots(self, race_dir: Path, race_url: str) -> dict[str, str]:
        """
        Use Playwright to navigate thedogs.com.au and capture key screenshots.
        Returns dict mapping screen name → absolute file path.
        """
        screenshots: dict[str, str] = {}
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()

                try:
                    board_url = race_url or f"{BASE_URL}/racing/racecards"
                    page.goto(board_url, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(3000)
                    board_path = race_dir / "board.png"
                    board_path.write_bytes(page.screenshot(full_page=True))
                    screenshots["board"] = str(board_path)
                    log.info(f"[dogs_capture] board screenshot saved: {board_path}")

                    if not race_url:
                        try:
                            page.locator("text=View").first.click(timeout=10000)
                            page.wait_for_load_state("networkidle", timeout=15000)
                            page.wait_for_timeout(3000)
                        except Exception as nav_err:
                            log.warning(f"[dogs_capture] Board nav failed: {nav_err}")

                    header_path = race_dir / "header.png"
                    header_path.write_bytes(page.screenshot(full_page=True))
                    screenshots["header"] = str(header_path)

                    try:
                        page.get_by_text("Expert Form", exact=True).click(timeout=10000)
                        page.wait_for_load_state("networkidle", timeout=15000)
                        page.wait_for_timeout(2000)
                        ef_path = race_dir / "expert_form.png"
                        ef_path.write_bytes(page.screenshot(full_page=True))
                        screenshots["expert_form"] = str(ef_path)
                    except Exception as ef_err:
                        log.warning(f"[dogs_capture] Expert Form tab failed: {ef_err}")

                    try:
                        page.get_by_text("Box History", exact=True).click(timeout=10000)
                        page.wait_for_load_state("networkidle", timeout=15000)
                        page.wait_for_timeout(2000)
                        bh_path = race_dir / "box_history.png"
                        bh_path.write_bytes(page.screenshot(full_page=True))
                        screenshots["box_history"] = str(bh_path)
                    except Exception as bh_err:
                        log.warning(f"[dogs_capture] Box History tab failed: {bh_err}")

                    try:
                        page.get_by_text("Results", exact=True).click(timeout=10000)
                        page.wait_for_load_state("networkidle", timeout=15000)
                        page.wait_for_timeout(2000)
                        res_path = race_dir / "results.png"
                        res_path.write_bytes(page.screenshot(full_page=True))
                        screenshots["results"] = str(res_path)
                    except Exception as res_err:
                        log.warning(f"[dogs_capture] Results tab failed: {res_err}")

                except Exception as page_err:
                    log.error(f"[dogs_capture] Page capture error: {page_err}")
                    debug_dir = race_dir / "debug"
                    _ensure_dir(debug_dir)
                    try:
                        page.screenshot(path=str(debug_dir / "failure.png"), full_page=True)
                        (debug_dir / "failure.html").write_text(page.content())
                    except Exception:
                        pass
                finally:
                    browser.close()

        except Exception as pw_err:
            log.error(f"[dogs_capture] Playwright error: {pw_err}")

        return screenshots

    def _extract_data(
        self,
        screenshots: dict[str, str],
        race_uid: str,
        date_str: str,
    ) -> dict[str, Any]:
        """Post screenshots to extraction service or fall back to OCR stub."""
        if EXTRACTION_SERVICE_URL and screenshots:
            return self._post_to_extraction_service(screenshots, race_uid, date_str)
        return self._ocr_stub(screenshots, race_uid, date_str)

    def _post_to_extraction_service(
        self,
        screenshots: dict[str, str],
        race_uid: str,
        date_str: str,
    ) -> dict[str, Any]:
        """POST screenshots to the configured extraction service."""
        import requests

        try:
            files = {}
            opened = []
            for name, path in screenshots.items():
                f = open(path, "rb")
                opened.append(f)
                files[name] = (f"{name}.png", f, "image/png")

            payload = {"race_uid": race_uid, "date": date_str}
            resp = requests.post(
                f"{EXTRACTION_SERVICE_URL}/extract/dogs/race",
                files=files,
                data=payload,
                timeout=60,
            )
            for f in opened:
                f.close()

            resp.raise_for_status()
            data = resp.json()
            log.info(f"[dogs_capture] Extraction service returned {len(data.get('runners', []))} runners")
            return data
        except Exception as e:
            log.error(f"[dogs_capture] Extraction service POST failed: {e}")
            return self._ocr_stub(screenshots, race_uid, date_str)

    def _ocr_stub(
        self,
        screenshots: dict[str, str],
        race_uid: str,
        date_str: str,
    ) -> dict[str, Any]:
        """Stub extraction — returns minimal structure until OCR is wired up."""
        return {
            "race_uid": race_uid,
            "date": date_str,
            "source": "ocr_stub",
            "runners": [],
            "form_lines": [],
            "times": {},
            "splits": {},
            "box_history_metrics": {},
            "derived_features": {},
            "screenshots_captured": list(screenshots.keys()),
        }
