"""
app.py - DemonPulse V8 Flask Application
All V7 routes kept. V8 adds: auth, signals, exotics, audit, env separation.

ENV ENFORCEMENT:
  DP_ENV=LIVE  → fake data blocked, deletions blocked, /api/test/* blocked
  DP_ENV=TEST  → all test routes active, fake data allowed, separate tables
"""
import os
import time
import logging
from datetime import date, datetime
from flask import Flask, request, jsonify, send_from_directory, g

logging.basicConfig(level=logging.INFO, format="%(asctime)s [V8] %(message)s")
log = logging.getLogger(__name__)

# ── ENV must be imported first — it configures everything ──────────
from env import env, EnvViolation, env_violation_response

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = os.environ.get("FLASK_SECRET", "dpv8-flask-secret-change-me")

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_SONNET  = "claude-sonnet-4-20250514"
CLAUDE_HAIKU   = "claude-haiku-4-5-20251001"

try:
    from system_prompt import V7_SYSTEM
except ImportError:
    V7_SYSTEM = "You are DEMONPULSE V8. Interpret the pre-scored race packet and give final BET/SESSION/PASS decision."

# ─────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────
def startup():
    try:
        from scheduler import start_scheduler
        start_scheduler()
        log.info("Scheduler started")
    except Exception as e:
        log.error(f"Scheduler failed: {e}")

    try:
        from auth import bootstrap_admin
        bootstrap_admin()
    except Exception as e:
        log.error(f"Auth bootstrap failed: {e}")

    try:
        from audit import log_event
        log_event(None, "system", "STARTUP", "app", {"version": "V8", "mode": env.mode})
    except Exception:
        pass

    # Lock mode after startup so it cannot be changed mid-process
    # (runtime switches still go through env.switch_mode which requires admin)
    # env.lock()  # uncomment to fully lock; left unlocked to allow admin switch

startup()

# ─────────────────────────────────────────────────────────────────
# GLOBAL ENV VIOLATION HANDLER
# ─────────────────────────────────────────────────────────────────
@app.errorhandler(EnvViolation)
def handle_env_violation(exc):
    return env_violation_response(exc)


# ─────────────────────────────────────────────────────────────────
# HELPERS (V7 kept)
# ─────────────────────────────────────────────────────────────────
FLASK_ONLY_COMMANDS = {"status","settings","alerts","board","help","bets","log","source health"}

def is_flask_cmd(msg): return msg.strip().lower().split()[0] in FLASK_ONLY_COMMANDS if msg.strip() else False
def needs_analysis(msg): return any(k in msg.lower() for k in ["analyse","analyze","ultra","next race","refresh","next 3","picks today"])
def get_model(msg):      return CLAUDE_SONNET if needs_analysis(msg) else CLAUDE_HAIKU
def get_max_tokens(msg):
    l = msg.lower()
    if any(k in l for k in ["analyse","analyze","ultra"]): return 1000
    if any(k in l for k in ["next race","refresh","next 3","picks today"]): return 700
    return 350

def call_claude(messages, model=None, max_tokens=700):
    if not CLAUDE_API_KEY: return "CLAUDE_API_KEY not configured."
    import requests as req
    try:
        r = req.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type":"application/json","x-api-key":CLAUDE_API_KEY,"anthropic-version":"2023-06-01"},
            json={"model":model or CLAUDE_SONNET,"max_tokens":max_tokens,"system":V7_SYSTEM,"messages":messages},
            timeout=45)
        d = r.json()
        if "error" in d: return f"API Error: {d['error'].get('message','Unknown')}"
        return "".join(b["text"] for b in d.get("content",[]) if b.get("type")=="text")
    except req.Timeout: return "Request timed out."
    except Exception as e: return f"Error: {e}"


# ─────────────────────────────────────────────────────────────────
# SPA — serve React frontend
# ─────────────────────────────────────────────────────────────────
# ── SPA ROUTING — serves React index.html for all frontend paths ──────────────
def _serve_spa():
    """Single named view function for all SPA routes — avoids lambda-per-route."""
    return send_from_directory(app.static_folder, "index.html")

_SPA_ROUTES = ["/","/home","/live","/betting","/reports","/simulator","/ai-learning","/settings","/audit","/users"]
for _route in _SPA_ROUTES:
    app.add_url_rule(_route, endpoint=f"spa_{_route.strip('/') or 'root'}", view_func=_serve_spa)


# ─────────────────────────────────────────────────────────────────
# ENV STATUS API
# ─────────────────────────────────────────────────────────────────
@app.route("/api/env")
def api_env_status():
    """Public endpoint — returns current mode. Clients use this to warn users."""
    return jsonify(env.info())

@app.route("/api/env/switch", methods=["POST"])
def api_env_switch():
    """Admin-only runtime mode switch. Requires confirmation token."""
    from auth import get_current_user
    from audit import log_event
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Forbidden"}), 403
    d = request.json or {}
    new_mode = (d.get("mode") or "").upper()
    confirm  = d.get("confirm", "")
    # Require explicit confirmation string to prevent accidental switches
    if new_mode == "TEST" and confirm != "I UNDERSTAND THIS IS TEST MODE":
        return jsonify({"error": "Must confirm with 'I UNDERSTAND THIS IS TEST MODE'"}), 400
    if new_mode == "LIVE" and confirm != "SWITCH TO LIVE":
        return jsonify({"error": "Must confirm with 'SWITCH TO LIVE'"}), 400
    try:
        env.switch_mode(new_mode, actor=user.get("username","admin"))
        return jsonify({"ok": True, "mode": env.mode})
    except (EnvViolation, ValueError) as e:
        return jsonify({"error": str(e)}), 400


# ─────────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────────
@app.route("/api/auth/login", methods=["POST"])
def api_login():
    from auth import get_user_by_username, check_password, generate_token, check_rate_limit, reset_rate_limit
    from audit import log_login
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(ip):
        return jsonify({"error": "Too many attempts. Wait 5 minutes."}), 429
    d = request.json or {}
    username = (d.get("username") or "").strip().lower()
    password = d.get("password") or ""
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    user = get_user_by_username(username)
    if not user or not check_password(password, user.get("password_hash", "")):
        log_login(None, username, ip=ip, success=False)
        return jsonify({"error": "Invalid credentials"}), 401
    if not user.get("active", True):
        return jsonify({"error": "Account disabled"}), 403
    token, jti = generate_token(user["id"], user["username"], user["role"])
    reset_rate_limit(ip)
    log_login(user["id"], user["username"], ip=ip, success=True)
    ttl = int(os.environ.get("SESSION_TIMEOUT_MIN","480"))*60
    try:
        from users import register_session, _record_activity
        register_session(user["id"], jti, ip, request.headers.get("User-Agent",""), ttl)
        _record_activity(user["id"], "LOGIN", {"ip": ip})
    except Exception as e:
        log.warning(f"Session register failed: {e}")
    try:
        from db import get_db, safe_query, T
        # W-03: update last_login/last_ip separately from login_count to reduce
        # window for the read-modify-write race condition. A proper atomic
        # increment requires migration 005 (increment_login_count RPC); until
        # that function is deployed this is the safest pattern with the
        # standard Supabase REST client.
        safe_query(lambda: get_db().table(T("users")).update({
            "last_login": datetime.utcnow().isoformat(),
            "last_ip": ip,
        }).eq("id", user["id"]).execute())
        # Attempt atomic RPC increment; silently skip if function not yet deployed
        try:
            safe_query(lambda: get_db().rpc(
                "increment_login_count", {"p_user_id": user["id"]}
            ).execute())
        except Exception:
            # Fallback to read-modify-write until migration 005 is applied
            cur = safe_query(lambda: get_db().table(T("users")).select("login_count")
                             .eq("id", user["id"]).single().execute().data) or {}
            safe_query(lambda: get_db().table(T("users")).update({
                "login_count": (cur.get("login_count") or 0) + 1
            }).eq("id", user["id"]).execute())
    except Exception:
        pass
    resp = jsonify({"ok":True,"token":token,"user":{"id":user["id"],"username":user["username"],"role":user["role"]},"env":env.mode})
    resp.set_cookie("dp_token", token, httponly=True, samesite="Lax", max_age=ttl)
    return resp

