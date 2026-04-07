"""
scorer.py - Local scoring engines E23-E29, E39 filters, EV, Kelly
Feature coverage: D14-D21, E22-E25
"""
import hashlib
import logging
import time
from datetime import datetime

log = logging.getLogger(__name__)

SCORER_VERSION = "1.0.0"

# ----------------------------------------------------------------
# SCORING SETTINGS — reads system_state + applied AEEE adjustments
# Cached 60 s so admin changes propagate without restart.
# ----------------------------------------------------------------

_settings_cache: dict = {"data": {}, "loaded_at": 0.0}
_aeee_cache:     dict = {"data": {}, "loaded_at": 0.0}
_CACHE_TTL = 60  # seconds


def _load_scoring_settings() -> dict:
    """Return admin-configurable scoring weights from system_state."""
    global _settings_cache
    now = time.time()
    if now - _settings_cache["loaded_at"] < _CACHE_TTL:
        return _settings_cache["data"]
    defaults = {
        "confidence_threshold": 0.65,
        "ev_threshold":         0.08,
        "tempo_weight":         1.0,
        "traffic_penalty":      0.8,
        "closer_boost":         1.1,
        "fade_penalty":         0.9,
        "simulation_depth":     1000,
    }
    try:
        from db import get_db, safe_query, T
        row = safe_query(
            lambda: get_db().table(T("system_state")).select(
                "confidence_threshold,ev_threshold,tempo_weight,"
                "traffic_penalty,closer_boost,fade_penalty,simulation_depth"
            ).eq("id", 1).single().execute().data
        ) or {}
        merged = {**defaults, **{k: v for k, v in row.items() if v is not None}}
        _settings_cache = {"data": merged, "loaded_at": now}
        return merged
    except Exception:
        return defaults


def _load_aeee_multipliers() -> dict:
    """
    Return {edge_type: float} multipliers from promoted AEEE adjustments.
    RAISE direction → multiplier > 1 (tighten threshold).
    LOWER direction → multiplier < 1 (loosen threshold).
    """
    global _aeee_cache
    now = time.time()
    if now - _aeee_cache["loaded_at"] < _CACHE_TTL:
        return _aeee_cache["data"]
    try:
        from db import get_db, safe_query, T
        rows = safe_query(
            lambda: get_db().table(T("aeee_adjustments")).select(
                "edge_type,direction,amount"
            ).eq("applied", True).eq("promoted", True).execute().data, []
        ) or []
        mults: dict = {}
        for row in rows:
            edge   = row.get("edge_type", "")
            amount = float(row.get("amount") or 0.0)
            m      = 1.0 + amount if row.get("direction") == "RAISE" else 1.0 - amount
            mults[edge] = round(mults.get(edge, 1.0) * m, 4)
        _aeee_cache = {"data": mults, "loaded_at": now}
        if mults:
            log.debug(f"AEEE multipliers active: {mults}")
        return mults
    except Exception:
        return {}


# ----------------------------------------------------------------
# E23 — EARLY SPEED + PRESSURE SCORE (feature 19)
# ----------------------------------------------------------------
def score_early_speed(runners):
    times = []
    for r in runners:
        bt = r.get("best_time")
        if bt:
            try:
                times.append((r["box_num"], float(bt)))
            except (ValueError, TypeError):
                pass

    fastest = min(t for _, t in times) if times else None
    time_map = {box: t for box, t in times}

    total_pressure = 0
    for r in runners:
        if r.get("scratched"):
            continue
        box = r["box_num"]
        bt = time_map.get(box)
        if bt is None:
            r["early_speed_rank"] = "UNKNOWN"
        elif bt <= fastest + 0.15:
            r["early_speed_rank"] = "FAST"
            total_pressure += 3
        elif bt <= fastest + 0.30:
            r["early_speed_rank"] = "MID"
            total_pressure += 1
        else:
            r["early_speed_rank"] = "SLOW"

        if not r.get("run_style"):
            box = r["box_num"]
            if box in [1, 2]:
                r["run_style"] = "RAILER"
            elif box in [7, 8]:
                r["run_style"] = "WIDE"
            elif r.get("early_speed_rank") == "FAST":
                r["run_style"] = "LEADER"
            else:
                r["run_style"] = "CHASER"

    # Early speed pressure score (feature 19)
    # Count fast runners directly - 1 fast = good, 4+ fast = extreme
    active = [r for r in runners if not r.get("scratched")]
    fast_count = sum(1 for r in active if r.get("early_speed_rank") == "FAST")
    pressure_score = min(10, fast_count * 2)

    return runners, pressure_score

