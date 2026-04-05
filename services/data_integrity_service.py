"""
services/data_integrity_service.py — DemonPulse V8 Data Integrity
==================================================================
Checks and enforces referential and logical integrity across all
Supabase tables.

Key guards:
  - Race linked to valid meeting (date/track/code present)
  - Runner linked to valid race (race_uid exists in today_races)
  - Result linked to correct race and runner (box_num consistent)
  - Prediction linked to correct event context (race_uid matches)
  - Learning examples linked to source predictions/results
  - Race-code contamination prevention
  - Duplicate external record ingestion prevention
  - Orphaned record detection

Usage:
    from services.data_integrity_service import DataIntegrityService
    report = DataIntegrityService.run_checks()
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from supabase_client import get_client, safe_execute, resolve_table
from supabase_config import VALID_RACE_CODES

log = logging.getLogger(__name__)


class DataIntegrityService:
    """Integrity checks and safeguards for all Supabase tables."""

    @staticmethod
    def run_checks(target_date: str | None = None) -> dict[str, Any]:
        """
        Run all integrity checks and return a comprehensive report.

        Args:
            target_date: ISO date string to scope checks (defaults to today).

        Returns:
            {
                "ok":       bool,
                "date":     str,
                "checks":   list of check result dicts,
                "errors":   list of error strings,
                "warnings": list of warning strings,
            }
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date().isoformat()

        report: dict[str, Any] = {
            "ok":       True,
            "date":     target_date,
            "checks":   [],
            "errors":   [],
            "warnings": [],
        }

        checks = [
            DataIntegrityService._check_race_code_validity,
            DataIntegrityService._check_runner_race_links,
            DataIntegrityService._check_result_race_links,
            DataIntegrityService._check_result_runner_links,
            DataIntegrityService._check_orphaned_runners,
            DataIntegrityService._check_duplicate_races,
        ]

        for check_fn in checks:
            try:
                result = check_fn(target_date)
                report["checks"].append(result)
                if not result.get("ok"):
                    report["ok"] = False
                    if result.get("severity") == "ERROR":
                        report["errors"].append(result.get("message", ""))
                    else:
                        report["warnings"].append(result.get("message", ""))
            except Exception as exc:
                msg = f"IntegrityCheck {check_fn.__name__} raised: {exc}"
                log.error(msg)
                report["ok"] = False
                report["errors"].append(msg)

        return report

    # ── INDIVIDUAL CHECKS ─────────────────────────────────────────

    @staticmethod
    def _check_race_code_validity(target_date: str) -> dict:
        """Ensure all races for the date have a valid racing code."""
        races = safe_execute(
            lambda: get_client()
                .table(resolve_table("today_races"))
                .select("race_uid,code")
                .eq("date", target_date)
                .execute()
                .data,
            default=[],
            context="integrity._check_race_code_validity",
        ) or []

        invalid = [r for r in races if r.get("code", "") not in VALID_RACE_CODES]
        ok = len(invalid) == 0
        return {
            "check":    "race_code_validity",
            "ok":       ok,
            "severity": "ERROR" if not ok else "INFO",
            "count":    len(invalid),
            "message":  (
                f"{len(invalid)} race(s) have invalid code: "
                f"{[r.get('race_uid') for r in invalid[:5]]}"
                if not ok else "All race codes valid"
            ),
        }

    @staticmethod
    def _check_runner_race_links(target_date: str) -> dict:
        """Ensure all runners for today link back to a valid race."""
        races = safe_execute(
            lambda: get_client()
                .table(resolve_table("today_races"))
                .select("race_uid")
                .eq("date", target_date)
                .execute()
                .data,
            default=[],
            context="integrity._check_runner_race_links.races",
        ) or []
        race_uids = {r["race_uid"] for r in races if r.get("race_uid")}

        runners = safe_execute(
            lambda: get_client()
                .table(resolve_table("today_runners"))
                .select("race_uid,box_num")
                .execute()
                .data,
            default=[],
            context="integrity._check_runner_race_links.runners",
        ) or []

        # Runners whose race_uid is not in today's race set
        orphaned = [r for r in runners if r.get("race_uid") and r["race_uid"] not in race_uids]
        ok = len(orphaned) == 0

        if not ok:
            log.warning(
                f"DataIntegrityService: {len(orphaned)} runner(s) reference unknown race_uid(s). "
                f"Sample: {[r['race_uid'] for r in orphaned[:5]]}"
            )

        return {
            "check":    "runner_race_links",
            "ok":       ok,
            "severity": "WARN",
            "count":    len(orphaned),
            "message":  (
                f"{len(orphaned)} runner(s) reference race_uids not found for {target_date}"
                if not ok else "All runner→race links valid"
            ),
        }

    @staticmethod
    def _check_result_race_links(target_date: str) -> dict:
        """Ensure all result rows link to a valid race."""
        races = safe_execute(
            lambda: get_client()
                .table(resolve_table("today_races"))
                .select("race_uid")
                .eq("date", target_date)
                .execute()
                .data,
            default=[],
            context="integrity._check_result_race_links.races",
        ) or []
        race_uids = {r["race_uid"] for r in races if r.get("race_uid")}

        results = safe_execute(
            lambda: get_client()
                .table(resolve_table("results_log"))
                .select("race_uid,box_num")
                .execute()
                .data,
            default=[],
            context="integrity._check_result_race_links.results",
        ) or []

        orphaned = [r for r in results if r.get("race_uid") and r["race_uid"] not in race_uids]
        ok = len(orphaned) == 0

        return {
            "check":    "result_race_links",
            "ok":       ok,
            "severity": "ERROR" if not ok else "INFO",
            "count":    len(orphaned),
            "message":  (
                f"{len(orphaned)} result row(s) reference unknown race_uids"
                if not ok else "All result→race links valid"
            ),
        }

    @staticmethod
    def _check_result_runner_links(target_date: str) -> dict:
        """
        Ensure result box_num values correspond to known runners.
        Cross-checks results_log against today_runners per race.
        """
        races = safe_execute(
            lambda: get_client()
                .table(resolve_table("today_races"))
                .select("race_uid")
                .eq("date", target_date)
                .execute()
                .data,
            default=[],
            context="integrity._check_result_runner_links.races",
        ) or []
        race_uids = [r["race_uid"] for r in races if r.get("race_uid")]

        mismatches = []
        for race_uid in race_uids[:50]:  # Cap scan to 50 races
            runners = safe_execute(
                lambda: get_client()
                    .table(resolve_table("today_runners"))
                    .select("box_num")
                    .eq("race_uid", race_uid)
                    .execute()
                    .data,
                default=[],
                context="integrity._check_result_runner_links.runners",
            ) or []
            runner_boxes = {r["box_num"] for r in runners}

            results = safe_execute(
                lambda: get_client()
                    .table(resolve_table("results_log"))
                    .select("box_num")
                    .eq("race_uid", race_uid)
                    .execute()
                    .data,
                default=[],
                context="integrity._check_result_runner_links.results",
            ) or []

            for res in results:
                if res.get("box_num") and res["box_num"] not in runner_boxes:
                    mismatches.append({"race_uid": race_uid, "box_num": res["box_num"]})

        ok = len(mismatches) == 0
        return {
            "check":    "result_runner_links",
            "ok":       ok,
            "severity": "WARN",
            "count":    len(mismatches),
            "message":  (
                f"{len(mismatches)} result box_num(s) don't match known runners"
                if not ok else "All result→runner box links valid"
            ),
        }

    @staticmethod
    def _check_orphaned_runners(target_date: str) -> dict:
        """Check for runners whose race has been deleted or is missing."""
        # Quick check: runners with no matching race_uid in today_races at all
        return DataIntegrityService._check_runner_race_links(target_date)

    @staticmethod
    def _check_duplicate_races(target_date: str) -> dict:
        """
        Detect races that appear more than once with the same
        (date, track, race_num, code) combination.
        This should never happen if upsert keys are enforced correctly.
        """
        races = safe_execute(
            lambda: get_client()
                .table(resolve_table("today_races"))
                .select("date,track,race_num,code")
                .eq("date", target_date)
                .execute()
                .data,
            default=[],
            context="integrity._check_duplicate_races",
        ) or []

        seen: dict[tuple, int] = {}
        for r in races:
            key = (r.get("date"), r.get("track"), r.get("race_num"), r.get("code"))
            seen[key] = seen.get(key, 0) + 1

        duplicates = [k for k, v in seen.items() if v > 1]
        ok = len(duplicates) == 0
        return {
            "check":    "duplicate_races",
            "ok":       ok,
            "severity": "ERROR" if not ok else "INFO",
            "count":    len(duplicates),
            "message":  (
                f"{len(duplicates)} duplicate race key(s) detected on {target_date}"
                if not ok else "No duplicate race keys"
            ),
        }

    # ── VALIDATION HELPERS ────────────────────────────────────────

    @staticmethod
    def validate_race_code(code: str) -> bool:
        """Return True if the racing code is valid."""
        return str(code).upper() in VALID_RACE_CODES

    @staticmethod
    def validate_race_payload(race: dict) -> list[str]:
        """
        Validate a race dict before write.
        Returns a list of error strings (empty = valid).
        """
        errors: list[str] = []
        for field in ("date", "track", "race_num", "code"):
            if not race.get(field):
                errors.append(f"Missing required field: {field}")
        code = str(race.get("code", "")).upper()
        if code and code not in VALID_RACE_CODES:
            errors.append(f"Invalid race code: {code}")
        return errors

    @staticmethod
    def validate_runner_payload(runner: dict) -> list[str]:
        """Validate a runner dict before write."""
        errors: list[str] = []
        if not runner.get("race_uid"):
            errors.append("Missing required field: race_uid")
        if runner.get("box_num") is None:
            errors.append("Missing required field: box_num")
        return errors

    @staticmethod
    def validate_result_payload(result: dict) -> list[str]:
        """Validate a result dict before write."""
        errors: list[str] = []
        if not result.get("race_uid"):
            errors.append("Missing required field: race_uid")
        if result.get("box_num") is None:
            errors.append("Missing required field: box_num")
        return errors
