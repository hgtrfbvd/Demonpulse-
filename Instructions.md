DemonPulse has Pro+ access to FormFav and uses OddsPro public API.
We need to collect ALL available data from both sources.
Implement the following changes across the codebase:

---
PART 1 — FORMFAV CONNECTOR: Capture all missing fields from /v1/form

FILE: connectors/formfav_connector.py

1a. Add missing fields to RunnerRecord dataclass (after line 102):

    # Missing fields from FormFav API
    last20_starts: str = ""              # last20Starts — 20-start summary
    racing_colours: str = ""             # racingColours — jockey silks
    gear_change: str = ""                # gearChange — equipment changes (VERY valuable)
    # Expanded stats breakdown (currently stored as raw JSONB only)
    stats_overall_starts: int | None = None
    stats_overall_wins: int | None = None
    stats_overall_places: int | None = None
    stats_overall_seconds: int | None = None
    stats_overall_thirds: int | None = None
    stats_overall_win_pct: float | None = None
    stats_overall_place_pct: float | None = None
    stats_first_up: dict | None = None   # firstUp stats
    stats_second_up: dict | None = None  # secondUp stats

1b. Add missing fields to RaceRecord dataclass (after line 60):

    prize_money: str = ""                # prizeMoney from /v1/form

1c. In fetch_race_form(), update the runner loop to capture new fields:

    In the RunnerRecord constructor call, add after existing fields:
        last20_starts=runner.get("last20Starts") or "",
        racing_colours=runner.get("racingColours") or "",
        gear_change=runner.get("gearChange") or "",
        stats_first_up=stats.get("firstUp") or None,
        stats_second_up=stats.get("secondUp") or None,
        stats_overall_starts=overall.get("starts"),
        stats_overall_wins=overall.get("wins"),
        stats_overall_places=overall.get("places"),
        stats_overall_seconds=overall.get("seconds"),
        stats_overall_thirds=overall.get("thirds"),
        stats_overall_win_pct=overall.get("winPercent"),
        stats_overall_place_pct=overall.get("placePercent"),

    (where overall = (stats or {}).get("overall") or {})

1d. In fetch_race_form(), update the RaceRecord constructor:

    Add: prize_money=payload.get("prizeMoney") or "",

---
PART 2 — DATABASE SCHEMA: Add missing columns to formfav_runner_enrichment

FILE: sql/001_canonical_schema.sql AND database.py

2a. Add to formfav_runner_enrichment table (in the CREATE TABLE and as ALTER TABLE):

    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS last20_starts      TEXT    DEFAULT '';
    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS racing_colours     TEXT    DEFAULT '';
    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS gear_change        TEXT    DEFAULT '';
    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS stats_first_up     JSONB;
    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS stats_second_up    JSONB;
    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS stats_overall_starts   INTEGER;
    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS stats_overall_wins     INTEGER;
    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS stats_overall_places   INTEGER;
    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS stats_overall_win_pct  NUMERIC;
    ALTER TABLE formfav_runner_enrichment ADD COLUMN IF NOT EXISTS stats_overall_place_pct NUMERIC;

2b. Add to formfav_race_enrichment table:

    ALTER TABLE formfav_race_enrichment ADD COLUMN IF NOT EXISTS prize_money TEXT DEFAULT '';

2c. Add new tables for Pro stats (add to sql/001_canonical_schema.sql):

    -- Jockey stats from FormFav /v1/stats/jockey/{name}
    CREATE TABLE IF NOT EXISTS jockey_stats (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        jockey_name     TEXT NOT NULL,
        race_code       TEXT NOT NULL DEFAULT 'gallops',
        total_starts    INTEGER DEFAULT 0,
        total_wins      INTEGER DEFAULT 0,
        overall_win_rate NUMERIC,
        overall_place_rate NUMERIC,
        recent_win_rate NUMERIC,
        track_stats     JSONB,
        raw_response    JSONB,
        fetched_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (jockey_name, race_code)
    );

    -- Trainer stats from FormFav /v1/stats/trainer/{name}
    CREATE TABLE IF NOT EXISTS trainer_stats (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        trainer_name    TEXT NOT NULL,
        race_code       TEXT NOT NULL DEFAULT 'gallops',
        total_starts    INTEGER DEFAULT 0,
        total_wins      INTEGER DEFAULT 0,
        overall_win_rate NUMERIC,
        overall_place_rate NUMERIC,
        recent_win_rate NUMERIC,
        track_stats     JSONB,
        raw_response    JSONB,
        fetched_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (trainer_name, race_code)
    );

    Also add test_ mirror tables:
    CREATE TABLE IF NOT EXISTS test_jockey_stats  ( LIKE jockey_stats  INCLUDING ALL );
    CREATE TABLE IF NOT EXISTS test_trainer_stats ( LIKE trainer_stats INCLUDING ALL );

