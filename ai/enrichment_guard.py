"""
ai/enrichment_guard.py - DemonPulse FormFav Enrichment Guard
=============================================================
Strict isolation layer between FormFav enrichment data and the core system.

Rules:
  - FormFav data is NEVER authoritative and NEVER overwrites core fields
  - All FormFav fields must be stored with the enrichment_* prefix
  - Protected fields (race_uid, odds, sectionals, race_code, positions, results)
    are NEVER overwritten regardless of what enrichment provides
  - Enrichment is purely additive — it can only ADD new enrichment_* fields
  - has_enrichment flag is set to 1 when enrichment is present

Architecture:
  - OddsPro is the ONLY authoritative source
  - FormFav is STRICTLY enrichment (never overrides, never writes to core tables)
  - No contamination between sources
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Fields that MUST NEVER be overwritten by enrichment, regardless of what
# FormFav provides. These are all sourced exclusively from OddsPro.
_PROTECTED_FIELDS: frozenset[str] = frozenset({
    "race_uid",
    "oddspro_race_id",
    "odds",
    "win_odds",
    "place_odds",
    "sectionals",
    "early_speed_score",
    "late_speed_score",
    "sectional_consistency_score",
    "race_code",
    "code",
    "positions",
    "results",
    "winner",
    "place_1",
    "place_2",
    "place_3",
    "runner_name",
    "box_num",
    "barrier",
    "race_id",
    "id",
    "source",
    "source_type",
    "implied_probability",
    "implied_prob",
    "collision_risk_score",
    "race_shape_fit",
})


def apply_enrichment(features: dict[str, Any], enrichment: dict[str, Any]) -> dict[str, Any]:
    """
    Merge FormFav enrichment into a feature dict WITHOUT overriding any
    authoritative fields.

    All FormFav fields are added with the enrichment_* prefix only.
    Sets has_enrichment = 1 when enrichment is present.

    Protected fields that MUST NEVER be overwritten:
        race_uid, odds, sectionals, race_code, positions, results
        and all other OddsPro-sourced fields (see _PROTECTED_FIELDS)

    Args:
        features  : authoritative feature dict (OddsPro-sourced)
        enrichment: FormFav enrichment dict for this runner

    Returns:
        New feature dict with enrichment_* fields added (original never mutated)
    """
    if not enrichment:
        return features

    result = dict(features)

    added = 0
    skipped = 0
    for key, value in enrichment.items():
        # Strip existing enrichment_ prefix if caller already prefixed it
        clean_key = key[len("enrichment_"):] if key.startswith("enrichment_") else key

        # Build the safe prefixed key
        safe_key = f"enrichment_{clean_key}"

        # NEVER allow overwriting protected core fields
        if safe_key in _PROTECTED_FIELDS or clean_key in _PROTECTED_FIELDS:
            log.warning(
                f"enrichment_guard: blocked attempt to overwrite protected field "
                f"'{clean_key}' via enrichment"
            )
            skipped += 1
            continue

        # Only write under the enrichment_* prefix — never raw
        result[safe_key] = value
        added += 1

    if added > 0:
        result["has_enrichment"] = 1
        log.debug(
            f"enrichment_guard: applied {added} enrichment fields "
            f"(skipped {skipped} protected)"
        )
    else:
        # Preserve existing has_enrichment if already set
        result.setdefault("has_enrichment", 0)

    return result


def apply_enrichment_to_field(
    features: list[dict[str, Any]],
    enrichment_by_runner: dict[str, dict[str, Any]],
    key_field: str = "runner_name",
) -> list[dict[str, Any]]:
    """
    Apply per-runner enrichment to a list of feature dicts.

    Matches runners by key_field (default: runner_name) and calls
    apply_enrichment for each matched runner.

    Args:
        features           : list of per-runner feature dicts
        enrichment_by_runner: dict mapping runner key → enrichment dict
        key_field          : field name to use as the join key (default: runner_name)

    Returns:
        New list of feature dicts with enrichment applied where available.
    """
    if not enrichment_by_runner:
        return features

    result = []
    for feat in features:
        runner_key = feat.get(key_field) or ""
        enrichment = enrichment_by_runner.get(runner_key) or {}
        result.append(apply_enrichment(feat, enrichment))

    return result


def strip_enrichment(features: dict[str, Any]) -> dict[str, Any]:
    """
    Remove all enrichment_* fields from a feature dict.

    Useful when you need a clean authoritative-only feature set.

    Args:
        features: feature dict potentially containing enrichment_* fields

    Returns:
        New feature dict with all enrichment_* fields removed.
    """
    return {
        k: v for k, v in features.items()
        if not k.startswith("enrichment_") and k != "has_enrichment"
    }


def validate_enrichment_isolation(features: dict[str, Any]) -> list[str]:
    """
    Validate that no protected fields have been contaminated by enrichment.

    Returns a list of violation messages (empty = clean).
    This is used for testing and auditing only.
    """
    violations = []
    for key in _PROTECTED_FIELDS:
        # If a field exists both as a plain key AND as enrichment_<key>,
        # the plain key must come from authoritative sources, not enrichment.
        # The enrichment variant should only appear as enrichment_<key>.
        enrich_key = f"enrichment_{key}"
        if enrich_key in features and key in features:
            # This is acceptable — the enrichment_ version is additive
            pass
        if key in features:
            val = features[key]
            if isinstance(val, str) and val.startswith("formfav:"):
                violations.append(
                    f"Protected field '{key}' appears to be sourced from FormFav: {val!r}"
                )
    return violations
