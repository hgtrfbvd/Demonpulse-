"""
safety.py - Live race safety, bet validity, circuit breaker
Feature coverage: C9-C13, G35
"""
import time
import logging
import threading
from datetime import datetime

log = logging.getLogger(__name__)

# ----------------------------------------------------------------
# BETTING WINDOW RULES (feature 11)
# ----------------------------------------------------------------
MIN_MINUTES_BEFORE_JUMP = 2     # too late if less than 2 min
MAX_MINUTES_BEFORE_JUMP = 90    # too early if more than 90 min

def check_betting_window(jump_time_str, anchor_time_str):
    """
    Returns: "VALID", "TOO_EARLY", "TOO_LATE", "UNKNOWN"
    """
    if not jump_time_str or not anchor_time_str:
        return "UNKNOWN"
    try:
        jh, jm = map(int, jump_time_str.split(":"))
        ah, am = map(int, anchor_time_str.split(":"))
        diff = (jh * 60 + jm) - (ah * 60 + am)
        if diff < MIN_MINUTES_BEFORE_JUMP:
            return "TOO_LATE"
        elif diff > MAX_MINUTES_BEFORE_JUMP:
            return "TOO_EARLY"
        return "VALID"
    except Exception:
        return "UNKNOWN"

# ----------------------------------------------------------------
# RECOMMENDATION EXPIRY (feature 9)
# ----------------------------------------------------------------
_recommendations = {}

def store_recommendation(race_uid, rec, jump_time_str):
    """Store recommendation with expiry."""
    _recommendations[race_uid] = {
        "rec": rec,
        "stored_at": time.time(),
        "jump_time": jump_time_str,
        "expired": False,
    }

def get_recommendation(race_uid):
    return _recommendations.get(race_uid)

def is_recommendation_valid(race_uid, anchor_time_str=None):
    """Check if stored recommendation is still valid."""
    stored = _recommendations.get(race_uid)
    if not stored or stored.get("expired"):
        return False, "No valid recommendation"

    # Expired by age (> 10 minutes old)
    age = time.time() - stored["stored_at"]
    if age > 600:
        stored["expired"] = True
        return False, "Recommendation expired (>10 min old)"

    # Check betting window
    if anchor_time_str and stored.get("jump_time"):
        window = check_betting_window(stored["jump_time"], anchor_time_str)
        if window == "TOO_LATE":
            stored["expired"] = True
            return False, "Past betting window"

    return True, "Valid"

def expire_recommendation(race_uid, reason="manual"):
    if race_uid in _recommendations:
        _recommendations[race_uid]["expired"] = True
        log.info(f"Recommendation expired for {race_uid}: {reason}")

# ----------------------------------------------------------------
# PRE-JUMP REVALIDATION (feature 10)
# ----------------------------------------------------------------
def revalidate_before_bet(race_uid, original_rec, current_runners, current_odds=None, anchor_time=None):
    """
    Re-check conditions before finalising a bet.
    Returns: (valid: bool, issues: list)
    """
    issues = []

    # Check for new scratchings
    original_runners = original_rec.get("all_runners", [])
    original_names = {r["name"] for r in original_runners}
    current_names = {r["name"] for r in current_runners if not r.get("scratched")}
    new_scratches = original_names - current_names
    if new_scratches:
        issues.append(f"New scratching since recommendation: {', '.join(new_scratches)}")

    # Check if selected runner is still running
    selection = original_rec.get("selection")
    selection_scratched = any(
        r.get("name") == selection and r.get("scratched")
        for r in current_runners
    )
    if selection_scratched:
        issues.append(f"Selected runner {selection} has been scratched")

    # Check odds drift (feature C - odds drift invalidation)
    if current_odds and original_rec.get("odds"):
        try:
            drift = abs(current_odds - original_rec["odds"]) / original_rec["odds"]
            if drift > 0.30:
                issues.append(f"Odds drifted {round(drift*100)}% since recommendation")
        except Exception:
            pass

    # Check confidence didn't drop
    original_conf = original_rec.get("confidence", "MODERATE")
    conf_levels = {"ELITE": 4, "HIGH": 3, "MODERATE": 2, "LOW": 1, "INSUFFICIENT": 0}
    if conf_levels.get(original_conf, 0) < 2:
        issues.append("Confidence below minimum threshold")

    # Check betting window
    if anchor_time and original_rec.get("jump_time"):
        window = check_betting_window(original_rec["jump_time"], anchor_time)
        if window == "TOO_LATE":
            issues.append("Race jump is imminent — betting window closed")
        elif window == "TOO_EARLY":
            issues.append("Too early to bet — wait for closer to jump")

    return len(issues) == 0, issues

