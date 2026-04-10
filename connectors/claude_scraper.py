"""
claude_scraper.py
=================
Fetches racing data from thedogs.com.au (greyhounds) and
publishingservices.racingaustralia.horse (horses) using the Claude API.

One call per venue returns all races as a JSON array.
Uses claude-haiku-4-5 for cost efficiency (~$0.80/MTok in, $4/MTok out).
"""
from __future__ import annotations

import os
import json
import logging
from anthropic import Anthropic

log = logging.getLogger(__name__)

GREYHOUND_SYSTEM = """You fetch Australian greyhound racing pages and extract structured data.
Return ONLY a valid JSON array of race objects. No markdown, no commentary, no code blocks.
Each race must match the exact schema provided. Use null for missing fields."""

HORSE_SYSTEM = """You fetch Australian thoroughbred racing pages and extract structured data.
Return ONLY a valid JSON array of race objects. No markdown, no commentary, no code blocks.
Each race must match the exact schema provided. Use null for missing fields."""

GREYHOUND_SCHEMA = """{
  "track_name": "Bet Nation Townsville",
  "state": "QLD",
  "date": "2026-04-10",
  "race_number": 5,
  "race_time": "14:02",
  "distance_m": 380,
  "grade": "6th Grade",
  "race_type": "Graded",
  "track_condition": "Good",
  "weather": null,
  "prize_money": "$2400",
  "first_bend_distance": 95,
  "runners": [
    {
      "box": 1,
      "name": "Hara's Rex",
      "trainer": "Hayley Wooler",
      "weight": null,
      "scratched": false,
      "last4": "17",
      "last_start_position": 1,
      "last_start_time": "22.10",
      "best_time_distance_match": "22.56",
      "split_time": "7.23",
      "avg_time_last_3": null,
      "career_starts": 3,
      "career_wins": 1,
      "career_places": 2,
      "win_pct": 33.3,
      "place_pct": 66.7,
      "prize_money_career": "$1800"
    }
  ]
}"""

HORSE_SCHEMA = """{
  "track_name": "Caulfield",
  "state": "VIC",
  "date": "2026-04-11",
  "race_number": 4,
  "race_time": "13:45",
  "distance_m": 1400,
  "race_class": "C3",
  "race_type": "SW",
  "track_condition": "Good 4",
  "rail_position": "+3m entire",
  "weather": null,
  "prize_money": "$300000",
  "speed_map": {
    "lead": ["Horse A", "Horse B"],
    "on_speed": ["Horse C"],
    "midfield": ["Horse D", "Horse E"],
    "backmarker": ["Horse F"]
  },
  "runners": [
    {
      "barrier": 4,
      "name": "Manifest The Milli",
      "trainer": "Danny O'Brien",
      "jockey": "Mark Zahra",
      "weight": 58.5,
      "scratched": false,
      "form_last5": "21384",
      "last_start_position": 2,
      "last_start_margin": 0.8,
      "last_start_distance": 1400,
      "days_since_last_run": 14,
      "first_up": false,
      "second_up": true,
      "run_style": "midfield",
      "track_record": "3:1-1-0",
      "distance_record": "8:2-1-1",
      "track_distance_record": "2:1-0-0",
      "wet_record": "4:1-1-0",
      "career_starts": 18,
      "career_wins": 4,
      "career_places": 7,
      "prize_money_career": "$285000",
      "techform_rating": 92,
      "class_level": "BM78"
    }
  ]
}"""

GREYHOUND_BATCH_SIZE = 4   # venues per call (~42k tokens each → 168k under 200k limit)
HORSE_BATCH_SIZE = 2       # venues per call (~72k tokens each → 144k under 200k limit)