@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    from auth import get_current_user
    from audit import log_logout
    user = get_current_user()
    if user:
        log_logout(user.get("sub"), user.get("username"))
        # Item 6b: LOGOUT in user_activity
        try:
            from users import _record_activity
            _record_activity(user["sub"], "LOGOUT", {
                "ip": request.remote_addr or "unknown"
            })
        except Exception:
            pass
    resp = jsonify({"ok": True})
    resp.delete_cookie("dp_token")
    return resp

@app.route("/api/auth/me")
def api_me():
    from auth import get_current_user, ROLE_PERMISSIONS
    user = get_current_user()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify({
        "id": user.get("sub"), "username": user.get("username"),
        "role": user.get("role"),
        "permissions": list(ROLE_PERMISSIONS.get(user.get("role","viewer"), set())),
        "env": env.mode,
    })


# ─────────────────────────────────────────────────────────────────
# USER MANAGEMENT — FULL CONTROL PANEL (admin only)
# ─────────────────────────────────────────────────────────────────
def _admin_only():
    from auth import get_current_user
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return None, (jsonify({"error":"Forbidden"}), 403)
    return user, None

@app.route("/api/users", methods=["GET"])
def api_users_list():
    user, err = _admin_only()
    if err: return err
    from users import get_all_users
    return jsonify(get_all_users())

@app.route("/api/users/create", methods=["POST"])
def api_users_create():
    user, err = _admin_only()
    if err: return err
    d = request.json or {}
    try:
        from users import create_user_full
        nu = create_user_full(
            username=d.get("username",""),
            password=d.get("password",""),
            role=d.get("role","operator"),
            display_name=d.get("display_name",""),
            email=d.get("email",""),
            active=d.get("active", True),
            starting_bankroll=float(d.get("bankroll", 1000)),
            creator_username=user["username"],
        )
        return jsonify({"ok":True,"user":nu})
    except Exception as e:
        return jsonify({"error":str(e)}), 400

@app.route("/api/users/<user_id>", methods=["GET"])
def api_users_get(user_id):
    user, err = _admin_only()
    if err: return err
    from users import get_user_full
    u = get_user_full(user_id)
    if not u: return jsonify({"error":"User not found"}), 404
    return jsonify(u)

@app.route("/api/users/<user_id>", methods=["PATCH"])
def api_users_update(user_id):
    user, err = _admin_only()
    if err: return err
    d = request.json or {}
    from users import update_user_profile
    try:
        changes = update_user_profile(user_id, user["username"], **d)
        return jsonify({"ok":True,"changes":changes})
    except Exception as e:
        return jsonify({"error":str(e)}), 400

@app.route("/api/users/<user_id>/password", methods=["POST"])
def api_users_password(user_id):
    user, err = _admin_only()
    if err: return err
    d = request.json or {}
    new_pw = d.get("password","")
    from users import reset_password
    try:
        reset_password(user_id, new_pw, user["username"])
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"error":str(e)}), 400

@app.route("/api/users/<user_id>", methods=["DELETE"])
def api_users_delete(user_id):
    user, err = _admin_only()
    if err: return err
    # Prevent self-delete
    if user_id == user.get("sub"):
        return jsonify({"error":"Cannot delete your own account"}), 400
    from users import delete_user
    try:
        delete_user(user_id, user["username"])
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"error":str(e)}), 400

@app.route("/api/users/<user_id>/force-logout", methods=["POST"])
def api_users_force_logout(user_id):
    user, err = _admin_only()
    if err: return err
    from users import revoke_all_sessions
    revoke_all_sessions(user_id, user["username"])
    return jsonify({"ok":True})

@app.route("/api/users/<user_id>/bankroll", methods=["GET","POST"])
def api_users_bankroll(user_id):
    user, err = _admin_only()
    if err: return err
    from users import get_user_account, update_user_bankroll
    from audit import log_event
    if request.method == "POST":
        d   = request.json or {}
        val = d.get("bankroll")
        if val is None or float(val) <= 0:
            return jsonify({"error": "Invalid bankroll amount"}), 400
        update_user_bankroll(user_id, float(val), user["username"])
        # MD-03: log admin bankroll changes to audit trail
        log_event(user["sub"], user["username"], "USER_BANKROLL_SET",
                  f"users/{user_id}/bankroll",
                  data={"new_bankroll": float(val), "target_user_id": user_id})
        return jsonify({"ok": True})
    return jsonify(get_user_account(user_id))

@app.route("/api/users/<user_id>/bets")
def api_users_bets(user_id):
    user, err = _admin_only()
    if err: return err
    from users import get_user_bets
    limit = int(request.args.get("limit", 100))
    return jsonify(get_user_bets(user_id, limit))

@app.route("/api/users/<user_id>/activity")
def api_users_activity(user_id):
    user, err = _admin_only()
    if err: return err
    from users import get_user_activity
    limit = int(request.args.get("limit", 100))
    return jsonify(get_user_activity(user_id, limit))

@app.route("/api/users/<user_id>/permissions", methods=["GET","PATCH"])
def api_users_permissions(user_id):
    user, err = _admin_only()
    if err: return err
    from users import get_user_permissions, update_user_permissions
    if request.method == "PATCH":
        d = request.json or {}
        result = update_user_permissions(
            user_id, d.get("granted",[]), d.get("revoked",[]), user["username"]
        )
        return jsonify({"ok":True, **result})
    return jsonify(get_user_permissions(user_id))

@app.route("/api/users/<user_id>/settings", methods=["GET","PATCH"])
def api_users_settings(user_id):
    user, err = _admin_only()
    if err: return err
    from users import get_user_settings, update_user_settings
    from audit import log_event
    if request.method == "PATCH":
        d = request.json or {}
        update_user_settings(user_id,
            settings=d.get("settings"), alerts=d.get("alerts"),
            admin_notes=d.get("admin_notes"))
        # MD-04: log settings changes to audit trail
        log_event(user["sub"], user["username"], "USER_SETTINGS_CHANGED",
                  f"users/{user_id}/settings",
                  data={"changed_by": user["username"], "target_user_id": user_id,
                        "keys_changed": list(d.keys())})
        return jsonify({"ok": True})
    return jsonify(get_user_settings(user_id))

@app.route("/api/users/<user_id>/sessions")
def api_users_sessions(user_id):
    user, err = _admin_only()
    if err: return err
    from users import get_active_sessions
    return jsonify(get_active_sessions(user_id))


# ─────────────────────────────────────────────────────────────────
# RACES
# ─────────────────────────────────────────────────────────────────
@app.route("/api/races/upcoming")
def api_races_upcoming():
    from auth import get_current_user
    user = get_current_user()  # allow None (no auth block)

    try:
        from db import get_db, safe_query, T
        from signals import get_signal_or_demo

        races = safe_query(
            lambda: get_db().table(T("today_races")).select("*")
                    .eq("date", date.today().isoformat())
                    .order("jump_time").limit(30).execute().data,
            []
        ) or []

        enriched = []
        for i, r in enumerate(races):
            sig = get_signal_or_demo(r.get("race_uid"), i)
            jump_ts = _compute_jump_ts(r.get("jump_time"))
            enriched.append({
                **r,
                "signal_data": sig,
                "jump_ts": jump_ts
            })

        return jsonify(enriched)

    except Exception as e:
        log.error(f"Upcoming races error: {e}")
        return jsonify([])

