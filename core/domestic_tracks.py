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
  TRACK_ALIASES   - dict mapping track name variants to canonical slugs

  normalize_track(track)     - canonical normalisation for lookup
  apply_track_alias(track)   - normalise then resolve via TRACK_ALIASES
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
# TRACK ALIASES — maps normalised variant slugs to canonical venue slugs
# ---------------------------------------------------------------------------
# Keys:   output of normalize_track() applied to the variant name
# Values: canonical slug that MUST exist in AU_TRACKS or NZ_TRACKS
# ---------------------------------------------------------------------------
TRACK_ALIASES: dict[str, str] = {
    # Mt Druitt (NSW greyhound)
    "mount-druitt":       "mt-druitt",
    "mt-druitt":          "mt-druitt",
    # The Meadows (VIC greyhound)
    "the-meadows":        "the-meadows",
    "meadows":            "the-meadows",
    # Wentworth Park (NSW greyhound)
    "wentworth-park":     "wentworth-park",
    "wentworthpark":      "wentworth-park",
    # Albion Park (QLD harness / greyhound)
    "albion-park":        "albion-park",
    "albion-park-raceway": "albion-park",
    # Sandown (VIC thoroughbred / greyhound)
    "sandown":            "sandown",
    "sandown-racecourse": "sandown",
    "sandown-park":       "sandown-park",
    # Gold Coast (QLD thoroughbred / greyhound)
    "gold-coast":         "gold-coast",
    "gold-coast-turf":    "gold-coast",
    # Flemington
    "flemington":         "flemington",
    "flemington-racecourse": "flemington",
    # Eagle Farm
    "eagle-farm":         "eagle-farm",
    "eagle-farm-racecourse": "eagle-farm",
    # Moonee Valley
    "moonee-valley":      "moonee-valley",
    "moonee-valley-racecourse": "moonee-valley",
    # Rosehill
    "rosehill":           "rosehill",
    "rosehill-gardens":   "rosehill",
    # Randwick
    "randwick":           "randwick",
    "royal-randwick":     "randwick",
    # Hawkesbury
    "hawkesbury":         "hawkesbury",
    "hawkesbury-racecourse": "hawkesbury",
    # Canterbury
    "canterbury":         "canterbury",
    "canterbury-park":    "canterbury",
    # Caulfield
    "caulfield":          "caulfield",
    "caulfield-racecourse": "caulfield",
    # Morphettville (SA)
    "morphettville":      "morphettville",
    "morphettville-parks": "morphettville",
    # Ascot (WA)
    "ascot":              "ascot",
    "ascot-racecourse":   "ascot",
    # Ellerslie (NZ)
    "ellerslie":          "ellerslie",
    "ellerslie-racecourse": "ellerslie",
    # Te Rapa (NZ)
    "te-rapa":            "te-rapa",
    "terapa":             "te-rapa",
    # Addington (NZ harness)
    "addington":          "addington",
    "addington-raceway":  "addington",
    # Whanganui / Wanganui (NZ)
    "wanganui":           "whanganui",
    "whanganui":          "whanganui",
    # Palmerston North (NZ)
    "palmerston-north":   "palmerston-north",
    "palmy":              "palmerston-north",
    # Gloucester Park (WA harness)
    "gloucester-park":    "gloucester-park",
    "gloucesterpark":     "gloucester-park",
    # Menangle (NSW harness)
    "menangle":           "menangle",
    "tabcorp-park-menangle": "tabcorp-park-menangle",
    # Angle Park (SA greyhound)
    "angle-park":         "angle-park",
    "anglepark":          "angle-park",
    # Cannington (WA greyhound)
    "cannington":         "cannington",
    "cannington-raceway": "cannington",
    # Dapto (NSW greyhound)
    "dapto":              "dapto",
    "dapto-dogs":         "dapto",
    # Richmond (VIC greyhound)
    "richmond":           "richmond",
    "richmond-raceway":   "richmond",
    # Wagga Wagga
    "wagga-wagga":        "wagga-wagga",
    "wagga":              "wagga-wagga",
    # Albury
    "albury":             "albury",
    "albury-racecourse":  "albury",
}


def apply_track_alias(track: str) -> str:
    """
    Normalise *track* then resolve it through TRACK_ALIASES.

    Returns the canonical slug (which should be present in DOMESTIC_TRACKS).
    If no alias entry exists the normalised slug is returned unchanged.
    """
    slug = normalize_track(track)
    return TRACK_ALIASES.get(slug, slug)


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
    "mt-druitt",  # NSW greyhound; aliases: mount-druitt, mt druitt
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
