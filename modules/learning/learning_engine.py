"""
modules/learning/learning_engine.py
=====================================
Learning engine: compares engine_output.primary vs result.finishing_order.

Tags error types:
  - early_speed_mismatch: predicted high early speed but lost early position
  - collision_event:      unexpected interference altered result
  - form_aberration:      form-based prediction was wrong
  - box_draw_miss:        inner box advantage did not materialise
  - tempo_misread:        tempo classification was wrong

Writes suggestions (never auto-applies) to race_packet.learning.
Aggregates patterns over time in Supabase learning_history table.

NOTE: All adjustments are SUGGESTIONS only — never auto-applied to engine weights.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from modules.base_module import BaseModule

log = logging.getLogger(__name__)

# Error tag definitions
_ERROR_TAGS = {
    "early_speed_mismatch": "Primary selection lost early lead despite high early speed rating",
    "collision_event": "Race result disrupted by interference/collision",
    "form_aberration": "Form-based prediction contradicted by result",
    "box_draw_miss": "Inner box draw advantage did not translate",
    "tempo_misread": "Tempo classification was incorrect vs actual race shape",
    "value_found": "Secondary selection ran better than primary — value identified",
    "correct_primary": "Primary selection won — engine correct",
    "correct_top3": "Primary in top 3 — acceptable result",
}


class LearningModule(BaseModule):
    """Compares predictions vs actual results and generates learning tags."""

    module_name = "learning"
    module_type = "learning"
    version = "1.0.0"
    input_requirements = ["engine_output", "result"]
    output_keys = ["learning"]

    def process(self, packet: dict[str, Any]) -> dict[str, Any]:
        engine_out = packet.get("engine_output") or {}
        result = packet.get("result") or {}

        if not engine_out or not result:
            return {}

        try:
            analysis = self._analyse(engine_out, result, packet)
            self._persist_to_supabase(packet.get("race_uid"), analysis)
            return {"learning": analysis}
        except Exception as e:
            log.error(f"[learning] process failed: {e}")
            return {}

    def _analyse(
        self,
        engine_out: dict[str, Any],
        result: dict[str, Any],
        packet: dict[str, Any],
    ) -> dict[str, Any]:
        primary = engine_out.get("primary") or {}
        secondary = engine_out.get("secondary") or {}
        finishing_order = result.get("finishing_order") or []

        primary_box = primary.get("box")
        secondary_box = secondary.get("box")

        error_tags: list[str] = []
        adjustments: list[str] = []
        notes: list[str] = []

        if not finishing_order:
            return {
                "error_tags": ["no_result_data"],
                "adjustments": [],
                "notes": ["Cannot analyse: no finishing order in result"],
                "analysed_at": datetime.utcnow().isoformat(),
            }

        winner_box = finishing_order[0] if finishing_order else None
        top3 = finishing_order[:3]

        if primary_box and primary_box == winner_box:
            error_tags.append("correct_primary")
            notes.append(f"✅ Primary Box {primary_box} WON")
        elif primary_box and primary_box in top3:
            error_tags.append("correct_top3")
            notes.append(f"✅ Primary Box {primary_box} placed (pos {top3.index(primary_box) + 1})")
        else:
            notes.append(f"❌ Primary Box {primary_box} missed — winner was Box {winner_box}")

            primary_speed = primary.get("early_speed", 0)
            winner_in_scored = None
            for sr in (engine_out.get("scored_runners") or []):
                if sr.get("box") == winner_box:
                    winner_in_scored = sr
                    break

            if winner_in_scored:
                winner_speed = winner_in_scored.get("early_speed", 0)
                if primary_speed > 0.7 and winner_speed < 0.5:
                    error_tags.append("early_speed_mismatch")
                    adjustments.append(
                        "SUGGESTION: early speed weight may be over-valued in this track condition"
                    )

                if winner_in_scored.get("box", 9) > 5 and primary.get("box", 1) <= 3:
                    error_tags.append("box_draw_miss")
                    adjustments.append(
                        "SUGGESTION: outer box runner won — check track bias data"
                    )

        if secondary_box and secondary_box == winner_box:
            error_tags.append("value_found")
            notes.append(f"💡 Secondary Box {secondary_box} would have been the winner")

        sim_out = packet.get("simulation_output") or {}
        sim_favourite = None
        win_probs = sim_out.get("win_probabilities") or {}
        if win_probs:
            sim_favourite = max(win_probs, key=win_probs.get)

        if sim_favourite and sim_favourite != winner_box and primary_box == sim_favourite:
            error_tags.append("form_aberration")
            notes.append("Both engine and sim agreed but result differed — possible form aberration")

        if not error_tags:
            error_tags.append("uncategorised")

        return {
            "error_tags": error_tags,
            "adjustments": adjustments,
            "notes": notes,
            "primary_was": primary_box,
            "winner_was": winner_box,
            "top3_was": top3,
            "analysed_at": datetime.utcnow().isoformat(),
        }

    def _persist_to_supabase(
        self,
        race_uid: str | None,
        analysis: dict[str, Any],
    ) -> None:
        """Write learning analysis to learning_history Supabase table."""
        if not race_uid:
            return
        try:
            from supabase_config import get_supabase_client
            client = get_supabase_client()
            client.table("learning_history").upsert({
                "race_uid": race_uid,
                "error_tags": analysis.get("error_tags", []),
                "adjustments": analysis.get("adjustments", []),
                "notes": analysis.get("notes", []),
                "primary_was": analysis.get("primary_was"),
                "winner_was": analysis.get("winner_was"),
                "top3_was": analysis.get("top3_was"),
                "analysed_at": analysis.get("analysed_at"),
            }).execute()
        except Exception as e:
            log.warning(f"[learning] Supabase persist failed: {e}")