@app.route("/api/races/<race_uid>/analysis")
def api_race_analysis(race_uid):
    from auth import get_current_user
    user = get_current_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401
    try:
        from db import get_db, safe_query, T
        from scorer import score_race
        from signals import generate_signal, get_signal
        race = safe_query(
            lambda: get_db().table(T("today_races")).select("*").eq("race_uid", race_uid).single().execute().data)
        runners = safe_query(
            lambda: get_db().table(T("today_runners")).select("*").eq("race_uid", race_uid).order("box_num").execute().data, []
        ) or []
        if not race:
            if env.is_test:
                return jsonify(_demo_race_analysis())
            return jsonify({"error": "Race not found"}), 404
        scored = score_race(race, runners, race.get("track", ""))
        sig = get_signal(race_uid) or generate_signal(scored)

        stored_score = safe_query(
            lambda: get_db().table("scored_races").select("*")
                    .eq("race_uid", race_uid).single().execute().data
        )

        return jsonify({
            "race": race,
            "runners": runners,
            "scored": scored,
            "signal": sig,
            "stored_score": stored_score,
        })
    except Exception as e:
        log.error(f"Race analysis error: {e}")
        if env.is_test:
            return jsonify(_demo_race_analysis())
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────
# SIGNALS
# ─────────────────────────────────────────────────────────────────
@app.route("/api/signals/generate", methods=["POST"])
def api_signals_generate():
    from auth import get_current_user
    from signals import generate_signal, save_signal
    from audit import log_event
    user = get_current_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401
    d       = request.json or {}
    race_uid = d.get("race_uid")
    sig     = generate_signal(d.get("scored", {}), d.get("settings"))
    if race_uid:
        save_signal(race_uid, sig)
    # MD-01: log signal generation to audit trail
    log_event(user["sub"], user["username"], "SIGNAL_GENERATE",
              f"signals/{race_uid or 'adhoc'}",
              data={"signal": sig.get("signal"), "alert_level": sig.get("alert_level"),
                    "race_uid": race_uid})
    return jsonify(sig)

@app.route("/api/signals/<race_uid>")
def api_signal_get(race_uid):
    from auth import get_current_user
    from signals import get_signal_or_demo
    user = get_current_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    sig = get_signal_or_demo(race_uid, 0)
    if not sig:
        return jsonify({"error":"No signal for this race"}), 404
    return jsonify(sig)


# ─────────────────────────────────────────────────────────────────
# EXOTICS
# ─────────────────────────────────────────────────────────────────
@app.route("/api/exotics/calculate", methods=["POST"])
def api_exotics_calculate():
    from auth import get_current_user
    user = get_current_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    from exotics import handle_calculate
    return jsonify(handle_calculate(request.json or {}))

@app.route("/api/exotics/suggest", methods=["POST"])
def api_exotics_suggest():
    from auth import get_current_user
    user = get_current_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401
    from exotics import auto_suggest
    from db import get_db, safe_query, T
    d        = request.json or {}
    race_uid = d.get("race_uid")
    signal   = d.get("signal", "")
    runners  = d.get("runners", [])
    unit     = float(d.get("unit", 1.0))

    suggestions = auto_suggest(signal, runners, unit)

    # Item 2: persist each suggestion to exotic_suggestions
    if race_uid and suggestions:
        now = datetime.utcnow().isoformat()
        for s in suggestions:
            safe_query(lambda s=s: get_db().table(T("exotic_suggestions")).insert({
                "race_uid":   race_uid,
                "signal":     signal,
                "exotic_type": s.get("type"),
                "selections": s.get("selections"),
                "cost":       s.get("cost"),
                "est_return": s.get("est_return"),
                "risk_level": s.get("risk"),
                "accepted":   False,
                "created_at": now,
            }).execute())

    return jsonify(suggestions)


# ─────────────────────────────────────────────────────────────────
# BETS
# ─────────────────────────────────────────────────────────────────
@app.route("/api/bet/log", methods=["POST"])
def log_bet_route():
    from auth import get_current_user
    from audit import log_bet
    from db import get_db, safe_query, T, get_or_create_daily_session
    user = get_current_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401
    d = request.json or {}

    # Item 3: attach today's session so bet_log.session_id is populated
    session_id = get_or_create_daily_session()

    result = safe_query(lambda: get_db().table(T("bet_log")).insert({
        "date":      d.get("date", date.today().isoformat()),
        "track":     d.get("track"),
        "race_uid":  d.get("race_uid"),
        "race_num":  d.get("race_num"),
        "runner":    d.get("runner"),
        "box_num":   d.get("box_num"),
        "bet_type":  d.get("bet_type"),
        "odds":      d.get("odds"),
        "stake":     d.get("stake"),
        "ev":        d.get("ev"),
        "confidence": d.get("confidence"),
        "edge_type": d.get("edge_type"),
        "decision":  d.get("decision"),
        "race_shape": d.get("race_shape"),
        "result":    "PENDING",
        "pl":        0,
        "placed_by": user.get("username"),
        "signal":    d.get("signal"),
        "user_id":   user.get("sub"),
        "session_id": session_id,
    }).execute())

    if result:
        log_bet(user["sub"], user["username"], d)
        # Item 6a: BET_PLACED in user_activity
        try:
            from users import _record_activity
            _record_activity(user["sub"], "BET_PLACED", {
                "race_uid": d.get("race_uid"),
                "runner":   d.get("runner"),
                "stake":    d.get("stake"),
                "odds":     d.get("odds"),
                "bet_type": d.get("bet_type"),
            })
        except Exception:
            pass
    return jsonify({"ok": result is not None})

