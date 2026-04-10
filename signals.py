"""
signals.py - V9 Signal Generation Engine
Converts scorer output to SNIPER / VALUE / GEM / WATCH / RISK / NO_BET

LIVE MODE:  demo_signal() is blocked — raises EnvViolation
TEST MODE:  demo_signal() allowed for seeding/stress testing
"""
import logging
from datetime import datetime
from env import env, no_fake_data

log = logging.getLogger(__name__)

SIGNALS = ["SNIPER", "VALUE", "GEM", "WATCH", "RISK", "NO_BET"]

SIGNAL_THRESHOLDS = {
    "confidence_sniper": 0.80,
    "confidence_value":  0.65,
    "confidence_gem":    0.55,
    "ev_sniper":         0.15,
    "ev_value":          0.08,
    "ev_gem":            0.04,
    "risk_cap":          0.45,
}


# Scorer confidence tier → float
_CONF_TIER_MAP = {"ELITE": 0.90, "HIGH": 0.75, "MODERATE": 0.55, "LOW": 0.35, "INSUFFICIENT": 0.15}
_DECISION_EV_MAP = {"BET": 0.14, "SESSION": 0.07, "PASS": -0.05, "LOCK": 0.00}


def normalise_scored(scored: dict) -> dict:
    """Bridge scorer.score_race() string-tier output → generate_signal() float inputs."""
    raw_conf = scored.get("confidence", 0)
    if isinstance(raw_conf, str):
        conf_float = _CONF_TIER_MAP.get(raw_conf.upper(), 0.40)
    else:
        try: conf_float = float(raw_conf)
        except (TypeError, ValueError): conf_float = 0.40

    raw_ev = scored.get("ev")
    if raw_ev is None:
        decision = scored.get("decision", "PASS")
        base_ev = _DECISION_EV_MAP.get(decision, 0.00)
        chf_score = (scored.get("filters") or {}).get("CHF", {}).get("score", 50)
        ev_float = round(base_ev + (chf_score - 50) / 1000, 3)
    else:
        try: ev_float = float(raw_ev)
        except (TypeError, ValueError): ev_float = 0.0

    return {**scored, "confidence": conf_float, "ev": ev_float}


def generate_signal(scored: dict, settings: dict | None = None) -> dict:
    scored = normalise_scored(scored)
    thresholds = {**SIGNAL_THRESHOLDS, **(settings or {})}
    confidence = float(scored.get("confidence") or 0)
    ev = float(scored.get("ev") or 0)
    chaos = float(scored.get("chaos_score") or 5)
    collapse_risk = scored.get("collapse_risk", "MODERATE")
    separation = scored.get("separation", "MODERATE")
    false_fav = bool(scored.get("false_favourite"))
    filters = scored.get("filters") or {}

    risk_flags = []
    if chaos > 7:           risk_flags.append("HIGH_CHAOS")
    if collapse_risk == "HIGH": risk_flags.append("COLLAPSE_RISK")
    if false_fav:           risk_flags.append("FALSE_FAVOURITE")
    if separation == "CLUSTER": risk_flags.append("CLUSTER_FIELD")
    if confidence < thresholds["risk_cap"] and ev < 0: risk_flags.append("NEGATIVE_EV")
    chf = filters.get("CHF", {})
    if chf.get("score", 100) < 35: risk_flags.append("CHF_FAIL")

    if len(risk_flags) >= 3 or (len(risk_flags) >= 2 and "NEGATIVE_EV" in risk_flags):
        signal = "RISK"
    elif confidence < 0.40 and ev < 0:
        signal = "NO_BET"
    elif (confidence >= thresholds["confidence_sniper"] and ev >= thresholds["ev_sniper"]
          and not risk_flags and separation == "CLEAR"):
        signal = "SNIPER"
    elif (confidence >= thresholds["confidence_value"] and ev >= thresholds["ev_value"]
          and len(risk_flags) <= 1):
        signal = "VALUE"
    elif confidence >= thresholds["confidence_gem"] and ev >= thresholds["ev_gem"] and false_fav:
        signal = "GEM"
    elif confidence >= 0.50 and ev >= 0:
        signal = "WATCH"
    elif len(risk_flags) >= 2:
        signal = "RISK"
    else:
        signal = "NO_BET"

    alert_level = _get_alert_level(signal, confidence, ev)
    top = scored.get("top_runner") or {}
    return {
        "signal":       signal,
        "confidence":   round(confidence, 3),
        "ev":           round(ev, 3),
        "risk_flags":   risk_flags,
        "alert_level":  alert_level,
        "hot_bet":      signal == "SNIPER" or (signal == "VALUE" and ev >= 0.18),
        "top_runner":   top.get("name"),
        "top_box":      top.get("box_num"),
        "top_odds":     top.get("odds"),
        "generated_at": datetime.utcnow().isoformat(),
        "env_mode":     env.mode,
    }


