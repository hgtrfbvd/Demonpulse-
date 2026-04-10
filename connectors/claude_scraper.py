"""
claude_scraper.py
=================
Fetches Australian thoroughbred racing data from
publishingservices.racingaustralia.horse using the Claude API.

One call per venue returns all races as a JSON array.
Uses claude-haiku-4-5 for cost efficiency (~$0.80/MTok in, $4/MTok out).

NOTE: Greyhound (DOGS) data collection has been removed from this module.
DOGS data is now collected via browser-based scraping in:
  collectors/dogs_board_collector.py
  collectors/dogs_race_capturer.py
  parsers/dogs_source_parser.py
  services/dogs_board_service.py
"""
from __future__ import annotations

import os
import re
import json
import hashlib
import logging
from datetime import datetime
from typing import Any
try:
    from anthropic import Anthropic, RateLimitError as _AnthropicRateLimitError
except ImportError:
    Anthropic = None  # type: ignore[assignment,misc]
    _AnthropicRateLimitError = None  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VENUE CACHE — last-good venue JSON persisted locally so board can continue
# building when Claude is temporarily rate-limited.
# ---------------------------------------------------------------------------
_VENUE_CACHE_FILE = os.environ.get(
    "VENUE_CACHE_FILE", "/tmp/demonpulse_venue_cache.json"
)
_venue_cache: dict[str, list] = {}  # in-memory mirror of the file


def save_venue_cache(key: str, venues: list) -> None:
    """Persist venues list to in-memory + file cache under *key*."""
    _venue_cache[key] = venues
    try:
        existing: dict = {}
        if os.path.exists(_VENUE_CACHE_FILE):
            with open(_VENUE_CACHE_FILE, "r") as fh:
                existing = json.load(fh)
        existing[key] = venues
        with open(_VENUE_CACHE_FILE, "w") as fh:
            json.dump(existing, fh)
        log.debug(f"[VENUE CACHE] saved key={key!r} count={len(venues)}")
    except Exception as exc:
        log.warning(f"[VENUE CACHE] file write failed key={key!r}: {exc}")


def load_venue_cache(key: str) -> list | None:
    """Return cached venues for *key*, or None if no cache exists."""
    if key in _venue_cache:
        return _venue_cache[key]
    try:
        if os.path.exists(_VENUE_CACHE_FILE):
            with open(_VENUE_CACHE_FILE, "r") as fh:
                data = json.load(fh)
            venues = data.get(key)
            if venues is not None:
                _venue_cache[key] = venues
                log.info(
                    f"[VENUE CACHE] loaded from file key={key!r} count={len(venues)}"
                )
                return venues
    except Exception as exc:
        log.warning(f"[VENUE CACHE] file read failed key={key!r}: {exc}")
    return None


# ---------------------------------------------------------------------------
# RATE-LIMIT HELPERS
# ---------------------------------------------------------------------------

