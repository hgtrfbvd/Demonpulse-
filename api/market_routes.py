"""
api/market_routes.py - DemonPulse Market Intelligence API Routes
=================================================================
Exposes OddsPro market-intelligence endpoints as internal API routes:

  GET /api/market/top-favs             - shortest-priced favorites
  GET /api/market/leaderboard          - bookmaker performance stats
  GET /api/market/movers               - top price shortenings
  GET /api/market/movers/track/<track> - track-specific price shortenings
  GET /api/market/drifters             - top price drifters (odds increases)

All data is sourced from OddsPro (authoritative).
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

market_bp = Blueprint("market", __name__, url_prefix="/api/market")


def _get_connector():
    from connectors.oddspro_connector import OddsProConnector
    return OddsProConnector()


@market_bp.route("/top-favs", methods=["GET"])
def get_top_favs():
    """
    GET /api/market/top-favs
    Shortest-priced favorites across all bookmakers.

    Query parameters (all optional):
      type      - T, H, G, all
      location  - domestic, international, all
      date      - YYYY-MM-DD
      track     - track name filter
      limit     - number of results (default: 10)
    """
    type_ = request.args.get("type")
    location = request.args.get("location")
    date = request.args.get("date")
    track = request.args.get("track")
    limit_raw = request.args.get("limit")
    limit = int(limit_raw) if limit_raw and limit_raw.isdigit() else None

    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503
        data = conn.fetch_top_favs(
            type_=type_, location=location, date=date, track=track, limit=limit
        )
        return jsonify({"ok": True, "data": data, "count": len(data)})
    except Exception as e:
        log.error(f"/api/market/top-favs failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve top favorites"}), 500


@market_bp.route("/leaderboard", methods=["GET"])
def get_leaderboard():
    """
    GET /api/market/leaderboard
    Bookmaker performance statistics.

    Query parameters (all optional):
      type      - T, H, G, all
      location  - domestic, international, all
      date      - YYYY-MM-DD
    """
    type_ = request.args.get("type")
    location = request.args.get("location")
    date = request.args.get("date")

    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503
        data = conn.fetch_leaderboard(type_=type_, location=location, date=date)
        return jsonify({"ok": True, "data": data, "count": len(data)})
    except Exception as e:
        log.error(f"/api/market/leaderboard failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve leaderboard"}), 500


@market_bp.route("/movers", methods=["GET"])
def get_movers():
    """
    GET /api/market/movers
    Top price shortenings — runners with the largest percentage price drops.

    Query parameters (all optional):
      type      - T, H, G, all
      location  - domestic, international, all
      track     - filter by track name
      maxOdds   - maximum current odds
      limit     - number of results (default: 10)
      date      - YYYY-MM-DD (default: today)
    """
    type_ = request.args.get("type")
    location = request.args.get("location")
    track = request.args.get("track")
    date = request.args.get("date")
    limit_raw = request.args.get("limit")
    limit = int(limit_raw) if limit_raw and limit_raw.isdigit() else None
    max_odds_raw = request.args.get("maxOdds")
    try:
        max_odds = float(max_odds_raw) if max_odds_raw else None
    except (TypeError, ValueError):
        max_odds = None

    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503
        data = conn.fetch_movers(
            type_=type_, location=location, track=track,
            max_odds=max_odds, limit=limit, date=date,
        )
        return jsonify({"ok": True, "data": data, "count": len(data)})
    except Exception as e:
        log.error(f"/api/market/movers failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve movers"}), 500


@market_bp.route("/movers/track/<path:track>", methods=["GET"])
def get_movers_by_track(track: str):
    """
    GET /api/market/movers/track/<track>
    Price shortenings filtered to a specific racing track.

    Path parameter:
      track     - track name (e.g. Flemington, Randwick)

    Query parameters (all optional):
      type      - T, H, G, all
      maxOdds   - maximum current odds
      limit     - number of results (default: 10)
    """
    type_ = request.args.get("type")
    limit_raw = request.args.get("limit")
    limit = int(limit_raw) if limit_raw and limit_raw.isdigit() else None
    max_odds_raw = request.args.get("maxOdds")
    try:
        max_odds = float(max_odds_raw) if max_odds_raw else None
    except (TypeError, ValueError):
        max_odds = None

    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503
        data = conn.fetch_movers_by_track(
            track=track, type_=type_, max_odds=max_odds, limit=limit
        )
        return jsonify({"ok": True, "data": data, "count": len(data), "track": track})
    except Exception as e:
        log.error(f"/api/market/movers/track/{track} failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve track movers"}), 500


@market_bp.route("/drifters", methods=["GET"])
def get_drifters():
    """
    GET /api/market/drifters
    Runners with significant price increases (drifting odds).

    Query parameters (all optional):
      type         - T, H, G, all
      location     - domestic, international, all
      track        - filter by track name
      maxOdds      - maximum current odds
      minMovement  - minimum drift % (default: 5)
      limit        - number of results (default: 10)
    """
    type_ = request.args.get("type")
    location = request.args.get("location")
    track = request.args.get("track")
    limit_raw = request.args.get("limit")
    limit = int(limit_raw) if limit_raw and limit_raw.isdigit() else None
    max_odds_raw = request.args.get("maxOdds")
    try:
        max_odds = float(max_odds_raw) if max_odds_raw else None
    except (TypeError, ValueError):
        max_odds = None
    min_movement_raw = request.args.get("minMovement")
    try:
        min_movement = float(min_movement_raw) if min_movement_raw else None
    except (TypeError, ValueError):
        min_movement = None

    try:
        conn = _get_connector()
        if not conn.is_enabled():
            return jsonify({"ok": False, "error": "OddsPro not configured"}), 503
        data = conn.fetch_drifters(
            type_=type_, location=location, track=track,
            max_odds=max_odds, min_movement=min_movement, limit=limit,
        )
        return jsonify({"ok": True, "data": data, "count": len(data)})
    except Exception as e:
        log.error(f"/api/market/drifters failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve drifters"}), 500
