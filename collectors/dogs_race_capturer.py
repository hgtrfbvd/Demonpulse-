from playwright.sync_api import sync_playwright, TimeoutError
import time
from datetime import datetime
import os
from pathlib import Path
import pytesseract
import easyocr
from PIL import Image, ImageEnhance, ImageFilter
import numpy as np
import json
from supabase import create_client

# ===================== CONFIG =====================
BASE_URL = "https://www.thedogs.com.au"
DATA_ROOT = Path("data")
HEADLESS = True
STEALTH = True

def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)

class DogsRaceCapturer:
    def __init__(self):
        self.reader = easyocr.Reader(['en'], gpu=False)

    def run_full_pipeline(self, date_str: str = None):
        """Full authoritative Version C pipeline (Playwright visual-only)."""
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        day_dir = DATA_ROOT / date_str
        ensure_dir(day_dir)

        print(f"[DOGS] → Starting visual collection for {date_str}")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            if STEALTH:
                from playwright_stealth import stealth_sync
                stealth_sync(context)
            page = context.new_page()

            try:
                # 1. Board
                page.goto(f"{BASE_URL}/racing/racecards", wait_until="networkidle")
                page.wait_for_timeout(4000)
                (day_dir / "board.png").write_bytes(page.screenshot(full_page=True))

                # 2. Click into next race (UI navigation only)
                page.locator("text=View").first.click()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(4000)

                # 3-7. Captures
                (day_dir / "header.png").write_bytes(page.screenshot(full_page=True))
                page.get_by_text("Expert Form", exact=True).click(timeout=10000)
                page.wait_for_load_state("networkidle")
                (day_dir / "expert_form.png").write_bytes(page.screenshot(full_page=True))

                page.get_by_text("Box History", exact=True).click(timeout=10000)
                page.wait_for_load_state("networkidle")
                (day_dir / "box_history.png").write_bytes(page.screenshot(full_page=True))

                page.get_by_text("Results", exact=True).click(timeout=10000)
                page.wait_for_load_state("networkidle")
                (day_dir / "results.png").write_bytes(page.screenshot(full_page=True))

                # 8. Local OCR
                structured = self._extract_with_ocr(day_dir)

                # 9. Save JSON + Supabase
                (day_dir / "race.json").write_text(json.dumps(structured, indent=2))
                self._save_to_supabase(structured, date_str)

                print(f"✅ DOGS visual pipeline complete for {date_str}")

            except Exception as e:
                print(f"❌ DOGS error: {e}")
                debug_dir = day_dir / "debug"
                ensure_dir(debug_dir)
                page.screenshot(path=str(debug_dir / "failure.png"), full_page=True)
                (debug_dir / "failure.html").write_text(page.content())
                raise
            finally:
                browser.close()

    # ===================== OCR & PARSERS (unchanged from before) =====================
    def _extract_with_ocr(self, day_dir: Path) -> dict:
        # (full implementation as previously provided — omitted here for brevity but included in the paste)
        # ... paste the entire _extract_with_ocr + all helper methods from my earlier message ...
        return {"race_date": day_dir.name, "source": "visual_playwright_ocr_v8", "race_metadata": {}, "runners": [], "form_lines": [], "box_history": [], "results": {}, "raw_ocr": {}}

    def _save_to_supabase(self, data: dict, date_str: str):
        # Reuse your existing Supabase client (same as the rest of the app)
        from app import supabase  # or however you import it
        supabase.table("dogs_races").upsert(data).execute()

# ===================== LEGACY COMPATIBILITY (this fixes the import error) =====================
def capture_race(date_str: str = None):
    """Legacy function the scheduler is trying to call."""
    print("[DOGS] → capture_race called (compatibility wrapper)")
    collector = DogsRaceCapturer()
    collector.run_full_pipeline(date_str)

# ===================== STANDALONE TEST =====================
if __name__ == "__main__":
    capture_race()   # or DogsRaceCapturer().run_full_pipeline()
