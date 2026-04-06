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
    from api.formfav_routes import formfav_bp
    app.register_blueprint(health_bp)
    app.register_blueprint(race_bp)
    app.register_blueprint(board_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(prediction_bp)
    app.register_blueprint(market_bp)
    app.register_blueprint(external_bp)
    app.register_blueprint(formfav_bp)
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
    # CF-07: core.shadow_learning (core/ package) does not exist.
    # shadow_active is always False until a real shadow-learning module is wired in.
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
            from users import register_session, _record_activity
            ttl = int(os.environ.get("SESSION_TIMEOUT_MIN", "480")) * 60
            register_session(user["id"], jti, ip, request.headers.get("User-Agent", ""), ttl)
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
            secure=env.is_live,
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


@app.route("/api/debug/formfav", methods=["GET"])
def api_debug_formfav():
    """
    GET /api/debug/formfav
    Expose the full OddsPro → FormFav pipeline state for debugging.

    Reads the latest persisted snapshot from formfav_debug_stats (DB) so
    counters always match what the logs show, even after process restarts.
    Falls back to the live in-memory state when no DB snapshot exists yet.

    Returns real runtime counters and the last 20 processed races with their
    pipeline status (discovered → domestic filter → FormFav eligibility → call result).
    """
    try:
        import pipeline_state
        mem_state = pipeline_state.get_state()

        # Prefer the DB snapshot — it is written after every pipeline run and
        # reflects the REAL execution state regardless of which worker/thread
        # is handling this request.
        db_row: dict = {}
        try:
            from database import get_latest_formfav_debug_stats
            db_row = get_latest_formfav_debug_stats() or {}
        except Exception as db_err:
            log.warning(f"/api/debug/formfav: could not read DB snapshot: {db_err}")

        # Counter values: prefer DB snapshot; fall back to in-memory.
        def _val(key: str) -> int:
            return int(db_row.get(key) or mem_state.get(key) or 0)

        return jsonify({
            "ok": True,
            "total_races_discovered":       _val("total_races_discovered"),
            "total_domestic_races":         _val("total_domestic_races"),
            "total_international_filtered": _val("total_international_filtered"),
            "total_formfav_eligible":       _val("total_formfav_eligible"),
            "total_formfav_called":         _val("total_formfav_called"),
            "total_formfav_success":        _val("total_formfav_success"),
            "total_formfav_failed":         _val("total_formfav_failed"),
            # recent_races comes from in-memory (not stored in DB)
            "recent_races":                 mem_state.get("recent_races", []),
            "last_reset":                   mem_state.get("last_reset"),
            "snapshot_recorded_at":         db_row.get("recorded_at"),
            "counter_source":               "db" if db_row else "memory",
        })
    except Exception as e:
        log.exception(f"/api/debug/formfav failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve pipeline debug state"}), 500

# ------------------------------------------------------------
# SMOKE TEST
# ------------------------------------------------------------
@app.route("/api/smoke-test", methods=["GET"])
def run_smoke_test():
    if os.environ.get("DP_ENV") != "TEST":
        return jsonify({"status": "error", "message": "DP_ENV must be TEST"}), 400

    try:
        from smoke_test import run_all_tests
        result = run_all_tests()
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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