# ----------------------------------------------------------------
# E24 — FIRST BEND MAP
# ----------------------------------------------------------------
BOX_PROFILES = {
    "horsham":    {1:"STRONG",2:"STRONG",3:"NEUTRAL",4:"NEUTRAL",5:"WEAK",6:"WEAK",7:"AVOID",8:"AVOID"},
    "bendigo":    {1:"STRONG",2:"STRONG",3:"NEUTRAL",4:"NEUTRAL",5:"NEUTRAL",6:"WEAK",7:"WEAK",8:"AVOID"},
    "ballarat":   {1:"STRONG",2:"STRONG",3:"NEUTRAL",4:"NEUTRAL",5:"NEUTRAL",6:"WEAK",7:"WEAK",8:"WEAK"},
    "sandown":    {1:"STRONG",2:"STRONG",3:"STRONG",4:"NEUTRAL",5:"NEUTRAL",6:"WEAK",7:"WEAK",8:"AVOID"},
    "meadows":    {1:"STRONG",2:"STRONG",3:"NEUTRAL",4:"NEUTRAL",5:"NEUTRAL",6:"NEUTRAL",7:"WEAK",8:"WEAK"},
    "cannington": {1:"STRONG",2:"STRONG",3:"NEUTRAL",4:"NEUTRAL",5:"NEUTRAL",6:"WEAK",7:"WEAK",8:"AVOID"},
    "mandurah":   {1:"STRONG",2:"STRONG",3:"NEUTRAL",4:"NEUTRAL",5:"NEUTRAL",6:"WEAK",7:"WEAK",8:"AVOID"},
    "angle-park": {1:"STRONG",2:"STRONG",3:"NEUTRAL",4:"NEUTRAL",5:"NEUTRAL",6:"WEAK",7:"WEAK",8:"AVOID"},
    "default":    {1:"STRONG",2:"STRONG",3:"NEUTRAL",4:"NEUTRAL",5:"NEUTRAL",6:"NEUTRAL",7:"WEAK",8:"WEAK"},
}

def score_box(track, box_num):
    profile = BOX_PROFILES.get(track.lower(), BOX_PROFILES["default"])
    return profile.get(box_num, "NEUTRAL")

def map_first_bend(runners, track):
    for r in runners:
        r["box_score"] = score_box(track, r["box_num"])
        r["collision_risk"] = "HIGH" if r["box_num"] in [3, 4, 5] else "MODERATE" if r["box_num"] in [2, 6] else "LOW"
    return runners

# ----------------------------------------------------------------
# E25 — RACE SHAPE + TEMPO/COLLAPSE PROJECTION (feature 21)
# ----------------------------------------------------------------
def classify_pace(runners):
    active = [r for r in runners if not r.get("scratched")]
    leaders = [r for r in active if r.get("early_speed_rank") == "FAST"]
    count = len(leaders)
    if count <= 1:
        return "SLOW"
    elif count == 2:
        return "MODERATE"
    elif count == 3:
        return "FAST"
    else:
        return "HOT"

def project_tempo_collapse(runners, pace_type, distance_m=400):
    """Feature 21 - tempo/race collapse projection."""
    active = [r for r in runners if not r.get("scratched")]
    leaders = [r for r in active if r.get("run_style") == "LEADER"]
    collapse_risk = "LOW"
    if pace_type == "HOT" and len(leaders) >= 3:
        collapse_risk = "HIGH"
    elif pace_type == "FAST" and distance_m >= 500:
        collapse_risk = "MODERATE"
    elif pace_type == "HOT":
        collapse_risk = "MODERATE"
    return {
        "collapse_risk": collapse_risk,
        "leader_count": len(leaders),
        "projected_leader": leaders[0]["name"] if leaders else "unclear"
    }

