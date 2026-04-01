import os
import json
import requests
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify
from supabase import create_client, Client

app = Flask(**name**)

SUPABASE_URL = os.environ.get(“SUPABASE_URL”, “”)
SUPABASE_KEY = os.environ.get(“SUPABASE_KEY”, “”)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

CLAUDE_API_KEY = os.environ.get(“CLAUDE_API_KEY”, “”)
CLAUDE_MODEL = “claude-sonnet-4-20250514”

try:
from system_prompt import V7_SYSTEM
except ImportError:
V7_SYSTEM = “You are DEMONPULSE SYNDICATE V7. Professional betting intelligence system for Australian racing.”

def get_state():
try:
res = supabase.table(“system_state”).select(”*”).eq(“id”, 1).single().execute()
return res.data or {}
except Exception:
return {}

def update_state(**kwargs):
allowed = [“bankroll”, “current_pl”, “bank_mode”, “active_code”, “posture”,
“sys_state”, “variance”, “session_type”, “time_anchor”]
filtered = {k: v for k, v in kwargs.items() if k in allowed}
if not filtered:
return
filtered[“updated_at”] = datetime.utcnow().isoformat()
supabase.table(“system_state”).update(filtered).eq(“id”, 1).execute()

def get_session_pl():
today = date.today().isoformat()
try:
res = supabase.table(“bet_log”).select(“pl, result”).eq(“date”, today).execute()
rows = res.data or []
return {
“total”: round(sum(r.get(“pl”) or 0 for r in rows), 2),
“bets”: len(rows),
“wins”: sum(1 for r in rows if r.get(“result”) == “WIN”),
“pending”: sum(1 for r in rows if r.get(“result”) == “PENDING”)
}
except Exception:
return {“total”: 0, “bets”: 0, “wins”: 0, “pending”: 0}

def call_claude(messages):
if not CLAUDE_API_KEY:
return “CLAUDE_API_KEY not set in Render environment variables.”
try:
r = requests.post(
“https://api.anthropic.com/v1/messages”,
headers={
“Content-Type”: “application/json”,
“x-api-key”: CLAUDE_API_KEY,
“anthropic-version”: “2023-06-01”
},
json={
“model”: CLAUDE_MODEL,
“max_tokens”: 1500,
“system”: V7_SYSTEM,
“tools”: [{“type”: “web_search_20250305”, “name”: “web_search”}],
“messages”: messages
},
timeout=45
)
data = r.json()
if “error” in data:
return f”API Error: {data[‘error’].get(‘message’, ‘Unknown’)}”
return “”.join(b[“text”] for b in data.get(“content”, []) if b.get(“type”) == “text”)
except requests.Timeout:
return “Request timed out. Try again.”
except Exception as e:
return f”Error: {str(e)}”

@app.route(”/”)
def index():
state = get_state()
session = get_session_pl()
try:
pending = supabase.table(“bet_log”).select(”*”).eq(“result”, “PENDING”).order(“created_at”, desc=True).execute().data or []
recent = supabase.table(“bet_log”).select(”*”).neq(“result”, “PENDING”).order(“created_at”, desc=True).limit(8).execute().data or []
except Exception:
pending, recent = [], []
return render_template(“index.html”, state=state, session=session, pending=pending, recent=recent, page=“home”)

@app.route(”/backtest”)
def backtest():
state = get_state()
session = get_session_pl()
try:
logs = supabase.table(“training_logs”).select(”*”).order(“created_at”, desc=True).limit(100).execute().data or []
except Exception:
logs = []
return render_template(“backtest.html”, state=state, session=session, logs=logs, page=“backtest”)

@app.route(”/performance”)
def performance():
state = get_state()
session = get_session_pl()
try:
bets = supabase.table(“bet_log”).select(”*”).order(“created_at”, desc=True).limit(200).execute().data or []
settled = [b for b in bets if b.get(“result”) not in [“PENDING”, None]]
summary = {
“total”: len(bets),
“wins”: sum(1 for b in bets if b.get(“result”) == “WIN”),
“settled”: len(settled),
“total_pl”: round(sum(b.get(“pl”) or 0 for b in bets), 2)
}
except Exception:
bets, summary = [], {“total”: 0, “wins”: 0, “settled”: 0, “total_pl”: 0}
return render_template(“performance.html”, state=state, session=session, bets=bets, summary=summary, page=“performance”)

@app.route(”/data”)
def data_control():
state = get_state()
session = get_session_pl()
try:
races = supabase.table(“today_races”).select(”*”).order(“fetched_at”, desc=True).limit(50).execute().data or []
except Exception:
races = []
return render_template(“data_control.html”, state=state, session=session, races=races, total=len(races), page=“data”)

@app.route(”/quality”)
def data_quality():
state = get_state()
session = get_session_pl()
try:
runners = supabase.table(“today_runners”).select(“early_speed, best_time, career”).execute().data or []
stats = {
“total”: len(runners),
“high_q”: sum(1 for r in runners if r.get(“early_speed”) and r.get(“best_time”) and r.get(“career”)),
“med_q”: sum(1 for r in runners if r.get(“early_speed”) or r.get(“best_time”))
}
except Exception:
stats = {“total”: 0, “high_q”: 0, “med_q”: 0}
return render_template(“data_quality.html”, state=state, session=session, stats=stats, page=“quality”)