@app.route("/api/bet/settle", methods=["POST"])
def settle_bet():
    from auth import get_current_user
    from audit import log_settle
    from db import get_db, safe_query, T, get_state, update_state
    user = get_current_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    d = request.json or {}
    bet_id = d.get("id")
    bet = safe_query(lambda: get_db().table(T("bet_log")).select("*").eq("id",bet_id).single().execute().data)
    if not bet: return jsonify({"ok":False,"error":"Bet not found"}), 404
    result = d.get("result")
    stake  = float(bet.get("stake") or 0)
    odds   = float(bet.get("odds") or 2.0)

    # CF-01: correct P/L by bet type
    # WIN:   full net profit = stake × (odds − 1)
    # PLACE: standard TAB formula (odds − 1) / 4 — divisor 4 for 8+ runner fields.
    #        Old bets settled with the incorrect flat-0.4 formula remain in DB;
    #        only new settlements from this point use the correct formula.
    # LOSS:  net = −stake
    if result == "WIN":
        pl = round(stake * (odds - 1), 2)
    elif result == "PLACE":
        pl = round(stake * max(0.0, (odds - 1) / 4.0), 2)
    else:
        pl = round(-stake, 2)

    safe_query(lambda: get_db().table(T("bet_log")).update(
        {"result": result, "pl": pl, "settled_at": datetime.utcnow().isoformat()}
    ).eq("id", bet_id).execute())
    state = get_state()
    update_state(
        bankroll=round((state.get("bankroll") or 0) + pl, 2),
        current_pl=round((state.get("current_pl") or 0) + pl, 2),
    )
    log_settle(user["sub"], user["username"], bet_id, result, pl)

    # Update per-user bankroll
    try:
        from users import apply_bet_pl, _record_activity
        uid = bet.get("user_id") or user.get("sub")
        if uid:
            apply_bet_pl(uid, pl)
            _record_activity(uid, "BET_SETTLED", {"result": result, "pl": pl, "bet_id": str(bet_id)})
    except Exception as e:
        log.warning(f"Per-user bankroll update failed: {e}")

    # CF-06: fetch scored_races context so ETG/EPR receive real data instead of None
    scored = None
    race_uid_key = bet.get("race_uid")
    if race_uid_key:
        try:
            scored = safe_query(
                lambda: get_db().table("scored_races").select("*")
                        .eq("race_uid", race_uid_key).single().execute().data
            )
        except Exception:
            pass  # scored_races may not exist for this race; learning handles None gracefully

    try:
        from learning_engine import process_result
        process_result(bet, result, pl, scored)   # CF-06: was always None
    except Exception:
        pass

    # Item 3: update session totals for today's session
    try:
        from db import get_or_create_daily_session
        sess_id = bet.get("session_id") or get_or_create_daily_session()
        if sess_id:
            won = result == "WIN"
            safe_query(lambda: get_db().rpc("increment_session_totals", {
                "p_session_id": sess_id, "p_pl": pl,
                "p_won": won,
            }).execute())
    except Exception:
        # RPC may not exist yet; fall back to a direct update
        try:
            from db import get_or_create_daily_session
            sess_id = bet.get("session_id") or get_or_create_daily_session()
            if sess_id:
                sess = safe_query(
                    lambda: get_db().table(T("sessions")).select("total_bets,wins,losses,pl")
                            .eq("id", sess_id).single().execute().data
                ) or {}
                safe_query(lambda: get_db().table(T("sessions")).update({
                    "total_bets": (sess.get("total_bets") or 0) + 1,
                    "wins":       (sess.get("wins") or 0) + (1 if result == "WIN" else 0),
                    "losses":     (sess.get("losses") or 0) + (1 if result == "LOSS" else 0),
                    "pl":         round((sess.get("pl") or 0) + pl, 2),
                    "bankroll_end": round((get_state().get("bankroll") or 0), 2),
                }).eq("id", sess_id).execute())
        except Exception:
            pass

    # Item 4: update performance_daily and performance_by_track
    try:
        today  = date.today().isoformat()
        track  = bet.get("track", "Unknown")
        code   = bet.get("code", "GREYHOUND")
        won    = result == "WIN"
        stake  = float(bet.get("stake") or 0)

        # performance_daily — upsert by date
        pd = safe_query(
            lambda: get_db().table("performance_daily").select("*")
                    .eq("date", today).limit(1).execute().data
        )
        pd = (pd or [None])[0]
        if pd:
            nb  = (pd.get("total_bets") or 0) + 1
            nw  = (pd.get("wins") or 0) + (1 if won else 0)
            npl = round((pd.get("pl") or 0) + pl, 2)
            safe_query(lambda: get_db().table("performance_daily").update({
                "total_bets":  nb,
                "wins":        nw,
                "losses":      nb - nw,
                "pl":          npl,
                "strike_rate": round(nw / nb * 100, 2) if nb else 0,
                "updated_at":  datetime.utcnow().isoformat(),
            }).eq("date", today).execute())
        else:
            safe_query(lambda: get_db().table("performance_daily").insert({
                "date": today, "code": code,
                "total_bets": 1,
                "wins":   1 if won else 0,
                "losses": 0 if won else 1,
                "pl": pl,
                "strike_rate": 100.0 if won else 0.0,
                "updated_at": datetime.utcnow().isoformat(),
            }).execute())

        # performance_by_track — upsert by track + code
        pt = safe_query(
            lambda: get_db().table("performance_by_track").select("*")
                    .eq("track", track).eq("code", code).limit(1).execute().data
        )
        pt = (pt or [None])[0]
        if pt:
            tb  = (pt.get("total_bets") or 0) + 1
            tw  = (pt.get("wins") or 0) + (1 if won else 0)
            tpl = round((pt.get("pl") or 0) + pl, 2)
            safe_query(lambda: get_db().table("performance_by_track").update({
                "total_bets":  tb,
                "wins":        tw,
                "pl":          tpl,
                "strike_rate": round(tw / tb * 100, 2) if tb else 0,
                "updated_at":  datetime.utcnow().isoformat(),
            }).eq("track", track).eq("code", code).execute())
        else:
            safe_query(lambda: get_db().table("performance_by_track").insert({
                "track": track, "code": code,
                "total_bets": 1,
                "wins":   1 if won else 0,
                "pl": pl,
                "strike_rate": 100.0 if won else 0.0,
                "updated_at": datetime.utcnow().isoformat(),
            }).execute())
    except Exception as e:
        log.warning(f"Performance table update failed: {e}")

    return jsonify({"ok": True, "pl": pl})

@app.route("/api/bets")
def api_bets_list():
    from auth import get_current_user
    from db import get_db, safe_query, T
    user = get_current_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401
    # W-02: admins see all bets; operators/viewers see only their own
    uid  = user.get("sub")
    role = user.get("role")
    if role == "admin":
        bets = safe_query(
            lambda: get_db().table(T("bet_log")).select("*")
                    .order("created_at", desc=True).limit(200).execute().data, []
        ) or []
    else:
        bets = safe_query(
            lambda: get_db().table(T("bet_log")).select("*")
                    .eq("user_id", uid)
                    .order("created_at", desc=True).limit(200).execute().data, []
        ) or []
    return jsonify(bets)


# ─────────────────────────────────────────────────────────────────
# SIMULATOR
# ─────────────────────────────────────────────────────────────────

# ── Monte Carlo engine singleton (initialised once, reused per request) ────────
_MC_ENGINE = None

def _get_mc_engine():
    """Return the Monte Carlo SimulationEngine, initialising on first call."""
    global _MC_ENGINE
    if _MC_ENGINE is None:
        try:
            from simulation.core_simulation_engine import SimulationEngine
            _MC_ENGINE = SimulationEngine()
            log.info("Monte Carlo SimulationEngine initialised")
        except Exception as exc:
            log.error(f"SimulationEngine init failed — will fall back to legacy: {exc}")
            _MC_ENGINE = False   # False = init tried and failed; None = not yet tried
    return _MC_ENGINE if _MC_ENGINE else None


def _runners_to_profiles(runners: list, race_code_str: str = "GREYHOUND") -> list:
    """
    Convert today_runners/frontend runner dicts to RunnerProfile objects.
    Derives scoring fields from available raw data with sensible defaults.
    """
    from simulation.models import RunnerProfile, RaceCode, RacePattern
    pattern_map = {
        "LEADER": RacePattern.LEADER, "FRONT": RacePattern.LEADER,
        "STALKER": RacePattern.STALKER, "RAILER": RacePattern.RAILER,
        "MIDFIELD": RacePattern.MIDFIELD, "MID": RacePattern.MIDFIELD,
        "CHASER": RacePattern.CHASER, "BACK": RacePattern.CHASER,
        "WIDE": RacePattern.WIDE, "PARKED": RacePattern.PARKED,
        "TRAILER": RacePattern.TRAILER,
    }
    speed_map = {
        "FAST": 8.0, "HIGH": 8.0, "STRONG": 7.5,
        "MODERATE": 5.5, "MED": 5.5, "MEDIUM": 5.5, "AVG": 5.5,
        "SLOW": 3.5, "LOW": 3.5, "WEAK": 3.5,
    }
    profiles = []
    for r in runners:
        if r.get("scratched"):
            continue
        box      = int(r.get("box_num") or r.get("barrier_or_box") or 1)
        odds     = float(r.get("odds") or r.get("market_odds") or 5.0)
        conf     = float(r.get("confidence") or 0.5)

        speed_raw = (r.get("early_speed") or "MODERATE").upper().split()[0]
        es_score  = speed_map.get(speed_raw, 5.5)

        style_raw = (r.get("run_style") or r.get("race_pattern") or "MIDFIELD").upper()
        pattern   = pattern_map.get(style_raw, RacePattern.MIDFIELD)

        profiles.append(RunnerProfile(
            runner_id              = str(r.get("id") or f"r{box}"),
            name                   = r.get("name") or f"Runner {box}",
            barrier_or_box         = box,
            early_speed_score      = es_score,
            start_consistency      = min(1.0, conf * 1.1),
            tactical_position_score= max(2.0, 5.0 + (es_score - 5.0) * 0.4),
            mid_race_strength      = max(2.0, 5.0 + (conf - 0.5) * 4.0),
            late_strength          = 5.0,
            stamina_score          = 5.5,
            race_pattern           = pattern,
            track_distance_suitability = float(r.get("track_distance_suitability") or 0.72),
            pressure_risk_score    = max(0.1, min(0.8, 0.5 - (conf - 0.5) * 0.6)),
            confidence_factor      = conf,
            market_odds            = max(1.01, odds),
            scratched              = False,
        ))
    return profiles


