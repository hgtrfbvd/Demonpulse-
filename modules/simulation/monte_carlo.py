"""
modules/simulation/monte_carlo.py
===================================
1000-cycle Monte Carlo simulation module.

Uses runner data from extracted_data to run 1000 iterations.
Per cycle models: early speed variance, collision events, track bias, finish strength.

Output → race_packet.simulation_output:
  {
    win_probabilities:      {box: pct, ...},
    top3_probabilities:     {box: pct, ...},
    most_likely_scenario:   [box1, box2, box3],
    alternate_scenario:     [box1, box2, box3],
    chaos_rating:           0.0–1.0,
    lead_at_first_bend_pct: {box: pct, ...},
    iterations:             1000,
    notes:                  [str]
  }
"""
from __future__ import annotations

import logging
import random
from collections import Counter
from typing import Any

from modules.base_module import BaseModule

log = logging.getLogger(__name__)

_ITERATIONS = 1000
_BOX_STRENGTH: dict[int, float] = {
    1: 1.25, 2: 1.15, 3: 1.00, 4: 0.95,
    5: 0.90, 6: 0.82, 7: 0.70, 8: 0.58,
}
_COLLISION_PROB_PER_RUNNER = 0.06  # per runner per race


class SimulationModule(BaseModule):
    """1000-cycle Monte Carlo greyhound race simulation."""

    module_name = "simulation"
    module_type = "simulation"
    version = "1.0.0"
    input_requirements = ["extracted_data"]
    output_keys = ["simulation_output"]

    def process(self, packet: dict[str, Any]) -> dict[str, Any]:
        extracted = packet.get("extracted_data") or {}
        runners = [r for r in (extracted.get("runners") or []) if not r.get("scratched")]

        if not runners:
            log.warning("[simulation] No active runners found in extracted_data")
            return {}

        try:
            results = self._run_monte_carlo(runners, packet)
            return {"simulation_output": results}
        except Exception as e:
            log.error(f"[simulation] monte carlo failed: {e}")
            return {}

    def _run_monte_carlo(
        self,
        runners: list[dict[str, Any]],
        packet: dict[str, Any],
    ) -> dict[str, Any]:
        """Run 1000 race simulations and aggregate outcomes."""
        win_counts: Counter = Counter()
        top3_counts: Counter = Counter()
        lead_bend_counts: Counter = Counter()
        scenario_counter: Counter = Counter()
        interference_count = 0

        for _ in range(_ITERATIONS):
            result = self._simulate_one(runners, packet)
            order = result["order"]
            if order:
                win_counts[order[0]] += 1
                for box in order[:3]:
                    top3_counts[box] += 1
                lead_bend_counts[result["lead_at_bend"]] += 1
                scenario_key = tuple(order[:3])
                scenario_counter[scenario_key] += 1
            if result["had_interference"]:
                interference_count += 1

        n = _ITERATIONS
        boxes = [r.get("box") or i + 1 for i, r in enumerate(runners)]

        win_pct = {b: round(win_counts.get(b, 0) / n * 100, 1) for b in boxes}
        top3_pct = {b: round(top3_counts.get(b, 0) / n * 100, 1) for b in boxes}
        lead_bend_pct = {b: round(lead_bend_counts.get(b, 0) / n * 100, 1) for b in boxes}

        top_scenarios = scenario_counter.most_common(2)
        most_likely = list(top_scenarios[0][0]) if top_scenarios else boxes[:3]
        alternate = list(top_scenarios[1][0]) if len(top_scenarios) > 1 else []

        # Chaos rating: how evenly distributed are win probabilities?
        win_vals = list(win_pct.values())
        if win_vals:
            max_win = max(win_vals)
            avg_win = sum(win_vals) / len(win_vals)
            chaos = 1.0 - (max_win - avg_win) / max(max_win, 1)
            chaos = round(max(0.0, min(1.0, chaos)), 3)
        else:
            chaos = 0.5

        notes = []
        if chaos > 0.7:
            notes.append(f"HIGH CHAOS: race very open (chaos={chaos})")
        top_win_box = max(win_pct, key=win_pct.get, default=None)
        if top_win_box:
            notes.append(f"Sim favourite: Box {top_win_box} ({win_pct[top_win_box]}% win)")
        if interference_count > _ITERATIONS * 0.3:
            notes.append(f"High interference race: {interference_count}/{_ITERATIONS} cycles had incidents")

        return {
            "win_probabilities": win_pct,
            "top3_probabilities": top3_pct,
            "most_likely_scenario": most_likely,
            "alternate_scenario": alternate,
            "chaos_rating": chaos,
            "lead_at_first_bend_pct": lead_bend_pct,
            "iterations": _ITERATIONS,
            "interference_rate": round(interference_count / _ITERATIONS, 3),
            "notes": notes,
        }

    def _simulate_one(
        self,
        runners: list[dict[str, Any]],
        packet: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run one race simulation cycle.
        Models: early speed variance, collision events, track bias, finish strength.
        Returns dict with order (list of boxes), lead_at_bend, had_interference.
        """
        distance = packet.get("distance_m") or 500
        is_sprint = distance <= 380

        scores: dict[int, float] = {}
        had_interference = False

        for r in runners:
            box = r.get("box") or 1
            early_speed = (r.get("early_speed_rating") or 5) / 10.0
            box_str = _BOX_STRENGTH.get(box, 0.80)

            speed_var = random.gauss(0, 0.08)
            early = early_speed * box_str + speed_var

            if random.random() < _COLLISION_PROB_PER_RUNNER:
                had_interference = True
                early -= random.uniform(0.05, 0.20)

            track_bias = random.gauss(0, 0.04)

            finish_str = (r.get("finish_strength_rating") or 5) / 10.0
            finish_var = random.gauss(0, 0.07)
            finish = finish_str + finish_var

            if is_sprint:
                scores[box] = early * 0.70 + finish * 0.15 + track_bias + random.gauss(0, 0.04)
            else:
                scores[box] = early * 0.50 + finish * 0.30 + track_bias + random.gauss(0, 0.04)

        lead_at_bend = max(scores, key=lambda b: scores[b])
        order = sorted(scores.keys(), key=lambda b: scores[b], reverse=True)

        return {
            "order": order,
            "lead_at_bend": lead_at_bend,
            "had_interference": had_interference,
        }