---
PART 3 — DATABASE LAYER: Wire up new storage functions

FILE: database.py

3a. Update upsert_formfav_runner_enrichment payload to include new fields:

    In the payload dict, add after existing fields:
        "last20_starts":            data.get("last20_starts") or "",
        "racing_colours":           data.get("racing_colours") or "",
        "gear_change":              data.get("gear_change") or "",
        "stats_first_up":           _as_json(data.get("stats_first_up")),
        "stats_second_up":          _as_json(data.get("stats_second_up")),
        "stats_overall_starts":     data.get("stats_overall_starts"),
        "stats_overall_wins":       data.get("stats_overall_wins"),
        "stats_overall_places":     data.get("stats_overall_places"),
        "stats_overall_win_pct":    data.get("stats_overall_win_pct"),
        "stats_overall_place_pct":  data.get("stats_overall_place_pct"),

3b. Update upsert_formfav_race_enrichment payload:

    Add: "prize_money": data.get("prize_money") or "",

3c. Add upsert functions for new tables:

def upsert_jockey_stats(data: dict) -> None:
    payload = {
        "jockey_name":       data.get("jockey_name") or data.get("jockeyName") or "",
        "race_code":         data.get("race_code") or "gallops",
        "total_starts":      int(data.get("total_starts") or data.get("totalStarts") or 0),
        "total_wins":        int(data.get("total_wins") or data.get("totalWins") or 0),
        "overall_win_rate":  data.get("overall_win_rate") or data.get("overallWinRate"),
        "overall_place_rate":data.get("overall_place_rate") or data.get("overallPlaceRate"),
        "recent_win_rate":   (data.get("recentStats") or {}).get("winRate"),
        "track_stats":       _as_json(data.get("trackStats") or []),
        "raw_response":      _as_json(data),
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
        "updated_at":        datetime.now(timezone.utc).isoformat(),
    }
    if not payload["jockey_name"]:
        return
    safe_query(lambda: get_db().table(T("jockey_stats"))
        .upsert(payload, on_conflict="jockey_name,race_code").execute())

def upsert_trainer_stats(data: dict) -> None:
    payload = {
        "trainer_name":      data.get("trainer_name") or data.get("trainerName") or "",
        "race_code":         data.get("race_code") or "gallops",
        "total_starts":      int(data.get("total_starts") or data.get("totalStarts") or 0),
        "total_wins":        int(data.get("total_wins") or data.get("totalWins") or 0),
        "overall_win_rate":  data.get("overall_win_rate") or data.get("overallWinRate"),
        "overall_place_rate":data.get("overall_place_rate") or data.get("overallPlaceRate"),
        "recent_win_rate":   (data.get("recentStats") or {}).get("winRate"),
        "track_stats":       _as_json(data.get("trackStats") or []),
        "raw_response":      _as_json(data),
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
        "updated_at":        datetime.now(timezone.utc).isoformat(),
    }
    if not payload["trainer_name"]:
        return
    safe_query(lambda: get_db().table(T("trainer_stats"))
        .upsert(payload, on_conflict="trainer_name,race_code").execute())

def upsert_track_bias(data: dict) -> None:
    """Store FormFav track bias data into track_profiles table."""
    track = (data.get("venue") or "").lower().replace(" ", "-")
    if not track:
        return
    race_type = data.get("raceType") or "R"
    code = {"R": "HORSE", "H": "HARNESS", "G": "GREYHOUND"}.get(race_type, "HORSE")
    barrier_stats = data.get("barrierStats") or []
    # Compute inside/outside bias from barrier stats
    inside = next((b for b in barrier_stats if b.get("barrierNumber") == 1), {})
    payload = {
        "track_name":       track,
        "code":             code,
        "inside_bias":      inside.get("advantage"),
        "leader_win_pct":   inside.get("winRate"),
        "updated_at":       datetime.now(timezone.utc).isoformat(),
    }
    safe_query(lambda: get_db().table(T("track_profiles"))
        .upsert(payload, on_conflict="track_name,code").execute())