@app.route("/api/simulator/run", methods=["POST"])
def api_simulator_run():
    from auth import get_current_user
    from audit import log_event
    user = get_current_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401

    d       = request.json or {}
    runners = d.get("runners", [])
    n_runs  = min(int(d.get("runs", 1000)), 5000 if env.is_live else 50000)

    # MD-02: log every simulator run
    log_event(user["sub"], user["username"], "SIMULATION_RUN", "simulator",
              data={"runs": n_runs, "runner_count": len(runners)})

    # CF-02: attempt the real Monte Carlo engine; fall back to legacy only on error
    mc = _get_mc_engine()
    if mc and runners:
        try:
            from simulation.models import RaceMeta, RaceCode
            # Infer race code from first runner if available
            code_raw = (runners[0].get("code") or d.get("code") or "GREYHOUND").upper()
            code_map = {
                "GREYHOUND": RaceCode.GREYHOUND,
                "THOROUGHBRED": RaceCode.THOROUGHBRED,
                "HORSE": RaceCode.THOROUGHBRED,
                "HARNESS": RaceCode.HARNESS,
            }
            race_code = code_map.get(code_raw, RaceCode.GREYHOUND)
            distance  = int(d.get("distance") or d.get("distance_m") or 460)
            track     = d.get("track") or "Unknown"
            race_uid  = d.get("race_uid") or "sim-adhoc"
            condition = d.get("condition") or "GOOD"

            profiles = _runners_to_profiles(runners, code_raw)
            if not profiles:
                return jsonify({"error": "No valid runners after scratching"})

            meta  = RaceMeta(race_uid, track, race_code, distance,
                             condition=condition, n_sims=n_runs)
            guide = mc.run(meta, profiles)
            agg   = guide.aggregated

            results = [{
                "name":       s.name,
                "box_num":    s.barrier_or_box,
                "win_pct":    s.win_pct,
                "place_pct":  s.place_pct,
                "top3_pct":   s.place_pct,    # alias so frontend bar chart still works
                "avg_finish": s.avg_finish,
                "sim_edge":   round(s.sim_edge * 100, 1),
                "flags": {
                    "false_favourite": s.is_false_favourite,
                    "hidden_value":    s.is_hidden_value,
                    "best_map":        s.is_best_map,
                },
            } for s in agg.runners]

            return_payload = {
                "runs":              n_runs,
                "results":           results,
                "top":               results[0] if results else None,
                "engine":            "monte_carlo",
                "decision":          guide.decision.value,
                "confidence_score":  guide.confidence_score,
                "chaos":             agg.chaos_rating.value,
                "pace_type":         agg.pace_type,
                "collapse_risk":     agg.collapse_risk,
                "simulation_summary": guide.simulation_summary,
            }

            # Item 1: persist simulation run to simulation_log
            try:
                from db import get_db as _gdb, safe_query as _sq, T as _T
                _sq(lambda: _gdb().table(_T("simulation_log")).insert({
                    "race_uid":         race_uid if race_uid != "sim-adhoc" else None,
                    "user_id":          user.get("sub"),
                    "engine":           "monte_carlo",
                    "n_runs":           n_runs,
                    "race_code":        code_raw,
                    "track":            track,
                    "distance_m":       distance,
                    "condition":        condition,
                    "decision":         guide.decision.value,
                    "confidence_score": guide.confidence_score,
                    "chaos_rating":     agg.chaos_rating.value,
                    "pace_type":        agg.pace_type,
                    "top_runner":       results[0]["name"] if results else None,
                    "top_win_pct":      results[0]["win_pct"] if results else None,
                    "results_json":     results,
                    "simulation_summary": guide.simulation_summary,
                    "created_at":       datetime.utcnow().isoformat(),
                }).execute())
            except Exception as _e:
                log.warning(f"simulation_log write failed: {_e}")

            return jsonify(return_payload)
        except Exception as exc:
            log.error(f"Monte Carlo simulation failed, falling back to legacy: {exc}")

    # Legacy fallback (CF-02: only reached if MC engine unavailable or init failed)
    return jsonify(_run_simulation_legacy(runners, n_runs))


def _compute_jump_ts(jump_time_str: str) -> float:
    """
    Convert 'HH:MM' or 'HH:MM:SS' (AEST/AEDT local time) to Unix timestamp.
    M-07: Uses zoneinfo for correct AEST (UTC+10) / AEDT (UTC+11) handling.
    zoneinfo is stdlib from Python 3.9+.
    """
    import time as _time
    if not jump_time_str:
        return _time.time() + 3600
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt, date as _date
        parts = jump_time_str.strip().split(":")
        hour, minute = int(parts[0]), int(parts[1])
        today = _date.today()
        # Build a timezone-aware datetime in AEST/AEDT (auto-selects correct offset)
        aest = ZoneInfo("Australia/Melbourne")
        local_dt = _dt(today.year, today.month, today.day, hour, minute, 0, tzinfo=aest)
        return local_dt.timestamp()
    except ImportError:
        # zoneinfo not available — safe fallback using fixed AEST offset UTC+10
        try:
            parts = jump_time_str.strip().split(":")
            hour, minute = int(parts[0]), int(parts[1])
            now = datetime.utcnow()
            target = now.replace(hour=max(0, hour - 10), minute=minute, second=0, microsecond=0)
            return target.timestamp()
        except Exception:
            return __import__('time').time() + 1800
    except Exception:
        return _time.time() + 1800

def _run_simulation_legacy(runners, n_runs):
    """
    Legacy Gaussian-noise fallback. Only used when the Monte Carlo engine
    is unavailable (init error). Not the primary simulation path (CF-02).
    """
    import random
    if not runners: return {"error": "No runners"}
    confidences = [max(0.001, float(r.get("confidence") or 1/len(runners))) for r in runners]
    total = sum(confidences); probs = [c/total for c in confidences]
    win_counts = [0]*len(runners); top3_counts = [0]*len(runners)
    for _ in range(n_runs):
        noise = [max(0.001, random.gauss(p, p*0.3)) for p in probs]
        nt = sum(noise); norm = [n/nt for n in noise]
        finish = sorted(range(len(runners)), key=lambda i: -norm[i])
        win_counts[finish[0]] += 1
        for pos in range(min(3,len(finish))): top3_counts[finish[pos]] += 1
    results = sorted([{
        "name":r.get("name",f"#{i+1}"),"box_num":r.get("box_num",i+1),
        "win_pct":round(win_counts[i]/n_runs*100,1),
        "top3_pct":round(top3_counts[i]/n_runs*100,1),
        "true_prob":round(probs[i]*100,1),
    } for i,r in enumerate(runners)], key=lambda x:-x["win_pct"])
    return {"runs":n_runs,"results":results,"top":results[0] if results else None}


