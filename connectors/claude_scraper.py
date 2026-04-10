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
import re
import json
import hashlib
import logging
from typing import Any
from anthropic import Anthropic

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PIPELINE STATE — updated on every Claude call; read by /api/debug/claude-pipeline
# ---------------------------------------------------------------------------
_pipeline_state: dict[str, Any] = {
    "prompt_source": "connectors/claude_scraper.py",
    "prompt_function": "_extract_raw",
    "prompt_fingerprint": None,
    "last_raw_response_preview": None,
    "last_response_appeared_json": None,
    "last_parse_success": None,
    "last_parse_error": None,
    "last_top_level_keys": None,
    "last_race_count": None,
    "last_runner_count": None,
}


def get_pipeline_state() -> dict:
    """Return a snapshot of the Claude pipeline state for diagnostics."""
    return dict(_pipeline_state)


# ---------------------------------------------------------------------------
# CANONICAL SYSTEM PROMPTS
# These are the ONLY active prompt sources. system_prompt.py (V7_SYSTEM) is
# the chat-assistant prompt for the interactive UI — it is NOT used here.
# ---------------------------------------------------------------------------

GREYHOUND_SYSTEM = """You fetch Australian greyhound racing pages and extract structured data.
Return ONLY valid JSON. No markdown. No commentary. No code fences. No explanation text before or after the JSON.
Your entire response must be a single JSON array of race objects. Nothing else.
Each race must match the exact schema provided. Use null for missing fields."""

HORSE_SYSTEM = """You fetch Australian thoroughbred racing pages and extract structured data.
Return ONLY valid JSON. No markdown. No commentary. No code fences. No explanation text before or after the JSON.
Your entire response must be a single JSON array of race objects. Nothing else.
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


# ---------------------------------------------------------------------------
# JSON PARSING HELPERS
# ---------------------------------------------------------------------------

def _parse_json_strict(text: str, context: str = "") -> list | dict | None:
    """
    Strict three-tier JSON parser:
      1. Direct json.loads(text)
      2. Fallback: extract the largest JSON object/array from text
      3. Fail: log structured error and return None
    """
    # Tier 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError as e1:
        log.warning(f"[CLAUDE PARSE] {context}: direct json.loads failed: {e1}")

    # Tier 2: extract largest JSON object or array from text
    result = _extract_json_from_text(text)
    if result is not None:
        log.info(f"[CLAUDE PARSE] {context}: fallback JSON extraction succeeded type={type(result).__name__}")
        return result

    # Tier 3: structured failure
    log.error(
        f"[CLAUDE PARSE ERROR] {context}: no valid JSON found. "
        f"text_len={len(text)} preview={text[:200]!r}"
    )
    return None


def _extract_json_from_text(text: str) -> list | dict | None:
    """
    Attempt to extract the largest valid JSON object or array from arbitrary text.
    Tries outermost [ ... ] first (most common for race arrays), then { ... }.
    """
    for start_ch, end_ch in [('[', ']'), ('{', '}')]:
        s = text.find(start_ch)
        e = text.rfind(end_ch)
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                pass
    return None


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
        # Remove opening fence: ```json or ``` (with optional language tag and trailing whitespace/newline)
        text = re.sub(r'^```[a-zA-Z0-9]*[ \t]*\n?', '', text)
        # Remove closing fence
        text = re.sub(r'\n?```$', '', text)
        return text.strip()

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
        system_hash = hashlib.md5(system.encode()).hexdigest()[:12]
        log.info(
            f"[CLAUDE PROMPT] source=connectors/claude_scraper.py "
            f"function=_extract_venues system_hash={system_hash} "
            f"system_preview={system[:300]!r}"
        )
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
            log.info("[CLAUDE RAW RESPONSE START]")
            log.info(f"[CLAUDE RAW RESPONSE] type={type(text).__name__} len={len(text)} "
                     f"preview={text[:1000]!r}")
            log.info("[CLAUDE RAW RESPONSE END]")
            appears_json = text.strip()[:1].startswith(('[', '{'))
            log.info(f"[CLAUDE PARSE] function=_extract_venues appears_json={appears_json}")
            text = self._strip_fences(text)
            result = _parse_json_strict(text, context="_extract_venues")
            return result if isinstance(result, list) else []
        except Exception as e:
            log.error(f"[CLAUDE REQUEST ERROR] _extract_venues failed: {e}", exc_info=True)
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
        Logs the raw response and parse outcome. Falls back to empty list on error.
        """
        system_hash = hashlib.md5(system.encode()).hexdigest()[:12]
        _pipeline_state["prompt_source"] = "connectors/claude_scraper.py"
        _pipeline_state["prompt_function"] = "_extract_raw"
        _pipeline_state["prompt_fingerprint"] = system_hash
        log.info(
            f"[CLAUDE PROMPT] source=connectors/claude_scraper.py "
            f"function=_extract_raw system_hash={system_hash} "
            f"system_preview={system[:300]!r}"
        )
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

            log.info("[CLAUDE RAW RESPONSE START]")
            log.info(
                f"[CLAUDE RAW RESPONSE] type={type(text).__name__} len={len(text)} "
                f"appears_json={text.strip().startswith(('{', '['))}"
            )
            log.info(f"[CLAUDE RAW RESPONSE PREVIEW] {text[:1000]!r}")
            log.info("[CLAUDE RAW RESPONSE END]")

            _pipeline_state["last_raw_response_preview"] = text[:1000]
            _pipeline_state["last_response_appeared_json"] = text.strip().startswith(('{', '['))

            text_clean = self._strip_fences(text)
            result = _parse_json_strict(text_clean, context="_extract_raw")

            if result is not None:
                top_keys = list(result.keys()) if isinstance(result, dict) else None
                race_count = len(result) if isinstance(result, list) else None
                runner_count = sum(
                    len(r.get("runners", [])) for r in result
                    if isinstance(r, dict)
                ) if isinstance(result, list) else None
                _pipeline_state.update({
                    "last_parse_success": True,
                    "last_parse_error": None,
                    "last_top_level_keys": top_keys,
                    "last_race_count": race_count,
                    "last_runner_count": runner_count,
                })
                log.info(
                    f"[CLAUDE PARSE] success=True type={type(result).__name__} "
                    f"race_count={race_count} runner_count={runner_count} "
                    f"top_keys={top_keys!r}"
                )
                return result

            # _parse_json_strict already logged details; update state
            _pipeline_state.update({
                "last_parse_success": False,
                "last_parse_error": f"no valid JSON found in response (len={len(text)})",
                "last_top_level_keys": None,
                "last_race_count": None,
                "last_runner_count": None,
            })
            return []

        except Exception as e:
            log.error(f"[CLAUDE REQUEST ERROR] _extract_raw failed: {e}", exc_info=True)
            _pipeline_state.update({
                "last_parse_success": False,
                "last_parse_error": f"request error: {e}",
            })
            return []
