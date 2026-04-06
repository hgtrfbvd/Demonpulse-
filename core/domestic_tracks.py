"""
core/domestic_tracks.py
-----------------------
Authoritative track-based whitelist for AU + NZ domestic racing venues.

This module is the SINGLE SOURCE OF TRUTH for domestic classification.
Country and state fields from external APIs are unreliable and MUST NOT be
used for classification decisions. Only track name membership in DOMESTIC_TRACKS
determines whether a race enters the DemonPulse pipeline.

Structure:
  AU_TRACKS       - frozenset of all known Australian venue slugs
  NZ_TRACKS       - frozenset of all known New Zealand venue slugs
  DOMESTIC_TRACKS - union of AU_TRACKS | NZ_TRACKS (the gate set)

  normalize_track(track) - canonical normalisation for lookup
"""

import re


def normalize_track(track: str) -> str:
    """
    Return the canonical slug for *track* suitable for DOMESTIC_TRACKS lookup.

    Steps:
      1. Lowercase
      2. Strip leading/trailing whitespace
      3. Replace one-or-more spaces with a single hyphen
      4. Remove any character that is not alphanumeric or hyphen
    """
    t = (track or "").strip().lower()
    t = re.sub(r"\s+", "-", t)
    t = re.sub(r"[^a-z0-9\-]", "", t)
    return t


# ---------------------------------------------------------------------------
# AUSTRALIAN TRACKS (all codes: thoroughbred, harness, greyhound)
# ---------------------------------------------------------------------------
AU_TRACKS: frozenset[str] = frozenset({
    # NSW thoroughbred
    "rosehill", "randwick", "warwick-farm", "canterbury", "newcastle",
    "gosford", "wyong", "kembla-grange", "hawkesbury", "muswellbrook",
    "armidale", "goulburn", "tamworth", "grafton", "lismore", "coffs-harbour",
    "taree", "scone", "cessnock", "wagga-wagga", "albury", "orange",
    "bathurst", "dubbo", "moruya", "nowra", "queanbeyan", "mudgee",
    # VIC thoroughbred
    "flemington", "caulfield", "moonee-valley", "sandown", "mornington",
    "ballarat", "bendigo", "hamilton", "cranbourne", "pakenham",
    "sale", "geelong", "seymour", "echuca", "swan-hill", "horsham",
    "warracknabeal", "donald", "stawell", "avoca", "mildura", "wangaratta",
    "wodonga", "benalla", "shepparton", "traralgon", "bairnsdale",
    # QLD thoroughbred
    "doomben", "eagle-farm", "gold-coast", "ipswich", "sunshine-coast",
    "toowoomba", "warwick", "rockhampton", "mackay", "townsville",
    "cairns", "bundaberg", "hervey-bay", "gympie", "beaudesert",
    # SA thoroughbred
    "morphettville", "victoria-park", "gawler", "mount-gambier",
    "port-augusta", "port-lincoln", "naracoorte", "murray-bridge", "oakbank",
    # WA thoroughbred
    "ascot", "belmont-park", "bunbury", "pinjarra", "northam",
    "kalgoorlie", "geraldton", "albany", "esperance",
    # TAS thoroughbred
    "elwick", "mowbray", "devonport", "launceston",
    # ACT thoroughbred
    "thoroughbred-park",
    # NT thoroughbred
    "darwin", "alice-springs",
    # Greyhound (AU)
    "angle-park", "albion-park-greyhound", "albion-park", "cannington",
    "dapto", "sandown-park", "the-meadows", "temora", "wentworth-park",
    "richmond", "lismore-greyhound", "townsville-greyhound",
    "ipswich-greyhound", "gold-coast-greyhound", "capalaba",
    "bundaberg-greyhound", "rockhampton-greyhound", "mackay-greyhound",
    "cairns-greyhound", "hobart-greyhound", "launceston-greyhound",
    "alice-springs-greyhound",
    # Harness (AU)
    "albion-park-harness", "menangle", "penrith", "bankstown",
    "newcastle-harness", "tabcorp-park-menangle", "gloucester-park",
    "wayville", "melton",
})

# ---------------------------------------------------------------------------
# NEW ZEALAND TRACKS (all codes: thoroughbred, harness, greyhound)
# ---------------------------------------------------------------------------
NZ_TRACKS: frozenset[str] = frozenset({
    # Thoroughbred
    "ellerslie", "te-rapa", "taupo", "hastings", "hawkes-bay",
    "rotorua", "wanganui", "whanganui", "otaki", "awapuni", "riccarton",
    "ashburton", "timaru", "gore", "winton", "invercargill",
    "ruakaka", "matamata", "cambridge", "pukekohe", "new-plymouth",
    "palmerston-north", "feilding", "foxton", "masterton",
    "levin", "woodville", "waverley", "marton", "wairoa",
    "napier", "gisborne", "tauranga", "huntly",
    "dargaville", "whangarei",
    # Greyhound (NZ)
    "auckland-dogs", "manukau", "manawatu-dogs", "christchurch-dogs",
    "invercargill-dogs",
    # Harness (NZ)
    "cambridge-harness", "addington", "forbury-park", "hutt-park",
    "alexandra-park", "teretonga",
    "motukarara",  # Canterbury harness; previously missing from whitelist
})

# ---------------------------------------------------------------------------
# MASTER WHITELIST — union of all domestic AU + NZ venues
# ---------------------------------------------------------------------------
DOMESTIC_TRACKS: frozenset[str] = AU_TRACKS | NZ_TRACKS