# ─────────────────────────────────────────────────────────────────
# STATE & SETTINGS
# ─────────────────────────────────────────────────────────────────
@app.route("/api/state", methods=["GET","POST"])
def api_state():
    from auth import get_current_user
    from db import get_state, update_state
    user = get_current_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    if request.method == "POST":
        if user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
        from audit import log_settings
        d = request.json or {}
        update_state(**d)
        log_settings(user["sub"], user["username"], d)
    state = get_state()
    return jsonify({**state, "env_mode": env.mode})

@app.route("/api/bankroll/set", methods=["POST"])
def set_bankroll():
    from auth import get_current_user
    from audit import log_bankroll_reset
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    d = request.json or {}
    val = d.get("bankroll")
    if not val or float(val) <= 0: return jsonify({"ok":False,"error":"Invalid amount"}), 400
    from db import update_state
    update_state(bankroll=round(float(val),2))
    log_bankroll_reset(user["sub"], user["username"], float(val))
    return jsonify({"ok":True,"bankroll":round(float(val),2)})

@app.route("/api/session/pl")
def session_pl():
    from auth import get_current_user
    user = get_current_user()
    if not user: return jsonify({"error": "Unauthorized"}), 401
    from db import get_session_pl
    # W-01: admins see global P/L; operators/viewers see only their own
    uid = None if user.get("role") == "admin" else user.get("sub")
    return jsonify(get_session_pl(user_id=uid))


# ─────────────────────────────────────────────────────────────────
# AUDIT
# ─────────────────────────────────────────────────────────────────
@app.route("/api/audit/log")
def api_audit_log():
    from auth import get_current_user
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    from audit import get_recent_logs, get_audit_summary
    return jsonify({
        "logs":    get_recent_logs(limit=int(request.args.get("limit",100)), event_type=request.args.get("type")),
        "summary": get_audit_summary(),
    })


# ─────────────────────────────────────────────────────────────────
# AI LEARNING (admin only)
# ─────────────────────────────────────────────────────────────────
@app.route("/api/learning/summary")
def learning_summary():
    from auth import get_current_user
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    try:
        from learning_engine import get_epr_summary, calibration_report, gpil_review, detect_system_streak, shadow_mode_stats
        return jsonify({"epr":get_epr_summary(),"calibration":calibration_report(),"gpil":gpil_review(),"streak":detect_system_streak(),"shadow":shadow_mode_stats()})
    except Exception as e:
        return jsonify({"error":str(e)})

@app.route("/api/learning/aeee")
def aeee_suggestions():
    from auth import get_current_user
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    try:
        from learning_engine import aeee_review
        return jsonify({"suggestions":aeee_review()})
    except Exception as e:
        return jsonify({"error":str(e)})

@app.route("/api/learning/promote", methods=["POST"])
def promote_aeee():
    from auth import get_current_user
    from audit import log_event
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    try:
        from learning_engine import promote_aeee as _promote
        sid = (request.json or {}).get("id")
        _promote(sid)
        log_event(user["sub"], user["username"], "LEARNING_PROMOTE", f"learning/{sid}")
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/learning/run", methods=["POST"])
def run_learning():
    from auth import get_current_user
    from audit import log_event
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    d = request.json or {}
    days = int(d.get("days",7))
    log_event(user["sub"], user["username"], "LEARNING_RUN", "learning", data={"days":days})
    try:
        from learning_engine import run_batch_review
        return jsonify({"ok":True,"result":run_batch_review(days)})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})


# ─────────────────────────────────────────────────────────────────
# SYSTEM (V7 kept, now with auth)
# ─────────────────────────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    from auth import get_current_user
    user = get_current_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    from db import get_state, get_db, safe_query, T
    from cache import make_key, is_duplicate, cache_get, cache_set
    data = request.json or {}
    session_id = data.get("session_id","default")
    user_msg = data.get("message","").strip()
    if not user_msg: return jsonify({"response":"Empty message.","ok":False})
    state = get_state()
    code  = state.get("active_code","GREYHOUND")
    anchor = state.get("time_anchor","")
    cache_key = make_key(user_msg, code)
    if is_duplicate(cache_key):
        cached = cache_get(cache_key)
        if cached: return jsonify({"response":cached,"ok":True,"cached":True})
    if needs_analysis(user_msg):
        cached = cache_get(cache_key)
        if cached: return jsonify({"response":cached,"ok":True,"cached":True})
    if is_flask_cmd(user_msg):
        return jsonify({"response":_handle_flask_cmd(user_msg, state, code),"ok":True,"source":"flask"})
    response = None
    if needs_analysis(user_msg):
        response = _try_pre_scored_analysis(user_msg, state, code, anchor)
    if not response:
        history = _get_history(session_id, 4 if needs_analysis(user_msg) else 2)
        history.append({"role":"user","content":f"ANCHOR:{anchor} Code:{code}\n{user_msg}"})
        response = call_claude(history, model=get_model(user_msg), max_tokens=get_max_tokens(user_msg))
    if needs_analysis(user_msg) and response and not response.startswith("Error"):
        cache_set(cache_key, response, ttl=90)
    _save_history(session_id, user_msg, response)
    return jsonify({"response":response,"ok":True})

def _try_pre_scored_analysis(msg, state, code, anchor):
    try:
        from data_engine import get_next_race, get_race_with_runners
        from scorer import score_race
        from packet_builder import build_packet, is_worth_sending_to_claude
        lower = msg.lower()
        race = get_next_race(anchor) if "next race" in lower or "refresh" in lower else None
        if not race: return None
        race, runners = get_race_with_runners(race["track"], race["race_num"])
        if not race: return None
        scored = score_race(race, runners or [], race.get("track",""))
        if not is_worth_sending_to_claude(scored): return f"PASS - {scored.get('pass_reason','')}"
        packet = build_packet(race, scored, runners or [], bankroll=state.get("bankroll",1000),
                              bank_mode=state.get("bank_mode","STANDARD"), anchor_time=anchor)
        return call_claude([{"role":"user","content":packet}], model=CLAUDE_SONNET, max_tokens=800)
    except Exception as e:
        log.error(f"Pre-scored analysis failed: {e}")
        return None

def _get_history(session_id, limit=4):
    try:
        from db import get_db, safe_query, T
        rows = safe_query(
            lambda: get_db().table(T("chat_history")).select("role,content")
                    .eq("session_id",session_id).order("id",desc=True).limit(limit).execute().data, []
        ) or []
        rows.reverse()
        return [{"role":r["role"],"content":r["content"]} for r in rows]
    except Exception: return []

def _save_history(session_id, user_msg, response):
    try:
        from db import get_db, T
        db = get_db()
        db.table(T("chat_history")).insert({"session_id":session_id,"role":"user","content":user_msg}).execute()
        db.table(T("chat_history")).insert({"session_id":session_id,"role":"assistant","content":response}).execute()
    except Exception: pass

def _handle_flask_cmd(msg, state, code):
    lower = msg.lower()
    if "status" in lower:
        from db import get_session_pl
        s = get_session_pl()
        return f"STATUS:{state.get('sys_state','STABLE')} | Code:{code} | Bank:${state.get('bankroll',0):.0f} | P/L:${s['total']:.2f} | ENV:{env.mode}"
    if "board" in lower:
        try:
            from data_engine import get_board
            races = get_board(10)
            if not races: return "Board empty."
            return "\n".join(f"#{i+1} {r['track'].upper()} R{r['race_num']} {r.get('jump_time','?')}" for i,r in enumerate(races))
        except Exception: return "Board unavailable."
    return f"Status:{state.get('sys_state','STABLE')} | Code:{code} | ENV:{env.mode}"

