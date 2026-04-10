"""
modules/dashboard_ui/routes.py
================================
Flask blueprint for the Pro Greyhound Dashboard.

Three-panel layout:
  Left:   meetings list, races list, next-up priority queue
  Center: race workstation (runners, odds, expert form, engine output)
  Right:  analyst rail (tempo, sim output, confidence, bet panel)
  Bottom: screenshots, raw extracted data, learning history, results, logs

All data read exclusively from the race packet via /api/packet/<race_uid>.
No direct DB queries in templates.

Endpoints:
  GET  /dogs/pro                          — 3-panel dashboard UI
  GET  /api/packet/<race_uid>             — full race packet JSON
  GET  /api/packet/list                   — list of today's packets
  POST /api/packet/<race_uid>/run         — trigger pipeline for packet
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, render_template, request

log = logging.getLogger(__name__)

_AEST = ZoneInfo("Australia/Sydney")

dashboard_ui_bp = Blueprint(
    "dashboard_ui",
    __name__,
    url_prefix="",
    template_folder="templates",
)


@dashboard_ui_bp.route("/dogs/pro")
def pro_dashboard():
    """Render the 3-panel pro dashboard."""
    try:
        return render_template("dogs_pro_dashboard.html")
    except Exception as e:
        log.error(f"[dashboard_ui] pro_dashboard render failed: {e}")
        return "<h1>Dashboard Error</h1><p>An internal error occurred.</p>", 500


@dashboard_ui_bp.route("/api/packet/<race_uid>")
def get_packet(race_uid: str):
    """
    Return the full race packet for a given race_uid.
    Data is sourced from Supabase dogs_race_packets table.
    Falls back to assembling from today_races + runners if not found.
    """
    try:
        packet = _load_packet(race_uid)
        if not packet:
            return jsonify({"ok": False, "error": "Packet not found"}), 404
        return jsonify({"ok": True, "packet": packet})
    except Exception as e:
        log.error(f"[dashboard_ui] get_packet failed for {race_uid}: {e}")
        return jsonify({"ok": False, "error": "Internal server error"}), 500


@dashboard_ui_bp.route("/api/packet/list")
def list_packets():
    """List all race packets for today (or given date)."""
    try:
        target_date = request.args.get("date") or datetime.now(_AEST).date().isoformat()
        packets = _list_packets_for_date(target_date)
        return jsonify({
            "ok": True,
            "date": target_date,
            "count": len(packets),
            "packets": packets,
        })
    except Exception as e:
        log.error(f"[dashboard_ui] list_packets failed: {e}")
        return jsonify({"ok": False, "error": "Internal server error"}), 500


@dashboard_ui_bp.route("/api/packet/<race_uid>/run", methods=["POST"])
def run_pipeline(race_uid: str):
    """Trigger the full pipeline for a race packet."""
    try:
        from packet_builder import build_packet_for_race
        packet = build_packet_for_race(race_uid)
        if not packet:
            return jsonify({"ok": False, "error": "Could not build packet"}), 500
        return jsonify({
            "ok": True,
            "race_uid": race_uid,
            "status": packet.get("status", "CAPTURED"),
        })
    except Exception as e:
        log.error(f"[dashboard_ui] run_pipeline failed for {race_uid}: {e}")
        return jsonify({"ok": False, "error": "Internal server error"}), 500


def _load_packet(race_uid: str) -> dict | None:
    """Load packet from Supabase or assemble from existing data."""
    try:
        from db import get_db
        client = get_db()
        res = client.table("dogs_race_packets").select("*").eq("race_uid", race_uid).single().execute()
        if res.data:
            return res.data
    except Exception:
        pass

    try:
        from database import get_race, get_runners_for_race
        race = get_race(race_uid)
        if not race:
            return None
        runners = get_runners_for_race(race_uid)
        return {
            "race_uid": race_uid,
            "status": "CAPTURED",
            "race": race,
            "runners": runners,
            "screenshots": {},
            "extracted_data": {},
            "engine_output": {},
            "simulation_output": {},
            "result": {},
            "learning": {},
        }
    except Exception as e:
        log.warning(f"[dashboard_ui] Fallback assembly failed: {e}")
        return None


def _list_packets_for_date(date_str: str) -> list[dict]:
    """List packets for a given date."""
    packets = []

    try:
        from db import get_db
        client = get_db()
        res = (
            client.table("dogs_race_packets")
            .select("race_uid,status,race_time,track_name,race_number")
            .eq("date", date_str)
            .order("race_time")
            .execute()
        )
        if res.data:
            return res.data
    except Exception:
        pass

    try:
        from database import get_races_for_date
        races = get_races_for_date(date_str)
        for r in races:
            if (r.get("code") or "").upper() == "GREYHOUND":
                packets.append({
                    "race_uid": r.get("race_uid"),
                    "status": "CAPTURED",
                    "race_time": r.get("jump_time"),
                    "track_name": r.get("track"),
                    "race_number": r.get("race_num"),
                })
    except Exception as e:
        log.warning(f"[dashboard_ui] Fallback list failed: {e}")

    return packets
