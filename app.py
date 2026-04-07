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


@app.route("/race")
def page_race_view():
    return render_template("race_view.html")


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


@app.route("/api/live/race/<race_uid>", methods=["GET"])
def api_live_race(race_uid: str):
    """
    Live race endpoint — returns race metadata, runners, FormFav enrichment,
    and any available signal/analysis data for a given race_uid.
    Used by live.html, race_view.html, and simulator.html.
    """
    try:
        from database import get_race, get_runners_for_race, get_formfav_enrichments_for_date
        from race_status import compute_ntj
        from datetime import date as _date

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        ntj = compute_ntj(race.get("jump_time"), race.get("date"))
        race_out = {**race, **ntj}

        runners = get_runners_for_race(race_uid)

        # Merge FormFav runner enrichment data
        try:
            from database import get_formfav_runner_enrichments
            ff_runner_rows = get_formfav_runner_enrichments(race_uid)
            ff_runner_map = {}
            for ff_r in ff_runner_rows:
                key = ff_r.get("number") or ff_r.get("box_num")
                if key is not None:
                    ff_runner_map[int(key)] = ff_r

            enriched_runners = []
            for r in runners:
                box = r.get("box_num") or r.get("number") or r.get("barrier")
                ff = ff_runner_map.get(int(box)) if box is not None else {}
                merged = {**r}
                if ff:
                    for field in ["form_string", "trainer", "jockey", "driver", "weight", "career",
                                  "best_time", "win_prob", "place_prob", "model_rank", "confidence",
                                  "decorators", "speed_map", "class_profile", "race_class_fit",
                                  "stats_overall", "stats_track", "stats_distance", "stats_condition",
                                  "stats_track_distance", "stats_full", "model_version"]:
                        if not merged.get(field) and ff.get(field):
                            merged[field] = ff[field]
                    merged["ff_win_prob"]      = ff.get("win_prob")
                    merged["ff_model_rank"]    = ff.get("model_rank")
                    merged["ff_confidence"]    = ff.get("confidence")
                    merged["ff_decorators"]    = ff.get("decorators") or []
                    merged["ff_speed_map"]     = ff.get("speed_map")
                    merged["ff_class_profile"] = ff.get("class_profile")
                    merged["ff_stats_full"]    = ff.get("stats_full") or {}
                    merged["ff_career_stats"]  = ff.get("stats_overall")
                enriched_runners.append(merged)
            runners = enriched_runners
        except Exception:
            pass

        # Attach stored FormFav enrichment if available
        ff_data: dict = {}
        try:
            today = race.get("date") or _date.today().isoformat()
            for ff_row in get_formfav_enrichments_for_date(today):
                if (ff_row.get("race_uid") or "") == race_uid:
                    ff_data = ff_row
                    break
        except Exception:
            pass

        if ff_data:
            race_out["formfav"] = ff_data

        # Build a lightweight analysis dict from available data
        formfav = race_out.get("formfav") or {}
        analysis: dict = {
            "signal": race_out.get("signal") or "—",
            "decision": race_out.get("decision") or "—",
            "confidence": race_out.get("confidence"),
            "selection": None,
            "ev": None,
            "pace_type": formfav.get("paceScenario"),
            "race_shape": formfav.get("weather"),
            "pass_reason": None,
            "all_runners": [
                {
                    "box": r.get("box_num"),
                    "barrier": r.get("barrier"),
                    "number": r.get("number"),
                    "name": r.get("name") or r.get("runner_name") or "—",
                    "odds": r.get("price") or r.get("win_odds"),
                    "trainer": r.get("trainer") or "—",
                    "jockey": r.get("jockey") or r.get("driver") or "—",
                    "scratched": r.get("scratched", False),
                    "status": "SCR" if r.get("scratched") else "OK",
                }
                for r in runners
            ],
        }

        return jsonify({
            "ok": True,
            "race": race_out,
            "runners": runners,
            "analysis": analysis,
            "signal": None,
        })
    except Exception as e:
        log.error(f"/api/live/race/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Race data unavailable"}), 500


@app.route("/api/live/watch-sim/<race_uid>", methods=["POST"])
def api_live_watch_sim(race_uid: str):
    """Trigger a simulation for the given race (proxies to simulation engine).
    TODO: wire to the Monte Carlo simulation engine once live trigger is supported.
    """
    try:
        from database import get_race

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        # Simulation engine live trigger is not yet wired — return 501 with context.
        return jsonify({
            "ok": False,
            "race_uid": race_uid,
            "simulation": None,
            "error": "Live simulation trigger not yet implemented",
        }), 501
    except Exception as e:
        log.error(f"/api/live/watch-sim/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Simulation unavailable"}), 500