@app.route("/api/sweep", methods=["POST"])
def manual_sweep():
    from auth import get_current_user
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    try:
        from data_engine import full_sweep
        return jsonify(full_sweep())
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/scheduler/status")
def scheduler_status():
    from auth import get_current_user
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    try:
        from scheduler import get_status
        from safety import circuit_breaker
        s = get_status(); s["circuit_breaker"] = circuit_breaker.status()
        return jsonify({**s, "env_mode": env.mode})
    except Exception as e:
        return jsonify({"error":str(e)})

@app.route("/api/cache/clear", methods=["POST"])
def cache_clear_api():
    from auth import get_current_user
    from audit import log_event
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    from cache import cache_clear
    cache_clear()
    log_event(user["sub"], user["username"], "CACHE_CLEAR", "cache")
    return jsonify({"ok":True})

@app.route("/api/performance/chart")
def perf_chart():
    from auth import get_current_user
    from db import get_db, safe_query, T
    user = get_current_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    rows = safe_query(lambda: get_db().table(T("bet_log")).select("date,pl").neq("result","PENDING").order("date").execute().data, []) or []
    by_date = {}
    for r in rows:
        d = r.get("date","")
        if d not in by_date: by_date[d] = {"pl":0,"bets":0}
        by_date[d]["pl"] += r.get("pl") or 0; by_date[d]["bets"] += 1
    cum, total = [], 0
    for d in sorted(by_date.keys())[-30:]:
        total += by_date[d]["pl"]
        cum.append({"date":d,"pl":round(total,2),"bets":by_date[d]["bets"]})
    return jsonify(cum)

@app.route("/api/export/csv")
def export_csv():
    from auth import get_current_user
    from db import get_db, safe_query, T
    user = get_current_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    import csv, io
    bets = safe_query(lambda: get_db().table(T("bet_log")).select("*").order("created_at",desc=True).limit(500).execute().data, []) or []
    if not bets: return jsonify({"error":"No bets"})
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=bets[0].keys())
    writer.writeheader(); writer.writerows(bets)
    from flask import Response
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=dpv8_bets.csv"})

# ─────────────────────────────────────────────────────────────────
# V7 RETAINED ROUTES — manual override, notes, source health, cache stats
# ─────────────────────────────────────────────────────────────────
@app.route("/api/manual/override", methods=["POST"])
def manual_override():
    """Manual race override console (V7 Feature 33)."""
    from auth import get_current_user
    from db import get_db, safe_query, T
    from audit import log_event
    user = get_current_user()
    if not user or user.get("role") not in ("admin","operator"):
        return jsonify({"error":"Forbidden"}), 403
    d = request.json or {}
    action   = d.get("action")
    race_uid = d.get("race_uid")
    if not race_uid or not action:
        return jsonify({"ok":False,"error":"Missing race_uid or action"}), 400

    if action == "force_rescore":
        from cache import cache_clear
        cache_clear(race_uid)
        log_event(user["sub"],user["username"],"RACE_LOCK",f"races/{race_uid}",{"action":action})
        return jsonify({"ok":True,"message":"Cache cleared, rescore on next request"})
    elif action == "lock_race":
        safe_query(lambda: get_db().table(T("today_races")).update({"lifecycle_state":"locked"}).eq("race_uid",race_uid).execute())
        log_event(user["sub"],user["username"],"RACE_LOCK",f"races/{race_uid}",{"action":action})
        return jsonify({"ok":True,"message":"Race locked"})
    elif action == "set_scratch":
        box = d.get("box_num")
        if box:
            safe_query(lambda: get_db().table(T("today_runners")).update({"scratched":True,"scratch_timing":"manual"}).eq("race_uid",race_uid).eq("box_num",box).execute())
            log_event(user["sub"],user["username"],"RACE_SCRATCH",f"races/{race_uid}",{"box":box})
            return jsonify({"ok":True,"message":f"Box {box} scratched"})
    elif action == "set_jump_time":
        jt = d.get("jump_time")
        if jt:
            safe_query(lambda: get_db().table(T("today_races")).update({"jump_time":jt}).eq("race_uid",race_uid).execute())
            return jsonify({"ok":True,"message":f"Jump time set to {jt}"})
    return jsonify({"ok":False,"error":"Unknown action"}), 400


@app.route("/api/notes", methods=["POST"])
def add_note():
    """Human note layer (V7 Feature 34)."""
    from auth import get_current_user
    from db import get_db, safe_query, T
    user = get_current_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    d = request.json or {}
    safe_query(lambda: get_db().table(T("activity_log")).insert({
        "session_id": d.get("session_id","default"),
        "event_type": "USER_NOTE",
        "description": d.get("note",""),
        "data": {"race_uid":d.get("race_uid"),"runner":d.get("runner"),"track":d.get("track")},
        "created_at": datetime.utcnow().isoformat(),
    }).execute())
    return jsonify({"ok":True})


@app.route("/api/source/health")
def source_health():
    """Data source health check (V7)."""
    from auth import get_current_user
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    try:
        from data_engine import get_source_health
        return jsonify(get_source_health())
    except Exception as e:
        return jsonify({"error":str(e)})


@app.route("/api/cache/stats")
def cache_stats_api():
    """Cache statistics (V7)."""
    from auth import get_current_user
    user = get_current_user()
    if not user or user.get("role") != "admin": return jsonify({"error":"Forbidden"}), 403
    try:
        from cache import cache_stats, get_rate_stats
        return jsonify({"cache":cache_stats(),"rate_limits":get_rate_stats()})
    except Exception as e:
        return jsonify({"error":str(e)})


# ─────────────────────────────────────────────────────────────────
# TEST-MODE ONLY ROUTES  (/api/test/*)
# All raise EnvViolation if DP_ENV != TEST
# ─────────────────────────────────────────────────────────────────
@app.route("/api/test/status")
def test_status():
    """Health check that confirms TEST mode is active."""
    env.require_test("GET /api/test/status")
    return jsonify({"mode": "TEST", "ok": True, "table_prefix": "test_"})

@app.route("/api/test/seed", methods=["POST"])
def test_seed():
    """Seed fake races into test tables for UI/stress testing."""
    env.require_test("POST /api/test/seed")
    from auth import get_current_user
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error":"Forbidden"}), 403
    from audit import log_event
    d = request.json or {}
    count = min(int(d.get("count", 10)), 100)
    races = _demo_races(n=count)
    from db import get_db, safe_query, T
    inserted = 0
    for r in races:
        rec = {k:v for k,v in r.items() if k not in ("runners","signal_data","ai_comment")}
        rec["date"] = date.today().isoformat()
        result = safe_query(lambda: get_db().table(T("today_races")).upsert(rec, on_conflict="race_uid").execute())
        if result: inserted += 1
    log_event(user["sub"], user["username"], "STRESS_TEST_RUN", "test/seed", data={"count":count,"inserted":inserted})
    return jsonify({"ok":True,"seeded":inserted,"mode":"TEST"})

@app.route("/api/test/purge", methods=["POST"])
def test_purge():
    """
    Delete ALL data from test tables.
    LIVE mode: EnvViolation
    TEST mode: requires admin + explicit confirmation
    """
    env.require_test("POST /api/test/purge")
    from auth import get_current_user
    from audit import log_event
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error":"Forbidden"}), 403
    d = request.json or {}
    if d.get("confirm") != "PURGE ALL TEST DATA":
        return jsonify({"error": "Must confirm with 'PURGE ALL TEST DATA'"}), 400
    from db import get_db, safe_query, T
    purged = []
    tables = ["bet_log","today_races","today_runners","signals","chat_history","sessions","training_logs"]
    for tbl in tables:
        result = safe_query(lambda: get_db().table(T(tbl)).delete().neq("id","00000000-0000-0000-0000-000000000000").execute())
        if result: purged.append(T(tbl))
    log_event(user["sub"], user["username"], "TEST_PURGE", "test/purge",
              data={"tables_purged": purged}, severity="WARN")
    return jsonify({"ok":True,"purged":purged,"mode":"TEST"})

