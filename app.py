import os
from datetime import datetime, date, timezone

import requests
from flask import Flask, render_template, request, jsonify
from supabase import create_client, Client

app = Flask(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "").strip()
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

try:
    from system_prompt import V7_SYSTEM
except ImportError:
    V7_SYSTEM = """You are DEMONPULSE SYNDICATE V7 — professional betting intelligence for Australian greyhound, horse, and harness racing. You are running inside the DEMONPULSE dashboard.

GLOBAL LAWS: Never fabricate race data. GRV.ORG.AU permanently banned. Positive EV required for BET. PASS is always valid. Time from user only.
BANNED SOURCES: grv.org.au, fasttrack.grv.org.au, punters.com.au, racingandsports.com.au, race.com.au, thegreyhoundrecorder.com.au
SOURCES: thedogs.com.au (greyhound), racenet.com.au (horse), harness.org.au (harness)

For race analysis commands (next race, refresh, analyse) — provide full structured V7 analysis.
For general questions — respond conversationally.
Always be direct and professional. No fluff."""


# ------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def safe_int(value, default=0) -> int:
    try:
        if value is None or value == "":
            return int(default)
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def table_select(table_name: str, columns: str = "*"):
    return supabase.table(table_name).select(columns)


def get_state() -> dict:
    try:
        res = (
            table_select("system_state")
            .eq("id", 1)
            .single()
            .execute()
        )
        return res.data or {
            "id": 1,
            "bankroll": 1000,
            "current_pl": 0,
            "bank_mode": "STANDARD",
            "active_code": "GREYHOUND",
            "posture": "NORMAL",
            "sys_state": "STABLE",
            "variance": "NORMAL",
            "session_type": "Live Betting",
            "time_anchor": "",
        }
    except Exception:
        return {
            "id": 1,
            "bankroll": 1000,
            "current_pl": 0,
            "bank_mode": "STANDARD",
            "active_code": "GREYHOUND",
            "posture": "NORMAL",
            "sys_state": "STABLE",
            "variance": "NORMAL",
            "session_type": "Live Betting",
            "time_anchor": "",
        }


def update_state(**kwargs) -> None:
    allowed = {
        "bankroll",
        "current_pl",
        "bank_mode",
        "active_code",
        "posture",
        "sys_state",
        "variance",
        "session_type",
        "time_anchor",
    }
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    if not filtered:
        return

    filtered["updated_at"] = utc_now_iso()

    try:
        supabase.table("system_state").update(filtered).eq("id", 1).execute()
    except Exception as e:
        raise RuntimeError(f"Failed to update system_state: {str(e)}")


def get_session_pl() -> dict:
    today = date.today().isoformat()

    try:
        res = (
            supabase.table("bet_log")
            .select("pl, result")
            .eq("date", today)
            .execute()
        )
        rows = res.data or []

        total = round(sum(safe_float(r.get("pl"), 0) for r in rows), 2)
        bets = len(rows)
        wins = sum(1 for r in rows if r.get("result") == "WIN")
        pending = sum(1 for r in rows if r.get("result") == "PENDING")

        return {
            "total": total,
            "bets": bets,
            "wins": wins,
            "pending": pending,
        }
    except Exception:
        return {
            "total": 0,
            "bets": 0,
            "wins": 0,
            "pending": 0,
        }


def get_chat_history(session_id: str, limit: int = 20) -> list[dict]:
    try:
        res = (
            supabase.table("chat_history")
            .select("id, role, content")
            .eq("session_id", session_id)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        rows = res.data or []
        rows.reverse()

        messages = []
        for row in rows:
            role = row.get("role")
            content = row.get("content")
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        return messages
    except Exception:
        return []


def save_chat_message(session_id: str, role: str, content: str) -> None:
    if not content:
        return
    try:
        supabase.table("chat_history").insert(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
            }
        ).execute()
    except Exception:
        pass


def call_claude(messages: list[dict]) -> str:
    if not CLAUDE_API_KEY:
        return "⚠️ CLAUDE_API_KEY not configured in Render environment variables."

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 2000,
                "system": V7_SYSTEM,
                "messages": messages,
            },
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            return f"API Error: {data['error'].get('message', 'Unknown')}"

        content = data.get("content", [])
        text_blocks = [block.get("text", "") for block in content if block.get("type") == "text"]
        out = "".join(text_blocks).strip()
        return out or "No text response returned."
    except requests.Timeout:
        return "⚠️ Request timed out — try again."
    except requests.RequestException as e:
        try:
            detail = e.response.text if e.response is not None else str(e)
        except Exception:
            detail = str(e)
        return f"⚠️ Claude request failed — {detail}"
    except Exception as e:
        return f"⚠️ Error: {str(e)}"


# ------------------------------------------------------------------
# PAGES
# ------------------------------------------------------------------

