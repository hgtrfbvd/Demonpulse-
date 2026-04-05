import os
import logging
from flask import Flask, jsonify, request, render_template, redirect, url_for

from env import env, EnvViolation, env_violation_response

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DemonPulse] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static", static_url_path="/static")
app.secret_key = os.environ.get("FLASK_SECRET", "demonpulse-dev-secret-change-me")

# Register API blueprints at module load time (always, regardless of startup mode)
try:
    from api.health_routes import health_bp
    from api.race_routes import race_bp
    from api.board_routes import board_bp
    from api.admin_routes import admin_bp
    from api.prediction_routes import prediction_bp
    from api.market_routes import market_bp
    from api.external_routes import external_bp
    app.register_blueprint(health_bp)
    app.register_blueprint(race_bp)
    app.register_blueprint(board_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(prediction_bp)
    app.register_blueprint(market_bp)
    app.register_blueprint(external_bp)
    log.info("API blueprints registered")
except Exception as _bp_err:
    log.warning(f"Blueprint registration failed: {_bp_err}")


# ------------------------------------------------------------
# STARTUP
# ------------------------------------------------------------
_started = False


def startup():
    global _started
    if _started:
        return

    try:
        from scheduler import start_scheduler
        start_scheduler()
        log.info("Scheduler started")
    except Exception as e:
        log.warning(f"Scheduler start skipped/failed: {e}")

    try:
        from auth import bootstrap_admin
        bootstrap_admin()
        log.info("Auth bootstrap complete")
    except Exception as e:
        log.warning(f"Auth bootstrap skipped/failed: {e}")

    _started = True
    log.info(f"DemonPulse startup complete in {env.mode} mode")


if os.environ.get("RUN_MAIN_STARTUP", "1") == "1":
    startup()


# ------------------------------------------------------------
# GLOBAL ERROR HANDLERS
# ------------------------------------------------------------
@app.errorhandler(EnvViolation)
def handle_env_violation(exc):
    return env_violation_response(exc)


@app.errorhandler(404)
def handle_404(_exc):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Not found"}), 404
    return render_template("home.html")


@app.errorhandler(500)
def handle_500(exc):
    log.exception(f"Unhandled server error: {exc}")
    return jsonify({"ok": False, "error": "Internal server error"}), 500


# ------------------------------------------------------------
# PAGE ROUTES
# ------------------------------------------------------------
@app.route("/")
def root():
    return redirect(url_for("page_home"))


@app.route("/home")
def page_home():
    return render_template("home.html")


@app.route("/live")
def page_live():
    return render_template("live.html")


@app.route("/simulator")
def page_simulator():
    return render_template("simulator.html")


@app.route("/betting")
def page_betting():
    return render_template("betting.html")


@app.route("/reports")
def page_reports():
    return render_template("reports.html")


@app.route("/learning")
def page_learning():
    return render_template("learning.html")


@app.route("/backtesting")
def page_backtesting():
    return render_template("backtesting.html")


@app.route("/settings")
def page_settings():
    return render_template("settings.html")


# ------------------------------------------------------------
# SYSTEM STATUS
# ------------------------------------------------------------
@app.route("/api/system/status")
def api_system_status():
    shadow_active = False
    try:
        from core.shadow_learning import get_shadow_status
        s = get_shadow_status() or {}
        shadow_active = bool(s.get("active"))
    except Exception:
        shadow_active = False

    return jsonify({
        "ok": True,
        "env": env.mode,
        "shadow_active": shadow_active,
    })


@app.route("/api/env")
def api_env():
    return jsonify(env.info())


# ------------------------------------------------------------
# AUTH
# ------------------------------------------------------------
@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    try:
        from auth import (
            get_user_by_username,
            check_password,
            generate_token,
            check_rate_limit,
            reset_rate_limit,
        )
    except Exception as e:
        log.exception(f"Auth module import failed: {e}")
        return jsonify({"ok": False, "error": "Authentication system unavailable"}), 500

    ip = request.remote_addr or "unknown"

    if not check_rate_limit(ip):
        return jsonify({"ok": False, "error": "Too many attempts. Wait 5 minutes."}), 429

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password required"}), 400

    try:
        user = get_user_by_username(username)
        if not user or not check_password(password, user.get("password_hash", "")):
            return jsonify({"ok": False, "error": "Invalid credentials"}), 401

        if not user.get("active", True):
            return jsonify({"ok": False, "error": "Account disabled"}), 403

        token, jti = generate_token(user["id"], user["username"], user["role"])
        reset_rate_limit(ip)

        try:
            from users import register_session, record_login, _record_activity
            ttl = int(os.environ.get("SESSION_TIMEOUT_MIN", "480")) * 60
            register_session(user["id"], jti, ip, request.headers.get("User-Agent", ""), ttl)
            record_login(user["id"], ip)
            _record_activity(user["id"], "LOGIN", {"ip": ip})
        except Exception as e:
            log.warning(f"Session/activity logging skipped: {e}")

        response = jsonify({
            "ok": True,
            "token": token,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
            },
            "env": env.mode,
        })

        response.set_cookie(
            "dp_token",
            token,
            httponly=True,
            samesite="Lax",
            secure=False,
            max_age=int(os.environ.get("SESSION_TIMEOUT_MIN", "480")) * 60,
        )
        return response

    except Exception as e:
        log.exception(f"Login failed unexpectedly: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    try:
        from auth import get_current_user, ROLE_PERMISSIONS
        user = get_current_user()
        if not user:
            return jsonify({"ok": False, "error": "Not authenticated"}), 401

        return jsonify({
            "ok": True,
            "id": user.get("sub"),
            "username": user.get("username"),
            "role": user.get("role"),
            "permissions": list(ROLE_PERMISSIONS.get(user.get("role", "viewer"), set())),
            "env": env.mode,
        })
    except Exception as e:
        log.exception(f"/api/auth/me failed: {e}")
        return jsonify({"ok": False, "error": "Authentication system unavailable"}), 500


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    response = jsonify({"ok": True})
    response.delete_cookie("dp_token")
    return response


# ------------------------------------------------------------
# DEBUG ROUTES
# ------------------------------------------------------------
@app.route("/api/debug/thedogs-meetings")
def api_debug_thedogs_meetings():
    try:
        from connectors.thedogs_connector import TheDogsConnector
        from datetime import date

        conn = TheDogsConnector()
        items = conn.fetch_meetings(date.today().isoformat()) or []

        return jsonify({
            "ok": True,
            "count": len(items),
            "items": [item.__dict__ if hasattr(item, "__dict__") else item for item in items],
        })
    except Exception as e:
        log.exception(f"/api/debug/thedogs-meetings failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/debug/thedogs-races")
def api_debug_thedogs_races():
    try:
        from connectors.thedogs_connector import TheDogsConnector
        from datetime import date

        conn = TheDogsConnector()
        meetings = conn.fetch_meetings(date.today().isoformat()) or []
        if not meetings:
            return jsonify({"ok": True, "count": 0, "items": [], "note": "no meetings"})

        first = meetings[0]
        races = conn.fetch_meeting_races(first) or []

        return jsonify({
            "ok": True,
            "meeting": first.__dict__ if hasattr(first, "__dict__") else first,
            "count": len(races),
            "items": [item.__dict__ if hasattr(item, "__dict__") else item for item in races],
        })
    except Exception as e:
        log.exception(f"/api/debug/thedogs-races failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ------------------------------------------------------------
# HOME BOARD
# ------------------------------------------------------------
@app.route("/api/home/board", methods=["GET"])
def api_home_board():
    """
    Home board endpoint — delegates to board_builder which uses OddsPro data.
    NTJ computed from stored jump_time (no external scraping).
    """
    try:
        from board_builder import get_board_for_today
        result = get_board_for_today()
        return jsonify(result)
    except Exception as e:
        log.warning(f"/api/home/board fallback used: {e}")
        return jsonify({"ok": True, "items": []})

@app.route("/api/debug/thedogs-fetch")
def api_debug_thedogs_fetch():
    try:
        from connectors.thedogs_connector import TheDogsConnector
        conn = TheDogsConnector()
        return jsonify({"ok": True, "result": conn.debug_racecards_fetch()})
    except Exception as e:
        log.exception(f"/api/debug/thedogs-fetch failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/debug/thedogs-scratchings-fetch")
def api_debug_thedogs_scratchings_fetch():
    try:
        from connectors.thedogs_connector import TheDogsConnector
        conn = TheDogsConnector()
        return jsonify({"ok": True, "result": conn.debug_scratchings_fetch()})
    except Exception as e:
        log.exception(f"/api/debug/thedogs-scratchings-fetch failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------------------------------------------
# HEALTH
# ------------------------------------------------------------
@app.route("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "app": "DemonPulse",
        "mode": env.mode,
    })


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(
        debug=False,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
    )