class ClaudeRateLimitError(Exception):
    """Raised when Anthropic returns HTTP 429 Too Many Requests."""

    def __init__(self, retry_after: float = 0.0, endpoint: str = "") -> None:
        self.retry_after = retry_after
        self.endpoint = endpoint
        super().__init__(
            f"Anthropic 429 rate limit (retry_after={retry_after:.0f}s "
            f"endpoint={endpoint!r})"
        )


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True when *exc* is an Anthropic HTTP 429 error."""
    if _AnthropicRateLimitError and isinstance(exc, _AnthropicRateLimitError):
        return True
    return getattr(exc, "status_code", None) == 429


def _get_retry_after(exc: Exception) -> float:
    """Extract retry-after seconds from an Anthropic 429 exception."""
    try:
        resp = getattr(exc, "response", None)
        if resp is not None:
            headers = getattr(resp, "headers", {}) or {}
            val = headers.get("retry-after") or headers.get(
                "x-ratelimit-reset-requests"
            )
            if val:
                return float(val)
    except Exception:
        pass
    return 0.0

# ---------------------------------------------------------------------------
# PIPELINE STATE — updated on every Claude call; read by /api/debug/claude-pipeline
# and /api/debug/board-status
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
    # 429 / rate-limit tracking
    "last_429_at": None,
    "last_429_endpoint": None,
    "last_429_stage": None,
    "last_429_retry_after": None,
    # data-source for the most recent venue fetch
    "last_fetch_source": None,   # live_claude | cached_claude | failed_no_cache
    "last_venues_count": None,
}


def get_pipeline_state() -> dict:
    """Return a snapshot of the Claude pipeline state for diagnostics."""
    return dict(_pipeline_state)


# ---------------------------------------------------------------------------
# CANONICAL SYSTEM PROMPTS
# These are the ONLY active prompt sources. system_prompt.py (V7_SYSTEM) is
# the chat-assistant prompt for the interactive UI — it is NOT used here.
# ---------------------------------------------------------------------------

HORSE_SYSTEM = """You fetch Australian thoroughbred racing pages and extract structured data.
Return ONLY valid JSON. No markdown. No commentary. No code fences. No explanation text before or after the JSON.
Your entire response must be a single JSON array of race objects. Nothing else.
Each race must match the exact schema provided. Use null for missing fields."""

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
        if Anthropic is None:
            raise ImportError(
                "The 'anthropic' package is required for the horse pipeline. "
                "Install it with: pip install anthropic"
            )
        # max_retries=0: fail fast on 429 so the pipeline can fall back to
        # cached venues immediately rather than blocking for ~51 s.
        self.client = Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            max_retries=0,
        )
        self.model = "claude-haiku-4-5-20251001"

    # ------------------------------------------------------------------
    # Single-venue helpers
    # ------------------------------------------------------------------

    def fetch_horse_venue(self, venue_name: str) -> list[dict]:
        """Fetch all races for a horse venue from the racebook."""
        url = f"https://publishingservices.racingaustralia.horse/racebooks/{venue_name}/"
        return self._extract(url, HORSE_SYSTEM, HORSE_SCHEMA)

    # ------------------------------------------------------------------
    # Batch helpers — multiple venues per call to fill the 200k context
    # ------------------------------------------------------------------

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
    # Single-race targeted refresh (horses only)
    # ------------------------------------------------------------------

    def fetch_single_race(
        self,
        code: str,
        venue_slug: str,
        date_slug: str,
        race_num: int,
    ) -> dict | None:
        """
        Targeted refresh for one horse race.
        GREYHOUND races are handled by services.dogs_capture_service.
        Returns a single race dict or None on failure.
        """
        if code == "GREYHOUND":
            raise ValueError(
                "ClaudeScraper.fetch_single_race no longer handles GREYHOUND. "
                "Use services.dogs_capture_service.refresh_race() instead."
            )
        url = (
            f"https://publishingservices.racingaustralia.horse"
            f"/racebooks/{venue_slug}/"
        )
        schema = f"Extract only race number {race_num}. " + HORSE_SCHEMA
        races = self._extract(url, HORSE_SYSTEM, schema)
        return races[0] if races else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _strip_fences(self, text: str) -> str:
        """Strip markdown code fences from a Claude response."""
        text = text.strip()
        # Remove opening fence: ```json or ``` (with optional language tag and trailing whitespace/newline)
        text = re.sub(r'^```[a-zA-Z0-9]*[ \t]*\n?', '', text)
        # Remove closing fence
        text = re.sub(r'\n?```$', '', text)
        return text.strip()

    # ------------------------------------------------------------------
    # Venue discovery (horses only)
    # ------------------------------------------------------------------

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
        """Fetch a schedule page and extract venue objects (not race data).

        Raises ClaudeRateLimitError on HTTP 429 so callers can fall back to
        cached venues rather than silently returning an empty list.
        """
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
            venues = result if isinstance(result, list) else []
            _pipeline_state["last_fetch_source"] = "live_claude"
            _pipeline_state["last_venues_count"] = len(venues)
            return venues
        except Exception as exc:
            if _is_rate_limit_error(exc):
                retry_after = _get_retry_after(exc)
                now_iso = datetime.utcnow().isoformat()
                _pipeline_state.update({
                    "last_429_at": now_iso,
                    "last_429_endpoint": url,
                    "last_429_stage": "venue_fetch",
                    "last_429_retry_after": retry_after,
                    "last_fetch_source": None,
                })
                log.error(
                    f"[VENUES_FETCH_429] endpoint={url!r} provider=anthropic "
                    f"stage=venue_fetch retry_delay={retry_after:.0f}s "
                    f"at={now_iso}"
                )
                raise ClaudeRateLimitError(
                    retry_after=retry_after, endpoint=url
                ) from exc
            log.error(
                f"[CLAUDE REQUEST ERROR] _extract_venues failed: {exc}",
                exc_info=True,
            )
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
            if _is_rate_limit_error(e):
                retry_after = _get_retry_after(e)
                now_iso = datetime.utcnow().isoformat()
                _pipeline_state.update({
                    "last_429_at": now_iso,
                    "last_429_endpoint": str(url_or_content)[:200],
                    "last_429_stage": "race_fetch",
                    "last_429_retry_after": retry_after,
                    "last_parse_success": False,
                    "last_parse_error": f"429 rate limit retry_after={retry_after:.0f}s",
                })
                log.error(
                    f"[CLAUDE_RACE_FETCH_429] provider=anthropic stage=race_fetch "
                    f"retry_delay={retry_after:.0f}s at={now_iso} "
                    f"endpoint={str(url_or_content)[:200]!r}"
                )
                return []
            log.error(f"[CLAUDE REQUEST ERROR] _extract_raw failed: {e}", exc_info=True)
            _pipeline_state.update({
                "last_parse_success": False,
                "last_parse_error": f"request error: {e}",
            })
            return []
