"""
learning_engine.py - Post-race learning layer
EPR, AEEE, GPIL, ETG, calibration, shadow mode
Feature coverage: F26-F32, E22
ISOLATION RULE: Never affects live decisions. Only runs post-result.
"""
import logging
from datetime import date, datetime

log = logging.getLogger(__name__)



# ----------------------------------------------------------------
# ETG — ERROR TAG GENERATOR (feature 26)
# ----------------------------------------------------------------
ETG_TAGS = [
    "FALSE_FAV", "CHAOS_MISS", "EV_MISS", "TIMING_ERROR",
    "STRUCTURE_ERROR", "MEMORY_ERROR", "MARKET_ERROR",
    "SESSION_ERROR", "INTERFERENCE", "VARIANCE"
]

def auto_tag_loss(bet, race_scored, result):
    """
    Automatically tag a losing bet with likely cause.
    Supports manual override (feature 26 - manual tag editing).
    """
    if result == "WIN":
        return None

    tags = []

    # False favourite error
    if race_scored and race_scored.get("false_favourite"):
        ff = race_scored["false_favourite"]
        if ff and ff.get("runner") == bet.get("runner"):
            tags.append("FALSE_FAV")

    # Chaos miss
    filters = race_scored.get("filters", {}) if race_scored else {}
    chf = filters.get("CHF", {})
    if chf.get("score", 100) < 40:
        tags.append("CHAOS_MISS")

    # Structure error
    if race_scored and race_scored.get("separation") == "CLUSTER":
        tags.append("STRUCTURE_ERROR")

    # Market error
    if race_scored and race_scored.get("false_favourite"):
        tags.append("MARKET_ERROR")

    # Default to variance if no specific tag
    if not tags:
        tags.append("VARIANCE")

    return tags[0] if tags else "VARIANCE"