@app.route("/api/live/mark-watched", methods=["POST"])
def api_live_mark_watched():
    """Record that the user has watched/noted a race."""
    try:
        body = request.get_json(silent=True) or {}
        race_uid = body.get("race_uid") or ""
        return jsonify({"ok": True, "race_uid": race_uid, "marked": True})
    except Exception as e:
        log.error(f"/api/live/mark-watched failed: {e}")
        return jsonify({"ok": False, "error": "Mark watched unavailable"}), 500


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

    Priority order for counter values:
      1. Persisted DB snapshot (formfav_debug_stats) — survives restarts / multi-worker
      2. Live in-memory pipeline_state — same worker only
      3. Direct DB table counts (today_races + formfav_race_enrichment) — always accurate
    """
    from datetime import date as _date

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

        # When neither DB snapshot nor memory has useful data (e.g. first startup
        # or multi-worker deployment before first persist), compute real counters
        # directly from the source tables so the endpoint never shows false zeros.
        live_counts: dict = {}
        counter_source = "db" if db_row else "memory"
        if not db_row and all(_val(k) == 0 for k in (
            "total_races_discovered", "total_formfav_called",
            "total_formfav_success", "total_formfav_failed",
        )):
            try:
                from database import get_races_for_date, get_formfav_enrichments_for_date
                today = _date.today().isoformat()
                all_races = get_races_for_date(today)
                _au_nz_codes = {"au", "aus", "australia", "nz", "nzl", "new zealand", "new-zealand"}
                _domestic = [r for r in all_races if (r.get("country") or "").strip().lower() in _au_nz_codes]
                _intl = len(all_races) - len(_domestic)
                ff_rows = get_formfav_enrichments_for_date(today)
                _ff_success = len([r for r in ff_rows if r.get("raw_response")])
                _ff_total = len(ff_rows)

                # Apply the same eligibility checks as formfav_sync so
                # total_formfav_eligible reflects the real eligible pool and is
                # strictly ≤ total_domestic_races (not inflated to equal
                # total_races_discovered as it was with the old formula).
                _ff_valid_codes_live = {"HORSE", "HARNESS", "GREYHOUND", "GALLOPS"}
                _eligible = [
                    r for r in _domestic
                    if (r.get("race_uid") or "") != ""
                    and (r.get("code") or "").upper() in _ff_valid_codes_live
                    and (r.get("track") or "") != ""
                    and int(r.get("race_num") or 0) > 0
                ]

                live_counts = {
                    "total_races_discovered":       len(all_races),
                    "total_domestic_races":         len(_domestic),
                    "total_international_filtered": _intl,
                    "total_formfav_eligible":       len(_eligible),
                    "total_formfav_called":         _ff_total,
                    "total_formfav_success":        _ff_success,
                    "total_formfav_failed":         _ff_total - _ff_success,
                }
                counter_source = "live_tables"
            except Exception as live_err:
                log.debug(f"/api/debug/formfav: live table fallback failed: {live_err}")

        def _final(key: str) -> int:
            return int(live_counts.get(key) or _val(key) or 0)

        # Report whether the FormFav connector is enabled so operators can
        # immediately see why total_formfav_called might be 0.
        _formfav_enabled: bool = False
        _formfav_disabled_reason: str | None = None
        try:
            from connectors.formfav_connector import FormFavConnector as _FFC
            _ff_conn = _FFC()
            _formfav_enabled = _ff_conn.is_enabled()
            if not _formfav_enabled:
                _formfav_disabled_reason = "FORMFAV_API_KEY not configured"
        except Exception:
            pass

        return jsonify({
            "ok": True,
            # ── Structured stage views ──────────────────────────────────────
            "merge_stage": {
                "called":  _final("formfav_merge_called"),
                "matched": _final("formfav_merge_matched"),
                "failed":  _final("formfav_merge_failed"),
            },
            "sync_stage": {
                "called":  _final("total_formfav_called"),
                "success": _final("total_formfav_success"),
                "failed":  _final("total_formfav_failed"),
            },
            # ── Legacy flat counters (kept for backward compatibility) ──────
            "total_races_discovered":       _final("total_races_discovered"),
            "total_domestic_races":         _final("total_domestic_races"),
            "total_international_filtered": _final("total_international_filtered"),
            "total_formfav_eligible":       _final("total_formfav_eligible"),
            "total_formfav_called":         _final("total_formfav_called"),
            "total_formfav_success":        _final("total_formfav_success"),
            "total_formfav_failed":         _final("total_formfav_failed"),
            # recent_races comes from in-memory (not stored in DB)
            "recent_races":                 mem_state.get("recent_races", []),
            "last_reset":                   mem_state.get("last_reset"),
            "snapshot_recorded_at":         db_row.get("recorded_at"),
            "counter_source":               counter_source,
            "formfav_enabled":              _formfav_enabled,
            "formfav_disabled_reason":      _formfav_disabled_reason,
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
    oddspro_enabled = False
    formfav_enabled = False
    try:
        from connectors.oddspro_connector import OddsProConnector
        oddspro_enabled = OddsProConnector().is_enabled()
    except Exception as e:
        log.debug(f"api_health: OddsPro connector check failed: {e}")
    try:
        from connectors.formfav_connector import FormFavConnector
        formfav_enabled = FormFavConnector().is_enabled()
    except Exception as e:
        log.debug(f"api_health: FormFav connector check failed: {e}")
    return jsonify({
        "ok": True,
        "app": "DemonPulse",
        "mode": env.mode,
        "oddspro_enabled": oddspro_enabled,
        "formfav_enabled": formfav_enabled,
    })


# ------------------------------------------------------------
# AI LEARNING STATUS
# ------------------------------------------------------------

@app.route("/api/ai/learning/status", methods=["GET"])
def api_ai_learning_status():
    """AI learning engine status — paper bets placed, results reviewed, model progress."""
    try:
        from database import get_races_for_date
        from ai.learning_store import get_performance_summary
        from datetime import date
        today = date.today().isoformat()

        perf = get_performance_summary(limit=200)

        all_races = get_races_for_date(today)
        predicted = [r for r in all_races if r.get("status") in ("final", "result_posted", "paying")]

        return jsonify({
            "ok": True,
            "model_version": perf.get("model_version", "baseline_v1"),
            "total_predictions": perf.get("total_predictions", 0),
            "total_evaluated": perf.get("total_evaluated", 0),
            "win_rate": perf.get("win_rate", 0),
            "roi": perf.get("roi", 0),
            "paper_bets_today": len(predicted),
            "results_reviewed_today": len([r for r in predicted if r.get("status") in ("final", "paying", "result_posted")]),
            "enrichment_rate": perf.get("enrichment_rate", 0),
            "active": True,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(
        debug=False,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
    )
