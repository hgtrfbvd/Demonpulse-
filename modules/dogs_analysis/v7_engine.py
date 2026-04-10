"""
modules/dogs_analysis/v7_engine.py
====================================
V7 Greyhound Analysis Engine.

Scoring weights (V7):
  - Early speed:    40% (highest weight)
  - Box draw:       20%
  - Track/distance: 15%
  - Collision risk: 15% (penalty)
  - Form:           10% (confirmation only — not leading factor)

Tempo classification: FAST | MODERATE | SLOW
PASS filter: skip races where confidence < threshold or chaos too high

Output → race_packet.engine_output:
  {
    tempo: FAST|MODERATE|SLOW,
    primary: {box, name, score, reason},
    secondary: {box, name, score, reason},
    confidence: 0.0–1.0,
    pass_filter: true|false,
    pass_reason: str,
    notes: [str]
  }
"""
from __future__ import annotations

import logging
from typing import Any

from modules.base_module import BaseModule

log = logging.getLogger(__name__)

# V7 Scoring weights
_W_EARLY_SPEED = 0.40
_W_BOX         = 0.20
_W_TRACK_DIST  = 0.15
_W_COLLISION   = 0.15  # penalty weight
_W_FORM        = 0.10

# Box advantage table (standard oval track defaults)
_BOX_ADVANTAGE: dict[int, float] = {
    1: 1.00, 2: 0.92, 3: 0.82, 4: 0.76,
    5: 0.70, 6: 0.62, 7: 0.54, 8: 0.46,
}

# PASS filter thresholds
_MIN_CONFIDENCE = 0.55
_MAX_CHAOS = 0.70


class DogsAnalysisModule(BaseModule):
    """V7 greyhound analysis engine."""

    module_name = "dogs_analysis"
    module_type = "analysis"
    version = "7.0.0"
    input_requirements = ["extracted_data"]
    output_keys = ["engine_output"]

    def process(self, packet: dict[str, Any]) -> dict[str, Any]:
        extracted = packet.get("extracted_data") or {}
        runners = extracted.get("runners") or []

        if not runners:
            log.warning("[dogs_analysis] No runners in extracted_data")
            return {}

        try:
            scored = self._score_runners(runners, packet)
            tempo = self._classify_tempo(runners)
            primary, secondary = self._pick_selections(scored)
            confidence = self._compute_confidence(scored, primary)
            pass_filter, pass_reason = self._apply_pass_filter(confidence, scored)

            notes = self._build_notes(scored, tempo)

            engine_output = {
                "tempo": tempo,
                "primary": primary,
                "secondary": secondary,
                "confidence": round(confidence, 4),
                "pass_filter": pass_filter,
                "pass_reason": pass_reason,
                "notes": notes,
                "scored_runners": scored,
            }
            return {"engine_output": engine_output}
        except Exception as e:
            log.error(f"[dogs_analysis] process failed: {e}")
            return {}

    def _score_runners(
        self,
        runners: list[dict[str, Any]],
        packet: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Score each runner using V7 weights."""
        scored = []
        for r in runners:
            if r.get("scratched"):
                continue

            box = r.get("box") or 0
            early_speed = self._normalise(r.get("early_speed_rating") or 0, 0, 10)
            box_adv = _BOX_ADVANTAGE.get(box, 0.50)
            track_dist = self._normalise(r.get("track_distance_record_pct") or 0, 0, 100)
            collision_risk = self._normalise(r.get("collision_risk") or 0, 0, 10)
            form_score = self._form_score(r.get("last4") or "")

            score = (
                early_speed * _W_EARLY_SPEED
                + box_adv * _W_BOX
                + track_dist * _W_TRACK_DIST
                - collision_risk * _W_COLLISION
                + form_score * _W_FORM
            )

            reasons = []
            if early_speed > 0.7:
                reasons.append(f"early speed {early_speed:.2f}")
            if box_adv > 0.80:
                reasons.append(f"box {box} advantage")
            if form_score > 0.6:
                reasons.append("confirmed form")

            scored.append({
                "box": box,
                "name": r.get("runner_name") or r.get("name") or f"Box {box}",
                "score": round(score, 4),
                "early_speed": round(early_speed, 4),
                "box_advantage": round(box_adv, 4),
                "track_dist": round(track_dist, 4),
                "collision_risk": round(collision_risk, 4),
                "form_score": round(form_score, 4),
                "reasons": reasons,
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    def _classify_tempo(self, runners: list[dict[str, Any]]) -> str:
        """Classify race tempo based on early speed distribution."""
        active = [r for r in runners if not r.get("scratched")]
        if not active:
            return "MODERATE"
        speeds = [r.get("early_speed_rating") or 0 for r in active]
        avg = sum(speeds) / len(speeds)
        fast_count = sum(1 for s in speeds if s >= 7)
        if fast_count >= 4 or avg >= 7.5:
            return "FAST"
        if avg <= 4.5 or fast_count == 0:
            return "SLOW"
        return "MODERATE"

    def _pick_selections(
        self,
        scored: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        primary = scored[0] if scored else None
        secondary = scored[1] if len(scored) > 1 else None
        return primary, secondary

    def _compute_confidence(
        self,
        scored: list[dict[str, Any]],
        primary: dict[str, Any] | None,
    ) -> float:
        if not scored or not primary:
            return 0.0
        top_score = primary["score"]
        if len(scored) < 2:
            return min(1.0, top_score)
        gap = top_score - scored[1]["score"]
        confidence = top_score * 0.6 + min(gap * 2, 0.4)
        return min(1.0, max(0.0, confidence))

    def _apply_pass_filter(
        self,
        confidence: float,
        scored: list[dict[str, Any]],
    ) -> tuple[bool, str]:
        """
        Return (pass_filter, reason).
        pass_filter=True means TAKE the race; False means PASS.
        """
        if confidence < _MIN_CONFIDENCE:
            return False, f"confidence {confidence:.2f} below threshold {_MIN_CONFIDENCE}"

        if len(scored) >= 3:
            top3_scores = [s["score"] for s in scored[:3]]
            spread = max(top3_scores) - min(top3_scores)
            if spread < 0.05:
                return False, f"field too even (top3 spread={spread:.3f})"

        return True, "all filters passed"

    def _build_notes(
        self,
        scored: list[dict[str, Any]],
        tempo: str,
    ) -> list[str]:
        notes = [f"Tempo: {tempo}"]
        if scored:
            top = scored[0]
            notes.append(
                f"Primary: Box {top['box']} {top['name']} "
                f"(score={top['score']:.3f}, reasons={', '.join(top['reasons'])})"
            )
        if len(scored) > 1:
            sec = scored[1]
            notes.append(
                f"Secondary: Box {sec['box']} {sec['name']} "
                f"(score={sec['score']:.3f})"
            )
        return notes

    @staticmethod
    def _normalise(val: float, lo: float, hi: float) -> float:
        if hi == lo:
            return 0.0
        return max(0.0, min(1.0, (val - lo) / (hi - lo)))

    @staticmethod
    def _form_score(last4: str) -> float:
        """Convert last4 form string (e.g. '1234') to 0–1 score."""
        if not last4:
            return 0.0
        total = 0.0
        count = 0
        for c in str(last4):
            if c.isdigit():
                pos = int(c)
                total += max(0.0, (9 - pos) / 8.0)
                count += 1
        return total / count if count else 0.0