class ClaudeScraper:
    def __init__(self):
        self.client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = "claude-haiku-4-5-20251001"

    # ------------------------------------------------------------------
    # Single-venue helpers
    # ------------------------------------------------------------------

    def fetch_greyhound_venue(self, venue_slug: str, date_slug: str) -> list[dict]:
        """Fetch all races for a greyhound venue. Returns list of race dicts."""
        fields_url = f"https://www.thedogs.com.au/racing/{venue_slug}/{date_slug}?trial=false"
        expert_urls = self._get_expert_form_urls(venue_slug, date_slug)
        combined = (
            f"FIELDS PAGE:\n{fields_url}\n\nEXPERT FORMS:\n" + "\n".join(expert_urls)
        )
        return self._extract(combined, GREYHOUND_SYSTEM, GREYHOUND_SCHEMA)

    def fetch_horse_venue(self, venue_name: str) -> list[dict]:
        """Fetch all races for a horse venue from the racebook."""
        url = f"https://publishingservices.racingaustralia.horse/racebooks/{venue_name}/"
        return self._extract(url, HORSE_SYSTEM, HORSE_SCHEMA)

    # ------------------------------------------------------------------
    # Batch helpers — multiple venues per call to fill the 200k context
    # ------------------------------------------------------------------

    def fetch_greyhound_batch(self, venues: list[dict], date_slug: str) -> dict[str, list[dict]]:
        """
        Fetch multiple greyhound venues in one call.
        Returns {slug: [races]} dict.
        """
        urls = [
            f"https://www.thedogs.com.au/racing/{v['slug']}/{date_slug}?trial=false"
            for v in venues
        ]
        prompt = (
            f"Fetch each of these {len(urls)} venue pages plus their expert form pages "
            f"and return a JSON object with venue slugs as keys, each containing an array "
            f"of race objects matching the schema.\n\n"
            f"Venues:\n" + "\n".join(f"- {v['slug']}: {url}" for v, url in zip(venues, urls))
        )
        result = self._extract_raw(prompt, GREYHOUND_SYSTEM, GREYHOUND_SCHEMA)
        if isinstance(result, dict):
            return result
        # Fallback: if a list was returned, key it to the first slug
        if isinstance(result, list) and venues:
            return {venues[0]["slug"]: result}
        return {}

    def fetch_horse_batch(self, venues: list[dict]) -> dict[str, list[dict]]:
        """
        Fetch multiple horse venues in one call.
        Returns {venue_name: [races]} dict.
        """
        urls = [
            f"https://publishingservices.racingaustralia.horse/racebooks/{v['name']}/"
            for v in venues
        ]
        prompt = (
            f"Fetch each of these {len(urls)} racebook pages and return a JSON object "
            f"with venue names as keys, each containing an array of race objects.\n\n"
            + "\n".join(f"- {v['name']}: {url}" for v, url in zip(venues, urls))
        )
        result = self._extract_raw(prompt, HORSE_SYSTEM, HORSE_SCHEMA)
        if isinstance(result, dict):
            return result
        if isinstance(result, list) and venues:
            return {venues[0]["name"]: result}
        return {}

    # ------------------------------------------------------------------
    # Single-race targeted refresh
    # ------------------------------------------------------------------

    def fetch_single_race(
        self,
        code: str,
        venue_slug: str,
        date_slug: str,
        race_num: int,
    ) -> dict | None:
        """
        Targeted refresh for one race. Used when a race is actively viewed.
        Returns a single race dict or None on failure.
        """
        if code == "GREYHOUND":
            url = (
                f"https://www.thedogs.com.au/racing/"
                f"{venue_slug}/{date_slug}/{race_num}/expert-form"
            )
            system = GREYHOUND_SYSTEM
            schema = GREYHOUND_SCHEMA
        else:
            url = (
                f"https://publishingservices.racingaustralia.horse"
                f"/racebooks/{venue_slug}/"
            )
            system = HORSE_SYSTEM
            schema = f"Extract only race number {race_num}. " + HORSE_SCHEMA

        races = self._extract(url, system, schema)
        return races[0] if races else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_expert_form_urls(self, venue_slug: str, date_slug: str, max_races: int = 12) -> list[str]:
        """Build expert-form URLs for each race number at the venue."""
        return [
            f"https://www.thedogs.com.au/racing/{venue_slug}/{date_slug}/{i}/expert-form"
            for i in range(1, max_races + 1)
        ]

    def _strip_fences(self, text: str) -> str:
        """Strip markdown code fences from a Claude response."""
        text = text.strip()
        if text.startswith("```"):
            text = text.lstrip("`").lstrip("json").strip()
        if text.endswith("```"):
            text = text.rstrip("`").strip()
        return text

    # ------------------------------------------------------------------
    # Venue discovery
    # ------------------------------------------------------------------

    def discover_greyhound_venues(self, date_slug: str) -> list[dict]:
        """
        Discover today's greyhound venues from thedogs.com.au.
        Returns list of {"slug": ..., "state": ...} dicts.
        """
        url = f"https://www.thedogs.com.au/racing/{date_slug}?trial=false"
        system = (
            "You fetch Australian greyhound racing schedule pages. "
            "Return ONLY a valid JSON array of venue objects. No markdown, no commentary."
        )
        schema = '[{"slug": "townsville", "state": "QLD"}]'
        result = self._extract_venues(url, system, schema)
        return result if isinstance(result, list) else []

    def discover_horse_venues(self) -> list[dict]:
        """
        Discover today's horse venues from racingaustralia.horse.
        Returns list of {"name": ..., "state": ...} dicts.
        """
        url = "https://publishingservices.racingaustralia.horse/racebooks/"
        system = (
            "You fetch Australian thoroughbred racing schedule pages. "
            "Return ONLY a valid JSON array of venue objects. No markdown, no commentary."
        )
        schema = '[{"name": "Caulfield", "state": "VIC"}]'
        result = self._extract_venues(url, system, schema)
        return result if isinstance(result, list) else []

    def _extract_venues(self, url: str, system: str, schema: str) -> list[dict]:
        """Fetch a schedule page and extract venue objects (not race data)."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                system=system,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Fetch this page and return a JSON array of venue objects "
                        f"matching this schema:\n{schema}\n\nPage: {url}"
                    ),
                }],
            )
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            text = self._strip_fences(text)
            result = json.loads(text)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError as e:
            log.error(f"ClaudeScraper._extract_venues: JSON decode failed: {e}")
            return []
        except Exception as e:
            log.error(f"ClaudeScraper._extract_venues failed: {e}")
            return []

    def _extract(self, url_or_content: str, system: str, schema: str) -> list[dict]:
        """Fetch and extract, expecting a JSON array response."""
        result = self._extract_raw(url_or_content, system, schema)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "races" in result:
            return result["races"]
        log.warning(f"ClaudeScraper._extract: unexpected shape {type(result)}")
        return []

    def _extract_raw(self, url_or_content: str, system: str, schema: str) -> list | dict:
        """
        Core Claude API call. Returns the parsed JSON (list or dict).
        Falls back to empty list on error.
        """
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                system=system,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Fetch this page and return ALL race and runner data as a JSON array "
                        f"matching this schema:\n{schema}\n\nPage: {url_or_content}"
                    ),
                }],
            )
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            text = self._strip_fences(text)
            return json.loads(text)
        except json.JSONDecodeError as e:
            log.error(f"ClaudeScraper: JSON decode failed: {e}")
            return []
        except Exception as e:
            log.error(f"ClaudeScraper extract failed: {e}")
            return []