@app.route(”/api/chat”, methods=[“POST”])
def chat():
data = request.json or {}
session_id = data.get(“session_id”, “default”)
user_msg = data.get(“message”, “”).strip()
if not user_msg:
return jsonify({“response”: “Empty message.”, “ok”: False})
state = get_state()
try:
history = supabase.table(“chat_history”).select(“role, content”).eq(“session_id”, session_id).order(“id”, desc=True).limit(8).execute().data or []
history.reverse()
messages = [{“role”: h[“role”], “content”: h[“content”]} for h in history]
except Exception:
messages = []
ctx = “”
if state.get(“time_anchor”):
ctx += f”ANCHOR_TIME: {state[‘time_anchor’]} AEST. “
ctx += f”Code: {state.get(‘active_code’, ‘GREYHOUND’)}. Bankroll: ${state.get(‘bankroll’, 0):.0f}. Bank mode: {state.get(‘bank_mode’, ‘STANDARD’)}.”
messages.append({“role”: “user”, “content”: f”{ctx}\n{user_msg}”})
response = call_claude(messages)
try:
supabase.table(“chat_history”).insert({“session_id”: session_id, “role”: “user”, “content”: user_msg}).execute()
supabase.table(“chat_history”).insert({“session_id”: session_id, “role”: “assistant”, “content”: response}).execute()
except Exception:
pass
return jsonify({“response”: response, “ok”: True})

@app.route(”/api/state”, methods=[“GET”, “POST”])
def api_state():
if request.method == “POST”:
update_state(**(request.json or {}))
return jsonify(get_state())

@app.route(”/api/bet/log”, methods=[“POST”])
def log_bet():
d = request.json or {}
try:
supabase.table(“bet_log”).insert({
“date”: date.today().isoformat(),
“track”: d.get(“track”),
“race_num”: d.get(“race_num”),
“runner”: d.get(“runner”),
“box_num”: d.get(“box_num”),
“bet_type”: d.get(“bet_type”),
“odds”: d.get(“odds”),
“stake”: d.get(“stake”),
“ev”: d.get(“ev”),
“confidence”: d.get(“confidence”),
“edge_type”: d.get(“edge_type”),
“decision”: d.get(“decision”),
“race_shape”: d.get(“race_shape”),
“result”: “PENDING”,
“pl”: 0
}).execute()
return jsonify({“ok”: True})
except Exception as e:
return jsonify({“ok”: False, “error”: str(e)})

@app.route(”/api/bet/settle”, methods=[“POST”])
def settle_bet():
d = request.json or {}
bet_id = d.get(“id”)
try:
bet = supabase.table(“bet_log”).select(”*”).eq(“id”, bet_id).single().execute().data
if not bet:
return jsonify({“ok”: False, “error”: “Bet not found”})
result = d.get(“result”)
stake = bet.get(“stake”) or 0
odds = bet.get(“odds”) or 2
if result == “WIN”:
pl = round(stake * (odds - 1), 2)
elif result == “PLACE”:
pl = round(stake * 0.4, 2)
else:
pl = round(-stake, 2)
error_tag = d.get(“error_tag”, “VARIANCE”) if result == “LOSS” else None
supabase.table(“bet_log”).update({
“result”: result,
“pl”: pl,
“error_tag”: error_tag,
“settled_at”: datetime.utcnow().isoformat()
}).eq(“id”, bet_id).execute()
state = get_state()
update_state(
bankroll=round((state.get(“bankroll”) or 0) + pl, 2),
current_pl=round((state.get(“current_pl”) or 0) + pl, 2)
)
return jsonify({“ok”: True, “pl”: pl})
except Exception as e:
return jsonify({“ok”: False, “error”: str(e)})

@app.route(”/api/chat/history”)
def chat_history_api():
session_id = request.args.get(“session_id”, “default”)
try:
h = supabase.table(“chat_history”).select(”*”).eq(“session_id”, session_id).order(“id”).limit(100).execute().data or []
return jsonify(h)
except Exception:
return jsonify([])

@app.route(”/api/chat/clear”, methods=[“POST”])
def clear_chat():
session_id = (request.json or {}).get(“session_id”, “default”)
try:
supabase.table(“chat_history”).delete().eq(“session_id”, session_id).execute()
return jsonify({“ok”: True})
except Exception as e:
return jsonify({“ok”: False, “error”: str(e)})

@app.route(”/api/performance/chart”)
def perf_chart():
try:
rows = supabase.table(“bet_log”).select(“date, pl”).neq(“result”, “PENDING”).order(“date”).execute().data or []
by_date = {}
for r in rows:
d = r.get(“date”, “”)
if d not in by_date:
by_date[d] = {“pl”: 0, “bets”: 0}
by_date[d][“pl”] += r.get(“pl”) or 0
by_date[d][“bets”] += 1
cum, total = [], 0
for d in sorted(by_date.keys())[-30:]:
total += by_date[d][“pl”]
cum.append({“date”: d, “pl”: round(total, 2), “bets”: by_date[d][“bets”]})
return jsonify(cum)
except Exception:
return jsonify([])

@app.route(”/api/races/add”, methods=[“POST”])
def add_race():
d = request.json or {}
try:
supabase.table(“today_races”).insert({
“track”: d.get(“track”),
“race_num”: d.get(“race_num”),
“distance”: d.get(“distance”),
“grade”: d.get(“grade”),
“jump_time”: d.get(“jump_time”),
“code”: d.get(“code”, “GREYHOUND”),
“date”: date.today().isoformat(),
“state”: d.get(“state”, “”),
“status”: “upcoming”
}).execute()
return jsonify({“ok”: True})
except Exception as e:
return jsonify({“ok”: False, “error”: str(e)})

if **name** == “**main**”:
app.run(debug=False, host=“0.0.0.0”, port=int(os.environ.get(“PORT”, 5000)))
