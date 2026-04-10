"""
features.py
===========
Compute derived data point fields from raw scraped race/runner data.
No external calls. Pure calculation from scraped fields.
"""
import statistics
import logging

log = logging.getLogger(__name__)


def compute_greyhound_derived(race: dict) -> dict:
    """
    Compute derived fields for a greyhound race.
    Modifies runner dicts in-place to add per-runner derived fields.
    Returns race-level derived fields dict.
    """
    runners = [r for r in race.get("runners", []) if not r.get("scratched")]
    if not runners:
        return {}

    # early_speed_rating: rank by best_time_distance_match (ascending = faster)
    times = []
    for r in runners:
        bt = r.get("best_time_distance_match")
        if bt and bt != "NBT":
            try:
                times.append((r.get("box", 0), float(bt)))
            except (TypeError, ValueError):
                pass
    times.sort(key=lambda x: x[1])
    speed_rank = {box: i + 1 for i, (box, _) in enumerate(times)}
    for r in runners:
        r["early_speed_rating"] = speed_rank.get(r.get("box"))

    # race shape counts
    leaders = sum(1 for r in runners if (r.get("early_speed_rating") or 99) <= 2)
    mid = sum(1 for r in runners if 3 <= (r.get("early_speed_rating") or 99) <= 5)
    back = sum(1 for r in runners if (r.get("early_speed_rating") or 99) >= 6)

    # tempo: average split time
    splits = []
    for r in runners:
        st = r.get("split_time")
        if st:
            try:
                splits.append(float(st))
            except (TypeError, ValueError):
                pass
    tempo = round(statistics.mean(splits), 2) if splits else None

    # finish_strength_rating per runner: (best_time - split_time) ratio vs field avg
    finish_deltas = []
    for r in runners:
        bt = r.get("best_time_distance_match")
        st = r.get("split_time")
        if bt and st and bt != "NBT":
            try:
                delta = float(bt) - float(st)
                finish_deltas.append((r, delta))
            except (TypeError, ValueError):
                pass
    if finish_deltas:
        avg_delta = statistics.mean(d for _, d in finish_deltas)
        for r, delta in finish_deltas:
            r["finish_strength_rating"] = round(delta / avg_delta, 3) if avg_delta else None

    # consistency_rating per runner: stdev of last4 positions
    for r in runners:
        last4 = r.get("last4", "")
        positions = [int(c) for c in str(last4) if c.isdigit()]
        r["consistency_rating"] = (
            round(statistics.stdev(positions), 2) if len(positions) >= 2 else None
        )

    return {
        "num_leaders": leaders,
        "num_mid_pack": mid,
        "num_backmarkers": back,
        "tempo_rating": tempo,
    }


def compute_horse_derived(race: dict) -> dict:
    """
    Compute derived fields for a horse race.
    Also sets early_speed_rating on each runner based on speed_map position.
    Returns race-level derived fields dict.
    """
    speed_map = race.get("speed_map", {})
    lead = speed_map.get("lead", [])
    on_speed = speed_map.get("on_speed", [])
    midfield = speed_map.get("midfield", [])
    backmarker = speed_map.get("backmarker", [])

    # Build lookup: runner name → early_speed_rating (1=lead, 2=on_speed, 3=mid, 4=back)
    esr_map: dict[str, int] = {}
    for name in lead:
        esr_map[name] = 1
    for name in on_speed:
        esr_map[name] = 2
    for name in midfield:
        esr_map[name] = 3
    for name in backmarker:
        esr_map[name] = 4

    for r in race.get("runners", []):
        name = r.get("name") or ""
        r["early_speed_rating"] = esr_map.get(name)
        r["run_style"] = r.get("run_style") or _run_style_from_esr(esr_map.get(name))

    return {
        "num_leaders": len(lead),
        "num_on_pace": len(on_speed),
        "num_midfield": len(midfield),
        "num_backmarkers": len(backmarker),
    }


def _run_style_from_esr(esr: int | None) -> str | None:
    """Map early_speed_rating to a run_style string."""
    return {1: "lead", 2: "on_speed", 3: "midfield", 4: "backmarker"}.get(esr or 0)