@app.route("/api/test/stress", methods=["POST"])
def test_stress():
    """Run a stress simulation: N fake races × M simulations each."""
    env.require_test("POST /api/test/stress")
    env.guard_stress_test()  # redundant but explicit
    from auth import get_current_user
    from audit import log_event
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error":"Forbidden"}), 403
    d = request.json or {}
    n_races = min(int(d.get("races", 5)), 50)
    n_sims  = min(int(d.get("sims", 500)), 10000)
    results = []
    races = _demo_races(n=n_races)
    for race in races:
        runners = race.get("runners", [])
        sim = _run_simulation(runners, n_sims)
        results.append({"race_uid": race["race_uid"], "track": race["track"],
                        "race_num": race["race_num"], "simulation": sim})
    log_event(user["sub"], user["username"], "STRESS_TEST_RUN", "test/stress",
              data={"n_races": n_races, "n_sims": n_sims})
    return jsonify({"ok":True,"mode":"TEST","races_simulated":len(results),"results":results})

@app.route("/api/test/fake-signal", methods=["POST"])
def test_fake_signal():
    """Generate and optionally save a fake signal for a given race_uid."""
    env.require_test("POST /api/test/fake-signal")
    from auth import get_current_user
    user = get_current_user()
    if not user: return jsonify({"error":"Unauthorized"}), 401
    from signals import demo_signal, save_signal
    d = request.json or {}
    race_num = int(d.get("race_num", 0))
    race_uid = d.get("race_uid", f"test-fake-{race_num}")
    sig = demo_signal(race_num)
    if d.get("save", False):
        save_signal(race_uid, sig)
    return jsonify({**sig, "race_uid": race_uid})


# ─────────────────────────────────────────────────────────────────
# DEMO DATA (TEST mode only)
# ─────────────────────────────────────────────────────────────────
def _demo_races(n: int = 8):
    """Generate fake race data. MUST only be called in TEST mode or on startup fallback."""
    import random
    TRACKS   = ["Sandown","Meadows","Cannington","Angle Park","Ballarat","Bendigo","Horsham","Mandurah"]
    SIGNALS  = ["SNIPER","VALUE","GEM","WATCH","VALUE","SNIPER","RISK","NO_BET"]
    ALERT    = {"SNIPER":"HOT","VALUE":"HIGH","GEM":"MEDIUM","WATCH":"LOW","RISK":"NONE","NO_BET":"NONE"}
    DISTS    = ["380m","460m","515m","595m","715m"]
    GRADES   = ["Grade 5","Grade 6","Grade 7","Maiden","Mixed Grade"]
    NAMES    = ["Dark Phoenix","Thunderstrike","Night Raider","Blaze Runner","Iron Will",
                "Storm Chaser","Shadow Bolt","Neon Flash","Crimson Edge","Steel Fury"]
    now = int(time.time())
    races = []
    for i in range(n):
        r  = random.Random(i + 42)
        track = TRACKS[i % len(TRACKS)]
        sig   = SIGNALS[i % len(SIGNALS)]
        conf  = round(0.45 + r.random() * 0.48, 2)
        ev    = round(-0.04 + r.random() * 0.30, 3)
        runners = [{
            "name": NAMES[(i*6+j) % len(NAMES)],
            "box_num": j+1,
            "odds": round(2 + r.random()*10, 1),
            "confidence": round(0.3 + r.random()*0.65, 2),
            "ev": round(-0.05 + r.random()*0.28, 3),
            "run_style": ["LEADER","RAILER","CHASER","WIDE"][j%4],
            "early_speed_rank": ["FAST","MID","SLOW"][j%3],
            "box_score": ["STRONG","NEUTRAL","WEAK"][j%3],
            "form": [r.randint(1,8) for _ in range(5)],
            "career_wins": f"{r.randint(1,12)}/{r.randint(10,30)}",
            "best_time": f"2{r.randint(5,6)}.{r.randint(60,99)}",
            "risk_flags": ["TRAFFIC"] if r.random() > 0.75 else [],
            "last_5_pos": [r.randint(1,8) for _ in range(5)],
        } for j in range(6)]
        races.append({
            "id": f"demo-{i}",
            "race_uid": f"demo-{date.today()}-{track.lower()}-r{i+1}",
            "track": track, "race_num": i+1,
            "distance": DISTS[i % len(DISTS)],
            "grade": GRADES[i % len(GRADES)],
            "jump_time": f"{10+i//2}:{['00','15','30','45'][i%4]}",
            "jump_ts": now + (i+1)*7*60,
            "status": "upcoming",
            "runners": runners,
            "ai_comment": f"{runners[0]['name']} maps perfectly from box {runners[0]['box_num']} with {runners[0].get('run_style','LEADER').lower()} style. {runners[1]['name']} is the danger from box {runners[1]['box_num']}. Pace map projects {['a contested pace race','a searching tempo','a well-structured tempo'][i%3]}.",
            "signal_data": {
                "signal": sig, "confidence": conf, "ev": ev,
                "alert_level": ALERT[sig], "hot_bet": sig == "SNIPER",
                "top_runner": runners[0]["name"], "top_odds": runners[0]["odds"],
                "risk_flags": [], "env_mode": "TEST",
            }
        })
    return races

def _demo_race_analysis():
    """Fake full race analysis for TEST mode fallback."""
    horses = [
        {"name":"Dark Phoenix","box_num":1,"odds":3.2,"confidence":0.78,"ev":0.18,
         "run_style":"LEADER","early_speed_rank":"FAST","box_score":"STRONG",
         "form":[1,2,1,3,2],"career_wins":"8/22","best_time":"25.88","risk_flags":[],
         "last_5_pos":[2,1,2,3,1],"barrier_wins":0.45},
        {"name":"Thunderstrike","box_num":2,"odds":4.5,"confidence":0.65,"ev":0.12,
         "run_style":"RAILER","early_speed_rank":"FAST","box_score":"STRONG",
         "form":[2,1,3,1,4],"career_wins":"7/19","best_time":"26.02","risk_flags":["TRAFFIC"],
         "last_5_pos":[1,3,2,1,2],"barrier_wins":0.38},
        {"name":"Night Raider","box_num":3,"odds":6.0,"confidence":0.52,"ev":0.05,
         "run_style":"CHASER","early_speed_rank":"MID","box_score":"NEUTRAL",
         "form":[3,4,2,5,1],"career_wins":"5/18","best_time":"26.15","risk_flags":["WIDE_DRAW"],
         "last_5_pos":[3,2,4,2,3],"barrier_wins":0.22},
    ]
    return {
        "race":{"track":"Sandown","race_num":1,"distance":"460m","grade":"Grade 5",
                "jump_time":"10:15","race_uid":"demo-r1","env_mode":"TEST"},
        "runners": horses,
        "scored":{"confidence":0.78,"ev":0.18,"chaos_score":3,"collapse_risk":"LOW",
                  "separation":"CLEAR","pace_type":"FAST","top_runner":{"name":"Dark Phoenix","box_num":1,"odds":3.2}},
        "signal":{"signal":"SNIPER","confidence":0.78,"ev":0.18,"alert_level":"HOT",
                  "hot_bet":True,"top_runner":"Dark Phoenix","top_odds":3.2,"risk_flags":[],"env_mode":"TEST"},
    }


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
