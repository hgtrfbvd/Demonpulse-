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


def build_packet_for_race(race_uid: str) -> dict | None:
    """
    Build and run the full module pipeline for a given race_uid.

    1. Loads race + runner data from the database.
    2. Constructs a DogsRacePacket.
    3. Runs enabled modules: dogs_capture → dogs_analysis → simulation.
    4. Persists the result to the dogs_race_packets Supabase table.
    5. Returns the final packet dict.
    """
    try:
        from database import get_race, get_runners_for_race
        race = get_race(race_uid)
        if not race:
            log.warning(f"build_packet_for_race: race_uid {race_uid} not found")
            return None

        runners = get_runners_for_race(race_uid) or []

        # Build initial packet dict
        packet: dict = {
            "race_uid": race_uid,
            "status": "CAPTURED",
            "source_name": race.get("source", "thedogs.com.au"),
            "source_url": race.get("source_url"),
            "track_name": race.get("track"),
            "state": race.get("state"),
            "date": race.get("date"),
            "race_number": race.get("race_num"),
            "race_time": race.get("jump_time"),
            "distance_m": race.get("distance"),
            "grade": race.get("grade"),
            "track_condition": race.get("condition"),
            "screenshots": {},
            "extracted_data": {},
            "engine_output": {},
            "simulation_output": {},
            "result": {},
            "learning": {},
        }

        # Embed runners into extracted_data so analysis modules can consume them
        if runners:
            packet["extracted_data"]["runners"] = runners
            if packet["status"] == "CAPTURED":
                packet["status"] = "EXTRACTED"

        # Run module pipeline
        from modules import get_loader
        loader = get_loader()

        _MODULE_ORDER = ["dogs_capture", "dogs_analysis", "simulation"]
        for module_name in _MODULE_ORDER:
            if not loader.is_enabled(module_name):
                log.info(f"build_packet_for_race: module {module_name} disabled, skipping")
                continue
            try:
                if module_name == "dogs_capture":
                    from modules.dogs_capture import DogsCaptureModule
                    mod = DogsCaptureModule()
                elif module_name == "dogs_analysis":
                    from modules.dogs_analysis import DogsAnalysisModule
                    mod = DogsAnalysisModule()
                elif module_name == "simulation":
                    from modules.simulation import SimulationModule
                    mod = SimulationModule()
                else:
                    continue

                if not mod.can_process(packet):
                    log.info(f"build_packet_for_race: {module_name} skipped (missing inputs)")
                    continue

                updates = mod.process(packet)
                if updates:
                    packet.update(updates)
                    log.info(f"build_packet_for_race: {module_name} completed, status={packet.get('status')}")
            except Exception as mod_err:
                log.error(f"build_packet_for_race: module {module_name} failed: {mod_err}")

        # Persist to Supabase dogs_race_packets table
        _persist_packet(packet)

        return packet

    except Exception as e:
        log.error(f"build_packet_for_race failed for {race_uid}: {e}")
        return None


def _persist_packet(packet: dict) -> None:
    """Upsert the race packet to Supabase dogs_race_packets table."""
    try:
        from db import get_db
        client = get_db()
        # Keep scalar fields and known structured dict fields; exclude raw runner lists
        # that would be too large or are stored separately in today_runners.
        _STRUCTURED_FIELDS = {"screenshots", "extracted_data", "engine_output", "simulation_output", "result", "learning"}
        serialisable = {
            k: v
            for k, v in packet.items()
            if not isinstance(v, list) or k in _STRUCTURED_FIELDS
        }
        client.table("dogs_race_packets").upsert(
            serialisable,
            on_conflict="race_uid",
        ).execute()
        log.debug(f"_persist_packet: upserted race_uid={packet.get('race_uid')}")
    except Exception as e:
        log.warning(f"_persist_packet failed: {e}")
