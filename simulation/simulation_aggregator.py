"""
simulation/simulation_aggregator.py
Aggregates results from N simulation runs into statistics,
flags, chaos rating, and confidence rating.
"""
from __future__ import annotations
import math
from collections import Counter
from .models import (
    RunnerProfile, RaceMeta, RaceCode,
    SingleSimResult, AggregatedResult, RunnerStats,
    ChaosRating, ConfidenceRating,
)


class SimulationAggregator:
    """
    Takes a list of SingleSimResult objects and computes the full
    AggregatedResult including win%, place%, flags, and ratings.
    """

    # Thresholds for flag detection
    _FALSE_FAV_THRESHOLD    = 0.60   # market-implied % exceeds sim-win by this factor
    _HIDDEN_VALUE_THRESHOLD = 1.50   # sim-win exceeds market-implied by this factor
    _VULNERABLE_THRESHOLD   = 0.55   # runner's market % >> sim %
    _CHAOS_HIGH_THRESHOLD   = 0.30   # >30% of sims had interference events
    _CHAOS_EXTREME_THRESHOLD= 0.55   # >55% of sims had interference events
    _CONFIDENCE_HIGH        = 0.35   # top runner wins >35% → high confidence
    _CONFIDENCE_SOLID       = 0.25
    _CONFIDENCE_MODERATE    = 0.18

    def aggregate(
        self,
        race_meta: RaceMeta,
        runners: list[RunnerProfile],
        sims: list[SingleSimResult],
    ) -> AggregatedResult:
        if not sims or not runners:
            return self._empty_result(race_meta)

        n = len(sims)
        active = [r for r in runners if not r.scratched]

        # ── WIN / PLACE TALLIES ────────────────────────────────
        win_counts:   dict[str, int] = {r.runner_id: 0 for r in active}
        place_counts: dict[str, int] = {r.runner_id: 0 for r in active}
        finish_sums:  dict[str, float] = {r.runner_id: 0.0 for r in active}
        leader_counts: dict[str, int] = {r.runner_id: 0 for r in active}

        total_interference = 0
        total_collapses    = 0
        pace_votes: list[str]         = []
        scenario_votes: list[str]     = []
        winner_pattern_votes: list[str] = []

        for sim in sims:
            order = sim.finish_order
            if order:
                winner = order[0]
                if winner in win_counts:
                    win_counts[winner] += 1
                for i, rid in enumerate(order[:3]):
                    if rid in place_counts:
                        place_counts[rid] += 1
                for i, rid in enumerate(order):
                    if rid in finish_sums:
                        finish_sums[rid] += float(i + 1)

            if sim.leader_at_turn and sim.leader_at_turn in leader_counts:
                leader_counts[sim.leader_at_turn] += 1

            total_interference += sim.interference_count
            if sim.collapse_occurred:
                total_collapses += 1

            pace_votes.append(sim.pace_type)
            scenario_votes.append(f"{sim.pace_type}_{sim.winner_pattern.value}")
            winner_pattern_votes.append(sim.winner_pattern.value)

        # ── BUILD RunnerStats ──────────────────────────────────
        stats_list: list[RunnerStats] = []
        runner_map = {r.runner_id: r for r in active}

        for r in active:
            wins  = win_counts.get(r.runner_id, 0)
            places = place_counts.get(r.runner_id, 0)
            total_pos = finish_sums.get(r.runner_id, 0.0)

            win_pct    = wins / n * 100.0
            place_pct  = places / n * 100.0
            # M-08: default avg_finish to field_size (last place) not 0.0 when no sims
            avg_finish = total_pos / n if n > 0 else float(len(active))

            market_pct = r.market_implied_prob * 100.0
            sim_edge   = win_pct / 100.0 - r.market_implied_prob

            stats = RunnerStats(
                runner_id=r.runner_id,
                name=r.name,
                barrier_or_box=r.barrier_or_box,
                win_count=wins,
                place_count=places,
                total_finish_pos=total_pos,
                n_sims=n,
                win_pct=round(win_pct, 2),
                place_pct=round(place_pct, 2),
                avg_finish=round(avg_finish, 2),
                sim_edge=round(sim_edge, 4),
            )
            stats_list.append(stats)

        # ── FLAGS ─────────────────────────────────────────────
        stats_list = self._compute_flags(stats_list, runner_map)

        # ── RACE-LEVEL STATS ──────────────────────────────────
        pace_type     = Counter(pace_votes).most_common(1)[0][0] if pace_votes else "MODERATE"
        most_scenario = Counter(scenario_votes).most_common(1)[0][0] if scenario_votes else ""
        avg_interf    = total_interference / n
        collapse_pct  = total_collapses / n

        leader_freq = {
            rid: round(cnt / n * 100, 1)
            for rid, cnt in leader_counts.items()
            if cnt > 0
        }

        chaos       = self._compute_chaos(avg_interf, collapse_pct, pace_type, len(active))
        confidence  = self._compute_confidence(stats_list, chaos)

        sorted_stats = sorted(stats_list, key=lambda s: s.win_pct, reverse=True)
        top    = sorted_stats[0] if sorted_stats else None
        second = sorted_stats[1] if len(sorted_stats) > 1 else None

        return AggregatedResult(
            race_uid=race_meta.race_uid,
            race_code=race_meta.race_code,
            n_sims=n,
            runners=sorted_stats,
            chaos_rating=chaos,
            confidence_rating=confidence,
            pace_type=pace_type,
            collapse_risk="HIGH" if collapse_pct >= 0.35 else "MODERATE" if collapse_pct >= 0.15 else "LOW",
            interference_rate=round(avg_interf, 2),
            most_common_scenario=most_scenario.replace("_", " — "),
            leader_frequency=leader_freq,
            top_runner=top,
            second_runner=second,
            raw_sims=sims,
        )

    # ─────────────────────────────────────────────────────────────
    # FLAGS
    # ─────────────────────────────────────────────────────────────

    def _compute_flags(
        self,
        stats: list[RunnerStats],
        runner_map: dict[str, RunnerProfile],
    ) -> list[RunnerStats]:
        """
        Flag: false favourite, hidden value, vulnerable, best map.
        """
        best_map_id = None
        best_map_edge = -999

        for s in stats:
            r = runner_map.get(s.runner_id)
            if not r:
                continue
            market_pct = r.market_implied_prob * 100.0

            # False favourite: market says it's the fav, sim says it shouldn't be
            if market_pct >= 30.0 and s.win_pct < market_pct * self._FALSE_FAV_THRESHOLD:
                s.is_false_favourite = True

            # Hidden value: sim win% >> market implied, not the fav in market
            if (s.sim_edge > 0 and
                s.win_pct >= market_pct * self._HIDDEN_VALUE_THRESHOLD and
                market_pct < 25.0):
                s.is_hidden_value = True

            # Vulnerable: market bet down heavy but sim disagrees significantly
            if market_pct >= 35.0 and s.win_pct < market_pct * 0.50:
                s.is_vulnerable = True

            # Best map: highest positive edge among non-favourite runners
            if s.sim_edge > best_map_edge and market_pct < 35.0:
                best_map_edge = s.sim_edge
                best_map_id = s.runner_id

        if best_map_id:
            for s in stats:
                if s.runner_id == best_map_id and best_map_edge > 0.02:
                    s.is_best_map = True

        return stats

    # ─────────────────────────────────────────────────────────────
    # CHAOS + CONFIDENCE
    # ─────────────────────────────────────────────────────────────

    def _compute_chaos(
        self,
        avg_interference: float,
        collapse_pct: float,
        pace_type: str,
        field_size: int = 8,
    ) -> ChaosRating:
        # Normalise interference by field size: a 2-event race with 8 runners
        # is less chaotic per-runner than 2 events with 3 runners.
        # Clamp to [0, 1] so large fields don't dominate.
        normalised_interference = min(1.0, avg_interference / max(field_size, 1))
        score = normalised_interference * 0.6 + collapse_pct * 0.4
        if pace_type == "HOT":
            score += 0.12
        elif pace_type == "FAST":
            score += 0.05

        if score >= self._CHAOS_EXTREME_THRESHOLD:
            return ChaosRating.EXTREME
        elif score >= self._CHAOS_HIGH_THRESHOLD:
            return ChaosRating.HIGH
        elif score >= 0.12:
            return ChaosRating.MODERATE
        return ChaosRating.LOW

    def _compute_confidence(
        self,
        stats: list[RunnerStats],
        chaos: ChaosRating,
    ) -> ConfidenceRating:
        """
        High confidence = top runner wins a large % of sims,
        and chaos is low.
        """
        if not stats:
            return ConfidenceRating.LOW
        top_win = stats[0].win_pct / 100.0 if stats else 0.0

        # Chaos penalty
        chaos_penalty = {
            ChaosRating.LOW: 0.0,
            ChaosRating.MODERATE: 0.04,
            ChaosRating.HIGH: 0.10,
            ChaosRating.EXTREME: 0.18,
        }[chaos]

        adjusted = top_win - chaos_penalty

        if adjusted >= self._CONFIDENCE_HIGH:
            return ConfidenceRating.HIGH
        elif adjusted >= self._CONFIDENCE_SOLID:
            return ConfidenceRating.SOLID
        elif adjusted >= self._CONFIDENCE_MODERATE:
            return ConfidenceRating.MODERATE
        return ConfidenceRating.LOW

    # ─────────────────────────────────────────────────────────────
    # EMPTY FALLBACK
    # ─────────────────────────────────────────────────────────────

    def _empty_result(self, race_meta: RaceMeta) -> AggregatedResult:
        return AggregatedResult(
            race_uid=race_meta.race_uid,
            race_code=race_meta.race_code,
            n_sims=0,
            runners=[],
            chaos_rating=ChaosRating.MODERATE,
            confidence_rating=ConfidenceRating.LOW,
        )
