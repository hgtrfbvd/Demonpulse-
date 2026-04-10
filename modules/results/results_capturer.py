"""
modules/results/results_capturer.py
=====================================
Post-race results capture module.

After race_time + buffer (default 5 mins), navigates to the Results tab,
captures the screenshot, extracts finishing order, margins, and official time.

Updates: race_packet.result and sets status = SETTLED
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.base_module import BaseModule

log = logging.getLogger(__name__)

DATA_ROOT = Path(os.getenv("DOGS_DATA_ROOT", "data/dogs"))
_RESULTS_BUFFER_SECS = int(os.getenv("RESULTS_BUFFER_SECS", "300"))  # 5 min default


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


class ResultsModule(BaseModule):
    """Post-race results capture."""

    module_name = "results"
    module_type = "results"
    version = "1.0.0"
    input_requirements = ["race_uid", "race_time"]
    output_keys = ["result", "status"]

    def process(self, packet: dict[str, Any]) -> dict[str, Any]:
        race_uid = packet.get("race_uid") or ""
        source_url = packet.get("source_url") or ""
        date_str = packet.get("date") or datetime.now().strftime("%Y-%m-%d")

        if not race_uid:
            log.warning("[results] No race_uid in packet")
            return {}

        race_dir = DATA_ROOT / date_str / race_uid
        _ensure_dir(race_dir)

        try:
            screenshot_path, raw_text = self._capture_results_page(source_url, race_dir)
            result = self._extract_result(raw_text, screenshot_path)
            return {
                "result": result,
                "status": "SETTLED" if result.get("finishing_order") else packet.get("status", "ANALYSED"),
            }
        except Exception as e:
            log.error(f"[results] process failed for {race_uid}: {e}")
            return {}

    def _capture_results_page(
        self,
        source_url: str,
        race_dir: Path,
    ) -> tuple[str, str]:
        """Navigate to Results tab and capture screenshot + text."""
        screenshot_path = ""
        raw_text = ""

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
                    url = source_url or "https://www.thedogs.com.au/racing/racecards"
                    page.goto(url, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(3000)

                    page.get_by_text("Results", exact=True).click(timeout=10000)
                    page.wait_for_load_state("networkidle", timeout=15000)
                    page.wait_for_timeout(2000)

                    res_path = race_dir / "results_settled.png"
                    res_path.write_bytes(page.screenshot(full_page=True))
                    screenshot_path = str(res_path)
                    raw_text = page.inner_text("body")

                except Exception as e:
                    log.warning(f"[results] Page capture error: {e}")
                finally:
                    browser.close()

        except Exception as e:
            log.error(f"[results] Playwright error: {e}")

        return screenshot_path, raw_text

    def _extract_result(self, raw_text: str, screenshot_path: str) -> dict[str, Any]:
        """Parse results page text to extract finishing order and times."""
        result: dict[str, Any] = {
            "screenshot_path": screenshot_path,
            "finishing_order": [],
            "margins": [],
            "official_time": None,
            "raw_text_length": len(raw_text),
        }

        if not raw_text:
            return result

        # Extract finishing order: look for box numbers in common formats
        finishing_order = []
        box_pattern = re.compile(r"(?:Box\s*)?(\d+)\s*[-–]", re.MULTILINE)
        for m in box_pattern.finditer(raw_text):
            box_num = int(m.group(1))
            if 1 <= box_num <= 8 and box_num not in finishing_order:
                finishing_order.append(box_num)

        # Fallback: numbered list pattern
        if not finishing_order:
            num_pattern = re.compile(r"^\s*(\d+)\.", re.MULTILINE)
            for m in num_pattern.finditer(raw_text):
                n = int(m.group(1))
                if 1 <= n <= 8:
                    finishing_order.append(n)

        result["finishing_order"] = finishing_order[:8]

        # Official time: look for patterns like "29.34s" or "Time: 29.34"
        time_pattern = re.compile(r"(?:Time[:\s]+)?(\d{2}\.\d{2})s?")
        time_match = time_pattern.search(raw_text)
        if time_match:
            result["official_time"] = time_match.group(1)

        # Margins: look for "by X.XXL" or "X lengths"
        margin_pattern = re.compile(r"(\d+\.?\d*)\s*(?:L|len(?:gth)?s?)", re.IGNORECASE)
        for m in margin_pattern.finditer(raw_text):
            result["margins"].append(float(m.group(1)))

        return result
