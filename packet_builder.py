"""
packet_builder.py - Build minimal pre-scored packets for Claude
Feature coverage: E24, J53, J55
Claude receives ONLY this. No raw data. No board rebuilding.
"""
import logging
from datetime import datetime

log = logging.getLogger(__name__)

PACKET_VERSION = "1.0.0"
MAX_RUNNERS_IN_PACKET = 4
MAX_PACKET_CHARS = 1200


def build_packet(race, scored, runners, bankroll=1000, bank_mode="STANDARD", anchor_time=None):
    """
    Build a minimal pre-scored packet for Claude to interpret.
    Returns a compact string Claude can work with in ~400 tokens.
    """
    if not scored or scored.get("decision") == "PASS":
        return _build_pass_packet(race, scored)

    # Top runners only (feature J55 - packet size limit)
    top_runners = (scored.get("all_runners") or [])[:MAX_RUNNERS_IN_PACKET]

    runner_lines = []
    for r in top_runners:
        line = (
            f"  Box {r['box']} {r['name']} "
            f"| {r.get('speed','?')} speed "
            f"| {r.get('style','?')} "
            f"| box={r.get('box_score','?')} "
            f"| crash={r.get('crash_map','?')} "
            f"| score={r.get('score',0)}"
        )
        runner_lines.append(line)

    filters = scored.get("filters", {})
    filter_str = " ".join(
        f"{k}={v['score']}[{v['action']}]"
        for k, v in filters.items()
    )

    ff = scored.get("false_favourite")
    ff_str = f"FALSE_FAV: {ff['runner']} (Box {ff['box']}) severity={ff['severity']}" if ff else "FALSE_FAV: NONE"

    conf_breakdown = scored.get("confidence_breakdown", {})
    conf_components = conf_breakdown.get("components", {})
    conf_str = " ".join(f"{k.replace('_confidence','')}={v}" for k, v in conf_components.items())

    packet = f"""=== V7 PRE-SCORED RACE PACKET ===
RACE: {race.get('track','?').upper()} R{race.get('race_num','?')} | {race.get('distance','')} | Jump {race.get('jump_time','PARTIAL')} | {race.get('grade','')}
DATE: {race.get('date','')} | CODE: {race.get('code','GREYHOUND')} | UID: {race.get('race_uid','')}
COMPLETENESS: {race.get('completeness_quality','?')} ({race.get('completeness_score',0)}%)
SHAPE: {scored.get('race_shape','')}
PACE: {scored.get('pace_type','')} | COLLAPSE RISK: {scored.get('collapse_risk','')} | PRESSURE: {scored.get('pressure_score',0)}/10
PRE-DECISION: {scored.get('decision','')} | CONFIDENCE: {scored.get('confidence','')}
CONF BREAKDOWN: {conf_str}
SELECTION: Box {scored.get('box','')} {scored.get('selection','')} | {scored.get('run_style','')} | {scored.get('trainer','')}
SEPARATION: {scored.get('separation','')} | CRASH MAP: {scored.get('crash_map','')}
{ff_str}
FILTERS: {filter_str}
TOP RUNNERS:
{chr(10).join(runner_lines)}
SESSION: Bankroll=${bankroll} | Mode={bank_mode}
PACKET_VERSION: {PACKET_VERSION}
=== END PACKET ==="""

    # Enforce max packet size
    if len(packet) > MAX_PACKET_CHARS:
        packet = packet[:MAX_PACKET_CHARS] + "\n[truncated]"

    return packet


def _build_pass_packet(race, scored):
    return f"""=== V7 PRE-SCORED RACE PACKET ===
RACE: {race.get('track','?').upper()} R{race.get('race_num','?')} | {race.get('distance','')} | Jump {race.get('jump_time','PARTIAL')}
PRE-DECISION: PASS
PASS REASON: {scored.get('pass_reason','Insufficient data') if scored else 'No score available'}
=== END PACKET ==="""


def is_worth_sending_to_claude(scored):
    """
    Feature J53 - Only send flagged races to Claude.
    Local PASS filter - obvious passes never reach Claude.
    """
    if not scored:
        return False
    decision = scored.get("decision")
    if decision == "PASS":
        return False
    confidence = scored.get("confidence", "INSUFFICIENT")
    if confidence == "INSUFFICIENT":
        return False
    # Only send BET or SESSION with decent confidence
    return decision in ["BET", "SESSION"] and confidence in ["HIGH", "MODERATE", "ELITE"]


def save_packet_snapshot(race_uid, packet):
    """Feature 24 - store exact packet used for each analysis."""
    try:
        from db import get_db
        db = get_db()
        db.table("scored_races").update({
            "packet_snapshot": packet,
            "packet_version": PACKET_VERSION,
            "packet_built_at": datetime.utcnow().isoformat(),
        }).eq("race_uid", race_uid).execute()

        log.debug(f"packet_builder: lifecycle packet_built for {race_uid}")
    except Exception as e:
        log.error(f"Save packet snapshot failed {race_uid}: {e}")
