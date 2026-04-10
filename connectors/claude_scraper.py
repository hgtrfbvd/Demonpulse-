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


class ClaudeScraper:
    def __init__(self):
        self.client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = "claude-haiku-4-5-20251001"

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

    def _get_expert_form_urls(self, venue_slug: str, date_slug: str, max_races: int = 12) -> list[str]:
        """Build expert-form URLs for each race number at the venue."""
        return [
            f"https://www.thedogs.com.au/racing/{venue_slug}/{date_slug}/{i}/expert-form"
            for i in range(1, max_races + 1)
        ]

    def _extract(self, url_or_content: str, system: str, schema: str) -> list[dict]:
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
            # Strip markdown code fences if present
            text = text.strip()
            if text.startswith("```"):
                text = text.lstrip("`").lstrip("json").strip()
            if text.endswith("```"):
                text = text.rstrip("`").strip()
            result = json.loads(text)
            if isinstance(result, list):
                return result
            if isinstance(result, dict) and "races" in result:
                return result["races"]
            log.warning(f"ClaudeScraper: unexpected response shape: {type(result)}")
            return []
        except json.JSONDecodeError as e:
            log.error(f"ClaudeScraper: JSON decode failed: {e}")
            return []
        except Exception as e:
            log.error(f"ClaudeScraper extract failed: {e}")
            return []
