"""
exotics.py - V8 Exotics Engine
Exacta, trifecta, boxed combinations, multi bets
Auto-suggestions based on signal type
SNIPER = anchor | VALUE = support | GEM = booster
"""
import logging
from itertools import permutations, combinations

log = logging.getLogger(__name__)

EXOTIC_TYPES = ["exacta", "trifecta", "exacta_box", "trifecta_box", "first4", "multi"]

UNIT_COST = 1.0  # base unit stake

RISK_PROFILES = {
    "exacta":       {"label": "Exacta",        "risk": "MEDIUM", "base_units": 1},
    "trifecta":     {"label": "Trifecta",       "risk": "HIGH",   "base_units": 1},
    "exacta_box":   {"label": "Exacta (Boxed)", "risk": "LOW",    "base_units": 2},
    "trifecta_box": {"label": "Trifecta (Box)", "risk": "MEDIUM", "base_units": 6},
    "first4":       {"label": "First 4",        "risk": "HIGH",   "base_units": 24},
    "multi":        {"label": "Multi Leg",      "risk": "VERY_HIGH", "base_units": 1},
}


# ─────────────────────────────────────────────────────────────────
# COMBINATION CALCULATORS
# ─────────────────────────────────────────────────────────────────
def calc_exacta(first: str, second: str, odds_first: float, odds_second: float,
                unit: float = UNIT_COST) -> dict:
    """Straight exacta: first then second."""
    cost = unit
    est_return = unit * odds_first * (odds_second * 0.5)  # simplified div estimate
    return {
        "type": "exacta",
        "label": "Exacta",
        "selections": [first, second],
        "order": f"{first} → {second}",
        "cost": round(cost, 2),
        "est_return": round(est_return, 2),
        "est_profit": round(est_return - cost, 2),
        "risk": "MEDIUM",
    }

def calc_exacta_box(runners: list[str], odds: list[float], unit: float = UNIT_COST) -> dict:
    """Boxed exacta: all permutations of 2 from selected."""
    n = len(runners)
    combos = list(permutations(range(n), 2))
    cost = unit * len(combos)
    avg_odds = sum(odds) / len(odds) if odds else 5
    est_return = unit * avg_odds * (avg_odds * 0.4)
    return {
        "type": "exacta_box",
        "label": f"Exacta Box ({n} runners)",
        "selections": runners,
        "combinations": len(combos),
        "cost": round(cost, 2),
        "est_return": round(est_return, 2),
        "est_profit": round(est_return - cost, 2),
        "risk": "LOW",
    }

def calc_trifecta(runners: list[str], odds: list[float], unit: float = UNIT_COST) -> dict:
    """Straight trifecta: exact 1-2-3."""
    cost = unit
    if len(odds) >= 3:
        est_return = unit * odds[0] * (odds[1] * 0.45) * (odds[2] * 0.3)
    else:
        est_return = unit * 150  # fallback estimate
    return {
        "type": "trifecta",
        "label": "Trifecta",
        "selections": runners[:3],
        "order": " → ".join(runners[:3]),
        "cost": round(cost, 2),
        "est_return": round(est_return, 2),
        "est_profit": round(est_return - cost, 2),
        "risk": "HIGH",
    }

def calc_trifecta_box(runners: list[str], odds: list[float], unit: float = UNIT_COST) -> dict:
    """Boxed trifecta: all permutations of 3."""
    n = len(runners)
    if n < 3:
        return {"error": "Need at least 3 runners for boxed trifecta"}
    combos = list(permutations(range(n), 3))
    cost = unit * len(combos)
    avg_odds = sum(odds) / len(odds) if odds else 6
    est_return = unit * avg_odds * (avg_odds * 0.4) * (avg_odds * 0.25)
    return {
        "type": "trifecta_box",
        "label": f"Trifecta Box ({n} runners, {len(combos)} combos)",
        "selections": runners,
        "combinations": len(combos),
        "cost": round(cost, 2),
        "est_return": round(min(est_return, cost * 80), 2),
        "est_profit": round(min(est_return, cost * 80) - cost, 2),
        "risk": "MEDIUM",
    }

def calc_first4_box(runners: list[str], odds: list[float], unit: float = UNIT_COST) -> dict:
    """Boxed First 4: all permutations of 4."""
    n = len(runners)
    if n < 4:
        return {"error": "Need at least 4 runners"}
    combos = list(permutations(range(n), 4))
    cost = unit * len(combos)
    avg_odds = sum(odds) / len(odds) if odds else 7
    est_return = unit * avg_odds ** 2 * 3
    return {
        "type": "first4",
        "label": f"First 4 Box ({n} runners, {len(combos)} combos)",
        "selections": runners,
        "combinations": len(combos),
        "cost": round(cost, 2),
        "est_return": round(min(est_return, cost * 120), 2),
        "est_profit": round(min(est_return, cost * 120) - cost, 2),
        "risk": "HIGH",
    }


