# DEMONPULSE — FULL SYSTEM FIX PROMPT FOR COPILOT

Generated: 2026-04-09
Based on: Full codebase audit + live screenshot analysis
Source: Demonpulse–main_71.zip

Apply ALL fixes in BOTH PART 1 and PART 2. Do not skip any section.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 1 — CORE SYSTEM BUGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

═══════════════════════════════════════════════════════════════
BUG 1 — RUNNER CARD DROPDOWN: ALL STATS SHOW “—”
═══════════════════════════════════════════════════════════════
FILE: app.py — inside /api/live/race/<race_uid>, in the enriched_runners loop

PROBLEM: FormFav fields are stored as snake_case (win_prob, form_string,
pace_style) but live.js reads camelCase. The merge block sets ff_win_prob
but never maps it to winProb.

FIX — After the ff merge block, add these aliases for every merged runner:
merged[“winProb”]     = merged.get(“ff_win_prob”) or merged.get(“win_prob”)
merged[“placeProb”]   = merged.get(“ff_place_prob”) or merged.get(“place_prob”)
merged[“formString”]  = merged.get(“form_string”) or merged.get(“form”) or “”
merged[“modelRank”]   = merged.get(“ff_model_rank”) or merged.get(“model_rank”)
merged[“earlySpeed”]  = merged.get(“early_speed”) or “”
merged[“paceStyle”]   = merged.get(“pace_style”) or merged.get(“speed_map”) or “”
merged[“bestTime”]    = merged.get(“best_time”) or “”
merged[“career”]      = merged.get(“career”) or merged.get(“stats_career”) or “”
merged[“winPct”]      = merged.get(“win_pct”) or “”
merged[“placePct”]    = merged.get(“place_pct”) or “”

FILE: static/js/live.js — in buildRunnerCards() and all runner stat blocks

FIX — Update all stat reads to use both formats:
const career    = r.career    || r.stats_career   || “—”;
const winPct    = r.winPct    || r.win_pct         || (r.ff_win_prob != null ? r.ff_win_prob + “%” : “—”);
const placePct  = r.placePct  || r.place_pct       || “—”;
const bestTime  = r.bestTime  || r.best_time       || “—”;
const earlySpd  = r.earlySpeed|| r.early_speed     || “—”;
const paceStyle = r.paceStyle || r.pace_style      || r.speed_map || “—”;
const aiWinProb = r.ff_win_prob != null ? r.ff_win_prob + “%” : (r.winProb != null ? r.winProb + “%” : “—”);

═══════════════════════════════════════════════════════════════
BUG 2 — HOME PAGE: FINISHED RACES STUCK AS “AWAITING” / NO RESULT SHOWN
═══════════════════════════════════════════════════════════════
FILE: static/js/home.js

PROBLEM A: statusLabel() and statusClass() only check seconds, not race.status.
Races with status “final”/“paying”/“result_posted” have negative seconds so
they show “AWAITING” instead of “RESULTED”.

FIX — Replace BOTH functions entirely:

function statusLabel(secs, status) {
const st = (status || “”).toLowerCase();
if ([“final”,“paying”,“result_posted”].includes(st)) return “RESULTED”;
if (st === “abandoned”) return “ABANDONED”;
if (secs == null)  return “UPCOMING”;
if (secs < -1800)  return “AWAITING”;
if (secs < 0)      return “PENDING”;
if (secs < 120)    return “IMMINENT”;
if (secs < 600)    return “NEAR”;
return “UPCOMING”;
}

function statusClass(secs, status) {
const st = (status || “”).toLowerCase();
if ([“final”,“paying”,“result_posted”].includes(st)) return “status-resulted”;
if (st === “abandoned”) return “status-abandoned”;
if (secs == null)  return “status-upcoming”;
if (secs < -1800)  return “status-awaiting”;
if (secs < 0)      return “status-pending”;
if (secs < 120)    return “status-imminent”;
if (secs < 600)    return “status-near”;
return “status-upcoming”;
}