@app.route("/")
def index():
    state = get_state()
    session = get_session_pl()

    try:
        pending = (
            supabase.table("bet_log")
            .select("*")
            .eq("result", "PENDING")
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception:
        pending = []

    try:
        recent = (
            supabase.table("bet_log")
            .select("*")
            .neq("result", "PENDING")
            .order("created_at", desc=True)
            .limit(8)
            .execute()
            .data
            or []
        )
    except Exception:
        recent = []

    return render_template(
        "index.html",
        state=state,
        session=session,
        pending=pending,
        recent=recent,
        page="home",
    )


@app.route("/backtest")
def backtest():
    state = get_state()
    session = get_session_pl()

    try:
        logs = (
            supabase.table("training_logs")
            .select("*")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
            .data
            or []
        )
    except Exception:
        logs = []

    return render_template(
        "backtest.html",
        state=state,
        session=session,
        logs=logs,
        page="backtest",
    )


@app.route("/performance")
def performance():
    state = get_state()
    session = get_session_pl()

    try:
        bets = (
            supabase.table("bet_log")
            .select("*")
            .order("created_at", desc=True)
            .limit(200)
            .execute()
            .data
            or []
        )
    except Exception:
        bets = []

    settled = [b for b in bets if b.get("result") not in ["PENDING", None]]
    summary = {
        "total": len(bets),
        "wins": sum(1 for b in bets if b.get("result") == "WIN"),
        "settled": len(settled),
        "total_pl": round(sum(safe_float(b.get("pl"), 0) for b in bets), 2),
    }

    return render_template(
        "performance.html",
        state=state,
        session=session,
        bets=bets,
        summary=summary,
        page="performance",
    )


@app.route("/data")
def data_control():
    state = get_state()
    session = get_session_pl()

    try:
        races = (
            supabase.table("today_races")
            .select("*")
            .order("fetched_at", desc=True)
            .limit(50)
            .execute()
            .data
            or []
        )
    except Exception:
        races = []

    return render_template(
        "data_control.html",
        state=state,
        session=session,
        races=races,
        total=len(races),
        page="data",
    )


@app.route("/quality")
def data_quality():
    state = get_state()
    session = get_session_pl()

    try:
        runners = (
            supabase.table("today_runners")
            .select("early_speed, best_time, career")
            .execute()
            .data
            or []
        )
    except Exception:
        runners = []

    stats = {
        "total": len(runners),
        "high_q": sum(
            1
            for r in runners
            if r.get("early_speed") and r.get("best_time") and r.get("career")
        ),
        "med_q": sum(
            1
            for r in runners
            if r.get("early_speed") or r.get("best_time")
        ),
    }

    return render_template(
        "data_quality.html",
        state=state,
        session=session,
        stats=stats,
        page="quality",
    )


# ------------------------------------------------------------------
# CHAT
# ------------------------------------------------------------------

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "default")
    user_msg = (data.get("message") or "").strip()

    if not user_msg:
        return jsonify({"response": "Empty message.", "ok": False}), 400

    state = get_state()
    messages = get_chat_history(session_id, limit=20)

    ctx = ""
    if state.get("time_anchor"):
        ctx += f"ANCHOR_TIME: {state.get('time_anchor')} AEST. "
    ctx += (
        f"Code: {state.get('active_code', 'GREYHOUND')}. "
        f"Bankroll: ${safe_float(state.get('bankroll'), 0):.0f}. "
        f"Bank mode: {state.get('bank_mode', 'STANDARD')}."
    )

    messages.append({"role": "user", "content": f"{ctx}\n{user_msg}"})
    response = call_claude(messages)

    save_chat_message(session_id, "user", user_msg)
    save_chat_message(session_id, "assistant", response)

    return jsonify({"response": response, "ok": True})


@app.route("/api/chat/history")
def chat_history_api():
    session_id = request.args.get("session_id", "default")

    try:
        rows = (
            supabase.table("chat_history")
            .select("*")
            .eq("session_id", session_id)
            .order("id")
            .limit(100)
            .execute()
            .data
            or []
        )
        return jsonify(rows)
    except Exception:
        return jsonify([])