def build_race_shape(runners, track, distance_m=400):
    pace = classify_pace(runners)
    beneficiary = {"SLOW": "LEADER", "MODERATE": "LEADER", "FAST": "CHASER", "HOT": "CHASER"}.get(pace, "UNPREDICTABLE")
    collapse = project_tempo_collapse(runners, pace, distance_m)
    active = [r for r in runners if not r.get("scratched")]
    leaders = [r["name"] for r in active if r.get("run_style") == "LEADER"]
    return {
        "pace_type": pace,
        "beneficiary": beneficiary,
        "shape_summary": f"{pace} pace - {beneficiary.lower()} advantaged - collapse risk {collapse['collapse_risk'].lower()}",
        "lead_candidate": leaders[0] if leaders else "unclear",
        "collapse_risk": collapse["collapse_risk"],
        "projected_leader": collapse["projected_leader"],
    }

# ----------------------------------------------------------------
# E26 — FATIGUE / CRASH MAP
# ----------------------------------------------------------------
def score_fatigue(runner):
    days = runner.get("days_since_last_run")
    style = runner.get("run_style", "UNKNOWN")
    dist = runner.get("distance_metres", 400)
    runs = runner.get("recent_run_count", 1)

    if days is None:
        freshness = "UNKNOWN"
    elif days >= 7:
        freshness = "FRESH"
    elif days >= 4:
        freshness = "NORMAL"
    elif days >= 2:
        freshness = "TIRED"
    else:
        freshness = "HIGH_RISK"

    crash_map = "SAFE"
    if freshness in ["TIRED", "HIGH_RISK"] and style == "LEADER" and dist >= 500:
        crash_map = "HIGH_RISK"
    elif freshness in ["TIRED", "HIGH_RISK"] and runs >= 3:
        crash_map = "HIGH_RISK"
    elif freshness == "HIGH_RISK":
        crash_map = "HIGH_RISK"
    elif freshness == "TIRED":
        crash_map = "CAUTION"

    return {"freshness": freshness, "crash_map": crash_map, "core_blocked": crash_map == "HIGH_RISK"}

# ----------------------------------------------------------------
# E27 — TRACK BIAS + CONDITION DRIFT (feature 20)
# ----------------------------------------------------------------
TRACK_BIAS = {
    "cannington":  {"type": "INSIDE", "strength": "STRONG",   "favours": "RAILER"},
    "mandurah":    {"type": "INSIDE", "strength": "MODERATE", "favours": "RAILER"},
    "horsham":     {"type": "INSIDE", "strength": "MODERATE", "favours": "LEADER"},
    "sandown":     {"type": "INSIDE", "strength": "STRONG",   "favours": "RAILER"},
    "meadows":     {"type": "EARLY",  "strength": "MODERATE", "favours": "LEADER"},
    "angle-park":  {"type": "INSIDE", "strength": "MODERATE", "favours": "LEADER"},
    "bendigo":     {"type": "INSIDE", "strength": "MODERATE", "favours": "LEADER"},
    "ballarat":    {"type": "INSIDE", "strength": "MODERATE", "favours": "LEADER"},
}

def get_track_bias(track):
    return TRACK_BIAS.get(track.lower(), {"type": "NEUTRAL", "strength": "NEUTRAL", "favours": "ANY"})

def check_bias_alignment(runner, track):
    bias = get_track_bias(track)
    style = runner.get("run_style", "UNKNOWN")
    box = runner.get("box_num", 5)
    if bias["type"] == "INSIDE" and box <= 2:
        return "ALIGNED"
    elif bias["type"] == "INSIDE" and box >= 7:
        return "OPPOSED"
    elif bias["favours"] == style:
        return "ALIGNED"
    return "NEUTRAL"