PROBLEM B: All call sites pass only one arg. Fix every call:
Change: statusClass(secs)  → statusClass(secs, race.status)
Change: statusLabel(secs)  → statusLabel(secs, race.status)

PROBLEM C: renderNtjStrip() filters out races where secs < -30, hiding
resulted races. Change the filter to:
.filter(item => {
const st = (item.status || “”).toLowerCase();
if ([“final”,“paying”,“result_posted”].includes(st)) return true;
return (getSecondsToJump(item) ?? -1) >= -30;
})

FILE: static/css/pages.css or components.css — ADD missing CSS classes:
.status-resulted  { color: #3dd68c; background: rgba(61,214,140,0.15); border-radius: 4px; padding: 2px 6px; font-size: 0.7rem; font-weight: 700; letter-spacing: .06em; }
.status-abandoned { color: #888; background: rgba(255,255,255,0.06); border-radius: 4px; padding: 2px 6px; font-size: 0.7rem; font-weight: 700; letter-spacing: .06em; }

═══════════════════════════════════════════════════════════════
BUG 3 — AI SIGNALS NOT SHOWING (Signal, Decision, Pace, Shape, Confidence, EV all “—”)
═══════════════════════════════════════════════════════════════
FILE: app.py — in the analysis dict build (around line 491)

PROBLEM A: analysis[“pace_type”] reads formfav.get(“paceScenario”) but the DB
stores it as “pace_scenario” (snake_case). It returns None always.

FIX:
“pace_type”: formfav.get(“pace_scenario”) or formfav.get(“paceScenario”) or stored_pred.get(“pace_type”),
“race_shape”: formfav.get(“race_shape”) or formfav.get(“beneficiary”) or formfav.get(“weather”) or stored_pred.get(“race_shape”),
“weather”: race_out.get(“weather”) or formfav.get(“weather”),

PROBLEM B: When stored_pred is empty (no prediction run yet), ALL signal fields
return “—”. The UI never auto-triggers a prediction.

FIX in static/js/live.js — in renderAnalysis(), after all setText() calls, add:
// Auto-trigger prediction if no signal data exists and race is not finished
const _status = (liveRace?.status || “”).toLowerCase();
const _hasSignal = (liveSignal?.signal && liveSignal.signal !== “—”) ||
(liveAnalysis?.signal && liveAnalysis.signal !== “—”);
if (!_hasSignal && ![“final”,“paying”,“result_posted”,“abandoned”].includes(_status)) {
const _uid = getRaceUid();
if (_uid && (_predAttempts[_uid] || 0) < 2) {
_predAttempts[_uid] = (_predAttempts[_uid] || 0) + 1;
const backoff = _predAttempts[_uid] * 4000;
setTimeout(() => {
fetch(`/api/predictions/race/${encodeURIComponent(_uid)}`, { method: “POST” })
.then(r => r.json())
.then(d => { if (d.ok) setTimeout(loadLiveRace, 2500); })
.catch(() => {});
}, backoff);
}
}

PROBLEM C: stored_pred[“signal”] defaults to “—” (a string dash) when no
prediction. The check `if stored_pred` passes because it is a non-empty dict
with dash strings. So signal object is returned but contains “—” not null.

FIX in the final return jsonify() block in app.py:
“signal”: {
“signal”:     stored_pred.get(“signal”) if stored_pred.get(“signal”) not in (None, “—”) else None,
“confidence”: stored_pred.get(“confidence”),
“ev”:         stored_pred.get(“ev”),
} if stored_pred.get(“signal”) not in (None, “—”) else None,

═══════════════════════════════════════════════════════════════
BUG 4 — BOARD_BUILDER EXCLUDES RESULTED RACES FROM HOME PAGE
═══════════════════════════════════════════════════════════════
FILE: board_builder.py — in build_board() function

PROBLEM: Line `if not is_race_live(race): settled_count += 1; continue`
removes ALL races with status “final”/“paying”/“result_posted” from the board.
Users can’t see today’s results on the home page at all.

FIX — Replace the settled check with a time-gated version that keeps results
visible for 2 hours after jump:

if not is_race_live(race):
# Keep resulted races visible on board for 2 hours post-jump for results display
jump_time_raw = race.get(“jump_time”)
ntj_check = compute_ntj(jump_time_raw, race.get(“date”))
secs = ntj_check.get(“seconds_to_jump”)
status = (race.get(“status”) or “”).lower()
is_recent_result = (
status in {“final”, “paying”, “result_posted”} and
secs is not None and secs > -7200  # within 2 hours of jump
)
if not is_recent_result:
settled_count += 1
continue
# Fall through — recent resulted race stays on board

Also confirm “status” field is included in every board item:
In _board_item(), ensure this line exists:
“status”: race.get(“status”) or “”,

FILE: database.py — get_active_races()

PROBLEM: If get_active_races() filters by status IN (LIVE_STATUSES), resulted
races are excluded from the DB query entirely before board_builder even sees them.

FIX — Change the status filter to include SETTLED_STATUSES for same-day races:
from race_status import LIVE_STATUSES, SETTLED_STATUSES
all_today_statuses = list(LIVE_STATUSES | SETTLED_STATUSES)
query = query.in_(“status”, all_today_statuses)

# OR better — remove the status filter entirely and filter by date only,

# letting board_builder apply the logic.

═══════════════════════════════════════════════════════════════
BUG 5 — RESULT NOT LINKED TO RACE_UID IN results_log
═══════════════════════════════════════════════════════════════
FILE: database.py — upsert_result()

PROBLEM: The results_log table upsert uses on_conflict=“date,track,race_num,code”
but does NOT store race_uid in the row. get_result(race_uid) then parses
race_uid back into parts to do a lookup — this breaks for tracks with
underscores in the name (e.g. “port_adelaide” becomes mis-parsed).

FIX — Add race_uid to the payload in upsert_result():
payload[“race_uid”] = result.get(“race_uid”) or “”

Then update get_result() to try a direct race_uid lookup first:
def get_result(race_uid: str) -> dict[str, Any] | None:
# Try direct race_uid lookup first (fast path)
direct = safe_query(
lambda: get_db().table(T(“results_log”))
.select(”*”).eq(“race_uid”, race_uid).limit(1).execute().data
)
if direct:
return direct[0]
# Fallback: parse race_uid into components (existing logic below)
…existing parse logic…

═══════════════════════════════════════════════════════════════
BUG 6 — SCHEDULER: RESULT POLLING INTERVAL TOO SLOW FOR LIVE USE
═══════════════════════════════════════════════════════════════
FILE: scheduler.py

PROBLEM: RESULT_CHECK_INTERVAL = 300 (5 minutes). Greyhound races run
every 18 minutes. Results arrive ~2 min after jump. So users wait up to
7 minutes to see a result.

FIX — Add a faster result check for recently-jumped races:

FAST_RESULT_INTERVAL = 90  # 90 seconds for just-jumped races

In the scheduler loop, add alongside the existing result check:
if now - last_fast_result_check >= FAST_RESULT_INTERVAL:
_run_fast_result_check()
last_fast_result_check = now

def _run_fast_result_check():
“”“Quick check for races that jumped in the last 15 minutes.”””
try:
from database import get_recently_jumped_races
from data_engine import check_results
races = get_recently_jumped_races(minutes_ago=15)
if races:
check_results()
except Exception as e:
log.warning(f”fast_result_check failed: {e}”)

Add to database.py:
def get_recently_jumped_races(minutes_ago: int = 15) -> list[dict]:
“”“Return races that jumped within the last N minutes and have no result yet.”””
return safe_query(
lambda: get_db().table(T(“today_races”))
.select(“race_uid, track, race_num, jump_time, status”)
.in_(“status”, [“jumped_estimated”, “awaiting_result”, “pending”, “open”])
.execute().data,
[]
) or []

═══════════════════════════════════════════════════════════════
BUG 7 — LIVE.JS: RESULT PANEL NEVER LOADS FOR “AWAITING” RACES
═══════════════════════════════════════════════════════════════
FILE: static/js/live.js — in loadLiveRace()

PROBLEM: The result panel only loads if status is in
[“final”,“paying”,“result_posted”,“abandoned”]. Races stuck in
“awaiting_result” or “jumped_estimated” never show the result even if
it exists in results_log.

FIX — Update the status check:
const status = (liveRace?.status || “”).toLowerCase();
if ([“final”,“paying”,“result_posted”,“abandoned”].includes(status)) {
loadAndRenderResult(raceUid);
} else if ([“jumped_estimated”,“awaiting_result”].includes(status) ||
(getSecondsNow(liveRace) !== null && getSecondsNow(liveRace) < -120)) {
// Race has jumped — try to load result, fall back to form guide if unavailable
try {
await loadAndRenderResult(raceUid);
} catch (_) {
if (liveRunners.length) buildRunnerCards(liveRunners, liveAnalysis);
}
} else if (liveRunners.length) {
buildRunnerCards(liveRunners, liveAnalysis);
}

Also fix loadAndRenderResult() to show a message when no winner yet:
if (data.ok && data.winner) {
// …existing result HTML…
} else if (data.ok) {
container.innerHTML = `<div style="padding:24px;text-align:center;"> <div style="color:var(--amber);font-size:0.85rem;letter-spacing:.06em;">AWAITING OFFICIAL RESULT</div> <div style="color:var(--text-dim);font-size:0.75rem;margin-top:8px;">Results post within 2–3 minutes of jump</div> </div>`;
}

═══════════════════════════════════════════════════════════════
BUG 8 — HOME PAGE: COUNTDOWN TIMERS LOOK FROZEN BETWEEN REFRESHES
═══════════════════════════════════════════════════════════════
FILE: static/js/home.js

PROBLEM: refreshTimer only calls the full API fetch every 30s. Countdown
numbers only update when the board re-renders. Looks frozen.

FIX — Add a local countdown ticker:
function startLocalCountdownTick() {
if (window._localTick) clearInterval(window._localTick);
window._localTick = setInterval(() => {
document.querySelectorAll(”.race-countdown[data-jump-iso]”).forEach(el => {
const iso = el.dataset.jumpIso;
if (!iso) return;
const secs = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
el.textContent = formatCountdown(secs);
el.className = “race-countdown “ + countdownClass(secs);
});
}, 1000);
}

In renderMeetingCards(), add data-jump-iso to the countdown element:
<span class="race-countdown ${cdCls}" data-jump-iso="${race.jump_dt_iso || ''}">${formatCountdown(secs)}</span>

Call startLocalCountdownTick() after first render.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 2 — DEEPER SYSTEM BUGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

═══════════════════════════════════════════════════════════════
BUG 9 — SIGNALS NEVER SAVED TO signals TABLE (CRITICAL)
═══════════════════════════════════════════════════════════════
FILE: ai/predictor.py

PROBLEM: predict_race() calls generate_signal() and stores the result into
prediction_snapshots — but it NEVER calls save_signal() from signals.py.
The signals table stays permanently empty. get_signal(race_uid) always
returns None so there is no fast-path signal lookup.

FIX — After result[“signal”] = sig.get(“signal”, “—”), add:
try:
from signals import save_signal
save_signal(race_uid, sig)
except Exception as _se:
log.warning(f”predictor: save_signal failed: {_se}”)

FIX 2 — In app.py /api/live/race/<race_uid>, BEFORE trying learning_store,
also try get_signal() as a faster first path:
from signals import get_signal
quick_sig = get_signal(race_uid)
if quick_sig:
stored_pred = {
“signal”:     quick_sig.get(“signal”),
“decision”:   quick_sig.get(“signal”),
“confidence”: quick_sig.get(“confidence”),
“ev”:         quick_sig.get(“ev”),
“selection”:  quick_sig.get(“top_runner”),
“runner_prob_map”: {},
}

═══════════════════════════════════════════════════════════════
BUG 10 — EARLY_SPEED NEVER POPULATED FROM FormFav (ALWAYS BLANK)
═══════════════════════════════════════════════════════════════
FILE: connectors/formfav_connector.py — RunnerRecord population

PROBLEM: The early_speed field exists in RunnerRecord but is NEVER set
when building runners from the API response. FormFav returns speedMap as
a dict. Early speed position must be extracted from it.

FIX — In the RunnerRecord() constructor call inside fetch_runners(), add:
early_speed=_extract_early_speed(runner.get(“speedMap”) or {}),

Add the helper function at module level:
def _extract_early_speed(speed_map: dict) -> str | None:
if not speed_map or not isinstance(speed_map, dict):
return None
pos = (speed_map.get(“earlyPosition”) or
speed_map.get(“early”) or
speed_map.get(“position”) or
speed_map.get(“earlySpeed”))
return str(pos) if pos is not None else None

FILE: database.py — upsert_formfav_runner_enrichment (around line 540-560)

FIX — In the enrichment payload dict, add:
“early_speed”: data.get(“early_speed”),
“pace_style”:  data.get(“speed_map”, {}).get(“style”) if isinstance(data.get(“speed_map”), dict) else None,

═══════════════════════════════════════════════════════════════
BUG 11 — CAREER STRING FORMAT MISMATCH (Win%/Place% Always “—”)
═══════════════════════════════════════════════════════════════
FILE: connectors/formfav_connector.py

PROBLEM: career is set as str(overall) where overall is a dict like
{“starts”: 24, “wins”: 8, “places”: 6, “shows”: 4}.
This becomes “{‘starts’: 24, ‘wins’: 8…}” which is unparseable by
live.js career regex: /^(\d+):\s*(\d+)-(\d+)-(\d+)/
So Win% and Place% are always calculated as 0 or shown as “—”.

FIX — Add a helper and use it instead of str(overall):

def _format_career(overall: dict | None) -> str | None:
if not overall or not isinstance(overall, dict):
return None
starts = overall.get(“starts”) or overall.get(“totalStarts”) or 0
wins   = overall.get(“wins”)   or overall.get(“totalWins”)   or 0
places = overall.get(“places”) or overall.get(“totalPlaces”) or 0
shows  = overall.get(“shows”)  or overall.get(“thirds”)      or 0
if not starts:
return None
return f”{starts}: {wins}-{places}-{shows}”

Then in RunnerRecord() construction:
career=_format_career(overall),

═══════════════════════════════════════════════════════════════
BUG 12 — get_stored_prediction RETURNS WRONG STRUCTURE
═══════════════════════════════════════════════════════════════
FILE: app.py — prediction reading block (around line 465)

PROBLEM: app.py reads pred_result.get(“snapshot”) to get signal data.
But get_stored_prediction() returns {“ok”: True, “prediction”: {…}}
with snapshot data nested under “prediction” not “snapshot”.
So snap is always {} and stored_pred is always empty dashes.

FIX — Update the reading logic to handle both response shapes:
pred_result = get_stored_prediction(race_uid)
if pred_result.get(“ok”):
snap = (pred_result.get(“snapshot”) or
pred_result.get(“prediction”) or
pred_result.get(“data”) or {})
runner_outputs = (pred_result.get(“runner_outputs”) or
snap.get(“runner_outputs”) or
pred_result.get(“runners”) or [])

═══════════════════════════════════════════════════════════════
BUG 13 — RACES TAB LIST: JUMPED RACES SHOW WRONG STATUS BADGE
═══════════════════════════════════════════════════════════════
FILE: static/js/live.js — renderMeetingRaceList() / meeting race list renderer

PROBLEM: The meeting race list (Races tab showing R1, R2, R3… with
AWAITING/PENDING badges as seen in screenshots) calls getStatusLabel()
and getStatusBadgeClass() with only secs — not race.status.
Resulted races show “PENDING” or “AWAITING” instead of “RESULTED”.

FIX — Find every call to getStatusLabel() and getStatusBadgeClass() in
the meeting list section and pass race.status as second argument:
const badgeCls = getStatusBadgeClass(secs, race.status);
const label    = getStatusLabel(secs, race.status);

Also update getStatusLabel() and getStatusBadgeClass() to accept and use
the status parameter, mirroring the exact same fix as home.js Bug 2:

function getStatusLabel(secs, status) {
const st = (status || “”).toLowerCase();
if ([“final”,“paying”,“result_posted”].includes(st)) return “RESULTED”;
if (st === “abandoned”) return “ABANDONED”;
if ([“jumped_estimated”,“awaiting_result”].includes(st) || (secs != null && secs < 0)) return “PENDING”;
if (secs != null && secs < 120) return “IMMINENT”;
return “”;
}

function getStatusBadgeClass(secs, status) {
const st = (status || “”).toLowerCase();
if ([“final”,“paying”,“result_posted”].includes(st)) return “badge-resulted”;
if (st === “abandoned”) return “badge-abandoned”;
if ([“jumped_estimated”,“awaiting_result”].includes(st) || (secs != null && secs < 0)) return “badge-pending”;
if (secs != null && secs < 120) return “badge-imminent”;
if (secs != null && secs < 600) return “badge-near”;
return “badge-upcoming”;
}

═══════════════════════════════════════════════════════════════
BUG 14 — PREDICTION AUTO-TRIGGER FIRES ENDLESSLY ON PAGE LOAD
═══════════════════════════════════════════════════════════════
FILE: static/js/live.js

PROBLEM: If prediction auto-trigger uses a window-level flag, it resets
on every page load and re-navigates, causing infinite POST loops.

FIX — Use a per-race-uid attempt counter declared at module scope:
const _predAttempts = {};  // declare at top of IIFE alongside other state vars

In renderAnalysis(), use:
if (!_hasSignal && ![“final”,“paying”,“result_posted”,“abandoned”].includes(_status)) {
const _uid = getRaceUid();
if (_uid && (_predAttempts[_uid] || 0) < 2) {
_predAttempts[_uid] = (_predAttempts[_uid] || 0) + 1;
const backoff = _predAttempts[_uid] * 4000;
setTimeout(() => {
fetch(`/api/predictions/race/${encodeURIComponent(_uid)}`, { method: “POST” })
.then(r => r.json())
.then(d => { if (d.ok) setTimeout(loadLiveRace, 2500); })
.catch(() => {});
}, backoff);
}
}

═══════════════════════════════════════════════════════════════
BUG 15 — SCHEDULER: FormFav SYNC NOT TRIGGERED FOR NEAR-JUMP RACES
═══════════════════════════════════════════════════════════════
FILE: scheduler.py — _run_near_jump_refresh()

PROBLEM: FormFav sync runs on its own slow timer. Near-jump races
(< 10 min to jump) need a FormFav sync to get win probabilities.
If the sync timer hasn’t fired, runner cards have no AI data at all.

FIX — In _run_near_jump_refresh(), after near_jump_refresh() call,
trigger FormFav sync when near-jump races are detected:
if result.get(“near_jump_races”, 0) > 0:
try:
from data_engine import formfav_sync
formfav_sync()
log.info(“near_jump: triggered formfav_sync for near-jump races”)
except Exception as _e:
log.warning(f”near_jump formfav_sync failed: {_e}”)

═══════════════════════════════════════════════════════════════
BUG 16 — “RUN SIM” BUTTON VISIBLE BUT NON-FUNCTIONAL
═══════════════════════════════════════════════════════════════
FILE: app.py — /api/live/watch-sim/<race_uid>

PROBLEM: The endpoint has a “TODO: wire to simulation engine” comment
and returns a placeholder. The Run Sim button in the UI does nothing.

FIX — Wire it to the actual simulation engine:
@app.route(”/api/live/watch-sim/<race_uid>”, methods=[“POST”])
def api_live_watch_sim(race_uid: str):
try:
from simulation.core_simulation_engine import run_simulation
from database import get_race, get_runners_for_race
race = get_race(race_uid)
runners = get_runners_for_race(race_uid)
if not race or not runners:
return jsonify({“ok”: False, “error”: “Race or runners not found”}), 404
result = run_simulation(race, runners)
return jsonify({“ok”: True, “simulation”: result})
except Exception as e:
log.error(f”/api/live/watch-sim/{race_uid} failed: {e}”)
return jsonify({“ok”: False, “error”: “Simulation failed”}), 500

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL VERIFICATION CHECKLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After ALL fixes (Parts 1 and 2), run:
python smoke_test.py

Verify ALL of the following:

1. GET /api/home/board
   → Returns items including resulted races with status=“final”
   → Each item has “status” field populated
   → Resulted races within 2hrs of jump are included
1. Home page race cards
   → Races with status=final show “RESULTED” badge in green
   → Countdowns tick every second without waiting for API refresh
   → No race stuck showing “AWAITING” when it has a result
1. Races tab (R1, R2, R3 list)
   → Jumped races show “Jumped” countdown text
   → Resulted races show “RESULTED” badge
   → Status badges match actual race status
1. GET /api/live/race/<uid> on a race with FormFav data
   → runners[0].earlySpeed must not be null/empty
   → runners[0].career must match format “24: 8-6-4” not a raw dict string
   → runners[0].ff_win_prob must be a float
   → analysis.signal must not be “—”
   → analysis.pace_type must be populated (not null)
   → analysis.race_shape must be populated (not null)
1. Runner card expanded dropdown
   → Career shows e.g. “24: 8-6-4”
   → Win % shows e.g. “33.3%”
   → Place % shows e.g. “58.3%”
   → Best Time shows a value if available
   → Early Speed shows a value if available
   → AI Win % shows a percentage if FormFav synced
   → No stat row shows “—” when data exists in DB
1. AI Signal panel (Decision, Pace, Race Shape, Confidence, EV)
   → All fields populated after prediction runs
   → Signal shows SNIPER / VALUE / GEM / WATCH / RISK / NO_BET
1. POST /api/predictions/race/<uid>
   → Returns ok:true
   → Subsequent GET /api/live/race/<uid> returns signal != “—”
   → Supabase signals table has a new row for this race_uid
1. Result display
   → Race with status=final shows winner, 2nd, 3rd, time, margin
   → Race with status=awaiting_result shows “AWAITING OFFICIAL RESULT”
   → No blank white space where result panel should be
1. Run Sim button
   → Clicking Run Sim returns simulation data
   → No console errors / 500 responses
1. python smoke_test.py
   → Zero import errors
   → Zero missing module errors

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILES MODIFIED SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Backend:
app.py                              — Bugs 1, 3, 9, 12
board_builder.py                    — Bug 4
database.py                         — Bugs 4, 5, 6
scheduler.py                        — Bugs 6, 15
signals.py                          — (no changes, already correct)
race_status.py                      — (no changes needed)
data_engine.py                      — Bug 6
ai/predictor.py                     — Bug 9
connectors/formfav_connector.py     — Bugs 10, 11

Frontend:
static/js/home.js                   — Bugs 2, 8
static/js/live.js                   — Bugs 3, 7, 13, 14
static/css/pages.css (or components.css) — Bug 2

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
END OF PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