def upsert_market_snapshot(data: dict) -> None:
    """Store OddsPro movers/drifters/top-favs data."""
    payload = {
        "race_uid":       data.get("race_uid") or "",
        "date":           data.get("date") or date.today().isoformat(),
        "track":          (data.get("track") or "").lower().replace(" ", "-"),
        "race_num":       data.get("raceNumber") or data.get("race_num"),
        "runner_name":    data.get("runnerName") or data.get("runner_name") or "",
        "box_num":        data.get("runnerNumber") or data.get("box_num"),
        "opening_price":  data.get("firstPrice") or data.get("opening_price"),
        "analysis_price": data.get("currentBestOdds") or data.get("analysis_price"),
        "price_movement": str(data.get("movementPercentage") or ""),
        "steam_flag":     bool(data.get("is_mover", False)),
        "drift_flag":     bool(data.get("is_drifter", False)),
        "snapshot_time":  datetime.now(timezone.utc).isoformat(),
    }
    safe_query(lambda: get_db().table(T("market_snapshots")).insert(payload).execute())

---
PART 4 — FORMFAV CONNECTOR: Add Pro stat fetch methods

FILE: connectors/formfav_connector.py

Add these methods to the FormFavConnector class:

    def fetch_track_bias(self, track: str, race_code: str = "gallops",
                         window: int | None = 90) -> dict | None:
        """GET /v1/stats/track-bias/{track} — barrier/box bias stats (Pro)."""
        if not self.api_key:
            return None
        params = {"race_code": race_code}
        if window:
            params["window"] = window
        try:
            resp = requests.get(
                f"{BASE_URL}/v1/stats/track-bias/{track}",
                params=params, headers=self._headers(), timeout=self.timeout
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.debug(f"[FORMFAV] track-bias fetch failed {track}: {e}")
            return None

    def fetch_jockey_stats(self, jockey_name: str,
                           race_code: str = "gallops") -> dict | None:
        """GET /v1/stats/jockey/{name} — jockey career stats (Pro)."""
        if not self.api_key:
            return None
        from urllib.parse import quote
        params = {"race_code": race_code}
        try:
            resp = requests.get(
                f"{BASE_URL}/v1/stats/jockey/{quote(jockey_name)}",
                params=params, headers=self._headers(), timeout=self.timeout
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.debug(f"[FORMFAV] jockey stats fetch failed {jockey_name}: {e}")
            return None

    def fetch_trainer_stats(self, trainer_name: str,
                            race_code: str = "gallops") -> dict | None:
        """GET /v1/stats/trainer/{name} — trainer career stats (Pro)."""
        if not self.api_key:
            return None
        from urllib.parse import quote
        params = {"race_code": race_code}
        try:
            resp = requests.get(
                f"{BASE_URL}/v1/stats/trainer/{quote(trainer_name)}",
                params=params, headers=self._headers(), timeout=self.timeout
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.debug(f"[FORMFAV] trainer stats fetch failed {trainer_name}: {e}")
            return None

    def fetch_venues(self, race_type: str | None = None,
                     country: str = "au") -> list[dict]:
        """GET /v1/form/venues — get canonical venue/track names (Pro)."""
        if not self.api_key:
            return []
        params = {"country": country}
        if race_type:
            params["raceType"] = race_type
        try:
            resp = requests.get(
                f"{BASE_URL}/v1/form/venues",
                params=params, headers=self._headers(), timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("venues") or []
        except Exception as e:
            log.warning(f"[FORMFAV] fetch_venues failed: {e}")
            return []

---
PART 5 — DATA ENGINE: Call new Pro endpoints in formfav_sync

FILE: data_engine.py

In formfav_sync(), after successfully enriching each race (after upsert_formfav_race_enrichment):

    # Fetch track bias for this track (once per track per day, not per race)
    # Use a set to avoid duplicate calls for the same track
    if not hasattr(formfav_sync, '_bias_fetched'):
        formfav_sync._bias_fetched = set()
    bias_key = f"{ff_track}_{mapped_race_code}"
    if bias_key not in formfav_sync._bias_fetched:
        try:
            bias_data = ff.fetch_track_bias(ff_track, race_code=mapped_race_code)
            if bias_data:
                from database import upsert_track_bias
                upsert_track_bias(bias_data)
                formfav_sync._bias_fetched.add(bias_key)
                log.info(f"[FORMFAV] TRACK_BIAS stored track={ff_track}")
        except Exception as _be:
            log.debug(f"formfav_sync: track bias fetch failed: {_be}")

Also in formfav_sync(), after storing runner enrichment, collect unique jockeys/trainers
and fetch their stats (rate-limited — only fetch if not already stored today):

    # After runner enrichment loop, collect unique persons for stats
    _jockeys_to_fetch = set()
    _trainers_to_fetch = set()
    for runner in ff_runners:
        if runner.jockey:
            _jockeys_to_fetch.add((runner.jockey, mapped_race_code))
        if runner.trainer:
            _trainers_to_fetch.add((runner.trainer, mapped_race_code))

    # Fetch jockey stats (max 5 per race to respect rate limits)
    from database import upsert_jockey_stats, upsert_trainer_stats
    for jockey, rc in list(_jockeys_to_fetch)[:5]:
        try:
            jstats = ff.fetch_jockey_stats(jockey, race_code=rc)
            if jstats:
                jstats["race_code"] = rc
                upsert_jockey_stats(jstats)
        except Exception:
            pass

    for trainer, rc in list(_trainers_to_fetch)[:5]:
        try:
            tstats = ff.fetch_trainer_stats(trainer, race_code=rc)
            if tstats:
                tstats["race_code"] = rc
                upsert_trainer_stats(tstats)
        except Exception:
            pass

---
PART 6 — DATA ENGINE: Use OddsPro movers/drifters/top-favs endpoints

FILE: data_engine.py

Add a new function market_snapshot_sweep() called by the scheduler every 5 min:

def market_snapshot_sweep(target_date: str | None = None) -> dict:
    """
    Fetch OddsPro market intelligence: movers, drifters, top-favs.
    Stores into market_snapshots table for display in the UI.
    Called every 5 minutes alongside near_jump_refresh.
    """
    conn = _get_oddspro()
    if not conn.is_enabled():
        return {"ok": False, "reason": "oddspro_not_configured"}

    today = target_date or date.today().isoformat()
    stored = 0

    try:
        from database import upsert_market_snapshot

        # Movers (price shortenings)
        movers = conn.fetch_movers(location="domestic", date=today, limit=20)
        for item in movers:
            item["is_mover"] = True
            item["date"] = today
            upsert_market_snapshot(item)
            stored += 1

        # Drifters (price increases)
        drifters = conn.fetch_drifters(location="domestic", date=today, limit=20)
        for item in drifters:
            item["is_drifter"] = True
            item["date"] = today
            upsert_market_snapshot(item)
            stored += 1

        # Top favs
        favs = conn.fetch_top_favs(location="domestic", date=today, limit=10)
        for item in favs:
            item["date"] = today
            upsert_market_snapshot(item)
            stored += 1

        log.info(f"[MARKET] snapshot_sweep: stored={stored} movers+drifters+favs")
        return {"ok": True, "stored": stored, "date": today}

    except Exception as e:
        log.error(f"market_snapshot_sweep failed: {e}")
        return {"ok": False, "error": str(e)}

---
PART 7 — SCHEDULER: Add market sweep and Pro stat sync to schedule

FILE: scheduler.py

Add new interval and cycle:

MARKET_SNAPSHOT_INTERVAL = 300   # 5 min — same as near_jump

In run_scheduler() loop:
    if now - last_market_snapshot >= MARKET_SNAPSHOT_INTERVAL:
        try:
            from data_engine import market_snapshot_sweep
            market_snapshot_sweep()
        except Exception as e:
            log.error(f"Market snapshot failed: {e}")
        last_market_snapshot = now

Also initialise: last_market_snapshot = now - MARKET_SNAPSHOT_INTERVAL

---
PART 8 — DATA ENGINE: Pass new runner fields through formfav_sync storage

FILE: data_engine.py

In formfav_sync(), in the runner_payload dict, add the new fields:

    "last20_starts":           runner.last20_starts,
    "racing_colours":          runner.racing_colours,
    "gear_change":             runner.gear_change,
    "stats_first_up":          runner.stats_first_up,
    "stats_second_up":         runner.stats_second_up,
    "stats_overall_starts":    runner.stats_overall_starts,
    "stats_overall_wins":      runner.stats_overall_wins,
    "stats_overall_places":    runner.stats_overall_places,
    "stats_overall_win_pct":   runner.stats_overall_win_pct,
    "stats_overall_place_pct": runner.stats_overall_place_pct,

---
PART 9 — ODDSPRO CONNECTOR: Fix undocumented 'country' param (already noted)

FILE: connectors/oddspro_connector.py

In ALL methods that start params with {"country": self.country}, remove that.
Replace with {} and only add "location": "domestic" where appropriate:
- fetch_meetings: params = {}  (location="domestic" added only when explicitly passed)
- fetch_results: params = {}
- fetch_meeting_races_with_runners: params = {}  (remove the country param)
- fetch_tracks: params = {}

---
PART 10 — MIGRATIONS: Add migration runner for new columns

FILE: migrations.py

Add a function run_pro_schema_migrations() that runs all the ALTER TABLE statements
from Part 2 above. Call it from app.py startup or via /api/admin/migrate-all.

---
SUMMARY OF NEW DATA FLOW:
1. OddsPro full_sweep → today_races + today_runners + meetings (race/runner basis)
2. FormFav formfav_sync → formfav_race_enrichment + formfav_runner_enrichment
   (now includes: gear_change, last20Starts, racingColours, full stats breakdown)
3. FormFav track bias → track_profiles (once per track per code per day)
4. FormFav jockey/trainer stats → jockey_stats + trainer_stats (per person per race day)
5. OddsPro movers/drifters/top-favs → market_snapshots (every 5 min)
6. OddsPro results → results_log (every 5 min result check)


Fix decorator/badge display in DemonPulse. Decorators come from FormFav Pro
and have this structure:
{
  "type": "last_start_winner",
  "label": "Last Start Winner",      // full label
  "shortLabel": "Winner",            // compact label for badges
  "category": "form",                // form|specialization|conditions|fitness|running_style|class|barrier|connections
  "sentiment": "+",                  // "+" positive, "/" neutral, "-" negative
  "description": "Won their most recent start",
  "detail": "Won last start"
}
Max 4 per runner. Pro tier only.

---
FIX 1 — static/js/live.js: Replace buildDecoratorBadges() 

Replace the current function entirely:

    function buildDecoratorBadges(decorators) {
        if (!decorators || !decorators.length) return "";
        const badges = Array.isArray(decorators) ? decorators : [];
        if (!badges.length) return "";

        // Color by sentiment, not label
        function sentimentColor(sentiment) {
            if (sentiment === "+") return "var(--green)";
            if (sentiment === "-") return "var(--red-1)";
            return "var(--amber)";
        }

        // Category → icon prefix
        function categoryIcon(category) {
            const icons = {
                "form":          "◆",
                "specialization":"★",
                "conditions":    "☁",
                "fitness":       "⚡",
                "running_style": "→",
                "class":         "▲",
                "barrier":       "▣",
                "connections":   "👤",
            };
            return icons[category] || "";
        }

        return `<div class="decorator-badges">${
            badges.map(d => {
                const label       = typeof d === "string" ? d : (d.label || "");
                const shortLabel  = typeof d === "string" ? d : (d.shortLabel || d.label || "");
                const sentiment   = typeof d === "object" ? (d.sentiment || "+") : "+";
                const category    = typeof d === "object" ? (d.category || "") : "";
                const description = typeof d === "object" ? (d.description || "") : "";
                const detail      = typeof d === "object" ? (d.detail || "") : "";
                const color       = sentimentColor(sentiment);
                const icon        = categoryIcon(category);
                const tooltip     = [description, detail].filter(Boolean).join(" — ");

                return `<span class="decorator-badge decorator-${sentiment === "+" ? "pos" : sentiment === "-" ? "neg" : "neu"}"
                    style="border-color:${color};color:${color}"
                    title="${esc(tooltip || label)}"
                    data-type="${esc(d.type || "")}"
                    data-category="${esc(category)}">${icon ? icon + " " : ""}${esc(shortLabel)}</span>`;
            }).join("")
        }</div>`;
    }

---
FIX 2 — static/js/live.js: Fix summary row badge display

The summary row currently shows up to 2 badges using d.label (long text).
Replace with shortLabel and show all up to 4, grouped by sentiment:

Replace this block (around line 423-428):
    const topBadges = (r.ff_decorators || []).slice(0, 2).map(d => {
        const label = typeof d === "string" ? d : (d.label || "");
        return label ? `<span class="runner-badge">${esc(label)}</span>` : "";
    }).join("");

With:
    const topBadges = (r.ff_decorators || []).slice(0, 4).map(d => {
        const shortLabel = typeof d === "string" ? d : (d.shortLabel || d.label || "");
        const sentiment  = typeof d === "object" ? (d.sentiment || "+") : "+";
        const tooltip    = typeof d === "object"
            ? [d.description, d.detail].filter(Boolean).join(" — ")
            : "";
        const cls = sentiment === "+" ? "runner-badge-pos"
                  : sentiment === "-" ? "runner-badge-neg"
                  : "runner-badge-neu";
        if (!shortLabel) return "";
        return `<span class="runner-badge ${cls}" title="${esc(tooltip || shortLabel)}">${esc(shortLabel)}</span>`;
    }).join("");

---
FIX 3 — static/css/pages.css: Add proper badge styles

Add these CSS rules (find the .decorator-badge rule and replace/extend it):

/* Runner summary row badges (compact) */
.runner-badge {
    display: inline-block;
    font-size: 0.65rem;
    font-weight: 700;
    padding: 1px 5px;
    border-radius: 4px;
    margin-left: 4px;
    vertical-align: middle;
    letter-spacing: 0.04em;
    white-space: nowrap;
}
.runner-badge-pos {
    background: rgba(61, 214, 140, 0.15);
    color: var(--green);
    border: 1px solid rgba(61, 214, 140, 0.35);
}
.runner-badge-neg {
    background: rgba(255, 45, 45, 0.12);
    color: var(--red-1);
    border: 1px solid rgba(255, 45, 45, 0.3);
}
.runner-badge-neu {
    background: rgba(255, 179, 71, 0.12);
    color: var(--amber);
    border: 1px solid rgba(255, 179, 71, 0.3);
}

/* Expanded section badges (full size) */
.decorator-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 8px 0;
}
.decorator-badge {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 0.75rem;
    font-weight: 700;
    padding: 3px 9px;
    border-radius: 999px;
    border: 1px solid;
    letter-spacing: 0.03em;
    cursor: default;
    white-space: nowrap;
}
.decorator-pos { background: rgba(61, 214, 140, 0.1); }
.decorator-neg { background: rgba(255, 45, 45, 0.08); }
.decorator-neu { background: rgba(255, 179, 71, 0.08); }

---
FIX 4 — static/js/live.js: Show description/detail in expanded runner section

In the runner expand template (inside the buildRunnerCards innerHTML), 
after buildDecoratorBadges(r.ff_decorators), add a detail list for badges
that have description/detail text:

Replace:
    ${buildDecoratorBadges(r.ff_decorators)}

With:
    ${buildDecoratorBadges(r.ff_decorators)}
    ${(() => {
        const detailed = (r.ff_decorators || [])
            .filter(d => typeof d === "object" && (d.description || d.detail));
        if (!detailed.length) return "";
        return `<div class="decorator-detail-list">${
            detailed.map(d => {
                const sentColor = d.sentiment === "+" ? "var(--green)"
                                : d.sentiment === "-" ? "var(--red-1)"
                                : "var(--amber)";
                return `<div class="decorator-detail-row">
                    <span class="decorator-detail-label" style="color:${sentColor}">${esc(d.shortLabel || d.label)}</span>
                    <span class="decorator-detail-text">${esc(d.description || "")}${d.detail ? ` <em>${esc(d.detail)}</em>` : ""}</span>
                </div>`;
            }).join("")
        }</div>`;
    })()}

Add CSS:
.decorator-detail-list {
    margin: 4px 0 8px;
    display: flex;
    flex-direction: column;
    gap: 3px;
}
.decorator-detail-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 0.78rem;
    padding: 2px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}
.decorator-detail-label {
    font-weight: 700;
    min-width: 80px;
    flex-shrink: 0;
}
.decorator-detail-text {
    color: var(--text-soft);
}
.decorator-detail-text em {
    color: var(--text-dim);
    font-style: normal;
    margin-left: 4px;
}

---
RESULT after fix:
- Summary row: shows up to 4 compact badges (shortLabel) — green/amber/red by sentiment
- Expanded view: full badges (shortLabel) with proper colours + category icon
- Expanded view: detail list showing description + stat detail for each badge
- Tooltips on hover show full description
- No more hardcoded label matching — sentiment field drives all colouring

Fix runner identity and data linkage in DemonPulse so enrichment data
always goes to the correct horse/dog in the correct race.

---
FIX 1 — database.py: Add runner_name as fallback join key in the merge

In app.py, the runner enrichment join currently does:
    key = ff_r.get("number") or ff_r.get("box_num")
    ff_runner_map[int(key)] = ff_r

This fails silently for gallops/harness when barrier ≠ saddlecloth number.

Replace the entire merge block in /api/live/race/<race_uid> with a 
two-pass join: first by number (exact), then by name (fallback):

        ff_runner_rows = get_formfav_runner_enrichments(race_uid)

        # Build TWO lookup indexes: by number AND by normalised name
        ff_by_number = {}
        ff_by_name   = {}
        for ff_r in ff_runner_rows:
            num = ff_r.get("number")
            if num is not None:
                ff_by_number[int(num)] = ff_r
            name = (ff_r.get("runner_name") or "").strip().lower()
            if name:
                ff_by_name[name] = ff_r

        enriched_runners = []
        for r in runners:
            # Pass 1: match by number (most reliable)
            box = r.get("box_num") or r.get("number") or r.get("barrier")
            ff = ff_by_number.get(int(box)) if box is not None else None

            # Pass 2: match by name if number lookup failed
            if not ff:
                rname = (r.get("name") or r.get("runner_name") or "").strip().lower()
                ff = ff_by_name.get(rname)

            merged = {**r}
            if ff:
                # ... rest of the field copy as before

Apply the same two-pass pattern everywhere this join appears:
- app.py /api/live/race/<race_uid>
- board_builder.py get_board_for_today()  
  (currently uses get_formfav_runner_enrichments_for_races which returns lists,
   so add name fallback after the number lookup in the board attach loop)

---
FIX 2 — database.py + sql/001_canonical_schema.sql:
Add date column to formfav_runner_enrichment for time-scoped queries

Currently formfav_runner_enrichment has no date column.
If a horse runs multiple times, old enrichment rows accumulate.
The UNIQUE(race_uid, number) constraint handles this correctly since
race_uid includes the date — but add explicit date for queries and indexes.

Add to formfav_runner_enrichment:
    ALTER TABLE formfav_runner_enrichment 
        ADD COLUMN IF NOT EXISTS date DATE;
    
    CREATE INDEX IF NOT EXISTS idx_formfav_runner_enrichment_date 
        ON formfav_runner_enrichment(date);

Update upsert_formfav_runner_enrichment payload to include:
    "date": data.get("date") or date.today().isoformat(),

---
FIX 3 — sql/001_canonical_schema.sql: Fix jockey_stats/trainer_stats 
to link back to specific races and runners

The new jockey_stats and trainer_stats tables (from the previous prompt)
need context columns so you know which race/runner they relate to.
Without this, jockey stats are floating lookup tables with no race context.

Redesign: Instead of standalone jockey/trainer stat tables, store per-runner-per-race:

Replace the proposed jockey_stats/trainer_stats tables with a single table
that stores person stats IN THE CONTEXT OF A SPECIFIC RACE:

    CREATE TABLE IF NOT EXISTS runner_connection_stats (
        id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        race_uid        TEXT        NOT NULL,
        date            DATE        NOT NULL,
        track           TEXT        NOT NULL DEFAULT '',
        race_num        INTEGER     NOT NULL,
        runner_name     TEXT        NOT NULL DEFAULT '',
        runner_number   INTEGER,
        person_type     TEXT        NOT NULL,  -- 'jockey' or 'trainer'
        person_name     TEXT        NOT NULL DEFAULT '',
        race_code       TEXT        NOT NULL DEFAULT 'gallops',
        total_starts    INTEGER,
        total_wins      INTEGER,
        overall_win_rate    NUMERIC,
        overall_place_rate  NUMERIC,
        recent_win_rate     NUMERIC,
        track_win_rate      NUMERIC,   -- win rate at THIS specific track
        track_starts        INTEGER,   -- starts at THIS specific track
        raw_response    JSONB,
        fetched_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (race_uid, runner_number, person_type)
    );

    CREATE INDEX IF NOT EXISTS idx_runner_connection_race_uid 
        ON runner_connection_stats(race_uid);
    CREATE INDEX IF NOT EXISTS idx_runner_connection_person 
        ON runner_connection_stats(person_name, person_type, race_code);

    CREATE TABLE IF NOT EXISTS test_runner_connection_stats 
        ( LIKE runner_connection_stats INCLUDING ALL );

This way every jockey/trainer stat row is anchored to:
- Which race (race_uid, date, track, race_num)
- Which runner (runner_name, runner_number)
- Which person type (jockey or trainer)

---
FIX 4 — data_engine.py: Update formfav_sync to store connection stats
with full race+runner context

In formfav_sync(), when fetching jockey/trainer stats, pass race context:

    from database import upsert_runner_connection_stats

    for runner in ff_runners:
        race_context = {
            "race_uid":      race_uid,
            "date":          race_date,
            "track":         track,
            "race_num":      race_num,
            "runner_name":   runner.name,
            "runner_number": runner.number if runner.number is not None else runner.box_num,
            "race_code":     mapped_race_code,
        }

        if runner.jockey:
            try:
                jstats = ff.fetch_jockey_stats(runner.jockey, race_code=mapped_race_code)
                if jstats:
                    # Extract track-specific stats for THIS track
                    track_stats = jstats.get("trackStats") or []
                    this_track = next(
                        (t for t in track_stats 
                         if (t.get("venue") or "").lower().replace(" ", "-") == track),
                        {}
                    )
                    upsert_runner_connection_stats({
                        **race_context,
                        "person_type":       "jockey",
                        "person_name":       runner.jockey,
                        "total_starts":      jstats.get("totalStarts"),
                        "total_wins":        jstats.get("totalWins"),
                        "overall_win_rate":  jstats.get("overallWinRate"),
                        "overall_place_rate":jstats.get("overallPlaceRate"),
                        "recent_win_rate":   (jstats.get("recentStats") or {}).get("winRate"),
                        "track_win_rate":    this_track.get("winRate"),
                        "track_starts":      this_track.get("totalStarts"),
                        "raw_response":      jstats,
                    })
            except Exception as _je:
                log.debug(f"jockey stats fetch failed {runner.jockey}: {_je}")

        if runner.trainer:
            try:
                tstats = ff.fetch_trainer_stats(runner.trainer, race_code=mapped_race_code)
                if tstats:
                    track_stats = tstats.get("trackStats") or []
                    this_track = next(
                        (t for t in track_stats
                         if (t.get("venue") or "").lower().replace(" ", "-") == track),
                        {}
                    )
                    upsert_runner_connection_stats({
                        **race_context,
                        "person_type":       "trainer",
                        "person_name":       runner.trainer,
                        "total_starts":      tstats.get("totalStarts"),
                        "total_wins":        tstats.get("totalWins"),
                        "overall_win_rate":  tstats.get("overallWinRate"),
                        "overall_place_rate":tstats.get("overallPlaceRate"),
                        "recent_win_rate":   (tstats.get("recentStats") or {}).get("winRate"),
                        "track_win_rate":    this_track.get("winRate"),
                        "track_starts":      this_track.get("totalStarts"),
                        "raw_response":      tstats,
                    })
            except Exception as _te:
                log.debug(f"trainer stats fetch failed {runner.trainer}: {_te}")

---
FIX 5 — database.py: Add upsert_runner_connection_stats function

def upsert_runner_connection_stats(data: dict) -> None:
    race_uid = data.get("race_uid") or ""
    runner_number = data.get("runner_number")
    person_type = data.get("person_type") or ""
    if not race_uid or runner_number is None or not person_type:
        return
    payload = {
        "race_uid":          race_uid,
        "date":              data.get("date") or date.today().isoformat(),
        "track":             data.get("track") or "",
        "race_num":          int(data.get("race_num") or 0),
        "runner_name":       data.get("runner_name") or "",
        "runner_number":     int(runner_number),
        "person_type":       person_type,
        "person_name":       data.get("person_name") or "",
        "race_code":         data.get("race_code") or "gallops",
        "total_starts":      data.get("total_starts"),
        "total_wins":        data.get("total_wins"),
        "overall_win_rate":  data.get("overall_win_rate"),
        "overall_place_rate":data.get("overall_place_rate"),
        "recent_win_rate":   data.get("recent_win_rate"),
        "track_win_rate":    data.get("track_win_rate"),
        "track_starts":      data.get("track_starts"),
        "raw_response":      _as_json(data.get("raw_response")),
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
    }
    safe_query(
        lambda: get_db()
        .table(T("runner_connection_stats"))
        .upsert(payload, on_conflict="race_uid,runner_number,person_type")
        .execute()
    )

---
FIX 6 — app.py /api/live/race: Attach connection stats to runners

After the formfav enrichment merge, add connection stats:

        try:
            from database import get_runner_connection_stats_for_race
            conn_stats = get_runner_connection_stats_for_race(race_uid)
            # Group by runner_number
            conn_by_runner = {}
            for cs in conn_stats:
                num = cs.get("runner_number")
                if num is not None:
                    conn_by_runner.setdefault(int(num), []).append(cs)

            for r in runners:
                box = r.get("box_num") or r.get("number") or r.get("barrier")
                if box is not None:
                    stats_list = conn_by_runner.get(int(box), [])
                    jockey_stat = next((s for s in stats_list if s["person_type"] == "jockey"), None)
                    trainer_stat = next((s for s in stats_list if s["person_type"] == "trainer"), None)
                    if jockey_stat:
                        r["jockey_win_rate"]        = jockey_stat.get("overall_win_rate")
                        r["jockey_track_win_rate"]  = jockey_stat.get("track_win_rate")
                        r["jockey_track_starts"]    = jockey_stat.get("track_starts")
                    if trainer_stat:
                        r["trainer_win_rate"]       = trainer_stat.get("overall_win_rate")
                        r["trainer_track_win_rate"] = trainer_stat.get("track_win_rate")
                        r["trainer_track_starts"]   = trainer_stat.get("track_starts")
        except Exception:
            pass

Also add to database.py:

def get_runner_connection_stats_for_race(race_uid: str) -> list[dict]:
    return safe_query(
        lambda: get_db()
        .table(T("runner_connection_stats"))
        .select("*")
        .eq("race_uid", race_uid)
        .execute()
        .data,
        []
    ) or []

---
RESULT after these fixes:

Every piece of data is anchored:
- Race data → race_uid (date + code + track + race_num)
- Runner data → (race_uid, box_num) in today_runners
- FormFav enrichment → (race_uid, number) joined by number then name fallback
- Jockey/trainer stats → (race_uid, runner_number, person_type)
- Market snapshots → (race_uid, runner_name, date)
- Results → (race_uid, date, track, race_num, code)

Nothing floats. Every row knows exactly which horse in which race it belongs to.