# ─────────────────────────────────────────────────────────────────
# MULTI BUILDER
# ─────────────────────────────────────────────────────────────────
def calc_multi(legs: list[dict], unit: float = UNIT_COST) -> dict:
    """Multi bet: win all legs."""
    if not legs:
        return {"error": "No legs provided"}
    cost = unit
    combined_odds = 1.0
    for leg in legs:
        combined_odds *= float(leg.get("odds", 2.0))
    est_return = unit * combined_odds
    return {
        "type": "multi",
        "label": f"{len(legs)}-Leg Multi",
        "legs": legs,
        "combined_odds": round(combined_odds, 2),
        "cost": round(cost, 2),
        "est_return": round(est_return, 2),
        "est_profit": round(est_return - cost, 2),
        "risk": "VERY_HIGH" if len(legs) >= 4 else "HIGH",
    }


# ─────────────────────────────────────────────────────────────────
# AUTO-SUGGESTIONS
# ─────────────────────────────────────────────────────────────────
def auto_suggest(signal: str, runners: list[dict], unit: float = UNIT_COST) -> list[dict]:
    """
    Auto-generate exotic suggestions based on signal type.
    SNIPER = anchor in 1st position
    VALUE  = support in 2nd/3rd
    GEM    = booster (wider boxed coverage)
    """
    if not runners:
        return []

    # Sort by confidence/rank
    sorted_runners = sorted(runners, key=lambda r: float(r.get("confidence") or 0), reverse=True)
    names = [r.get("name", f"#{r.get('box_num', i+1)}") for i, r in enumerate(sorted_runners)]
    odds_list = [float(r.get("odds") or 3.0) for r in sorted_runners]

    suggestions = []

    if signal == "SNIPER":
        # Straight exacta: #1 → #2
        if len(names) >= 2:
            suggestions.append(calc_exacta(names[0], names[1], odds_list[0], odds_list[1], unit))
        # Exacta box: top 2
        if len(names) >= 2:
            suggestions.append(calc_exacta_box(names[:2], odds_list[:2], unit))
        # Trifecta: top 3 straight
        if len(names) >= 3:
            suggestions.append(calc_trifecta(names[:3], odds_list[:3], unit))

    elif signal == "VALUE":
        # Exacta box: top 3 (anchor uncertain)
        if len(names) >= 3:
            suggestions.append(calc_exacta_box(names[:3], odds_list[:3], unit))
        # Trifecta box: top 3
        if len(names) >= 3:
            suggestions.append(calc_trifecta_box(names[:3], odds_list[:3], unit))

    elif signal == "GEM":
        # Wider coverage — boxed with top 4
        if len(names) >= 3:
            suggestions.append(calc_exacta_box(names[:3], odds_list[:3], unit))
        if len(names) >= 4:
            suggestions.append(calc_trifecta_box(names[:4], odds_list[:4], unit))

    elif signal in ("WATCH",):
        # Conservative: exacta box top 2 only
        if len(names) >= 2:
            suggestions.append(calc_exacta_box(names[:2], odds_list[:2], unit))

    # Tag each with signal
    for s in suggestions:
        s["signal"] = signal

    return suggestions


# ─────────────────────────────────────────────────────────────────
# API HANDLER
# ─────────────────────────────────────────────────────────────────
def handle_calculate(data: dict) -> dict:
    exotic_type = data.get("type", "exacta_box")
    runners = data.get("runners", [])
    unit = float(data.get("unit", UNIT_COST))
    names = [r.get("name", str(i)) for i, r in enumerate(runners)]
    odds_list = [float(r.get("odds", 3.0)) for r in runners]

    if exotic_type == "exacta":
        if len(runners) < 2:
            return {"error": "exacta requires at least 2 runners"}
        return calc_exacta(names[0], names[1], odds_list[0], odds_list[1], unit)
    elif exotic_type == "exacta_box":
        return calc_exacta_box(names, odds_list, unit)
    elif exotic_type == "trifecta":
        if len(runners) < 3:
            return {"error": "trifecta requires at least 3 runners"}
        return calc_trifecta(names, odds_list, unit)
    elif exotic_type == "trifecta_box":
        return calc_trifecta_box(names, odds_list, unit)
    elif exotic_type == "first4":
        return calc_first4_box(names, odds_list, unit)
    elif exotic_type == "multi":
        return calc_multi(data.get("legs", []), unit)
    return {"error": f"Unknown exotic type '{exotic_type}'. Valid: exacta, exacta_box, trifecta, trifecta_box, first4, multi"}
