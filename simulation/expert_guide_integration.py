"""
simulation/expert_guide_integration.py
Generates the full structured ExpertGuide from simulation + filter results.
Produces human-readable summaries for each section.
"""
from __future__ import annotations
from .models import (
    RunnerProfile, RaceMeta, RaceCode,
    AggregatedResult, FilterDecision, ExpertGuide,
    ChaosRating, ConfidenceRating, Decision, RacePattern,
)
from .race_shape_engine import RaceShape


class ExpertGuideGenerator:
    """Generates a complete ExpertGuide from simulation output."""

    # ─────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────

    def generate(
        self,
        race_meta: RaceMeta,
        runners: list[RunnerProfile],
        aggregated: AggregatedResult,
        race_shape: RaceShape,
        filter_decision: FilterDecision,
    ) -> ExpertGuide:
        top_stats  = aggregated.top_runner
        top_runner = next((r for r in runners if r.runner_id == (top_stats.runner_id if top_stats else "")), None)
        runner_map = {r.runner_id: r for r in runners}

        sim_summary   = self._simulation_summary(aggregated, race_meta, top_runner, top_stats)
        projected_run = self._projected_race_run(aggregated, race_shape, runners)
        shape_insights = self._race_shape_insights(race_shape, race_meta)
        runner_notes  = self._runner_impact_notes(aggregated, runners, race_shape)
        final_note    = self._final_decision_note(filter_decision, top_runner, top_stats, aggregated)

        return ExpertGuide(
            race_uid=race_meta.race_uid,
            race_code=race_meta.race_code,
            track=race_meta.track,
            decision=filter_decision.decision,
            confidence_score=filter_decision.confidence_score,
            chaos_rating=aggregated.chaos_rating,
            confidence_rating=aggregated.confidence_rating,
            simulation_summary=sim_summary,
            projected_race_run=projected_run,
            race_shape_insights=shape_insights,
            runner_impact_notes=runner_notes,
            filter_results_panel=filter_decision,
            final_decision_note=final_note,
            aggregated=aggregated,
        )

    # ─────────────────────────────────────────────────────────────
    # SECTION 1: SIMULATION SUMMARY
    # ─────────────────────────────────────────────────────────────

    def _simulation_summary(
        self,
        agg: AggregatedResult,
        meta: RaceMeta,
        top_runner: RunnerProfile | None,
        top_stats,
    ) -> str:
        n = agg.n_sims
        code_label = {"greyhound":"Greyhound","thoroughbred":"Horse","harness":"Harness"}.get(
            meta.race_code.value, meta.race_code.value.title()
        )
        lines = [
            f"{n} {code_label} race simulations completed for {meta.track} {meta.distance_m}m.",
        ]
        if top_stats and top_runner:
            lines.append(
                f"Simulation leader: {top_runner.name} wins {top_stats.win_pct:.1f}% | "
                f"places {top_stats.place_pct:.1f}% | avg finish {top_stats.avg_finish:.1f}."
            )
            edge_pct = top_stats.sim_edge * 100
            if edge_pct >= 3:
                lines.append(f"Market overlay: +{edge_pct:.1f}% edge over market implied probability.")
            elif edge_pct <= -3:
                lines.append(f"Market underlay: {edge_pct:.1f}% — market rates this runner higher than simulation.")
        lines.append(
            f"Chaos: {agg.chaos_rating.value} | "
            f"Confidence: {agg.confidence_rating.value} | "
            f"Pace: {agg.pace_type} | "
            f"Collapse risk: {agg.collapse_risk}"
        )
        if agg.interference_rate > 1.0:
            lines.append(f"Interference: {agg.interference_rate:.1f} events/sim on average.")
        if agg.most_common_scenario:
            lines.append(f"Most common scenario: {agg.most_common_scenario}.")
        return " ".join(lines)

    # ─────────────────────────────────────────────────────────────
    # SECTION 2: PROJECTED RACE RUN
    # ─────────────────────────────────────────────────────────────

    def _projected_race_run(
        self,
        agg: AggregatedResult,
        shape: RaceShape,
        runners: list[RunnerProfile],
    ) -> str:
        runner_map = {r.runner_id: r for r in runners}
        lines = []

        if shape.projected_leader:
            lines.append(f"Expected to lead: {shape.projected_leader}.")

        # Leader frequency
        if agg.leader_frequency:
            top_leaders = sorted(agg.leader_frequency.items(), key=lambda x: x[1], reverse=True)[:3]
            leader_str  = ", ".join(
                f"{runner_map[rid].name if rid in runner_map else rid} ({pct:.0f}%)"
                for rid, pct in top_leaders
            )
            lines.append(f"Led at midpoint: {leader_str}.")

        # Pace description
        pace_desc = {
            "HOT":      "a blistering pace that will test every runner's stamina",
            "FAST":     "a fast pace with likely fade in the closing stages",
            "MODERATE": "a moderate tempo, giving runners every chance",
            "SLOW":     "a crawling pace that should suit the leader",
        }.get(shape.pace_type, "an uncertain tempo")
        lines.append(f"Projected tempo: {pace_desc}.")

        if shape.collapse_risk == "HIGH":
            lines.append("Leader collapse projected in a significant % of simulations — watch for late closers.")

        # Top 3 runners
        if agg.runners:
            podium = agg.runners[:3]
            podium_str = " | ".join(
                f"{s.name} ({s.win_pct:.1f}% win)" for s in podium
            )
            lines.append(f"Sim podium order: {podium_str}.")

        return " ".join(lines)

    # ─────────────────────────────────────────────────────────────
    # SECTION 3: RACE SHAPE INSIGHTS
    # ─────────────────────────────────────────────────────────────

    def _race_shape_insights(self, shape: RaceShape, meta: RaceMeta) -> str:
        lines = [
            f"Tempo band: {shape.tempo_band} | Pace score: {shape.pace_score:.1f}/10.",
        ]
        if shape.leader_names:
            lines.append(f"On-pace runners: {', '.join(shape.leader_names)}.")
        if shape.closer_advantage:
            lines.append("Late runners have structural advantage in this race shape.")
        lines.extend(shape.notes or [])
        return " ".join(lines)

    # ─────────────────────────────────────────────────────────────
    # SECTION 4: RUNNER IMPACT NOTES
    # ─────────────────────────────────────────────────────────────

    def _runner_impact_notes(
        self,
        agg: AggregatedResult,
        runners: list[RunnerProfile],
        shape: RaceShape,
    ) -> list[dict]:
        runner_map = {r.runner_id: r for r in runners}
        notes = []
        for stats in agg.runners:
            r = runner_map.get(stats.runner_id)
            if not r:
                continue

            flags  = []
            impact = []

            if stats.is_false_favourite:
                flags.append("FALSE_FAV")
                impact.append(f"Market overrates — sim shows only {stats.win_pct:.1f}%.")
            if stats.is_hidden_value:
                flags.append("HIDDEN_VALUE")
                impact.append(f"Underpriced at market: sim={stats.win_pct:.1f}% vs market={r.market_implied_prob*100:.1f}%.")
            if stats.is_vulnerable:
                flags.append("VULNERABLE")
                impact.append("Heavy market support not backed by simulation.")
            if stats.is_best_map:
                flags.append("BEST_MAP")
                impact.append("Best positional advantage for projected race shape.")

            # Pattern note
            pattern_note = {
                RacePattern.LEADER:   "On pace — will set/dictate tempo.",
                RacePattern.STALKER:  "Stalking position — ideal if pace is genuine.",
                RacePattern.CHASER:   "Coming from back — needs pace collapse to threaten.",
                RacePattern.WIDE:     "Running wide — extra ground to cover.",
                RacePattern.RAILER:   "Rail runner — box advantage key.",
                RacePattern.PARKED:   "Likely to be parked — significant energy cost.",
                RacePattern.TRAILER:  "Trailing — energy saving, needs gap to open.",
                RacePattern.MIDFIELD: "Midfield — balanced runner.",
            }.get(r.race_pattern, "")

            if pattern_note:
                impact.append(pattern_note)

            notes.append({
                "runner_id":  stats.runner_id,
                "name":       stats.name,
                "win_pct":    stats.win_pct,
                "place_pct":  stats.place_pct,
                "avg_finish": stats.avg_finish,
                "sim_edge":   stats.sim_edge,
                "flags":      flags,
                "note":       " ".join(impact) if impact else "No significant flags.",
            })
        return notes

    # ─────────────────────────────────────────────────────────────
    # FINAL DECISION NOTE
    # ─────────────────────────────────────────────────────────────

    def _final_decision_note(
        self,
        fd: FilterDecision,
        top_runner: RunnerProfile | None,
        top_stats,
        agg: AggregatedResult,
    ) -> str:
        name   = top_runner.name if top_runner else "Unknown"
        score  = fd.confidence_score

        decision_lines = {
            Decision.BET:       f"BET — {name}. Strong simulation + filter alignment. Confidence {score:.0%}.",
            Decision.SMALL_BET: f"SMALL BET — {name}. Moderate confidence ({score:.0%}). Reduce stake.",
            Decision.SAVE_BET:  f"SAVE BET — {name}. Hidden value detected. Track odds for entry.",
            Decision.CAUTION:   f"CAUTION — {name}. Mixed signals. Observe only, minimal exposure.",
            Decision.PASS:      f"PASS — {name}. Insufficient edge or structural issue identified.",
        }

        summary = decision_lines.get(fd.decision, f"{fd.decision.value} — {name}")

        # Add hard block reason if triggered
        if fd.hard_blocked_by:
            summary += f" Blocked by: {fd.hard_blocked_by.reason}."

        # Key trigger summary
        key_triggers = [r for r in fd.triggered_filters if abs(r.confidence_delta) >= 0.05]
        if key_triggers:
            trigger_str = "; ".join(r.reason[:60] for r in key_triggers[:3])
            summary += f" Key factors: {trigger_str}."

        return summary

    # ─────────────────────────────────────────────────────────────
    # EMPTY FALLBACK
    # ─────────────────────────────────────────────────────────────

    def empty_guide(self, race_meta: RaceMeta) -> ExpertGuide:
        from .models import ChaosRating, ConfidenceRating, FilterDecision, Decision, FilterMode
        return ExpertGuide(
            race_uid=race_meta.race_uid,
            race_code=race_meta.race_code,
            track=race_meta.track,
            decision=Decision.PASS,
            confidence_score=0.0,
            chaos_rating=ChaosRating.EXTREME,
            confidence_rating=ConfidenceRating.LOW,
            simulation_summary="No active runners — simulation aborted.",
            projected_race_run="N/A",
            race_shape_insights="N/A",
            runner_impact_notes=[],
            filter_results_panel=FilterDecision(
                decision=Decision.PASS, confidence_score=0.0,
                triggered_filters=[], passed_filters=[], hard_blocked_by=None,
                reasoning=["No runners"], top_runner_id=None, top_runner_name=None,
            ),
            final_decision_note="PASS — no runners to simulate.",
            aggregated=AggregatedResult(
                race_uid=race_meta.race_uid, race_code=race_meta.race_code, n_sims=0, runners=[],
            ),
        )