def save_etg_tag(bet_id, race_uid, tag, notes=None, manual=False):
    try:
        from db import get_db, T
        db = get_db()
        db.table(T("etg_tags")).insert({
            "bet_id": str(bet_id),
            "race_uid": race_uid,
            "error_tag": tag,
            "notes": notes,
            "manual_override": manual,
            "date": date.today().isoformat(),
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        log.error(f"Save ETG tag failed: {e}")


# ----------------------------------------------------------------
# EPR — EDGE PERFORMANCE REGISTRY
# ----------------------------------------------------------------
def save_epr_entry(bet, result, pl, scored):
    try:
        from db import get_db, T
        db = get_db()
        db.table(T("epr_data")).insert({
            "edge_type": scored.get("edge_type", "STRUCTURAL") if scored else "UNKNOWN",
            "code": bet.get("code", "GREYHOUND"),
            "track": bet.get("track"),
            "confidence_tier": bet.get("confidence"),
            "ev_at_analysis": bet.get("ev"),
            "result": result,
            "pl": pl,
            "execution_mode": "LIVE",
            "session_id": bet.get("session_id"),
            "date": date.today().isoformat(),
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        log.error(f"Save EPR entry failed: {e}")


def get_epr_summary():
    try:
        from db import get_db, T
        db = get_db()
        rows = db.table(T("epr_data")).select("*").execute().data or []
        summary = {}
        for r in rows:
            et = r.get("edge_type", "UNKNOWN")
            if et not in summary:
                summary[et] = {"bets": 0, "wins": 0, "pl": 0}
            summary[et]["bets"] += 1
            if r.get("result") == "WIN":
                summary[et]["wins"] += 1
            summary[et]["pl"] += r.get("pl") or 0
        for et, s in summary.items():
            s["strike_rate"] = round(s["wins"] / s["bets"] * 100, 1) if s["bets"] > 0 else 0
            s["roi"] = round(s["pl"] / (s["bets"] * 10) * 100, 1) if s["bets"] > 0 else 0
        return summary
    except Exception as e:
        log.error(f"EPR summary failed: {e}")
        return {}


# ----------------------------------------------------------------
# AEEE — ADAPTIVE EDGE EFFICIENCY ENGINE
# ----------------------------------------------------------------
BASE_EV_THRESHOLDS = {"ELITE": 0.08, "HIGH": 0.10, "MODERATE": 0.12, "SESSION": 0.05}
_aeee_adjustments = {}

def aeee_review():
    """
    Review EPR data and suggest EV threshold adjustments.
    Never auto-applies. Requires user PROMOTE (feature 29).
    """
    epr = get_epr_summary()
    suggestions = []
    for edge_type, stats in epr.items():
        if stats["bets"] < 10:
            continue
        roi = stats["roi"]
        if roi < -5:
            suggestions.append({
                "edge_type": edge_type,
                "direction": "RAISE",
                "amount": 0.02,
                "reason": f"ROI {roi}% below -5% threshold",
                "bets_sample": stats["bets"],
                "roi": roi,
            })
        elif roi > 15:
            suggestions.append({
                "edge_type": edge_type,
                "direction": "LOWER",
                "amount": 0.01,
                "reason": f"ROI {roi}% above +15% threshold",
                "bets_sample": stats["bets"],
                "roi": roi,
            })
    return suggestions


def save_aeee_suggestion(suggestion):
    try:
        from db import get_db, T
        db = get_db()
        db.table(T("aeee_adjustments")).insert({
            "edge_type": suggestion["edge_type"],
            "direction": suggestion["direction"],
            "amount": suggestion["amount"],
            "reason": suggestion["reason"],
            "roi_trigger": suggestion["roi"],
            "bets_sample": suggestion["bets_sample"],
            "applied": False,
            "promoted": False,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        log.error(f"Save AEEE suggestion failed: {e}")


def promote_aeee(suggestion_id):
    """Feature 29 - promotion gate. User must approve before anything goes live."""
    try:
        from db import get_db, T
        db = get_db()
        db.table(T("aeee_adjustments")).update({
            "promoted": True,
            "applied": True,
        }).eq("id", suggestion_id).execute()
        log.info(f"AEEE suggestion {suggestion_id} promoted and applied")
    except Exception as e:
        log.error(f"AEEE promote failed: {e}")


# ----------------------------------------------------------------
# GPIL — GLOBAL PATTERN INTELLIGENCE (feature 32)
# ----------------------------------------------------------------
def gpil_review():
    """
    Identify cross-track patterns from EPR data.
    Minimum 10 results required.
    """
    try:
        from db import get_db, T
        db = get_db()
        rows = db.table(T("epr_data")).select("track, code, result, pl, confidence_tier").execute().data or []
        if len(rows) < 10:
            return {"status": "INSUFFICIENT", "message": f"Need 10+ results, have {len(rows)}"}

        by_track = {}
        for r in rows:
            t = r.get("track", "unknown")
            if t not in by_track:
                by_track[t] = {"bets": 0, "wins": 0, "pl": 0}
            by_track[t]["bets"] += 1
            if r.get("result") == "WIN":
                by_track[t]["wins"] += 1
            by_track[t]["pl"] += r.get("pl") or 0

        patterns = []
        for track, stats in by_track.items():
            if stats["bets"] < 5:
                continue
            roi = stats["pl"] / (stats["bets"] * 10) * 100
            if roi > 10:
                patterns.append({"track": track, "pattern": "PROFITABLE", "roi": round(roi, 1), "bets": stats["bets"]})
            elif roi < -5:
                patterns.append({"track": track, "pattern": "DANGEROUS", "roi": round(roi, 1), "bets": stats["bets"]})

        return {"status": "OK", "patterns": patterns, "total_bets": len(rows)}
    except Exception as e:
        log.error(f"GPIL review failed: {e}")
        return {"status": "ERROR", "error": str(e)}


# ----------------------------------------------------------------
# AUTO SKIP LEARNING (feature 27)
# ----------------------------------------------------------------
def log_pass_decision(race_uid, pass_reason, scored):
    """Log why a race was passed for later review."""
    try:
        from db import get_db, T
        db = get_db()
        db.table(T("pass_log")).upsert({
            "race_uid": race_uid,
            "pass_reason": pass_reason,
            "local_decision": scored.get("decision") if scored else "PASS",
            "confidence": scored.get("confidence") if scored else "INSUFFICIENT",
            "date": date.today().isoformat(),
            "created_at": datetime.utcnow().isoformat(),
        }, on_conflict="race_uid").execute()
    except Exception as e:
        log.error(f"Log pass failed: {e}")


# ----------------------------------------------------------------
# CONFIDENCE CALIBRATION (feature 28)
# ----------------------------------------------------------------
def calibration_report():
    try:
        from db import get_db
        db = get_db()
        rows = db.table("bet_log").select("confidence, result").neq("result", "PENDING").execute().data or []
        by_conf = {}
        for r in rows:
            c = r.get("confidence", "UNKNOWN")
            if c not in by_conf:
                by_conf[c] = {"total": 0, "wins": 0}
            by_conf[c]["total"] += 1
            if r.get("result") == "WIN":
                by_conf[c]["wins"] += 1
        report = {}
        for conf, stats in by_conf.items():
            actual_sr = round(stats["wins"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0
            report[conf] = {"bets": stats["total"], "wins": stats["wins"], "actual_strike_rate": actual_sr}
        return report
    except Exception as e:
        log.error(f"Calibration report failed: {e}")
        return {}


# ----------------------------------------------------------------
# SHADOW MODE (feature 30)
# ----------------------------------------------------------------
_shadow_results = []

def shadow_mode_record(race_uid, shadow_decision, live_decision):
    """Record shadow engine decision alongside live for comparison."""
    _shadow_results.append({
        "race_uid": race_uid,
        "shadow": shadow_decision,
        "live": live_decision,
        "match": shadow_decision == live_decision,
        "recorded_at": datetime.utcnow().isoformat(),
    })

def shadow_mode_stats():
    if not _shadow_results:
        return {"total": 0, "match_rate": 0}
    matches = sum(1 for r in _shadow_results if r["match"])
    return {
        "total": len(_shadow_results),
        "matches": matches,
        "match_rate": round(matches / len(_shadow_results) * 100, 1),
        "recent": _shadow_results[-5:],
    }


# ----------------------------------------------------------------
# STREAK DETECTION (feature 31)
# ----------------------------------------------------------------
def detect_system_streak():
    try:
        from db import get_db
        db = get_db()
        recent = db.table("bet_log").select("result, pl").neq("result", "PENDING").order("created_at", desc=True).limit(20).execute().data or []
        if not recent:
            return {"streak": "NEUTRAL", "length": 0}

        results = [r["result"] for r in recent if r.get("result")]
        if not results:
            return {"streak": "NEUTRAL", "length": 0}
        streak_type = results[0]
        streak_len = 0
        for r in results:
            if r == streak_type:
                streak_len += 1
            else:
                break

        if streak_len >= 5 and streak_type == "WIN":
            status = "HOT"
        elif streak_len >= 4 and streak_type == "LOSS":
            status = "COLD"
        else:
            status = "NEUTRAL"

        return {"streak": status, "type": streak_type, "length": streak_len}
    except Exception as e:
        log.error(f"Streak detection failed: {e}")
        return {"streak": "NEUTRAL", "length": 0}


# ----------------------------------------------------------------
# POST-RESULT PROCESSING
# ----------------------------------------------------------------
def process_result(bet, result, pl, scored=None):
    """
    Run all learning layer updates after a result.
    Isolated from live decisions.
    """
    # ETG tagging
    tag = auto_tag_loss(bet, scored, result)
    if tag:
        save_etg_tag(bet.get("id"), bet.get("race_uid"), tag)

    # EPR update
    save_epr_entry(bet, result, pl, scored)

    # AEEE review (runs silently, saves suggestions only)
    suggestions = aeee_review()
    for s in suggestions:
        save_aeee_suggestion(s)

    log.info(f"Learning updated for {bet.get('track')} R{bet.get('race_num')}: {result} tag={tag}")


# ----------------------------------------------------------------
# BATCH REVIEW — called by /api/learning/run (admin only)
# Scans the last N days of settled bets, runs all learning layers,
# and surfaces new AEEE suggestions.
# ISOLATION RULE: never mutates live scoring weights directly.
# ----------------------------------------------------------------
def run_batch_review(days: int = 7) -> dict:
    """
    Run a full learning review over the last N days of settled bets.
    Returns a summary dict with counts of errors found and suggestions made.
    """
    from datetime import date, timedelta
    from db import get_db, safe_query, T

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    log.info(f"[BATCH] Starting review: last {days} days (since {cutoff})")

    bets = safe_query(
        lambda: get_db().table(T("bet_log"))
                .select("*")
                .neq("result", "PENDING")
                .gte("date", cutoff)
                .order("date")
                .execute().data, []
    ) or []

    if not bets:
        log.info("[BATCH] No settled bets in window.")
        return {"days": days, "bets_reviewed": 0, "errors_tagged": 0, "suggestions": 0}

    errors_tagged = 0
    suggestions_saved = 0

    for bet in bets:
        try:
            result = bet.get("result", "")
            pl     = float(bet.get("pl") or 0)
            tag    = auto_tag_loss(bet, None, result)
            if tag and tag != "VARIANCE":
                save_etg_tag(bet.get("id"), bet.get("race_uid"), tag)
                errors_tagged += 1
            save_epr_entry(bet, result, pl, None)
        except Exception as e:
            log.error(f"[BATCH] Error processing bet {bet.get('id')}: {e}")

    # Generate AEEE suggestions from accumulated EPR data
    try:
        suggestions = aeee_review()
        for s in suggestions:
            save_aeee_suggestion(s)
            suggestions_saved += 1
    except Exception as e:
        log.error(f"[BATCH] AEEE review failed: {e}")

    summary = {
        "days":           days,
        "bets_reviewed":  len(bets),
        "errors_tagged":  errors_tagged,
        "suggestions":    suggestions_saved,
        "cutoff_date":    cutoff,
    }
    log.info(f"[BATCH] Complete: {summary}")
    return summary
