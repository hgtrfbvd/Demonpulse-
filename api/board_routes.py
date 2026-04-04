"""
api/board_routes.py - Board endpoints.
"""

import logging
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
board_bp = Blueprint("board", __name__)


@board_bp.route("/api/board")
def api_board():
    """Full board with NTJ, sorted by jump_time."""
    from board_builder import build_board

    date_str = request.args.get("date")
    try:
        result = build_board(date_str)
        return jsonify(result)
    except Exception as e:
        log.exception(f"/api/board failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@board_bp.route("/api/board/ntj")
def api_ntj():
    """Next-to-jump race only."""
    from board_builder import build_board

    date_str = request.args.get("date")
    try:
        result = build_board(date_str)
        ntj = result.get("ntj")
        return jsonify({"ok": True, "ntj": ntj})
    except Exception as e:
        log.exception(f"/api/board/ntj failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