@app.route("/api/chat/clear", methods=["POST"])
def clear_chat():
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id", "default")

    try:
        supabase.table("chat_history").delete().eq("session_id", session_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ------------------------------------------------------------------
# STATE
# ------------------------------------------------------------------

@app.route("/api/state", methods=["GET", "POST"])
def api_state():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        try:
            update_state(**payload)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify(get_state())


@app.route("/api/session/pl")
def api_session_pl():
    return jsonify(get_session_pl())


@app.route("/api/bankroll/set", methods=["POST"])
def api_bankroll_set():
    payload = request.get_json(silent=True) or {}
    bankroll = payload.get("bankroll")

    try:
        bankroll = round(float(bankroll), 2)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid bankroll"}), 400

    if bankroll <= 0:
        return jsonify({"ok": False, "error": "Bankroll must be greater than 0"}), 400

    try:
        update_state(bankroll=bankroll)
        return jsonify({"ok": True, "bankroll": bankroll})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ------------------------------------------------------------------
# BETS
# ------------------------------------------------------------------

@app.route("/api/bet/log", methods=["POST"])
def log_bet():
    d = request.get_json(silent=True) or {}

    try:
        payload = {
            "date": date.today().isoformat(),
            "track": d.get("track"),
            "race_num": safe_int(d.get("race_num"), None) if d.get("race_num") is not None else None,
            "code": d.get("code", "GREYHOUND"),
            "runner": d.get("runner"),
            "box_num": safe_int(d.get("box_num"), None) if d.get("box_num") is not None else None,
            "bet_type": d.get("bet_type"),
            "odds": safe_float(d.get("odds"), None) if d.get("odds") is not None else None,
            "stake": safe_float(d.get("stake"), None) if d.get("stake") is not None else None,
            "ev": safe_float(d.get("ev"), None) if d.get("ev") is not None else None,
            "confidence": d.get("confidence"),
            "edge_type": d.get("edge_type"),
            "edge_status": d.get("edge_status"),
            "decision": d.get("decision"),
            "race_shape": d.get("race_shape"),
            "result": "PENDING",
            "pl": 0,
        }

        supabase.table("bet_log").insert(payload).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bet/settle", methods=["POST"])
def settle_bet():
    d = request.get_json(silent=True) or {}
    bet_id = d.get("id")
    result = d.get("result")

    if not bet_id:
        return jsonify({"ok": False, "error": "Missing bet id"}), 400

    if result not in {"WIN", "PLACE", "LOSS"}:
        return jsonify({"ok": False, "error": "Result must be WIN, PLACE, or LOSS"}), 400

    try:
        bet = (
            supabase.table("bet_log")
            .select("*")
            .eq("id", bet_id)
            .single()
            .execute()
            .data
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to fetch bet: {str(e)}"}), 500

    if not bet:
        return jsonify({"ok": False, "error": "Bet not found"}), 404

    stake = safe_float(bet.get("stake"), 0)
    odds = safe_float(bet.get("odds"), 0)

    if result == "WIN":
        pl = round(stake * (odds - 1), 2)
    elif result == "PLACE":
        pl = round(stake * 0.4, 2)
    else:
        pl = round(-stake, 2)

    error_tag = d.get("error_tag", "VARIANCE") if result == "LOSS" else None

    try:
        supabase.table("bet_log").update(
            {
                "result": result,
                "pl": pl,
                "error_tag": error_tag,
                "settled_at": utc_now_iso(),
            }
        ).eq("id", bet_id).execute()

        state = get_state()
        update_state(
            bankroll=round(safe_float(state.get("bankroll"), 0) + pl, 2),
            current_pl=round(safe_float(state.get("current_pl"), 0) + pl, 2),
        )

        return jsonify({"ok": True, "pl": pl})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ------------------------------------------------------------------
# PERFORMANCE
# ------------------------------------------------------------------

@app.route("/api/performance/chart")
def perf_chart():
    try:
        rows = (
            supabase.table("bet_log")
            .select("date, pl, result")
            .neq("result", "PENDING")
            .order("date")
            .execute()
            .data
            or []
        )
    except Exception:
        return jsonify([])

    by_date = {}
    for row in rows:
        row_date = row.get("date")
        if not row_date:
            continue
        if row_date not in by_date:
            by_date[row_date] = {"pl": 0, "bets": 0}
        by_date[row_date]["pl"] += safe_float(row.get("pl"), 0)
        by_date[row_date]["bets"] += 1

    running_total = 0
    cumulative = []
    for row_date in sorted(by_date.keys())[-30:]:
        running_total += by_date[row_date]["pl"]
        cumulative.append(
            {
                "date": row_date,
                "pl": round(running_total, 2),
                "bets": by_date[row_date]["bets"],
            }
        )

    return jsonify(cumulative)


# ------------------------------------------------------------------
# RACES
# ------------------------------------------------------------------

@app.route("/api/races/add", methods=["POST"])
def add_race():
    d = request.get_json(silent=True) or {}

    try:
        payload = {
            "track": d.get("track"),
            "race_num": safe_int(d.get("race_num"), None) if d.get("race_num") is not None else None,
            "distance": d.get("distance"),
            "grade": d.get("grade"),
            "jump_time": d.get("jump_time"),
            "code": d.get("code", "GREYHOUND"),
            "date": date.today().isoformat(),
            "state": d.get("state", ""),
            "status": d.get("status", "upcoming"),
        }

        supabase.table("today_races").insert(payload).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ------------------------------------------------------------------
# APP ENTRY
# ------------------------------------------------------------------

if __name__ == "__main__":
    app.run(
        debug=False,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
    )
