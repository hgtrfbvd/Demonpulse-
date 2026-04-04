"""
connectors/oddspro_connector.py - OddsPro API connector (PRIMARY data source).
"""

import os
import logging
import requests
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_BASE_URL_DEFAULT = "https://api.oddspro.com.au"


class OddsProConnector:
    def __init__(self):
        self.base_url = os.environ.get("ODDSPRO_BASE_URL", _BASE_URL_DEFAULT).rstrip("/")
        self.api_key = os.environ.get("ODDSPRO_API_KEY", "")
        self.timeout = int(os.environ.get("ODDSPRO_TIMEOUT", "30"))
        self._session = requests.Session()
        if self.api_key:
            self._session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "DemonPulse/1.0",
            })

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def healthcheck(self) -> dict:
        if not self.is_enabled():
            return {"ok": False, "reason": "ODDSPRO_API_KEY not set", "enabled": False}
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            resp = self._get("/api/external/meetings", params={"date": today})
            return {"ok": True, "enabled": True, "status_code": resp.status_code}
        except Exception as e:
            log.warning(f"OddsPro healthcheck failed: {e}")
            return {"ok": False, "enabled": True}

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        url = f"{self.base_url}{path}"
        log.debug(f"OddsPro GET {url} params={list(params.keys()) if params else None}")
        resp = self._session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def fetch_today_meetings(self, date_str: str) -> list[dict]:
        if not self.is_enabled():
            log.warning("OddsPro not enabled — skipping fetch_today_meetings")
            return []
        try:
            resp = self._get("/api/external/meetings", params={"date": date_str})
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("meetings", data.get("data", []))
        except Exception as e:
            log.error(f"fetch_today_meetings failed: {e}")
            raise

    def fetch_meeting_detail(self, meeting_id: str) -> dict | None:
        if not self.is_enabled():
            return None
        try:
            resp = self._get(f"/api/external/meeting/{meeting_id}")
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                log.warning(f"Meeting {meeting_id} not found (404)")
                return None
            raise
        except Exception as e:
            log.error(f"fetch_meeting_detail({meeting_id}) failed: {e}")
            raise

    def fetch_race_detail(self, race_id: str) -> dict | None:
        if not self.is_enabled():
            return None
        try:
            resp = self._get(f"/api/external/race/{race_id}")
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                log.warning(f"Race {race_id} not found (404)")
                return None
            raise
        except Exception as e:
            log.error(f"fetch_race_detail({race_id}) failed: {e}")
            raise

    def fetch_results(self, date_str: str) -> list[dict]:
        if not self.is_enabled():
            return []
        try:
            resp = self._get("/api/external/results", params={"date": date_str})
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("results", data.get("data", []))
        except Exception as e:
            log.error(f"fetch_results failed: {e}")
            raise

    def fetch_race_result(self, race_id: str) -> dict | None:
        """Returns None on 404 (result not ready yet). Raises on other errors."""
        if not self.is_enabled():
            return None
        try:
            resp = self._get(f"/api/races/{race_id}/results")
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                log.debug(f"Race result {race_id} not ready yet (404)")
                return None
            raise
        except Exception as e:
            log.error(f"fetch_race_result({race_id}) failed: {e}")
            raise