# ----------------------------------------------------------------
# E28 — CLASS / FORM + CONSISTENCY INDEX (feature 18)
# ----------------------------------------------------------------
def score_form(runner):
    career = runner.get("career", "")
    if not career:
        return {"trajectory": "UNKNOWN", "class_direction": "LEVEL", "reliability": "THIN",
                "consistency_index": 0, "starts": 0, "wins": 0, "win_pct": 0}
    try:
        parts = career.split(":")
        if len(parts) >= 2:
            starts = int(parts[0].strip()) if parts[0].strip().isdigit() else 0
            record = parts[1].strip().split("-")
            wins = int(record[0]) if len(record) > 0 and record[0].isdigit() else 0
            places = int(record[1]) if len(record) > 1 and record[1].isdigit() else 0
        elif len(parts) == 1:
            # Format: starts-wins-places
            record = parts[0].strip().split("-")
            starts = int(record[0]) if len(record) > 0 and record[0].isdigit() else 0
            wins = int(record[1]) if len(record) > 1 and record[1].isdigit() else 0
            places = int(record[2]) if len(record) > 2 and record[2].isdigit() else 0
        else:
            return {"trajectory": "UNKNOWN", "class_direction": "LEVEL", "reliability": "THIN",
                    "consistency_index": 0, "starts": 0, "wins": 0, "win_pct": 0}

        win_pct = (wins / starts * 100) if starts > 0 else 0
        place_pct = ((wins + places) / starts * 100) if starts > 0 else 0
        consistency = round(place_pct)

        if win_pct >= 40:
            trajectory = "ACCELERATING"
        elif win_pct >= 20:
            trajectory = "STABLE"
        elif win_pct >= 10:
            trajectory = "PEAKING"
        else:
            trajectory = "DECLINING"

        reliability = "RELIABLE" if starts >= 5 else "MIXED" if starts >= 3 else "THIN"
        return {
            "trajectory": trajectory,
            "class_direction": "LEVEL",
            "reliability": reliability,
            "consistency_index": consistency,
            "starts": starts,
            "wins": wins,
            "win_pct": round(win_pct, 1),
        }
    except Exception:
        pass
    return {"trajectory": "UNKNOWN", "class_direction": "LEVEL", "reliability": "THIN",
            "consistency_index": 0, "starts": 0, "wins": 0, "win_pct": 0}

# ----------------------------------------------------------------
# NO BET ZONE DETECTION (feature 14)
# ----------------------------------------------------------------
def check_no_bet_zone(runners, race_meta, pressure_score):
    active = [r for r in runners if not r.get("scratched")]
    reasons = []

    if len(active) < 4:
        reasons.append("too few runners after scratchings")
    unknowns = sum(1 for r in active if r.get("early_speed_rank") == "UNKNOWN")
    if unknowns >= len(active) // 2:
        reasons.append("too many unknowns")
    first_starters = sum(1 for r in active if r.get("starts", 1) == 0)
    if first_starters >= 3:
        reasons.append("too many first starters")
    high_collision = sum(1 for r in active if r.get("collision_risk") == "HIGH")
    if high_collision >= 4:
        reasons.append("collision chaos")
    if race_meta.get("completeness_quality") == "LOW":
        reasons.append("poor data completeness")
    if pressure_score >= 8:
        reasons.append("extreme speed pressure")

    return {"no_bet": len(reasons) > 0, "reasons": reasons}

# ----------------------------------------------------------------
# FALSE FAVOURITE DETECTION (feature 16)
# ----------------------------------------------------------------
def detect_false_favourite(runners, track):
    active = [r for r in runners if not r.get("scratched")]
    if not active:
        return None
    # Identify shortest-priced runner (by internal score proxy)
    scored = [(r, r.get("internal_score", 50)) for r in active]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[0][0]
    checks = 0
    if top.get("early_speed_rank") in ["SLOW", "UNKNOWN"]:
        checks += 1
    if top.get("box_score") in ["WEAK", "AVOID"]:
        checks += 1
    bias = get_track_bias(track)
    if bias["favours"] != top.get("run_style") and bias["strength"] == "STRONG":
        checks += 1
    if top.get("form", {}).get("win_pct", 50) < 20:
        checks += 1
    if checks >= 2:
        return {"runner": top["name"], "box": top["box_num"], "severity": "HIGH" if checks >= 3 else "MODERATE"}
    return None

