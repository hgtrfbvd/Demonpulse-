import os
import json
import logging
import requests as _requests
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
    from api.bet_routes import bet_bp
    app.register_blueprint(health_bp)
    app.register_blueprint(race_bp)
    app.register_blueprint(board_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(prediction_bp)
    app.register_blueprint(bet_bp)
    log.info("API blueprints registered")
except Exception as _bp_err:
    log.warning(f"Blueprint registration failed: {_bp_err}")


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------
def _nonempty(val):
    """Return val if it is a non-empty non-whitespace string, else None."""
    return val if val and str(val).strip() else None


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

    import scheduler as _sched_module
    sched_status = _sched_module.get_status()

    # Self-heal: restart scheduler if thread died
    if not sched_status.get("thread_alive"):
        try:
            _sched_module.start_scheduler()
            log.warning("/api/system/status: restarted dead scheduler thread")
        except Exception as _se:
            log.error(f"/api/system/status: scheduler restart failed: {_se}")

    return jsonify({
        "ok": True,
        "env": env.mode,
        "shadow_active": shadow_active,
        "scheduler": sched_status,
    })


@app.route("/api/scheduler/watchdog", methods=["POST", "GET"])
def scheduler_watchdog():
    """Ensure scheduler is running. Safe to call repeatedly."""
    try:
        import scheduler as _s
        status = _s.get_status()
        was_alive = status.get("thread_alive", False)
        if not was_alive:
            _s.start_scheduler()
            return jsonify({"ok": True, "action": "restarted", "was_alive": False})
        return jsonify({"ok": True, "action": "none", "was_alive": True})
    except Exception as e:
        log.error(f"/api/scheduler/watchdog failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sweep", methods=["POST"])
def api_sweep():
    """Trigger a full pipeline sweep for today."""
    try:
        from pipeline import full_sweep
        from datetime import date
        target_date = (request.get_json(silent=True) or {}).get("date") or date.today().isoformat()
        result = full_sweep(target_date)
        return jsonify(result)
    except Exception as e:
        log.error(f"/api/sweep failed: {e}")
        return jsonify({"ok": False, "error": "Sweep failed"}), 500


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
    return jsonify({"ok": False, "error": "thedogs_connector removed — use Claude scraper"}), 410


@app.route("/api/debug/thedogs-races")
def api_debug_thedogs_races():
    return jsonify({"ok": False, "error": "thedogs_connector removed — use Claude scraper"}), 410


# ------------------------------------------------------------
# HOME BOARD
# ------------------------------------------------------------
@app.route("/api/home/board", methods=["GET"])
def api_home_board():
    """Home board endpoint — delegates to board_service."""
    try:
        from board_service import get_board_for_today
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
        from database import get_race, get_runners_for_race
        from race_status import compute_ntj
        from datetime import date as _date

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        ntj = compute_ntj(race.get("jump_time"), race.get("date"))
        race_out = {**race, **ntj}

        runners = get_runners_for_race(race_uid)
        if not runners:
            log.warning(f"/api/live/race: race {race_uid!r} found in today_races but get_runners_for_race returned 0 rows")

        # Add camelCase aliases expected by frontend
        for r in runners:
            r["earlySpeed"] = r.get("early_speed") or r.get("early_speed_rating") or ""
            r["bestTime"]   = r.get("best_time") or ""
            r["winProb"]    = r.get("win_prob")
            r["placeProb"]  = r.get("place_prob")
            r["formString"] = r.get("form_last5") or r.get("last4") or r.get("form") or ""
            r["modelRank"]  = r.get("model_rank")
            r["paceStyle"]  = r.get("run_style") or ""
            r["winPct"]     = r.get("win_pct") or ""
            r["placePct"]   = r.get("place_pct") or ""
            r["recent_starts"] = []

        # Fetch stored prediction snapshot for signal/decision/selection/ev
        stored_pred = {}
        try:
            # Fast-path: check signals table first
            from signals import get_signal
            quick_sig = get_signal(race_uid)
            if quick_sig:
                stored_pred = {
                    "signal":     quick_sig.get("signal"),
                    "decision":   quick_sig.get("signal"),
                    "confidence": quick_sig.get("confidence"),
                    "ev":         quick_sig.get("ev"),
                    "selection":  quick_sig.get("top_runner"),
                    "runner_prob_map": {},
                }
        except Exception:
            pass

        if not stored_pred:
            try:
                from ai.learning_store import get_stored_prediction
                pred_result = get_stored_prediction(race_uid)
                if pred_result.get("ok"):
                    snap = (pred_result.get("snapshot") or
                            pred_result.get("prediction") or
                            pred_result.get("data") or {})
                    runner_outputs = (pred_result.get("runner_outputs") or
                                      snap.get("runner_outputs") or
                                      pred_result.get("runners") or [])
                    top = next((r for r in runner_outputs if r.get("predicted_rank") == 1), None)
                    stored_pred = {
                        "signal":    snap.get("signal") or "—",
                        "decision":  snap.get("decision") or "—",
                        "confidence": snap.get("confidence"),
                        "ev":        snap.get("ev"),
                        "selection": top.get("runner_name") if top else None,
                        "runner_prob_map": {
                            r.get("runner_name"): r.get("win_prob") for r in runner_outputs
                        },
                    }
            except Exception:
                pass

        # Build analysis dict from stored prediction and available FormFav data
        formfav = race_out.get("formfav") or {}
        race_out_signal = race_out.get("signal")
        race_out_decision = race_out.get("decision")
        analysis: dict = {
            "signal":     stored_pred.get("signal")     or race_out_signal   or "—",
            "decision":   stored_pred.get("decision")   or race_out_decision or "—",
            "confidence": stored_pred.get("confidence") or race_out.get("confidence"),
            "selection":  stored_pred.get("selection"),
            "ev":         stored_pred.get("ev"),
            "pace_type":  (_nonempty(formfav.get("pace_scenario"))
                          or _nonempty(formfav.get("paceScenario"))
                          or _nonempty(stored_pred.get("pace_type"))
                          or "—"),
            "race_shape": (_nonempty(formfav.get("race_shape"))
                          or _nonempty(formfav.get("beneficiary"))
                          or _nonempty(formfav.get("weather"))
                          or _nonempty(stored_pred.get("race_shape"))
                          or "—"),
            "weather":    (_nonempty(race_out.get("weather"))
                          or _nonempty(formfav.get("weather"))
                          or "—"),
            "condition":  (_nonempty(race_out.get("track_condition"))
                          or _nonempty(race_out.get("condition"))
                          or "—"),
            "pass_reason": None,
            "all_runners": [
                {
                    "box":      r.get("box_num"),
                    "barrier":  r.get("barrier"),
                    "number":   r.get("number"),
                    "name":     r.get("name") or r.get("runner_name") or "—",
                    "odds":     r.get("price") or r.get("win_odds"),
                    "win_prob": r.get("ff_win_prob") or r.get("win_prob"),
                    "trainer":  r.get("trainer") or "—",
                    "jockey":   r.get("jockey") or r.get("driver") or "—",
                    "scratched": r.get("scratched", False),
                    "status":   "SCR" if r.get("scratched") else "OK",
                }
                for r in runners
            ],
        }

        return jsonify({
            "ok":       True,
            "race":     race_out,
            "runners":  runners,
            "analysis": analysis,
            "signal": {
                "signal":     stored_pred.get("signal"),
                "confidence": stored_pred.get("confidence"),
                "ev":         stored_pred.get("ev"),
            } if stored_pred.get("signal") not in (None, "—") else None,
        })
    except Exception as e:
        import traceback
        log.error(
            f"/api/live/race/{race_uid} failed: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        return jsonify({"ok": False, "error": "Race data unavailable"}), 500


@app.route("/api/live/watch-sim/<race_uid>", methods=["POST"])
def api_live_watch_sim(race_uid: str):
    """Trigger a simulation for the given race (proxies to simulation engine)."""
    try:
        from database import get_race, get_runners_for_race
        from simulation.core_simulation_engine import SimulationEngine
        from simulation.models import RaceMeta, RunnerProfile, normalize_race_code

        race = get_race(race_uid)
        if not race:
            return jsonify({"ok": False, "error": "Race not found"}), 404

        runners_raw = get_runners_for_race(race_uid)
        if not runners_raw:
            return jsonify({"ok": False, "error": "No runners found"}), 404

        try:
            race_code = normalize_race_code(race.get("code") or "GREYHOUND")
        except ValueError:
            race_code = normalize_race_code("GREYHOUND")

        race_meta = RaceMeta(
            race_uid=race_uid,
            track=race.get("track") or "",
            race_code=race_code,
            distance_m=int(race.get("distance") or 400),
            grade=race.get("grade") or "",
            condition=race.get("condition") or "GOOD",
            field_size=len(runners_raw),
        )

        runner_profiles = []
        for r in runners_raw:
            if r.get("scratched"):
                continue
            box = r.get("box_num") or r.get("barrier") or r.get("number") or 1
            odds = float(r.get("price") or r.get("win_odds") or 5.0)
            runner_profiles.append(RunnerProfile(
                runner_id=str(box),
                name=r.get("name") or r.get("runner_name") or f"Runner {box}",
                barrier_or_box=int(box),
                market_odds=odds,
                scratched=bool(r.get("scratched", False)),
            ))

        engine = SimulationEngine()
        guide = engine.run(race_meta, runner_profiles)

        result = {
            "decision": guide.decision.value if hasattr(guide.decision, "value") else str(guide.decision),
            "confidence": guide.confidence_rating.value if hasattr(guide.confidence_rating, "value") else str(guide.confidence_rating),
            "chaos": guide.chaos_rating.value if hasattr(guide.chaos_rating, "value") else str(guide.chaos_rating),
            "top_runner": guide.top_runner,
            "summary": guide.summary if hasattr(guide, "summary") else "",
        }
        return jsonify({"ok": True, "simulation": result})
    except Exception as e:
        log.error(f"/api/live/watch-sim/{race_uid} failed: {e}")
        return jsonify({"ok": False, "error": "Simulation failed"}), 500


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
    return jsonify({"ok": False, "error": "thedogs_connector removed — use Claude scraper"}), 410


@app.route("/api/debug/thedogs-scratchings-fetch")
def api_debug_thedogs_scratchings_fetch():
    return jsonify({"ok": False, "error": "thedogs_connector removed — use Claude scraper"}), 410


@app.route("/api/debug/formfav", methods=["GET"])
def api_debug_formfav():
    """FormFav is removed. Returns pipeline stats from stored data."""
    from datetime import date as _date
    try:
        from database import get_races_for_date
        today = _date.today().isoformat()
        all_races = get_races_for_date(today)
        return jsonify({
            "ok": True,
            "note": "FormFav removed — Claude API is now the only data source",
            "total_races_stored": len(all_races),
            "date": today,
        })
    except Exception as e:
        log.exception(f"/api/debug/formfav failed: {e}")
        return jsonify({"ok": False, "error": "Could not retrieve pipeline state"}), 500
    """
    End-to-end pipeline diagnostic for the Claude-powered data pipeline.
    No auth required, no writes.
    """
    from datetime import date as _date
    today = _date.today().isoformat()
    result: dict = {"date": today}
    tests: dict = {}

    # Claude API connectivity
    result["claude_api_key_present"] = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())

    # Database: today_races count
    try:
        from database import get_races_for_date
        tests["database"] = {
            "ok": True,
            "today_races_count": len(get_races_for_date(today)),
        }
    except Exception as e:
        tests["database"] = {"ok": False, "error": str(e)}

    # Scheduler state
    try:
        import scheduler
        sched_status = scheduler.get_status()
        tests["scheduler"] = {
            "last_full_sweep_at":     sched_status.get("last_full_sweep_at"),
            "last_full_sweep_result": sched_status.get("last_full_sweep_result"),
            "last_error":             sched_status.get("last_error"),
        }
    except Exception as e:
        tests["scheduler"] = {"ok": False, "error": str(e)}

    result["tests"] = tests
    return jsonify(result)


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
    claude_enabled = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    return jsonify({
        "ok": True,
        "app": "DemonPulse",
        "mode": env.mode,
        "claude_enabled": claude_enabled,
        "data_source": "claude",
    })


# ------------------------------------------------------------
# AI COMMENTARY PROXY
# ------------------------------------------------------------

@app.route("/api/ai/commentary", methods=["POST"])
def api_ai_commentary():
    try:
        import anthropic
        data    = request.get_json(silent=True) or {}
        prompt  = data.get("prompt") or ""
        if not prompt:
            return jsonify({"ok": False, "error": "prompt required"}), 400
        client  = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        text = message.content[0].text if message.content else "Commentary unavailable."
        return jsonify({"ok": True, "text": text})
    except Exception as e:
        log.error(f"/api/ai/commentary failed: {e}")
        return jsonify({"ok": False, "text": "Commentary unavailable."}), 500


# ------------------------------------------------------------
# AI LEARNING STATUS
# ------------------------------------------------------------

@app.route("/api/ai/learning/status", methods=["GET"])
def api_ai_learning_status():
    """AI learning engine status — paper bets placed, results reviewed, model progress."""
    try:
        from database import get_active_races, get_formfav_enrichments_for_date
        from race_status import compute_ntj
        from datetime import date
        from db import get_db, safe_query, T

        today = date.today().isoformat()
        races = get_active_races(today)

        # Split into next-60-min window vs later
        next_hour = []
        later = []
        for r in races:
            ntj = compute_ntj(r.get("jump_time"), r.get("date"))
            secs = ntj.get("seconds_to_jump")
            if secs is not None and 0 < secs <= 3600:
                next_hour.append(r)
            elif secs is not None and secs > 3600:
                later.append(r)

        # Check FormFav enrichment coverage for next-hour races
        enriched_uids = {
            row.get("race_uid")
            for row in get_formfav_enrichments_for_date(today)
            if row.get("race_uid")
        }
        next_hour_enriched = sum(
            1 for r in next_hour if r.get("race_uid") in enriched_uids
        )

        # Count today's prediction snapshots by created_at (race_date may not exist yet)
        try:
            snap_rows = safe_query(
                lambda: get_db()
                .table(T("prediction_snapshots"))
                .select("model_version,race_uid")
                .gte("created_at", today + "T00:00:00Z")
                .lte("created_at", today + "T23:59:59Z")
                .execute()
                .data,
                []
            ) or []
        except Exception:
            snap_rows = []

        total_predictions = len(snap_rows)
        model_version = "baseline_v1"
        if snap_rows:
            try:
                latest = safe_query(
                    lambda: get_db()
                    .table(T("prediction_snapshots"))
                    .select("model_version")
                    .gte("created_at", today + "T00:00:00Z")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                    .data,
                    []
                ) or []
                model_version = (latest[0].get("model_version") or "baseline_v1") if latest else "baseline_v1"
            except Exception:
                pass

        return jsonify({
            "ok": True,
            "next_hour_races": len(next_hour),
            "next_hour_enriched": next_hour_enriched,
            "next_hour_pending": len(next_hour) - next_hour_enriched,
            "later_races": len(later),
            "formfav_coverage_pct": round(
                (next_hour_enriched / len(next_hour) * 100) if next_hour else 0, 1
            ),
            "total_predictions": total_predictions,
            "model_version": model_version,
        })
    except Exception as e:
        import traceback
        log.error(f"/api/ai/learning/status failed: {e}\n{traceback.format_exc()}")
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