# ----------------------------------------------------------------
# CONFIDENCE DECAY (feature 12)
# ----------------------------------------------------------------
def apply_confidence_decay(confidence, minutes_since_scored):
    """Reduce confidence if data is getting stale near jump."""
    decay_map = {
        "ELITE": [(30, "HIGH"), (60, "MODERATE")],
        "HIGH": [(30, "MODERATE"), (60, "LOW")],
        "MODERATE": [(30, "LOW"), (60, "INSUFFICIENT")],
    }
    thresholds = decay_map.get(confidence, [])
    # Sort descending so highest threshold wins
    for minutes, new_confidence in sorted(thresholds, key=lambda x: x[0], reverse=True):
        if minutes_since_scored >= minutes:
            return new_confidence
    return confidence

# ----------------------------------------------------------------
# SCRATCH TIMING WEIGHTING (feature 13)
# ----------------------------------------------------------------
def weight_scratch_impact(runners, scratch_timing="early"):
    """
    Weight the impact of scratchings based on timing.
    Late scratches require full map recalculation.
    """
    active = [r for r in runners if not r.get("scratched")]
    scratched = [r for r in runners if r.get("scratched")]

    late_scratches = [r for r in scratched if r.get("scratch_timing") == "late"]

    return {
        "requires_rescore": len(late_scratches) > 0,
        "late_scratch_count": len(late_scratches),
        "active_runners": len(active),
        "impact": "HIGH" if len(late_scratches) >= 1 else "LOW",
    }

# ----------------------------------------------------------------
# CIRCUIT BREAKER (feature 35)
# ----------------------------------------------------------------
class CircuitBreaker:
    def __init__(self):
        self.failures = 0
        self.open = False
        self.opened_at = None
        self.threshold = 5
        self.reset_after = 300
        self._lock = threading.Lock()

    def record_failure(self, reason=""):
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold and not self.open:
                self.open = True
                self.opened_at = time.time()
                log.warning(f"CIRCUIT BREAKER OPENED: {self.failures} failures. Reason: {reason}")

    def record_success(self):
        with self._lock:
            self.failures = max(0, self.failures - 1)
            if self.open and (time.time() - (self.opened_at or 0)) > self.reset_after:
                self.open = False
                self.failures = 0
                log.info("Circuit breaker reset")

    def is_open(self):
        with self._lock:
            if self.open and (time.time() - (self.opened_at or 0)) > self.reset_after:
                self.open = False
                self.failures = 0
                return False
            return self.open

    def status(self):
        with self._lock:
            return {
                "open": self.open,
                "failures": self.failures,
                "threshold": self.threshold,
                "opened_at": datetime.fromtimestamp(self.opened_at).isoformat() if self.opened_at else None,
            }

circuit_breaker = CircuitBreaker()

# ----------------------------------------------------------------
# MARKET VOLATILITY INDEX (feature 15)
# ----------------------------------------------------------------
def calculate_mvi(odds_snapshots):
    """
    Calculate market volatility index from a list of odds snapshots.
    Returns 0-100 score. Higher = more volatile.
    """
    if len(odds_snapshots) < 2:
        return 0
    try:
        diffs = []
        for i in range(1, len(odds_snapshots)):
            prev = odds_snapshots[i-1]
            curr = odds_snapshots[i]
            for runner in curr:
                prev_odds = next((r["odds"] for r in prev if r["name"] == runner["name"]), None)
                if prev_odds:
                    diff = abs(runner["odds"] - prev_odds) / prev_odds
                    diffs.append(diff)
        if not diffs:
            return 0
        avg_drift = sum(diffs) / len(diffs)
        return min(100, round(avg_drift * 100 * 10))
    except Exception:
        return 0