# ----------------------------------------------------------------
# EV + KELLY
# ----------------------------------------------------------------
def calculate_ev(win_prob, decimal_odds):
    return round((win_prob * decimal_odds) - 1, 3)

def ev_threshold(tier):
    return {"ELITE": 0.08, "HIGH": 0.10, "MODERATE": 0.12, "LOW": 0.05}.get(tier, 0.10)

def kelly_stake(bankroll, win_prob, decimal_odds, tier, bank_mode="STANDARD"):
    edge = (win_prob * decimal_odds) - 1
    if edge <= 0:
        return 0
    fractions = {"ELITE": 0.40, "HIGH": 0.30, "MODERATE": 0.20, "LOW": 0.10}
    multipliers = {"SAFE": 0.60, "STANDARD": 1.00, "AGGRESSIVE": 1.15}
    frac = fractions.get(tier, 0.20)
    mult = multipliers.get(bank_mode, 1.00)
    full_kelly = edge / (decimal_odds - 1)
    stake = bankroll * full_kelly * frac * mult
    return round(min(stake, bankroll * 0.07), 2)

# ----------------------------------------------------------------
# CONFIDENCE BREAKDOWN (feature 22)
# ----------------------------------------------------------------
def build_confidence_breakdown(speed_quality, map_quality, data_quality, form_quality):
    components = {
        "speed_confidence": speed_quality,
        "map_confidence": map_quality,
        "data_confidence": data_quality,
        "track_confidence": "HIGH",
        "market_confidence": "MODERATE",
    }
    scores = {"HIGH": 3, "MODERATE": 2, "LOW": 1, "UNKNOWN": 0}
    avg = sum(scores.get(v, 1) for v in components.values()) / len(components)
    if avg >= 2.5:
        overall = "HIGH"
    elif avg >= 1.8:
        overall = "MODERATE"
    else:
        overall = "LOW"
    return {"components": components, "overall": overall}

# ----------------------------------------------------------------
# E39 FILTER SCORES
# ----------------------------------------------------------------
def score_dif(data_trust, field_verified, integrity_ok):
    trust_map = {"HIGH": 50, "MODERATE": 30, "LOW": 0, "UNTRUSTED": -100}
    score = trust_map.get(data_trust, 0) + (30 if field_verified else 15) + (15 if integrity_ok else 8) + 5
    action = "HARD_BLOCK" if score < 20 else "SUPPRESS" if score < 60 else "NEUTRAL"
    return score, action

def score_tdf(dominance, dom_conf, separation, pace_ok):
    s = ({"TRUE_DOM":40,"CO_DOM":25,"CHAOS":0}.get(dominance,0) +
         {"HIGH":30,"MODERATE":18,"LOW":8}.get(dom_conf,0) +
         {"CLEAR":20,"NARROW":12,"CLUSTER":4}.get(separation,4) +
         {"YES":10,"NEUTRAL":5,"NO":0}.get(pace_ok,5))
    return s, "BOOST" if s >= 80 else "NEUTRAL" if s >= 65 else "SUPPRESS"

def score_vef(ev, threshold, conviction, value_status):
    if ev < 0:
        return 0, "HARD_BLOCK"
    s = ((40 if ev >= threshold + 0.10 else 20 if ev >= threshold else 0) +
         {"HIGH":30,"MODERATE":18,"LOW":8}.get(conviction,8) +
         {"IMPROVED":25,"CONFIRMED":20,"DECAYED":5}.get(value_status,20) + 6)
    return s, "BOOST" if s >= 70 else "NEUTRAL" if s >= 40 else "SUPPRESS"

