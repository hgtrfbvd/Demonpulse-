from playwright.sync_api import sync_playwright, TimeoutError
import time
from datetime import datetime
import os
from pathlib import Path
import pytesseract
import easyocr
from PIL import Image
import json
from supabase import create_client  # your existing supabase client

# ===================== CONFIG =====================
BASE_URL = "https://www.thedogs.com.au"
DATA_ROOT = Path("data")
HEADLESS = True
STEALTH = True

def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)

class DogsVisualCollector:
    def __init__(self):
        self.reader = easyocr.Reader(['en'], gpu=False)  # fallback OCR

    def run_full_pipeline(self, date_str: str = None):
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        day_dir = DATA_ROOT / date_str
        ensure_dir(day_dir)

        with sync_playwright() as p:
            # === 1. LAUNCH WITH STEALTH ===
            browser = p.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
            )
            if STEALTH:
                from playwright_stealth import stealth_sync
                stealth_sync(context)
            page = context.new_page()

            try:
                # === 2. OPEN MAIN RACE GUIDE (BOARD) ===
                print("[DOGS] → Opening main race guide board")
                page.goto(f"{BASE_URL}/racing/racecards", wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(3000)  # extra render time

                # === 3. CAPTURE BOARD ===
                board_path = day_dir / "board.png"
                page.screenshot(path=str(board_path), full_page=True)
                print(f"✅ board.png saved → {board_path}")

                # === 4. DETECT & NAVIGATE TO NEXT UPCOMING RACE VIA UI ===
                print("[DOGS] → Detecting next upcoming race and clicking...")
                # Look for any "View" or race row that has time in the future
                race_row = page.locator("text=View").first  # adjust selector if needed after testing
                race_row.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(4000)

                # === 5. CAPTURE RACE HEADER ===
                header_path = day_dir / "header.png"
                page.screenshot(path=str(header_path), full_page=True)
                print(f"✅ header.png saved")

                # === 6. ENSURE & CAPTURE EXPERT FORM ===
                print("[DOGS] → Waiting for Expert Form tab...")
                page.get_by_text("Expert Form", exact=True).click(timeout=10000)
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(3000)

                expert_path = day_dir / "expert_form.png"
                page.screenshot(path=str(expert_path), full_page=True)
                print(f"✅ expert_form.png saved")

                # === 7. CAPTURE BOX HISTORY TABLES ===
                page.get_by_text("Box History", exact=True).click(timeout=10000)  # or whatever the tab text is
                page.wait_for_load_state("networkidle", timeout=15000)
                box_path = day_dir / "box_history.png"
                page.screenshot(path=str(box_path), full_page=True)
                print(f"✅ box_history.png saved")

                # === 8. CAPTURE RESULTS (post-race) ===
                # (run this part again later or in a separate post-race job)
                page.get_by_text("Results", exact=True).click(timeout=10000)
                page.wait_for_load_state("networkidle", timeout=15000)
                results_path = day_dir / "results.png"
                page.screenshot(path=str(results_path), full_page=True)
                print(f"✅ results.png saved")

                # === 9. LOCAL OCR → STRUCTURED JSON ===
                print("[DOGS] → Running local OCR + parsing...")
                structured = self._extract_with_ocr(day_dir)
                
                json_path = day_dir / "race.json"
                with open(json_path, "w") as f:
                    json.dump(structured, f, indent=2)
                print(f"✅ race.json saved → {json_path}")

                # === 10. SAVE TO SUPABASE ===
                self._save_to_supabase(structured, date_str)

            except Exception as e:
                print(f"❌ Error: {e}")
                # Debug dump
                debug_dir = day_dir / "debug"
                ensure_dir(debug_dir)
                page.screenshot(path=str(debug_dir / "failure.png"), full_page=True)
                with open(debug_dir / "failure.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
                raise
            finally:
                browser.close()

    def _extract_with_ocr(self, day_dir: Path) -> dict:
        # TODO: Implement your deterministic Tesseract + EasyOCR parsing here
        # (extract race metadata, runners, form lines, box history, times, etc.)
        # Return clean structured JSON as per spec
        return {
            "race_date": str(day_dir.name),
            "metadata": {...},
            "runners": [...],
            "form_lines": [...],
            # ... full structure you need
            "source": "visual_playwright_ocr"
        }

    def _save_to_supabase(self, data: dict, date_str: str):
        # your existing Supabase client
        supabase = create_client(...)  # reuse your db.py / supabase_config
        supabase.table("dogs_races").upsert(data).execute()
        print(f"✅ Saved to Supabase for {date_str}")

# ===================== USAGE =====================
if __name__ == "__main__":
    collector = DogsVisualCollector()
    collector.run_full_pipeline()   # or pass specific date
