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


@bet_bp.route("/open", methods=["GET"])
def open_bets():
    rows = safe_query(lambda: get_db().table(T("bet_log"))
        .select("*").eq("result", "PENDING")
        .order("created_at", desc=True).execute().data, []) or []
    return jsonify({"ok": True, "bets": rows, "count": len(rows)})


@bet_bp.route("/settle", methods=["POST"])
def settle_bet():
    from datetime import datetime, timezone
    data = request.get_json(silent=True) or {}
    bet_id  = data.get("bet_id") or ""
    result  = (data.get("result") or "").upper()
    if not bet_id or result not in ("WIN", "LOSE", "PLACE"):
        return jsonify({"ok": False, "error": "bet_id and valid result required"}), 400
    rows = safe_query(lambda: get_db().table(T("bet_log"))
        .select("odds,stake").eq("id", bet_id).limit(1).execute().data, []) or []
    if not rows:
        return jsonify({"ok": False, "error": "Bet not found"}), 404
    bet = rows[0]
    odds  = float(bet.get("odds") or 0)
    stake = float(bet.get("stake") or 0)
    if result == "WIN":
        pl = round(stake * odds - stake, 2)
    elif result == "PLACE":
        pl = round(stake * (odds / 4) - stake, 2)
    else:
        pl = round(-stake, 2)
    safe_query(lambda: get_db().table(T("bet_log")).update({
        "result": result,
        "pl": pl,
        "settled_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", bet_id).execute())
    return jsonify({"ok": True, "bet_id": bet_id, "result": result, "pl": pl})


@bet_bp.route("/reset-bank", methods=["POST"])
def reset_bank():
    from db import update_state
    data = request.get_json(silent=True) or {}
    amount = float(data.get("amount") or 1000)
    update_state(bankroll=amount)
    return jsonify({"ok": True, "bankroll": amount})
