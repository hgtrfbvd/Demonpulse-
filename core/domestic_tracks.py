"""
core/domestic_tracks.py
-----------------------
Authoritative track-based whitelist for AU + NZ domestic racing venues.

This module provides:
  1. Race-code-specific track whitelists (HORSE_AU_TRACKS, HORSE_NZ_TRACKS,
     GREYHOUND_AU_TRACKS, GREYHOUND_NZ_TRACKS, HARNESS_AU_TRACKS,
     HARNESS_NZ_TRACKS) — the primary domestic classification mechanism.
  2. Convenience union sets (AU_TRACKS, NZ_TRACKS, DOMESTIC_TRACKS) retained
     for backward-compatibility with existing callers.
  3. State/region identifier sets (AU_STATE_IDS, NZ_STATE_IDS) kept for any
     legacy callers; they are NOT used in domestic classification logic.

Classification (enforced in data_engine):
  Country is determined SOLELY by track membership in the race-code-specific
  sets.  No API country/state fields are consulted.

  IF code == HORSE:     check HORSE_AU_TRACKS / HORSE_NZ_TRACKS
  IF code == GREYHOUND: check GREYHOUND_AU_TRACKS / GREYHOUND_NZ_TRACKS
  IF code == HARNESS:   check HARNESS_AU_TRACKS / HARNESS_NZ_TRACKS

  Not found in the correct set → EXCLUDE (not domestic).

Primary entry point:
  classify_track_by_code(track, race_code)
      → 'au' | 'nz' | None

Structure:
  HORSE_AU_TRACKS      - Australian thoroughbred venues
  HORSE_NZ_TRACKS      - New Zealand thoroughbred venues
  GREYHOUND_AU_TRACKS  - Australian greyhound venues
  GREYHOUND_NZ_TRACKS  - New Zealand greyhound venues
  HARNESS_AU_TRACKS    - Australian harness venues
  HARNESS_NZ_TRACKS    - New Zealand harness venues
  AU_TRACKS            - union of all AU code-specific sets (compat)
  NZ_TRACKS            - union of all NZ code-specific sets (compat)
  DOMESTIC_TRACKS      - AU_TRACKS | NZ_TRACKS (compat gate set)
  TRACK_ALIASES        - dict mapping track name variants to canonical slugs

  normalize_track(track)                  - canonical normalisation for lookup
  apply_track_alias(track)                - normalise then resolve via TRACK_ALIASES
  classify_track_by_code(track, code)     - primary classification function
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
# COUNTRY / STATE IDENTIFIERS — used for Tier 1 + Tier 2 classification
# ---------------------------------------------------------------------------
# These frozensets contain normalised (lowercase, stripped) identifiers that
# appear in OddsPro country / state / location / region API fields.
#
# Classification priority (see module docstring):
#   TIER 1 — explicit country field → match against AU/NZ prefixes below
#   TIER 2 — state/location/region field → match against AU_STATE_IDS / NZ_STATE_IDS
#   TIER 3 — track whitelist (AU_TRACKS / NZ_TRACKS) — fallback only
# ---------------------------------------------------------------------------

#: Normalised AU country codes and state/territory names/abbreviations.
AU_STATE_IDS: frozenset[str] = frozenset({
    # Country-level identifiers
    "au", "aus", "australia",
    # State abbreviations (as returned by various OddsPro fields)
    "vic", "nsw", "qld", "sa", "wa", "tas", "act", "nt",
    # State full names
    "victoria", "new south wales", "queensland", "south australia",
    "western australia", "tasmania", "australian capital territory",
    "northern territory",
    # Numeric state codes: OddsPro has been observed returning integer state IDs
    # in its state/location fields for some AU endpoints.
    # 1=NSW, 2=VIC, 3=QLD, 4=SA, 5=WA, 6=TAS, 7=ACT, 8=NT.
    # Only include if OddsPro API documentation or live observation confirms usage.
    "1",   # NSW
    "2",   # VIC
    "3",   # QLD
    "4",   # SA
    "5",   # WA
    "6",   # TAS
    "7",   # ACT
    "8",   # NT
})

#: Normalised NZ country codes and region/city names.
NZ_STATE_IDS: frozenset[str] = frozenset({
    # Country-level identifiers
    "nz", "new zealand", "new-zealand",
    # Major NZ regions / cities that OddsPro may return
    "auckland", "waikato", "bay of plenty", "hawkes bay", "hawke's bay",
    "taranaki", "manawatu", "wellington", "nelson", "marlborough",
    "west coast", "canterbury", "otago", "southland", "northland",
    "gisborne",
    # Common OddsPro short-forms for NZ
    "nzl",
})

#: Canonical lowercase country codes for AU races (including common ISO aliases).
#: Used to deduplicate the check `country in ('au', 'aus', 'australia')` across files.
AU_COUNTRY_CODES: frozenset[str] = frozenset({"au", "aus", "australia"})

#: Canonical lowercase country codes for NZ races (including common ISO aliases).
#: Used to deduplicate the check `country in ('nz', 'new zealand', 'new-zealand', 'nzl')`.
NZ_COUNTRY_CODES: frozenset[str] = frozenset({"nz", "new zealand", "new-zealand", "nzl"})

#: Union of AU and NZ canonical country codes — a race is domestic if its
#: normalised country field is in this set.
DOMESTIC_COUNTRY_CODES: frozenset[str] = AU_COUNTRY_CODES | NZ_COUNTRY_CODES


# ---------------------------------------------------------------------------
# TRACK ALIASES — maps normalised variant slugs to canonical venue slugs
# ---------------------------------------------------------------------------
# Keys:   output of normalize_track() applied to the variant name
# Values: canonical slug that MUST exist in AU_TRACKS or NZ_TRACKS
# ---------------------------------------------------------------------------
TRACK_ALIASES: dict[str, str] = {
    # Mt Druitt (NSW greyhound): "mount druitt" / "mt druitt" → "mt-druitt"
    "mount-druitt":              "mt-druitt",
    # The Meadows (VIC greyhound): "meadows" → "the-meadows"
    "meadows":                   "the-meadows",
    # Wentworth Park (NSW greyhound): no-space variant
    "wentworthpark":             "wentworth-park",
    # Albion Park: API may return "Albion Park Raceway"
    "albion-park-raceway":       "albion-park",
    # Sandown (VIC thoroughbred): "Sandown Racecourse" → "sandown"
    "sandown-racecourse":        "sandown",
    # Gold Coast: "Gold Coast Turf" → "gold-coast"
    "gold-coast-turf":           "gold-coast",
    # Flemington: long form
    "flemington-racecourse":     "flemington",
    # Eagle Farm: long form
    "eagle-farm-racecourse":     "eagle-farm",
    # Moonee Valley: long form
    "moonee-valley-racecourse":  "moonee-valley",
    # Rosehill: "Rosehill Gardens" → "rosehill"
    "rosehill-gardens":          "rosehill",
    # Randwick: "Royal Randwick" → "randwick"
    "royal-randwick":            "randwick",
    # Hawkesbury: long form
    "hawkesbury-racecourse":     "hawkesbury",
    # Canterbury: "Canterbury Park" → "canterbury"
    "canterbury-park":           "canterbury",
    # Caulfield: long form
    "caulfield-racecourse":      "caulfield",
    # Morphettville: "Morphettville Parks" → "morphettville"
    "morphettville-parks":       "morphettville",
    # Ascot (WA): long form
    "ascot-racecourse":          "ascot",
    # Ellerslie (NZ): long form
    "ellerslie-racecourse":      "ellerslie",
    # Te Rapa (NZ): no-hyphen variant
    "terapa":                    "te-rapa",
    # Addington (NZ harness): "Addington Raceway" → "addington"
    "addington-raceway":         "addington",
    # Whanganui / Wanganui (NZ): both spellings resolve to canonical "whanganui"
    "wanganui":                  "whanganui",
    # Palmerston North (NZ): common abbreviation
    "palmy":                     "palmerston-north",
    # Gloucester Park (WA harness): no-space variant
    "gloucesterpark":            "gloucester-park",
    # Angle Park (SA greyhound): no-hyphen variant
    "anglepark":                 "angle-park",
    # Cannington (WA greyhound): "Cannington Raceway" → "cannington"
    "cannington-raceway":        "cannington",
    # Dapto (NSW greyhound): "Dapto Dogs" → "dapto"
    "dapto-dogs":                "dapto",
    # Richmond (VIC greyhound): "Richmond Raceway" → "richmond"
    "richmond-raceway":          "richmond",
    # Wagga Wagga: short form
    "wagga":                     "wagga-wagga",
    # Albury: long form
    "albury-racecourse":         "albury",
    # Gloucester Park harness (WA): long form
    "gloucester-park-raceway":   "gloucester-park",
    # Wayville harness (SA): "Globe Derby Park"
    "globe-derby-park":          "wayville",
    "globe-derby":               "wayville",
    # Alexandra Park (NZ harness): "Alexandra Park Raceway"
    "alexandra-park-raceway":    "alexandra-park",
    # Rangiora (NZ): long form
    "rangiora-raceway":          "rangiora",
    # Belmont (WA thoroughbred): short form → "belmont-park"
    "belmont":                   "belmont-park",
    # Wanganui / Whanganui (NZ): also map whanganui back as identity
    # (primary alias wanganui → whanganui already above)
    # Trentham (NZ thoroughbred): long form
    "trentham-racecourse":       "trentham",
    # Oamaru (NZ thoroughbred): no common variants; listed for explicitness
    # Wingatui (NZ thoroughbred): Dunedin venue, no common variants
    # Warrnambool (VIC thoroughbred): long form
    "warrnambool-racecourse":    "warrnambool",
    # Kensington (WA thoroughbred): sometimes listed as "Kensington Park"
    "kensington-park":           "kensington",
    # Shepparton (VIC greyhound / harness): no-hyphen variant
    "shepparton-raceway":        "shepparton",
    # Warragul (VIC greyhound): "Warragul Greyhounds" → "warragul"
    "warragul-greyhounds":       "warragul",
    # Murray Bridge (SA greyhound): "Murray Bridge Greyhound" → "murray-bridge"
    "murray-bridge-greyhound":   "murray-bridge",
    # Mandurah (WA greyhound): "Mandurah Greyhound" → "mandurah"
    "mandurah-greyhound":        "mandurah",
    # Casino (NSW greyhound): "Casino Greyhounds" → "casino"
    "casino-greyhound":          "casino",
    # Cambridge (NZ harness/greyhound): "Cambridge Raceway" → "cambridge"
    "cambridge-raceway":         "cambridge",
    # Kilmore (VIC harness): "Kilmore Raceway" → "kilmore"
    "kilmore-raceway":           "kilmore",
    # Redcliffe (QLD harness): "Redcliffe Paceway" → "redcliffe"
    "redcliffe-paceway":         "redcliffe",
    # Marburg (QLD harness): "Marburg Paceway" → "marburg"
    "marburg-paceway":           "marburg",
    # Port Pirie (SA harness): "Port Pirie Raceway" → "port-pirie"
    "port-pirie-raceway":        "port-pirie",
    # Methven (NZ harness): "Methven Raceway" → "methven"
    "methven-raceway":           "methven",
    # Manawatu (NZ harness): "Manawatu Raceway" → "manawatu"
    "manawatu-raceway":          "manawatu",
    # Invercargill (NZ harness): "Invercargill Raceway" → "invercargill"
    "invercargill-raceway":      "invercargill",
    # Forbury Park (NZ harness): "Forbury Park Raceway" → "forbury-park"
    "forbury-park-raceway":      "forbury-park",
    # Hobart (TAS): "Hobart Racecourse" → "hobart"
    "hobart-racecourse":         "hobart",
    # Menangle (NSW harness): "Tabcorp Park Menangle" → "menangle"
    "tabcorp-park-menangle":     "menangle",
    # Melton (VIC harness): "Tabcorp Park Melton" → "melton"
    "tabcorp-park-melton":       "melton",
    # "Tabcorp Park" (without suffix) → assume Menangle as the primary venue
    "tabcorp-park":              "menangle",
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
# RACE-CODE-SPECIFIC HARDCODED TRACK SETS
# ---------------------------------------------------------------------------
# These are the PRIMARY classification sets.  Country is determined SOLELY
# by track membership in the appropriate code-specific set — no API country
# or state fields are consulted.
#
# Track names are stored as normalised slugs (lowercase, spaces → hyphens).
# All lookup must go through apply_track_alias() first so that aliases
# (e.g. "belmont" → "belmont-park", "wanganui" → "whanganui") are resolved
# before the membership check.
# ---------------------------------------------------------------------------

#: Australian thoroughbred venues.
HORSE_AU_TRACKS: frozenset[str] = frozenset({
    # NSW
    "randwick", "rosehill", "canterbury", "warwick-farm", "kensington",
    "newcastle", "wyong", "gosford", "kembla-grange", "hawkesbury",
    # VIC
    "flemington", "caulfield", "moonee-valley", "sandown", "mornington",
    "ballarat", "bendigo", "geelong", "sale", "warrnambool", "traralgon",
    # QLD
    "eagle-farm", "doomben", "sunshine-coast", "gold-coast",
    # SA
    "morphettville", "oakbank", "gawler", "port-lincoln",
    # WA
    "ascot", "belmont-park", "pinjarra", "bunbury", "geraldton",
    # TAS
    "launceston", "hobart", "devonport",
})

#: New Zealand thoroughbred venues.
HORSE_NZ_TRACKS: frozenset[str] = frozenset({
    "ellerslie", "te-rapa", "pukekohe", "taupo", "tauranga", "ruakaka",
    "new-plymouth", "whanganui", "woodville", "otaki", "trentham",
    "riccarton", "ashburton", "timaru", "oamaru", "wingatui", "riverton",
})

#: Australian greyhound venues.
GREYHOUND_AU_TRACKS: frozenset[str] = frozenset({
    # VIC
    "the-meadows", "sandown-park", "sale", "ballarat", "bendigo", "geelong",
    "traralgon", "shepparton", "warragul",
    # SA
    "angle-park", "gawler", "murray-bridge",
    # VIC/NSW
    "richmond", "wentworth-park", "dapto",
    # NSW
    "casino", "lismore", "dubbo",
    # TAS
    "hobart", "launceston", "devonport",
    # WA
    "cannington", "mandurah", "northam",
})

#: New Zealand greyhound venues.
GREYHOUND_NZ_TRACKS: frozenset[str] = frozenset({
    "addington", "whanganui", "manukau", "cambridge",
})

#: Australian harness venues.
HARNESS_AU_TRACKS: frozenset[str] = frozenset({
    # NSW — "menangle-park" and "menangle" cover both API slugs;
    # "tabcorp-park-menangle" resolves to "menangle" via TRACK_ALIASES
    # so it need not be listed separately.
    "menangle", "menangle-park", "newcastle", "bathurst", "wagga-wagga",
    # VIC — "tabcorp-park-melton" resolves to "melton" via TRACK_ALIASES
    "melton", "ballarat", "bendigo", "kilmore", "shepparton", "geelong",
    # WA
    "gloucester-park", "pinjarra", "bunbury",
    # QLD
    "albion-park", "redcliffe", "marburg",
    # SA
    "angle-park", "port-pirie",
    # TAS
    "launceston", "hobart", "devonport",
})

#: New Zealand harness venues.
HARNESS_NZ_TRACKS: frozenset[str] = frozenset({
    "addington", "ashburton", "rangiora", "methven",
    "alexandra-park", "cambridge", "manawatu",
    "motukarara", "forbury-park", "invercargill",
})

# ---------------------------------------------------------------------------
# CLASSIFY BY CODE — primary entry point for domestic classification
# ---------------------------------------------------------------------------

# Internal map from race code to (AU set, NZ set) for O(1) lookup.
_CODE_TO_SETS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "HORSE":     (HORSE_AU_TRACKS,     HORSE_NZ_TRACKS),
    "GREYHOUND": (GREYHOUND_AU_TRACKS, GREYHOUND_NZ_TRACKS),
    "HARNESS":   (HARNESS_AU_TRACKS,   HARNESS_NZ_TRACKS),
}


def classify_track_by_code(track: str, race_code: str) -> str | None:
    """
    Classify a track as domestic AU/NZ using only the hardcoded race-code-
    specific sets.  No API country or state fields are consulted.

    Parameters
    ----------
    track:
        Raw track name (mixed-case, may have spaces).  Normalised internally
        via apply_track_alias() before lookup.
    race_code:
        OddsPro canonical race code: 'HORSE', 'GREYHOUND', or 'HARNESS'.
        'GALLOPS' is accepted as a legacy alias for 'HORSE'.

    Returns
    -------
    'au'   — track found in the AU set for the given race code
    'nz'   — track found in the NZ set for the given race code
    None   — track NOT found in either set (exclude; not domestic)
    """
    code = (race_code or "").upper()
    if code == "GALLOPS":
        code = "HORSE"

    sets = _CODE_TO_SETS.get(code)
    if sets is None:
        # Unknown race code — cannot classify
        return None

    slug = apply_track_alias(track)
    au_set, nz_set = sets
    if slug in au_set:
        return "au"
    if slug in nz_set:
        return "nz"
    return None



# ---------------------------------------------------------------------------
# AUSTRALIAN TRACKS (all codes: thoroughbred, harness, greyhound)
# ---------------------------------------------------------------------------
# Backward-compatibility union — union of all AU code-specific sets plus
# additional historical entries.  New classification code should use
# classify_track_by_code() instead of this set directly.
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
    "elwick", "mowbray", "devonport", "launceston", "hobart",
    # ACT thoroughbred
    "thoroughbred-park",
    # NT thoroughbred
    "darwin", "alice-springs",
    # WA thoroughbred — additional
    "kensington",
    # VIC thoroughbred — additional
    "warrnambool",
    # Greyhound (AU)
    "angle-park", "albion-park-greyhound", "albion-park", "cannington",
    "dapto", "sandown-park", "the-meadows", "temora", "wentworth-park",
    "mt-druitt",  # NSW greyhound; aliases: mount-druitt, mt druitt
    "richmond", "lismore-greyhound", "townsville-greyhound",
    "ipswich-greyhound", "gold-coast-greyhound", "capalaba",
    "bundaberg-greyhound", "rockhampton-greyhound", "mackay-greyhound",
    "cairns-greyhound", "hobart-greyhound", "launceston-greyhound",
    "alice-springs-greyhound",
    # Greyhound (AU) — additional
    "shepparton", "warragul", "murray-bridge", "casino", "mandurah",
    # Harness (AU)
    "albion-park-harness", "menangle", "penrith", "bankstown",
    "newcastle-harness", "tabcorp-park-menangle", "gloucester-park",
    "wayville", "melton",
    # Additional AU harness venues
    "menangle-park", "cambridge-park",
    "bathurst-harness", "parkes-harness",
    "wagga-wagga-harness", "albury-harness",
    "mildura-harness", "bendigo-harness",
    "ballarat-harness", "cranbourne-harness",
    "geelong-harness", "shepparton-harness",
    "tabcorp-park-melton",
    "pinjarra-harness",
    "bunbury-harness",
    # Harness (AU) — additional
    "kilmore", "redcliffe", "marburg", "port-pirie",
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
    # Additional NZ harness venues
    "rangiora", "feilding-harness", "ashburton-harness",
    "invercargill-harness", "gore-harness",
    # NZ thoroughbred — additional
    "trentham", "oamaru", "wingatui", "riverton",
    # NZ harness — additional
    "methven", "manawatu", "invercargill",
})

# ---------------------------------------------------------------------------
# MASTER WHITELIST — union of all domestic AU + NZ venues
# ---------------------------------------------------------------------------
# Includes all code-specific sets plus legacy AU_TRACKS / NZ_TRACKS entries.
# New classification code should use classify_track_by_code() instead.
# ---------------------------------------------------------------------------
DOMESTIC_TRACKS: frozenset[str] = (
    AU_TRACKS | NZ_TRACKS
    | HORSE_AU_TRACKS | HORSE_NZ_TRACKS
    | GREYHOUND_AU_TRACKS | GREYHOUND_NZ_TRACKS
    | HARNESS_AU_TRACKS | HARNESS_NZ_TRACKS
)


# ---------------------------------------------------------------------------
# CODE-GATED TRACKS — track + race_code aware classification
# ---------------------------------------------------------------------------
# Some track slugs are shared between AU venues and overseas venues of the
# same name.  These tracks are ONLY classified as domestic when the race_code
# matches the expected code for the AU venue.
#
# dict: normalized_slug → frozenset of valid OddsPro race codes
# A track that appears here is domestic ONLY when the race's code is in the set.
# ---------------------------------------------------------------------------
CODE_GATED_TRACKS: dict[str, frozenset[str]] = {
    # "sandown-park" is the Sandown Park greyhound venue in Melbourne, VIC.
    # UK's "Sandown Park" is a thoroughbred (HORSE) venue — must be excluded.
    "sandown-park": frozenset({"GREYHOUND"}),
}


# ---------------------------------------------------------------------------
# FORMFAV TRACK SUPPORT — separate from domestic classification
# ---------------------------------------------------------------------------
# These frozensets represent tracks that the FormFav API is known to support.
# A race can be classified as domestic (AU/NZ) but still NOT be in FormFav's
# database — especially small country meetings or overseas tracks that are
# incorrectly labelled with country='au' by OddsPro.
#
# Rules:
#   - Only tracks confirmed to exist in FormFav's venue list are included.
#   - Tracks NOT in this set are skipped before any FormFav API call is made.
#   - This set is intentionally defined independently of AU_TRACKS / NZ_TRACKS
#     so that each can evolve without coupling.
#
# Known excluded examples (produce FormFav 404):
#   - "riverton"  — small SA country harness venue; not in FormFav
#   - "mohawk"    — Ontario (Canada) harness track; incorrectly tagged country=au
#                   by some OddsPro feeds
# ---------------------------------------------------------------------------

#: FormFav-supported track slugs for Australian meetings.
FORMFAV_AU_TRACKS: frozenset[str] = frozenset({
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
    # AU greyhound
    "angle-park", "albion-park-greyhound", "albion-park", "cannington",
    "dapto", "sandown-park", "the-meadows", "temora", "wentworth-park",
    "mt-druitt", "richmond", "lismore-greyhound", "townsville-greyhound",
    "ipswich-greyhound", "gold-coast-greyhound", "capalaba",
    "bundaberg-greyhound", "rockhampton-greyhound", "mackay-greyhound",
    "cairns-greyhound", "hobart-greyhound", "launceston-greyhound",
    "alice-springs-greyhound",
    # AU harness
    "albion-park-harness", "menangle", "penrith", "bankstown",
    "newcastle-harness", "tabcorp-park-menangle", "gloucester-park",
    "wayville", "melton", "menangle-park", "cambridge-park",
    "bathurst-harness", "parkes-harness",
    "wagga-wagga-harness", "albury-harness",
    "mildura-harness", "bendigo-harness",
    "ballarat-harness", "cranbourne-harness",
    "geelong-harness", "shepparton-harness",
    "tabcorp-park-melton",
    "pinjarra-harness", "bunbury-harness",
})

#: FormFav-supported track slugs for New Zealand meetings.
FORMFAV_NZ_TRACKS: frozenset[str] = frozenset({
    # NZ thoroughbred
    "ellerslie", "te-rapa", "taupo", "hastings", "hawkes-bay",
    "rotorua", "wanganui", "whanganui", "otaki", "awapuni", "riccarton",
    "ashburton", "timaru", "gore", "winton", "invercargill",
    "ruakaka", "matamata", "cambridge", "pukekohe", "new-plymouth",
    "palmerston-north", "feilding", "foxton", "masterton",
    "levin", "woodville", "waverley", "marton", "wairoa",
    "napier", "gisborne", "tauranga", "huntly",
    "dargaville", "whangarei",
    # NZ greyhound
    "auckland-dogs", "manukau", "manawatu-dogs", "christchurch-dogs",
    "invercargill-dogs",
    # NZ harness
    "cambridge-harness", "addington", "forbury-park", "hutt-park",
    "alexandra-park", "teretonga", "motukarara",
    "rangiora", "feilding-harness", "ashburton-harness",
    "invercargill-harness", "gore-harness",
})

#: Union of all FormFav-supported AU + NZ track slugs.
FORMFAV_SUPPORTED_TRACKS: frozenset[str] = FORMFAV_AU_TRACKS | FORMFAV_NZ_TRACKS

#: FormFav-specific track name aliases.
#: These map OddsPro track slugs (after normalize_track) to the slug FormFav
#: expects.  Use only when the FormFav slug differs from the canonical domestic
#: slug already handled by TRACK_ALIASES.
FORMFAV_TRACK_ALIASES: dict[str, str] = {
    # "royal-randwick" is already mapped to "randwick" in TRACK_ALIASES;
    # keep here as explicit FormFav confirmation.
    "royal-randwick":        "randwick",
    # FormFav uses plain "ballarat" for both the thoroughbred and harness meetings.
    "ballarat-racecourse":   "ballarat",
    # FormFav uses plain "mornington" (no suffix).
    "mornington-racecourse": "mornington",
}


def resolve_formfav_track(track: str, country: str = "au") -> str | None:
    """
    Resolve and validate a track name for use with the FormFav API.

    Returns the FormFav-compatible track slug if the track is supported for
    the given country, or `None` if the track/country combination is NOT
    in FormFav's supported venue list.

    Processing order:
      1. normalize_track() — lowercase, spaces→hyphens, strip non-alnum
      2. Apply TRACK_ALIASES (shared domestic aliases)
      3. Apply FORMFAV_TRACK_ALIASES (FormFav-specific overrides)
      4. Validate against FORMFAV_AU_TRACKS (country=au) or
         FORMFAV_NZ_TRACKS (country=nz)

    Parameters
    ----------
    track:
        Raw track name as stored in today_races (may be mixed-case, have
        spaces, etc.).
    country:
        FormFav country code — 'au' or 'nz'.  Any other value causes the
        function to return `None` immediately (FormFav only supports AU/NZ).
        The caller is responsible for logging why the resolution failed.
    """
    norm_country = (country or "").strip().lower()
    if norm_country not in ("au", "nz"):
        return None

    slug = normalize_track(track)
    # Apply shared domestic alias (e.g. royal-randwick → randwick)
    slug = TRACK_ALIASES.get(slug, slug)
    # Apply FormFav-specific alias override
    slug = FORMFAV_TRACK_ALIASES.get(slug, slug)

    if norm_country == "au":
        return slug if slug in FORMFAV_AU_TRACKS else None
    # nz
    return slug if slug in FORMFAV_NZ_TRACKS else None
