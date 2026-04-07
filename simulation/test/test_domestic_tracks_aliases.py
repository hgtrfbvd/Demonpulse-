"""
simulation/test/test_domestic_tracks_aliases.py
------------------------------------------------
Validates that every value in TRACK_ALIASES resolves to a slug that exists
in at least one of the code-specific domestic frozensets.

Rule enforced:
    For each canonical target in TRACK_ALIASES.values(), the slug must be
    present in at least one of:
        HORSE_AU_TRACKS, HORSE_NZ_TRACKS,
        GREYHOUND_AU_TRACKS, GREYHOUND_NZ_TRACKS,
        HARNESS_AU_TRACKS, HARNESS_NZ_TRACKS
"""
import sys
import os

# Ensure the repo root is on the path so we can import core.domestic_tracks
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from core.domestic_tracks import (
    TRACK_ALIASES,
    HORSE_AU_TRACKS,
    HORSE_NZ_TRACKS,
    GREYHOUND_AU_TRACKS,
    GREYHOUND_NZ_TRACKS,
    HARNESS_AU_TRACKS,
    HARNESS_NZ_TRACKS,
)

ALL_SETS = (
    HORSE_AU_TRACKS,
    HORSE_NZ_TRACKS,
    GREYHOUND_AU_TRACKS,
    GREYHOUND_NZ_TRACKS,
    HARNESS_AU_TRACKS,
    HARNESS_NZ_TRACKS,
)


@pytest.mark.parametrize("alias,canonical", sorted(TRACK_ALIASES.items()))
def test_alias_target_in_whitelist(alias, canonical):
    """Every canonical target of TRACK_ALIASES must exist in at least one frozenset."""
    found = any(canonical in s for s in ALL_SETS)
    assert found, (
        f"TRACK_ALIASES[{alias!r}] = {canonical!r} but {canonical!r} "
        f"is not present in any code-specific frozenset. "
        f"Add it to the appropriate set (HORSE/GREYHOUND/HARNESS AU/NZ)."
    )
