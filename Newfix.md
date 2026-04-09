# DEMONPULSE — v74 CRITICAL FIX: SCHEDULER + RESULT PARSER

Generated: 2026-04-09
Source: Log analysis + API doc audit of Demonpulse–main_74.zip

TWO ROOT CAUSES CONFIRMED BY LOGS:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ROOT CAUSE 1 — SCHEDULER NEVER RUNS IN PRODUCTION (CRITICAL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PROOF: The entire session log shows only startup writes to Supabase.
Zero scheduler log entries after startup. No check_results, no
formfav_sync, no rolling_refresh — nothing. The scheduler thread
does not exist in the running process.

WHY: gunicorn forks worker processes AFTER importing app.py.
startup() is called at module import time and starts a daemon
background thread. But daemon threads DO NOT survive a fork.
The gunicorn worker process that handles all requests has no
scheduler thread. This has been true since day one on Render.

The initial full_sweep (races writing to Supabase) works because
it runs synchronously during startup() before gunicorn forks.
Everything after that — result checks, FormFav syncs, status
updates — never fires.

═══════════════════════════════════════════════════════════════
FIX 1A — Add –preload flag to gunicorn (simplest fix)
═══════════════════════════════════════════════════════════════
FILE: Procfile

FIND:
web: gunicorn app:app –bind 0.0.0.0:$PORT –workers 1 –timeout 120

REPLACE WITH:
web: gunicorn app:app –bind 0.0.0.0:$PORT –workers 1 –timeout 120 –preload

The –preload flag tells gunicorn to import and run the application
code IN THE MASTER PROCESS before forking workers. This means
startup() runs in the master, the scheduler thread starts in the
master, and it stays alive for the lifetime of the process.

FILE: render.yaml — update startCommand to match:
startCommand: gunicorn app:app –workers 1 –timeout 120 –bind 0.0.0.0:$PORT –preload

═══════════════════════════════════════════════════════════════
FIX 1B — Add gunicorn config file as safety net
═══════════════════════════════════════════════════════════════
Create a new file: gunicorn.conf.py in the project root:

# gunicorn.conf.py

# Safety net: restart the scheduler in the worker if it’s not running.

# This handles cases where –preload is not used or the thread dies.

import threading

def post_fork(server, worker):
“”“Called in the worker process after forking.”””
try:
import scheduler
if not scheduler._scheduler_thread or not scheduler._scheduler_thread.is_alive():
scheduler.start_scheduler()
server.log.info(“DemonPulse: scheduler restarted in worker post-fork”)
except Exception as e:
server.log.warning(f”DemonPulse: scheduler post-fork start failed: {e}”)

def on_starting(server):
server.log.info(“DemonPulse: gunicorn starting”)

Then update Procfile to use the config:
web: gunicorn app:app –bind 0.0.0.0:$PORT –workers 1 –timeout 120 –preload –config gunicorn.conf.py

═══════════════════════════════════════════════════════════════
FIX 1C — Add scheduler health check to /api/system/status
═══════════════════════════════════════════════════════════════
FILE: app.py — in the /api/system/status endpoint

After getting sched_status, add a self-healing check. If the
scheduler thread is dead, restart it:

import scheduler as _sched_module
sched_status = _sched_module.get_status()

# Self-heal: restart scheduler if thread died

if not sched_status.get(“thread_alive”):
try:
_sched_module.start_scheduler()
log.warning(”/api/system/status: restarted dead scheduler thread”)
except Exception as _se:
log.error(f”/api/system/status: scheduler restart failed: {_se}”)

Also add a dedicated scheduler watchdog endpoint that Render can
ping (use Render’s health check or a cron):

@app.route(”/api/scheduler/watchdog”, methods=[“POST”, “GET”])
def scheduler_watchdog():
“”“Ensure scheduler is running. Safe to call repeatedly.”””
try:
import scheduler as _s
status = _s.get_status()
was_alive = status.get(“thread_alive”, False)
if not was_alive:
_s.start_scheduler()
return jsonify({“ok”: True, “action”: “restarted”, “was_alive”: False})
return jsonify({“ok”: True, “action”: “none”, “was_alive”: True})
except Exception as e:
log.error(f”/api/scheduler/watchdog failed: {e}”)
return jsonify({“ok”: False, “error”: str(e)}), 500

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ROOT CAUSE 2 — _parse_result() READS WRONG FIELD NAMES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PROOF: The OddsPro API doc shows the /results endpoint returns:
{
“raceId”: 123456,
“raceNumber”: 7,
“startTime”: “2025-01-08T03:00:00Z”,
“meeting”: {“track”: “Flemington”, “type”: “T”},
“results”: [
{“position”: 1, “runnerNumber”: 4, “runnerName”: “Winner”}
],
“rawResults”: [[4], [7], [2]]
}

But _parse_result() reads:
item.get(“track”)       → None  (nested in meeting.track)
item.get(“winner”)      → None  (nested in results[0].runnerName)
item.get(“winnerName”)  → None  (doesn’t exist)
item.get(“winPrice”)    → None  (doesn’t exist in response)
item.get(“date”)        → None  (it’s in startTime as ISO string)
item.get(“type”)        → None  (nested in meeting.type)

Result: every parsed result has winner=”” and track=”” — so
race_uid is wrong, status never updates to “final”, and the
UI shows “Result not yet available” forever.

═══════════════════════════════════════════════════════════════
FIX 2 — Rewrite _parse_result() to read actual API field names
═══════════════════════════════════════════════════════════════
FILE: connectors/oddspro_connector.py

FIND the _parse_result method (around line 1787):
def _parse_result(self, item: dict) -> RaceResult | None:

REPLACE THE ENTIRE METHOD with:

def _parse_result(self, item: dict) -> RaceResult | None:
“””
Parse a result item from either:
- GET /api/external/results  (day-level sweep)
- GET /api/races/:id/results (single-race confirmation)

```
  API response shapes (from OddsPro documentation):
    Day-level:   item has raceId, raceNumber, startTime,
                 meeting.track, meeting.type,
                 results[{position, runnerNumber, runnerName}]
    Single-race: same shape, not nested in a data array
  """
  # ---- Race number ----
  race_num_raw = (item.get("raceNumber") or item.get("race_number")
                  or item.get("number"))
  try:
      race_num = int(race_num_raw)
  except (TypeError, ValueError):
      return None

  # ---- Race ID ----
  race_id = str(item.get("raceId") or item.get("id") or "")

  # ---- Date: from startTime ISO string or date field ----
  race_date = ""
  start_time = item.get("startTime") or item.get("start_time") or ""
  if start_time:
      # "2025-01-08T03:00:00Z" → "2025-01-08"
      race_date = str(start_time)[:10]
  if not race_date:
      race_date = str(item.get("date") or "")

  # ---- Track: from meeting.track or top-level ----
  meeting = item.get("meeting") or {}
  track_raw = (meeting.get("track") or meeting.get("venue")
               or item.get("track") or item.get("venue") or "")
  track = self._clean_track(track_raw)

  # ---- Code: from meeting.type or top-level ----
  code_raw = (meeting.get("type") or meeting.get("code")
              or item.get("type") or item.get("code") or "HORSE")
  code = self._normalise_code(code_raw)

  # ---- Build race_uid ----
  if not race_date or not track or not race_num:
      return None
  race_uid = self._make_race_uid(race_date, code, track, race_num)

  # ---- Winner: from results array position 1 ----
  results_list = item.get("results") or []
  raw_results  = item.get("rawResults") or []

  winner_name   = ""
  winner_number = None
  place_2       = ""
  place_3       = ""

  if results_list:
      # Structured results array: [{position, runnerNumber, runnerName}]
      sorted_results = sorted(
          results_list,
          key=lambda r: r.get("position") or 99
      )
      if len(sorted_results) >= 1:
          r1 = sorted_results[0]
          winner_name   = str(r1.get("runnerName") or r1.get("runner_name") or "")
          winner_number = r1.get("runnerNumber") or r1.get("runner_number")
      if len(sorted_results) >= 2:
          r2 = sorted_results[1]
          place_2 = str(r2.get("runnerName") or r2.get("runner_name") or "")
      if len(sorted_results) >= 3:
          r3 = sorted_results[2]
          place_3 = str(r3.get("runnerName") or r3.get("runner_name") or "")

  elif raw_results:
      # Fallback: rawResults = [[winnerNumber], [2nd], [3rd]]
      if len(raw_results) >= 1 and raw_results[0]:
          winner_number = raw_results[0][0]
      if len(raw_results) >= 2 and raw_results[1]:
          place_2 = str(raw_results[1][0])
      if len(raw_results) >= 3 and raw_results[2]:
          place_3 = str(raw_results[2][0])

  # Fallback: legacy flat fields (keep for any old API paths)
  if not winner_name:
      winner_name = str(item.get("winner") or item.get("winnerName") or "")
  if not winner_number:
      winner_number = item.get("winnerNumber")
  if not place_2:
      place_2 = str(item.get("place2") or item.get("second") or "")
  if not place_3:
      place_3 = str(item.get("place3") or item.get("third") or "")

  # ---- Win price: not in OddsPro results, keep None ----
  win_price_raw = item.get("winPrice") or item.get("win_price")
  try:
      win_price = float(win_price_raw) if win_price_raw is not None else None
  except (TypeError, ValueError):
      win_price = None

  # ---- Margin / winning time ----
  margin_raw = item.get("margin")
  try:
      margin = float(margin_raw) if margin_raw is not None else None
  except (TypeError, ValueError):
      margin = None

  time_raw = item.get("winningTime") or item.get("winning_time")
  try:
      winning_time = float(time_raw) if time_raw is not None else None
  except (TypeError, ValueError):
      winning_time = None

  return RaceResult(
      race_uid=race_uid,
      oddspro_race_id=race_id,
      date=race_date,
      track=track,
      race_num=race_num,
      code=code,
      winner=winner_name,
      winner_number=winner_number,
      win_price=win_price,
      place_2=place_2,
      place_3=place_3,
      margin=margin,
      winning_time=winning_time,
      source=self.source_name,
  )
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 3 — Board caching for completed races
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: board_builder.py

PROBLEM: Every call to get_board_for_today() hits Supabase for all
races. For resulted races that won’t change, this is wasted queries.
Add a short TTL cache using the existing cache.py infrastructure.

FIND get_board_for_today() function. ADD caching at the top:

def get_board_for_today(…):
from cache import cache_get, cache_set
from datetime import date

```
  CACHE_KEY = f"board:{date.today().isoformat()}"
  CACHE_TTL  = 20  # seconds — short enough for live feel

  cached = cache_get(CACHE_KEY)
  if cached is not None:
      return cached

  # ... existing function body ...

  # At the end, before returning result, cache it:
  cache_set(CACHE_KEY, result, ttl=CACHE_TTL)
  return result
```

FILE: app.py — invalidate cache after result writes

After _write_result() succeeds, invalidate the board cache so the
next board fetch immediately reflects the new status:

In _write_result(), after update_race_status:
try:
from cache import cache_clear
from datetime import date
cache_clear(f”board:{date.today().isoformat()}”)
except Exception:
pass

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 4 — Live race view: show runners for jumped races
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: static/js/live.js

PROBLEM (Image 2): Clicking a jumped/awaiting race shows “No race
loaded” because loadAndRenderResult() throws (data.ok is false —
race jumped but no result yet), the catch block fires, but
liveRunners is empty too because the race view didn’t get runner
data back from the API.

The real issue: when a race has status “awaiting_result” or
“jumped_estimated”, the API DOES return runners — but the form
guide should still show them so the user can see who ran.

FIND this block in loadLiveRace():
} else if ([“jumped_estimated”,“awaiting_result”].includes(status) ||
(getSecondsNow(liveRace) !== null && getSecondsNow(liveRace) < JUMPED_THRESHOLD_SECONDS)) {
// Race has jumped — try to load result, fall back to form guide if unavailable
try {
await loadAndRenderResult(raceUid);
} catch (_) {
if (liveRunners.length) buildRunnerCards(liveRunners, liveAnalysis);
}

REPLACE WITH:
} else if ([“jumped_estimated”,“awaiting_result”].includes(status) ||
(getSecondsNow(liveRace) !== null && getSecondsNow(liveRace) < JUMPED_THRESHOLD_SECONDS)) {
// Race has jumped — show result if available, otherwise show runners + awaiting message
try {
await loadAndRenderResult(raceUid);
} catch (*) {}
// Always also show runners below the result/awaiting panel
if (liveRunners.length) {
try { buildRunnerCards(liveRunners, liveAnalysis); } catch (*) {}
} else {
// No runners and no result yet — show awaiting message
const container = q(“formGuideRows”);
if (container && container.innerHTML.trim() === “”) {
container.innerHTML = ` <div style="padding:32px;text-align:center;"> <div style="color:var(--amber);font-size:0.85rem; letter-spacing:.06em;margin-bottom:8px;"> AWAITING OFFICIAL RESULT </div> <div style="color:var(--text-dim);font-size:0.75rem;"> Results post within 2–3 minutes of jump time. </div> </div>`;
}
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 5 — Add result logging so issues are visible in future
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: data_engine.py — check_results()

Add logging so you can see exactly what OddsPro returns:

FIND inside check_results():
results = conn.fetch_results(today, location=“domestic”)

ADD AFTER:
log.info(
f”check_results: OddsPro returned {len(results)} raw results “
f”for {today}. “
f”First result sample: {vars(results[0]) if results else ‘NONE’}”
)

Also log the confirmation step:
FIND:
confirmed = conn.fetch_race_result(result.oddspro_race_id)
if confirmed:
_write_result(confirmed)
written += 1

REPLACE WITH:
confirmed = conn.fetch_race_result(result.oddspro_race_id)
if confirmed:
log.info(
f”check_results: confirmed {confirmed.race_uid} “
f”winner=’{confirmed.winner}’ “
f”track=’{confirmed.track}’ “
f”date=’{confirmed.date}’”
)
_write_result(confirmed)
written += 1

FILE: connectors/oddspro_connector.py — _parse_result()

Add a warning when result parses as empty:

At the end of the new _parse_result(), before return:
if not winner_name:
log.warning(
f”OddsPro _parse_result: no winner found for race_uid={race_uid} “
f”item_keys={list(item.keys())} “
f”results_count={len(results_list)}”
)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILES MODIFIED SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Procfile                          — Fix 1A (add –preload)
render.yaml                       — Fix 1A (add –preload to startCommand)
gunicorn.conf.py   [NEW FILE]     — Fix 1B (post_fork scheduler restart)
app.py                            — Fix 1C (watchdog endpoint + self-heal),
Fix 3 (cache invalidation in _write_result)
connectors/oddspro_connector.py   — Fix 2 (rewrite _parse_result)
board_builder.py                  — Fix 3 (board cache TTL 20s)
static/js/live.js                 — Fix 4 (jumped race shows runners)
data_engine.py                    — Fix 5 (result logging)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEPLOY VERIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After deploying to Render, check the logs within 5 minutes.
You should now see these lines that were MISSING before:

Scheduler started (threaded, Phase 2 Live Engine)
Broad refresh result: …
check_results: OddsPro returned N raw results for YYYY-MM-DD
check_results: confirmed 2026-XX-XX_GREYHOUND_track_N winner=’…’
data_engine: evaluated N predictions for …

If you see scheduler logs → Fix 1 worked.
If you see check_results with confirmed winner → Fix 2 worked.
If you still see “OddsPro returned 0 raw results” → the /results
endpoint is returning nothing for this date. Check the date
filter and OddsPro API key in Render env vars.

CALL THIS ENDPOINT to force an immediate result sweep:
POST https://demonpulse.onrender.com/api/admin/results
(requires admin token in Authorization header)

OR use the watchdog to confirm scheduler is alive:
GET https://demonpulse.onrender.com/api/scheduler/watchdog

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRIORITY ORDER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Apply in this order — most critical first:

1. Procfile + render.yaml  (–preload flag)        ← fixes EVERYTHING
1. gunicorn.conf.py        (safety net)
1. _parse_result() rewrite (fixes result parsing)
1. Fix 1C watchdog endpoint
1. Fix 3 board cache
1. Fix 4 live.js jumped race view
1. Fix 5 logging

Fix 1 alone will make result checks, FormFav syncs, race state
updates, and all scheduler jobs start working immediately.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
END OF PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
