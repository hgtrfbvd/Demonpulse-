import os, json, sqlite3, requests
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, g
from system_prompt import V7_SYSTEM

app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "dpv7.db")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS races (id INTEGER PRIMARY KEY AUTOINCREMENT, track TEXT, race_num INTEGER, distance TEXT, grade TEXT, jump_time TEXT, code TEXT DEFAULT 'GREYHOUND', date TEXT, state TEXT, status TEXT DEFAULT 'upcoming', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS runners (id INTEGER PRIMARY KEY AUTOINCREMENT, race_id INTEGER, box_num INTEGER, name TEXT, trainer TEXT, run_style TEXT, early_speed TEXT, weight REAL, best_time TEXT, career TEXT, scratched INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, race_id INTEGER, winner TEXT, win_price REAL, place2 TEXT, place3 TEXT, margin REAL, winning_time TEXT, date TEXT);
        CREATE TABLE IF NOT EXISTS bet_log (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, track TEXT, race_num INTEGER, runner TEXT, box_num INTEGER, bet_type TEXT, odds REAL, stake REAL, ev REAL, confidence TEXT, result TEXT DEFAULT 'PENDING', pl REAL DEFAULT 0, error_tag TEXT, session_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS training_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, epoch INTEGER, accuracy REAL, roi REAL, drawdown REAL, win_rate REAL, top3_rate REAL, error_tempo REAL, error_position REAL, error_traffic REAL, error_distance REAL, error_condition REAL, error_variance REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS performance_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, total_bets INTEGER, wins INTEGER, places INTEGER, roi REAL, pl REAL, strike_rate REAL, avg_odds REAL, best_win REAL, worst_loss REAL, code TEXT DEFAULT 'GREYHOUND');
        CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS system_state (id INTEGER PRIMARY KEY, bankroll REAL DEFAULT 1000, current_pl REAL DEFAULT 0, bank_mode TEXT DEFAULT 'STANDARD', active_code TEXT DEFAULT 'GREYHOUND', posture TEXT DEFAULT 'NORMAL', sys_state TEXT DEFAULT 'STABLE', variance TEXT DEFAULT 'NORMAL', session_type TEXT DEFAULT 'Live Betting', time_anchor TEXT DEFAULT '', updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        INSERT OR IGNORE INTO system_state (id) VALUES (1);
    """)
    db.commit()
    db.close()

init_db()

def get_state():
    db = get_db()
    row = db.execute("SELECT * FROM system_state WHERE id=1").fetchone()
    return dict(row) if row else {}

def update_state(**kwargs):
    db = get_db()
    allowed = ["bankroll","current_pl","bank_mode","active_code","posture","sys_state","variance","session_type","time_anchor"]
    filtered = {k:v for k,v in kwargs.items() if k in allowed}
    if not filtered: return
    sets = ", ".join(f"{k}=?" for k in filtered)
    db.execute(f"UPDATE system_state SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=1", list(filtered.values()))
    db.commit()

def get_session_pl():
    db = get_db()
    today = date.today().isoformat()
    row = db.execute("SELECT COALESCE(SUM(pl),0) as total, COUNT(*) as bets, SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins, SUM(CASE WHEN result='PENDING' THEN 1 ELSE 0 END) as pending FROM bet_log WHERE date=?", (today,)).fetchone()
    return dict(row)



def call_claude(messages):
    if not CLAUDE_API_KEY:
        return "⚠️ CLAUDE_API_KEY not configured. Go to Render dashboard → Environment → add CLAUDE_API_KEY."
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"Content-Type":"application/json","x-api-key":CLAUDE_API_KEY,"anthropic-version":"2023-06-01"},
            json={"model":CLAUDE_MODEL,"max_tokens":1500,"system":V7_SYSTEM,"tools":[{"type":"web_search_20250305","name":"web_search"}],"messages":messages},
            timeout=45)
        data = r.json()
        if "error" in data: return f"API Error: {data['error'].get('message','Unknown')}"
        return "".join(b["text"] for b in data.get("content",[]) if b.get("type")=="text")
    except requests.Timeout: return "⚠️ Request timed out — try again."
    except Exception as e: return f"⚠️ Error: {str(e)}"

@app.route("/")
def index():
    state = get_state()
    session = get_session_pl()
    db = get_db()
    pending = db.execute("SELECT * FROM bet_log WHERE result='PENDING' ORDER BY created_at DESC").fetchall()
    recent = db.execute("SELECT * FROM bet_log WHERE result!='PENDING' ORDER BY created_at DESC LIMIT 8").fetchall()
    return render_template("index.html", state=state, session=session, pending=pending, recent=recent, page="home")

@app.route("/backtest")
def backtest():
    state = get_state()
    session = get_session_pl()
    db = get_db()
    logs = db.execute("SELECT * FROM training_logs ORDER BY created_at DESC LIMIT 100").fetchall()
    return render_template("backtest.html", state=state, session=session, logs=[dict(l) for l in logs], page="backtest")

@app.route("/performance")
def performance():
    state = get_state()
    session = get_session_pl()
    db = get_db()
    bets = db.execute("SELECT * FROM bet_log ORDER BY created_at DESC LIMIT 200").fetchall()
    summary = db.execute("SELECT COUNT(*) as total, SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins, SUM(CASE WHEN result NOT IN ('PENDING') THEN 1 ELSE 0 END) as settled, COALESCE(SUM(pl),0) as total_pl FROM bet_log").fetchone()
    return render_template("performance.html", state=state, session=session, bets=bets, summary=dict(summary), page="performance")

@app.route("/data")
def data_control():
    state = get_state()
    session = get_session_pl()
    db = get_db()
    races = db.execute("SELECT * FROM races ORDER BY created_at DESC LIMIT 50").fetchall()
    total = db.execute("SELECT COUNT(*) as c FROM races").fetchone()["c"]
    return render_template("data_control.html", state=state, session=session, races=races, total=total, page="data")

@app.route("/quality")
def data_quality():
    state = get_state()
    session = get_session_pl()
    db = get_db()
    stats = db.execute("SELECT COUNT(*) as total, SUM(CASE WHEN early_speed IS NOT NULL AND best_time IS NOT NULL AND career IS NOT NULL THEN 1 ELSE 0 END) as high_q, SUM(CASE WHEN early_speed IS NOT NULL OR best_time IS NOT NULL THEN 1 ELSE 0 END) as med_q FROM runners").fetchone()
    return render_template("data_quality.html", state=state, session=session, stats=dict(stats), page="quality")

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json or {}
    session_id = data.get("session_id","default")
    user_msg = data.get("message","").strip()
    if not user_msg: return jsonify({"response":"Empty message.","ok":False})
    state = get_state()
    db = get_db()
    history = db.execute("SELECT role, content FROM chat_history WHERE session_id=? ORDER BY id DESC LIMIT 8", (session_id,)).fetchall()
    messages = [{"role":h["role"],"content":h["content"]} for h in reversed(history)]
    ctx = f"{'ANCHOR_TIME: '+state.get('time_anchor','')+' AEST. ' if state.get('time_anchor') else ''}Code: {state.get('active_code','GREYHOUND')}. Bankroll: ${state.get('bankroll',0):.0f}. Bank mode: {state.get('bank_mode','STANDARD')}."
    messages.append({"role":"user","content":f"{ctx}\n{user_msg}"})
    response = call_claude(messages)
    db.execute("INSERT INTO chat_history (session_id,role,content) VALUES (?,?,?)", (session_id,"user",user_msg))
    db.execute("INSERT INTO chat_history (session_id,role,content) VALUES (?,?,?)", (session_id,"assistant",response))
    db.commit()
    return jsonify({"response":response,"ok":True})

@app.route("/api/state", methods=["GET","POST"])
def api_state():
    if request.method=="POST":
        update_state(**(request.json or {}))
    return jsonify(get_state())

@app.route("/api/bet/log", methods=["POST"])
def log_bet():
    d = request.json or {}
    db = get_db()
    db.execute("INSERT INTO bet_log (date,track,race_num,runner,box_num,bet_type,odds,stake,ev,confidence,session_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (date.today().isoformat(),d.get("track"),d.get("race_num"),d.get("runner"),d.get("box_num"),d.get("bet_type"),d.get("odds"),d.get("stake"),d.get("ev"),d.get("confidence"),d.get("session_id","default")))
    db.commit()
    return jsonify({"ok":True})

@app.route("/api/bet/settle", methods=["POST"])
def settle_bet():
    d = request.json or {}
    db = get_db()
    bet = db.execute("SELECT * FROM bet_log WHERE id=?", (d.get("id"),)).fetchone()
    if not bet: return jsonify({"ok":False})
    result = d.get("result")
    pl = bet["stake"]*(bet["odds"]-1) if result=="WIN" else bet["stake"]*0.4 if result=="PLACE" else -(bet["stake"] or 0)
    db.execute("UPDATE bet_log SET result=?, pl=?, error_tag=? WHERE id=?", (result, pl, d.get("error_tag","VARIANCE"), d.get("id")))
    state = get_state()
    update_state(bankroll=(state.get("bankroll") or 0)+pl, current_pl=(state.get("current_pl") or 0)+pl)
    db.commit()
    return jsonify({"ok":True,"pl":round(pl,2)})

@app.route("/api/chat/history")
def chat_history_api():
    db = get_db()
    h = db.execute("SELECT * FROM chat_history WHERE session_id=? ORDER BY id ASC LIMIT 100", (request.args.get("session_id","default"),)).fetchall()
    return jsonify([dict(x) for x in h])

@app.route("/api/chat/clear", methods=["POST"])
def clear_chat():
    db = get_db()
    db.execute("DELETE FROM chat_history WHERE session_id=?", ((request.json or {}).get("session_id","default"),))
    db.commit()
    return jsonify({"ok":True})

@app.route("/api/performance/chart")
def perf_chart():
    db = get_db()
    rows = db.execute("SELECT date, SUM(pl) as daily_pl, COUNT(*) as bets FROM bet_log WHERE result!='PENDING' GROUP BY date ORDER BY date ASC LIMIT 30").fetchall()
    cum, total = [], 0
    for r in rows:
        total += r["daily_pl"] or 0
        cum.append({"date":r["date"],"pl":round(total,2),"bets":r["bets"]})
    return jsonify(cum)

@app.route("/api/races/add", methods=["POST"])
def add_race():
    d = request.json or {}
    db = get_db()
    db.execute("INSERT INTO races (track,race_num,distance,grade,jump_time,code,date,state) VALUES (?,?,?,?,?,?,?,?)",
        (d.get("track"),d.get("race_num"),d.get("distance"),d.get("grade"),d.get("jump_time"),d.get("code","GREYHOUND"),date.today().isoformat(),d.get("state","")))
    db.commit()
    return jsonify({"ok":True})

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