def score_chf(fast_count, field_size=8):
    base = 80 if fast_count <= 1 else 55 if fast_count <= 2 else 25 if fast_count <= 3 else 0
    adj = 10 if field_size <= 8 else 0 if field_size <= 10 else -15
    total = base + adj
    return total, "HARD_BLOCK" if total < 20 else "SUPPRESS" if total < 40 else "NEUTRAL"

def score_mtf(trap_type):
    s = {"NO_TRAP":80,"STEAM":40,"FALSE_FAV":35,"REVERSAL":10,"BLOCK":0}.get(trap_type,40)
    return s, "HARD_BLOCK" if trap_type == "BLOCK" else "NEUTRAL" if s >= 60 else "SUPPRESS"

# ----------------------------------------------------------------
# MAIN SCORE RACE
# ----------------------------------------------------------------
def score_race(race, runners, track, anchor_time=None):
    """
    Full V7 scoring pipeline. Returns scored dict with decision, confidence,
    breakdown, filters, audit trail (feature 23), and scorer version (feature 25).
    Reads system_state weights (fix 7) and AEEE multipliers (fix 1) on every call
    via a 60-second cache so admin changes propagate without a restart.
    """
    # Load live admin settings and any promoted AEEE adjustments
    settings = _load_scoring_settings()
    aeee     = _load_aeee_multipliers()

    active = [r for r in runners if not r.get("scratched")]
    if len(active) < 2:
        return _pass_result("Not enough active runners")

    # Run engines
    runners, pressure_score = score_early_speed(active)
    runners = map_first_bend(runners, track)
    distance_m = _parse_distance(race.get("distance", "400m"))
    shape = build_race_shape(runners, track, distance_m)

    # No-bet zone check (feature 14)
    nbz = check_no_bet_zone(runners, race, pressure_score)
    if nbz["no_bet"]:
        return _pass_result(f"No-bet zone: {', '.join(nbz['reasons'])}")

    # Score each runner
    runner_scores = []
    for r in runners:
        fatigue = score_fatigue(r)
        form = score_form(r)
        bias_align = check_bias_alignment(r, track)

        pts = 0
        if r.get("early_speed_rank") == "FAST":   pts += 30
        elif r.get("early_speed_rank") == "MID":   pts += 15
        if r.get("box_score") == "STRONG":          pts += 20
        elif r.get("box_score") == "NEUTRAL":       pts += 10
        elif r.get("box_score") == "WEAK":          pts -= 10
        elif r.get("box_score") == "AVOID":         pts -= 20
        if bias_align == "ALIGNED":                 pts += 15
        elif bias_align == "OPPOSED":               pts -= 10
        if form["trajectory"] == "ACCELERATING":   pts += 15
        elif form["trajectory"] == "STABLE":        pts += 8
        elif form["trajectory"] == "DECLINING":     pts -= 10

        # Settings fix 7: apply admin-configured weights
        tempo_w  = float(settings.get("tempo_weight", 1.0))
        traf_pen = float(settings.get("traffic_penalty", 0.8))
        close_b  = float(settings.get("closer_boost", 1.1))
        fade_pen = float(settings.get("fade_penalty", 0.9))

        # Tempo weight — amplifies or reduces race-shape beneficiary bonus
        if shape["beneficiary"] == r.get("run_style"):
            pts += int(10 * tempo_w)
        # Traffic/wide penalty — increases cost of poor positioning
        if r.get("collision_risk") == "HIGH":
            pts -= int(5 / traf_pen)   # higher traf_pen = less penalty
        # Closer boost — rewards closers in collapse-risk races
        if shape.get("collapse_risk") == "HIGH" and r.get("run_style") in ("CHASER","WIDE","TRAILER"):
            pts = int(pts * close_b)
        # Fade penalty — punishes leaders in high-pressure races
        if shape.get("pace_type") == "HOT" and r.get("run_style") == "LEADER":
            pts = int(pts * fade_pen)

        if fatigue["crash_map"] == "HIGH_RISK":     pts -= 30
        elif fatigue["crash_map"] == "CAUTION":     pts -= 10
        pts += min(form.get("consistency_index", 0) // 10, 10)

        r["internal_score"] = pts
        runner_scores.append({
            "runner": r, "score": pts,
            "form": form, "fatigue": fatigue, "bias_align": bias_align
        })

    runner_scores.sort(key=lambda x: x["score"], reverse=True)
    if not runner_scores:
        return _pass_result("No scoreable runners")

    top = runner_scores[0]
    second = runner_scores[1] if len(runner_scores) > 1 else None
    gap = (top["score"] - second["score"]) if second else 99
    separation = "CLEAR" if gap >= 15 else "NARROW" if gap >= 8 else "CLUSTER"
    dominance = "TRUE_DOM" if separation == "CLEAR" else "CO_DOM" if separation == "NARROW" else "CHAOS"

    # False favourite detection (feature 16)
    ff = detect_false_favourite(runners, track)

    # Filter scores
    # Settings fix 7: use admin-configured EV threshold for VEF
    admin_ev_threshold = float(settings.get("ev_threshold", 0.10))
    # AEEE fix 1: apply edge-type multiplier if one exists for the top runner's edge
    top_edge_type = top["runner"].get("edge_type", "STRUCTURAL")
    aeee_mult = aeee.get(top_edge_type, aeee.get("ALL", 1.0))
    effective_ev_threshold = round(admin_ev_threshold * aeee_mult, 4)

    dif_s, dif_a = score_dif("HIGH", True, True)
    tdf_s, tdf_a = score_tdf(dominance, "HIGH" if separation == "CLEAR" else "MODERATE", separation,
                              "YES" if shape["beneficiary"] == top["runner"].get("run_style") else "NEUTRAL")
    chf_s, chf_a = score_chf(sum(1 for r in runners if r.get("early_speed_rank") == "FAST"), len(runners))
    vef_s, vef_a = score_vef(0.12, effective_ev_threshold, "MODERATE", "CONFIRMED")
    mtf_type = "FALSE_FAV" if ff else "NO_TRAP"
    mtf_s, mtf_a = score_mtf(mtf_type)

    # Hard blocks
    if dif_a == "HARD_BLOCK":
        return _pass_result("Data integrity block")
    if chf_a == "HARD_BLOCK":
        return _pass_result("Chaos filter block")
    if top["fatigue"]["crash_map"] == "HIGH_RISK":
        return _pass_result("HIGH RISK crash map")
    if top["runner"].get("box_score") in ["WEAK", "AVOID"]:
        confidence_tier = "MODERATE"
        decision = "SESSION"
    elif tdf_a == "BOOST" and dif_s >= 60 and mtf_a != "HARD_BLOCK":
        confidence_tier = "HIGH"
        decision = "BET"
    elif tdf_a == "NEUTRAL":
        confidence_tier = "MODERATE"
        decision = "SESSION"
    else:
        return _pass_result("Insufficient dominance")

    # Confidence breakdown (feature 22)
    speed_q = "HIGH" if top["runner"].get("early_speed_rank") == "FAST" else "MODERATE"
    map_q = "HIGH" if top["runner"].get("box_score") == "STRONG" else "MODERATE"
    data_q = race.get("completeness_quality", "MODERATE")
    form_q = "HIGH" if top["form"]["trajectory"] == "ACCELERATING" else "MODERATE"
    conf_breakdown = build_confidence_breakdown(speed_q, map_q, data_q, form_q)

    # Audit trail (feature 23)
    audit = {
        "engines_fired": ["E23", "E24", "E25", "E26", "E27", "E28"],
        "key_drivers": {
            "speed_rank": top["runner"].get("early_speed_rank"),
            "box_score": top["runner"].get("box_score"),
            "bias_align": top["bias_align"],
            "trajectory": top["form"]["trajectory"],
            "crash_map": top["fatigue"]["crash_map"],
            "separation": separation,
        },
        "filter_outcomes": {
            "DIF": dif_a, "TDF": tdf_a, "CHF": chf_a, "VEF": vef_a, "MTF": mtf_a
        },
        # Settings fix 7: record which weights were active
        "active_settings": {
            "ev_threshold":      admin_ev_threshold,
            "effective_ev_threshold": effective_ev_threshold,
            "tempo_weight":      settings.get("tempo_weight"),
            "traffic_penalty":   settings.get("traffic_penalty"),
            "closer_boost":      settings.get("closer_boost"),
            "fade_penalty":      settings.get("fade_penalty"),
        },
        # AEEE fix 1: record which adjustments were applied
        "aeee_multipliers": aeee if aeee else {},
        "scorer_version": SCORER_VERSION,
        "scored_at": datetime.utcnow().isoformat(),
    }

    return {
        "decision": decision,
        "confidence": confidence_tier,
        "confidence_breakdown": conf_breakdown,
        "selection": top["runner"]["name"],
        "box": top["runner"]["box_num"],
        "run_style": top["runner"].get("run_style"),
        "trainer": top["runner"].get("trainer"),
        "score": top["score"],
        "separation": separation,
        "race_shape": shape["shape_summary"],
        "pace_type": shape["pace_type"],
        "beneficiary": shape["beneficiary"],
        "collapse_risk": shape["collapse_risk"],
        "pressure_score": pressure_score,
        "false_favourite": ff,
        "bias_alignment": top["bias_align"],
        "crash_map": top["fatigue"]["crash_map"],
        "box_score": top["runner"].get("box_score"),
        "form": top["form"],
        "filters": {
            "DIF": {"score": dif_s, "action": dif_a},
            "TDF": {"score": tdf_s, "action": tdf_a},
            "VEF": {"score": vef_s, "action": vef_a},
            "CHF": {"score": chf_s, "action": chf_a},
            "MTF": {"score": mtf_s, "action": mtf_a},
        },
        "audit": audit,
        "scorer_version": SCORER_VERSION,
        "all_runners": [
            {
                "name": s["runner"]["name"],
                "box": s["runner"]["box_num"],
                "score": s["score"],
                "speed": s["runner"].get("early_speed_rank"),
                "style": s["runner"].get("run_style"),
                "box_score": s["runner"].get("box_score"),
                "crash_map": s["fatigue"]["crash_map"],
                "consistency": s["form"].get("consistency_index", 0),
            }
            for s in runner_scores
        ],
    }

def _pass_result(reason):
    return {
        "decision": "PASS",
        "confidence": "INSUFFICIENT",
        "pass_reason": reason,
        "scorer_version": SCORER_VERSION,
    }

def _parse_distance(dist_str):
    try:
        return int(str(dist_str).replace("m", "").strip())
    except Exception:
        return 400

def save_scored_race(race_uid, scored):
    """Save scoring output to Supabase (separate from raw data - feature A4)."""
    try:
        from db import get_db
        db = get_db()
        db.table("scored_races").upsert({
            "race_uid": race_uid,
            "decision": scored.get("decision"),
            "confidence": scored.get("confidence"),
            "selection": scored.get("selection"),
            "box_num": scored.get("box"),
            "race_shape": scored.get("race_shape"),
            "pace_type": scored.get("pace_type"),
            "collapse_risk": scored.get("collapse_risk"),
            "pressure_score": scored.get("pressure_score"),
            "separation": scored.get("separation"),
            "crash_map": scored.get("crash_map"),
            "filters_json": str(scored.get("filters", {})),
            "audit_json": str(scored.get("audit", {})),
            "confidence_breakdown_json": str(scored.get("confidence_breakdown", {})),
            "scorer_version": SCORER_VERSION,
            "scored_at": datetime.utcnow().isoformat(),
        }, on_conflict="race_uid").execute()

        # Update lifecycle
        from data_engine import update_lifecycle
        update_lifecycle(race_uid, "scored")
    except Exception as e:
        log.error(f"Save scored race failed {race_uid}: {e}")