def _get_alert_level(signal: str, confidence: float, ev: float) -> str:
    if signal == "SNIPER":                        return "HOT"
    if signal == "VALUE" and ev >= 0.15:          return "HIGH"
    if signal in ("VALUE", "GEM"):                return "MEDIUM"
    if signal == "WATCH":                         return "LOW"
    return "NONE"


def generate_signals_for_board(races_scored: list, settings: dict | None = None) -> list:
    result = []
    for item in races_scored:
        race   = item.get("race") or {}
        scored = item.get("scored") or {}
        sig    = generate_signal(scored, settings)
        result.append({
            "race_uid":  race.get("race_uid"),
            "track":     race.get("track"),
            "race_num":  race.get("race_num"),
            "jump_time": race.get("jump_time"),
            "distance":  race.get("distance"),
            "grade":     race.get("grade"),
            **sig,
        })
    priority = {"HOT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "NONE": 4}
    result.sort(key=lambda x: (priority.get(x["alert_level"], 4), x.get("jump_time", "")))
    return result


def save_signal(race_uid: str, signal_data: dict):
    try:
        from db import get_db, safe_query, T
        safe_query(lambda: get_db().table(T("signals")).upsert({
            "race_uid":     race_uid,
            "signal":       signal_data["signal"],
            "confidence":   signal_data["confidence"],
            "ev":           signal_data["ev"],
            "alert_level":  signal_data["alert_level"],
            "hot_bet":      signal_data["hot_bet"],
            "risk_flags":   signal_data["risk_flags"],
            "top_runner":   signal_data.get("top_runner"),
            "top_odds":     signal_data.get("top_odds"),
            "generated_at": signal_data["generated_at"],
        }, on_conflict="race_uid").execute())
    except Exception as e:
        log.error(f"Save signal failed: {e}")


def get_signal(race_uid: str) -> dict | None:
    try:
        from db import get_db, safe_query, T
        return safe_query(
            lambda: get_db().table(T("signals")).select("*").eq("race_uid", race_uid).single().execute().data
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────
# DEMO / FAKE SIGNAL — TEST MODE ONLY
# ─────────────────────────────────────────────────────────────────
@no_fake_data
def demo_signal(race_num: int) -> dict:
    """
    Deterministic fake signal for UI/stress testing.
    BLOCKED in LIVE mode via @no_fake_data decorator.
    """
    patterns = [
        {"signal": "SNIPER", "confidence": 0.88, "ev": 0.21, "alert_level": "HOT",    "hot_bet": True},
        {"signal": "VALUE",  "confidence": 0.72, "ev": 0.13, "alert_level": "HIGH",   "hot_bet": False},
        {"signal": "GEM",    "confidence": 0.61, "ev": 0.09, "alert_level": "MEDIUM", "hot_bet": False},
        {"signal": "WATCH",  "confidence": 0.55, "ev": 0.04, "alert_level": "LOW",    "hot_bet": False},
        {"signal": "VALUE",  "confidence": 0.69, "ev": 0.17, "alert_level": "HIGH",   "hot_bet": True},
        {"signal": "RISK",   "confidence": 0.38, "ev": -0.05,"alert_level": "NONE",   "hot_bet": False},
        {"signal": "SNIPER", "confidence": 0.91, "ev": 0.24, "alert_level": "HOT",    "hot_bet": True},
        {"signal": "NO_BET", "confidence": 0.31, "ev": -0.12,"alert_level": "NONE",   "hot_bet": False},
    ]
    p = patterns[race_num % len(patterns)]
    return {**p, "risk_flags": [], "generated_at": datetime.utcnow().isoformat(), "env_mode": "TEST"}


def get_signal_or_demo(race_uid: str, race_num: int = 0) -> dict | None:
    """
    Production path: get from DB, no fallback.
    Test path: get from DB, fallback to demo.
    """
    real = get_signal(race_uid)
    if real:
        return real
    if env.is_test:
        return demo_signal(race_num)
    return None
