from flask import Blueprint, jsonify, request
from db import get_db, safe_query, T
from datetime import date

bet_bp = Blueprint("bets", __name__, url_prefix="/api/bets")


@bet_bp.route("/summary", methods=["GET"])
def bet_summary():
    rows = safe_query(lambda: get_db().table(T("bet_log"))
        .select("pl,result,stake").execute().data, []) or []
    total = len(rows)
    wins = sum(1 for r in rows if r.get("result") == "WIN")
    total_pl = round(sum(float(r.get("pl") or 0) for r in rows), 2)
    total_staked = round(sum(float(r.get("stake") or 0) for r in rows), 2)
    roi = f"{round((total_pl / total_staked * 100), 1)}%" if total_staked else "0%"
    win_rate = f"{round((wins / total * 100), 1)}%" if total else "0%"
    return jsonify({"ok": True, "total_bets": total, "wins": wins,
                    "pl": total_pl, "roi": roi, "win_rate": win_rate})


@bet_bp.route("/history", methods=["GET"])
def bet_history():
    rows = safe_query(lambda: get_db().table(T("bet_log"))
        .select("*").order("created_at", desc=True).limit(200).execute().data, []) or []
    return jsonify({"ok": True, "bets": rows})


@bet_bp.route("/place", methods=["POST"])
def place_bet():
    from datetime import datetime, timezone
    data = request.get_json(silent=True) or {}
    race_uid = data.get("race_uid") or ""
    runner   = data.get("runner") or ""
    odds     = float(data.get("odds") or 0)
    stake    = float(data.get("stake") or 0)
    bet_type = data.get("bet_type") or "WIN"
    if not race_uid or not runner or odds <= 0 or stake <= 0:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400
    row = {
        "race_uid": race_uid, "runner": runner, "odds": odds,
        "stake": stake, "bet_type": bet_type, "result": "PENDING",
        "pl": 0, "date": date.today().isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = safe_query(lambda: get_db().table(T("bet_log")).insert(row).execute().data)
    return jsonify({"ok": True, "bet": result[0] if result else row})
