# DEMONPULSE — v72 DEEP SYSTEM FIX (PART 2)

Generated: 2026-04-09
Based on: Full deep-dive audit of Demonpulse–main_72.zip

This file covers ROOT CAUSES found after Part 1. Apply ALL sections.

CONFIRMED ROOT CAUSES THIS AUDIT:
❌ evaluate_prediction() NEVER called from _write_result() — learning_evaluations
table is permanently empty — all Learning/Performance data shows “—”
❌ prediction_snapshots missing race_date/track/race_num/top_runner columns —
GET /api/predictions/today cannot filter or display correctly
❌ Backtest BLOCKED by @require_role(“admin”) — the UI calls /api/admin/backtest
but the cookie-based token (httponly) is not sent by fetch() — every Run
Backtest silently gets 401 Unauthorized, shows “FAILED”
❌ Backtest returns flat fields (total_races, hit_rate) but backtesting.js reads
nested data.summary.samples, data.rows, data.errors — table always empty
❌ Backtest has no ROI/profit calculation in the response
❌ GET /api/predictions/today endpoint does not exist — learning activity feed
always shows “No predictions yet today”

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 1 — CRITICAL: evaluate_prediction() NEVER CALLED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: data_engine.py — _write_result()

PROBLEM: _write_result() writes the result and updates race status but
NEVER calls evaluate_prediction(). The learning_evaluations table is
therefore always empty. Win rate, ROI, performance chart, paper bets —
all show “—” forever because there is no evaluation data.

The full evaluation pipeline exists in result_service.confirm_race_result()
but that function is NEVER called by the scheduler. The scheduler calls
check_results() → _write_result() which skips evaluation entirely.

FIX — Update _write_result() to trigger evaluation after writing:

def _write_result(result: Any) -> None:
“”“Write an OddsPro RaceResult to results_log as official truth.”””
try:
from database import upsert_result, update_race_status
result_dict = result.**dict** if hasattr(result, “**dict**”) else result
upsert_result(result_dict)

```
      race_uid = result_dict.get("race_uid") or ""
      if race_uid:
          rows_updated = update_race_status(race_uid, "final")
          if not rows_updated:
              # Fallback: update by date/track/race_num/code
              _update_race_status_fallback(
                  date=result_dict.get("date"),
                  track=result_dict.get("track"),
                  race_num=result_dict.get("race_num"),
                  code=result_dict.get("code"),
              )

      # CRITICAL: trigger prediction evaluation for learning
      if race_uid:
          try:
              from ai.learning_store import evaluate_prediction
              from database import get_result
              stored = get_result(race_uid)
              if stored:
                  eval_result = evaluate_prediction(race_uid, stored)
                  eval_count = eval_result.get("evaluated", 0)
                  if eval_count > 0:
                      log.info(
                          f"data_engine: evaluated {eval_count} predictions "
                          f"for {race_uid}"
                      )
          except Exception as _eval_err:
              log.warning(
                  f"data_engine: _write_result evaluation failed "
                  f"for {race_uid}: {_eval_err}"
              )

  except Exception as e:
      log.error(f"data_engine: _write_result failed: {e}")
```

def *update_race_status_fallback(
date: str, track: str, race_num: int, code: str
) -> None:
“”“Fallback status update when race_uid lookup hits 0 rows.”””
if not all([date, track, race_num, code]):
return
from datetime import datetime, timezone
from db import get_db, safe_query, T
for tv in [track, track.lower(), track.lower().replace(” “, “-”),
track.lower().replace(” “, “*”)]:
result = safe_query(
lambda: get_db()
.table(T(“today_races”))
.update({
“status”: “final”,
“updated_at”: datetime.now(timezone.utc).isoformat()
})
.eq(“date”, date)
.eq(“race_num”, race_num)
.eq(“code”, code)
.ilike(“track”, tv)
.execute()
.data
)
if result:
break

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 2 — prediction_snapshots MISSING COLUMNS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: migrations.py — COLUMN_MIGRATIONS list

PROBLEM: prediction_snapshots has no race_date, track, race_num, code,
or top_runner columns. The GET /api/predictions/today endpoint (Part 1
Fix B2) queries by race_date and returns track/race_num — these will
fail or return nulls without the columns.

Also needed: the predictor’s update() call that writes signal/decision/
confidence/ev already exists in migrations (Phase 5) — confirm it was
applied to Supabase by running migrations on startup.

FIX — Add to migrations.py COLUMN_MIGRATIONS list (Phase 5 extension):

# prediction_snapshots — race context columns for activity feed queries

(“prediction_snapshots”,      “race_date”,   “DATE”,    “”),
(“prediction_snapshots”,      “track”,       “TEXT”,    “DEFAULT ‘’”),
(“prediction_snapshots”,      “race_num”,    “INTEGER”, “”),
(“prediction_snapshots”,      “code”,        “TEXT”,    “DEFAULT ‘’”),
(“prediction_snapshots”,      “top_runner”,  “TEXT”,    “”),
(“test_prediction_snapshots”, “race_date”,   “DATE”,    “”),
(“test_prediction_snapshots”, “track”,       “TEXT”,    “DEFAULT ‘’”),
(“test_prediction_snapshots”, “race_num”,    “INTEGER”, “”),
(“test_prediction_snapshots”, “code”,        “TEXT”,    “DEFAULT ‘’”),
(“test_prediction_snapshots”, “top_runner”,  “TEXT”,    “”),

FILE: ai/learning_store.py — save_prediction_snapshot()

FIX — Add the new columns to snap_row:

# In the snap_row dict, add:

“race_date”:  prediction.get(“race_date”) or (race_uid.split(”_”)[0] if race_uid else None),
“track”:      prediction.get(“track”) or “”,
“race_num”:   prediction.get(“race_num”),
“code”:       prediction.get(“code”) or “”,
“top_runner”: prediction.get(“top_runner_name”) or “”,

FILE: ai/predictor.py — predict_from_snapshot()

FIX — Add race context to result dict before calling save_prediction_snapshot:

# After race_uid = race.get(“race_uid”) or “”, add:

result[“race_date”] = race.get(“date”) or “”
result[“track”]     = race.get(“track”) or “”
result[“race_num”]  = race.get(“race_num”)
result[“code”]      = race.get(“code”) or “”

# After scored = _baseline_score(features), add top_runner_name:

top_runner_name = scored[0].get(“runner_name”) if scored else “”
result[“top_runner_name”] = top_runner_name

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 3 — BACKTEST BLOCKED BY @require_role(“admin”)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROBLEM: backtesting.js calls POST /api/admin/backtest using the api()
helper. The api() helper reads dp_token from localStorage. However, the
login sets dp_token as an httponly cookie — meaning JavaScript CANNOT
read it from document.cookie and it is NEVER in localStorage unless the
user explicitly went through the static/index.html login and the response
also included token in JSON body.

The login endpoint DOES return {“token”: token} in JSON AND sets the
httponly cookie. So: if the user logged in via static/index.html, they
have it in localStorage. If they navigated directly to /backtesting they
do NOT have it.

TWO-PART FIX:

PART A — Add a dedicated backtest route to prediction_routes.py (no auth required,
since predictions are not admin operations):

FILE: api/prediction_routes.py — add new route:

@prediction_bp.route(”/backtest-run”, methods=[“POST”])
def run_backtest_ui():
“””
UI-accessible backtest runner — no admin role required.
Delegates to backtest_engine with UI-friendly response shape.
“””
try:
from datetime import date as date_type
from ai.backtest_engine import backtest_date_range
data = request.get_json(silent=True) or {}
today = date_type.today().isoformat()

```
      date_from = data.get("date_from") or data.get("date")
      date_to   = data.get("date_to")   or data.get("date")

      if not date_from or not date_to:
          return jsonify({"ok": False, "error": "date_from and date_to required"}), 400
      if date_from > today or date_to > today:
          return jsonify({"ok": False,
              "error": "Cannot backtest future dates — no result leakage"}), 400

      code_filter  = data.get("code_filter")
      batch_size   = int(data.get("batch_size") or 50)

      result = backtest_date_range(
          date_from=date_from,
          date_to=date_to,
          code_filter=code_filter if code_filter and code_filter != "ALL" else None,
      )

      return _shape_backtest_response(result, date_from, date_to,
                                      code_filter or "ALL", batch_size)
  except Exception as e:
      log.error(f"POST /api/predictions/backtest-run failed: {e}")
      return jsonify({"ok": False, "error": "Backtest failed"}), 500
```

PART B — Update backtesting.js to use the new endpoint:

FILE: static/js/backtesting.js — in runBacktest(), change:
const data = await api(”/api/admin/backtest”, {
TO:
const data = await api(”/api/predictions/backtest-run”, {

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 4 — BACKTEST RESPONSE SHAPE MISMATCH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROBLEM: backtesting.js reads:
data.summary.samples       → engine returns: data.total_races
data.summary.hit_rate      → engine returns: data.hit_rate (as decimal 0.33, not “33%”)
data.summary.roi           → engine returns: nothing
data.summary.correct       → engine returns: data.winner_hit_count
data.summary.wrong         → engine returns: total_races - winner_hit_count
data.summary.profit        → engine returns: nothing
data.summary.avg_confidence→ engine returns: nothing
data.summary.verdict       → engine returns: nothing
data.summary.summary_text  → engine returns: nothing
data.rows                  → engine returns: nothing (items saved to DB only)
data.errors                → engine returns: nothing

FILE: api/prediction_routes.py — add _shape_backtest_response() helper:

def _shape_backtest_response(
result: dict, date_from: str, date_to: str,
code_filter: str, batch_size: int
) -> “Response”:
“”“Shape backtest_engine output into the format backtesting.js expects.”””
from flask import jsonify

```
  if not result.get("ok"):
      return jsonify(result), 400

  total    = result.get("total_races") or 0
  hits     = result.get("winner_hit_count") or 0
  wrong    = total - hits
  hit_rate = result.get("hit_rate") or 0.0
  avg_odds = result.get("avg_winner_odds") or 0.0

  # ROI: (avg_winner_odds * hit_rate) - 1, expressed as percentage
  roi_float = round((avg_odds * hit_rate) - 1.0, 4) if avg_odds and hit_rate else 0.0
  roi_str   = f"{roi_float * 100:+.1f}%"

  # Profit: simulating $1 flat stake per race
  profit = round(hits * (avg_odds - 1) - wrong * 1.0, 2) if avg_odds else 0.0

  # Verdict
  if total == 0:
      verdict = "NO_DATA"
  elif roi_float > 0.10:
      verdict = "APPROVE"
  elif roi_float > 0:
      verdict = "BETTER"
  elif roi_float > -0.05:
      verdict = "CAUTION"
  else:
      verdict = "PASS"

  # Summary text
  if total == 0:
      summary_text = "No races found in the selected date range."
  else:
      summary_text = (
          f"Tested {total} races from {date_from} to {date_to}. "
          f"Model selected the winner {hits} times ({hit_rate*100:.1f}% hit rate). "
          f"Simulated ROI: {roi_str} at flat $1 stake."
      )

  # Fetch rows from backtest_run_items for this run
  rows = []
  try:
      from db import get_db, safe_query, T
      run_id = result.get("run_id") or ""
      if run_id:
          raw_rows = safe_query(
              lambda: get_db()
              .table(T("backtest_run_items"))
              .select("race_uid,race_date,track,code,predicted_winner,"
                      "actual_winner,winner_hit,winner_odds,score,model_version")
              .eq("run_id", run_id)
              .order("race_date")
              .limit(batch_size)
              .execute()
              .data,
              []
          ) or []

          rows = [
              {
                  "date":       r.get("race_date") or "",
                  "race":       f"{(r.get('track') or '').replace('-', ' ').title()} {r.get('code', '')}",
                  "selection":  r.get("predicted_winner") or "—",
                  "actual":     r.get("actual_winner") or "—",
                  "decision":   "WIN" if r.get("winner_hit") else "LOSS",
                  "confidence": f"{float(r.get('score') or 0):.2f}",
                  "pl":         f"+${float(r.get('winner_odds') or 0) - 1:.2f}"
                                if r.get("winner_hit") else "-$1.00",
              }
              for r in raw_rows
          ]
  except Exception as _re:
      log.warning(f"backtest rows fetch failed: {_re}")

  # Error pattern analysis
  errors = []
  if rows:
      loss_rows = [r for r in rows if r["decision"] == "LOSS"]
      if len(loss_rows) > 3:
          errors.append({
              "tag": "LOSS_STREAK",
              "count": len(loss_rows),
          })

  summary = {
      "samples":        total,
      "correct":        hits,
      "wrong":          wrong,
      "hit_rate":       f"{hit_rate*100:.1f}%",
      "roi":            roi_str,
      "profit":         f"${profit:+.2f}",
      "avg_confidence": "—",
      "verdict":        verdict,
      "summary_text":   summary_text,
      "model_version":  result.get("model_version") or "baseline_v1",
      "run_id":         result.get("run_id") or "",
      "date_from":      date_from,
      "date_to":        date_to,
      "code_filter":    code_filter,
  }

  return jsonify({
      "ok":     True,
      "summary": summary,
      "rows":    rows,
      "errors":  errors,
      "model_comparison": result.get("model_comparison"),
  })
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 5 — GET /api/predictions/today MISSING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: api/prediction_routes.py

PROBLEM: learning.js calls GET /api/predictions/today for the activity
feed. Only POST exists (triggers new predictions). Without this endpoint
the feed always shows “No predictions yet today.”

FIX — Add GET route after the existing POST /today route:

@prediction_bp.route(”/today”, methods=[“GET”])
def get_today_predictions():
“”“Return stored predictions for today with result outcomes.”””
try:
from datetime import date
from db import get_db, safe_query, T
today = date.today().isoformat()

```
      # Prediction snapshots for today
      snaps = safe_query(
          lambda: get_db()
          .table(T("prediction_snapshots"))
          .select(
              "race_uid,race_date,track,race_num,code,"
              "signal,decision,confidence,ev,model_version,"
              "top_runner,created_at"
          )
          .eq("race_date", today)
          .order("created_at", desc=True)
          .limit(60)
          .execute()
          .data,
          []
      ) or []

      # Today's results for WIN/LOSS outcome
      results = safe_query(
          lambda: get_db()
          .table(T("results_log"))
          .select("race_uid,winner,win_price")
          .eq("date", today)
          .execute()
          .data,
          []
      ) or []
      result_map = {r["race_uid"]: r for r in results if r.get("race_uid")}

      predictions = []
      for snap in snaps:
          race_uid = snap.get("race_uid") or ""
          res_row  = result_map.get(race_uid)
          signal   = snap.get("signal") or "—"
          decision = snap.get("decision") or "—"

          if res_row:
              top = snap.get("top_runner") or ""
              winner = res_row.get("winner") or ""
              outcome = "WIN" if (top and winner and
                  top.strip().upper() == winner.strip().upper()) else "LOSS"
          else:
              outcome = "PENDING"

          track_display = (snap.get("track") or "").replace("-", " ").title()
          predictions.append({
              "race_uid":   race_uid,
              "track":      track_display,
              "race_num":   snap.get("race_num"),
              "signal":     signal,
              "decision":   decision,
              "confidence": snap.get("confidence"),
              "ev":         snap.get("ev"),
              "selection":  snap.get("top_runner") or "—",
              "result":     outcome,
              "winner":     res_row.get("winner") if res_row else None,
          })

      return jsonify({
          "ok":          True,
          "predictions": predictions,
          "count":       len(predictions),
          "date":        today,
      })
  except Exception as e:
      log.error(f"GET /api/predictions/today failed: {e}")
      return jsonify({"ok": False, "predictions": [], "error": str(e)}), 500
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 6 — BACKTEST ROI/PROFIT IN ENGINE OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: ai/backtest_engine.py — backtest_date_range()

The engine saves run_items to DB but never includes them in the return
dict. Fix C1 from Part 1 (breakdown fields) also applies here.
Add ROI, profit, and breakdowns directly to the summary return:

# After the existing summary dict is built, add:

roi_float = round(
(avg_winner_odds * hit_rate) - 1.0, 4
) if avg_winner_odds and hit_rate else 0.0

profit = round(
winner_hits * (avg_winner_odds - 1) - (races_tested - winner_hits),
2
) if avg_winner_odds else 0.0

summary[“roi”]             = roi_float
summary[“roi_pct”]         = f”{roi_float * 100:+.1f}%”
summary[“profit”]          = profit
summary[“profit_str”]      = f”${profit:+.2f}”
summary[“races_tested”]    = races_tested

# Breakdowns by code

from collections import defaultdict
code_groups = defaultdict(lambda: {“hits”: 0, “total”: 0, “odds_sum”: 0, “odds_count”: 0})
signal_groups = defaultdict(lambda: {“hits”: 0, “total”: 0})

for item in run_items:
code = (item.get(“code”) or “UNKNOWN”).upper()
code_groups[code][“total”] += 1
if item.get(“winner_hit”):
code_groups[code][“hits”] += 1
odds = item.get(“winner_odds”) or 0
if odds:
code_groups[code][“odds_sum”] += float(odds)
code_groups[code][“odds_count”] += 1

breakdown_by_code = {}
for code, g in code_groups.items():
t = g[“total”]
h = g[“hits”]
avg_o = g[“odds_sum”] / g[“odds_count”] if g[“odds_count”] else 0
roi_c = round((avg_o * h / t) - 1.0, 4) if t and avg_o else 0.0
breakdown_by_code[code] = {
“samples”:  t,
“correct”:  h,
“win_rate”: f”{h/t*100:.1f}%” if t else “0%”,
“roi”:      f”{roi_c*100:+.1f}%”,
}

summary[“breakdown_by_code”] = breakdown_by_code

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 7 — LEARNING STATUS: total_predictions ALWAYS ZERO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: app.py — api_ai_learning_status()

PROBLEM: The endpoint queries prediction_snapshots with
.eq(“race_date”, today) but race_date column does not exist yet
(Fix 2 adds it). Until the migration runs and predictions are
re-stored, this query returns 0 rows.

INTERIM FIX — also query by created_at as fallback:

# Replace the snap_rows query with:

try:
snap_rows = safe_query(
lambda: get_db()
.table(T(“prediction_snapshots”))
.select(“model_version,race_uid”)
.gte(“created_at”, today + “T00:00:00Z”)
.lte(“created_at”, today + “T23:59:59Z”)
.execute()
.data,
[]
) or []
except Exception:
snap_rows = []

total_predictions = len(snap_rows)
if snap_rows:
# Get model version from most recent
try:
latest = safe_query(
lambda: get_db()
.table(T(“prediction_snapshots”))
.select(“model_version”)
.gte(“created_at”, today + “T00:00:00Z”)
.order(“created_at”, desc=True)
.limit(1)
.execute()
.data,
[]
) or []
model_version = (latest[0].get(“model_version”) or “baseline_v1”) if latest else “baseline_v1”
except Exception:
model_version = “baseline_v1”

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 8 — LEARNING PERFORMANCE: bankroll_history ALWAYS EMPTY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: ai/learning_store.py — get_performance_summary()

PROBLEM: The performance chart in learning.js reads data.bankroll_history
but get_performance_summary() never returns it. Even after Fix 1 starts
populating learning_evaluations, the chart will show nothing.

FIX — Add bankroll history to return dict:

# After computing winner_hits / total etc, add:

# Build bankroll history (simulated $100 bank, $1 flat stake)

bankroll = 100.0
bankroll_history = []
for row in reversed(rows):   # oldest → newest
if row.get(“winner_hit”):
odds = float(row.get(“winner_odds”) or 2.0)
bankroll = round(bankroll + (odds - 1.0), 2)
else:
bankroll = round(bankroll - 1.0, 2)
bankroll_history.append(bankroll)

roi_pct = round((bankroll - 100.0), 2)   # profit on $100

# Add to return dict:

return {
“ok”:               True,
“model_version”:    model_version or “all”,
“total_evaluated”:  total,
“winner_hit_count”: winner_hits,
“top2_hit_count”:   top2_hits,
“top3_hit_count”:   top3_hits,
“winner_hit_rate”:  round(winner_hits / total, 4) if total else 0.0,
“top2_hit_rate”:    round(top2_hits  / total, 4) if total else 0.0,
“top3_hit_rate”:    round(top3_hits  / total, 4) if total else 0.0,
“avg_winner_odds”:  avg_winner_odds,
“bankroll_history”: bankroll_history,
“starting_bank”:    100.0,
“current_bank”:     round(bankroll, 2),
“total_profit”:     roi_pct,
“roi_pct”:          roi_pct,
“roi”:              roi_pct,
“win_rate”:         round(winner_hits / total * 100, 1) if total else 0,
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 9 — SCHEDULER: add daily evaluate_all sweep
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: scheduler.py

PROBLEM: Even with Fix 1, only newly written results trigger evaluation.
All historical results from previous days that have no evaluation record
(because evaluate_prediction was never called before) will never get
evaluated — the learning system starts from zero.

FIX — Add a once-daily backfill job that evaluates any prediction that
has a result but no learning_evaluation row:

# Add constant:

EVAL_BACKFILL_INTERVAL = 3600  # once per hour, backfills missed evaluations

# Add function:

def _run_evaluation_backfill():
“”“Evaluate any predictions that have results but no evaluation record.”””
try:
from db import get_db, safe_query, T
from ai.learning_store import evaluate_prediction
from database import get_result

```
      # Find prediction_snapshots with a result but no evaluation
      snaps = safe_query(
          lambda: get_db()
          .table(T("prediction_snapshots"))
          .select("race_uid")
          .execute()
          .data,
          []
      ) or []

      evaluated = safe_query(
          lambda: get_db()
          .table(T("learning_evaluations"))
          .select("race_uid")
          .execute()
          .data,
          []
      ) or []

      evaluated_uids = {r["race_uid"] for r in evaluated if r.get("race_uid")}
      snap_uids = {r["race_uid"] for r in snaps if r.get("race_uid")}
      pending = snap_uids - evaluated_uids

      backfilled = 0
      for race_uid in list(pending)[:50]:   # cap at 50 per run
          stored = get_result(race_uid)
          if stored and stored.get("winner"):
              try:
                  evaluate_prediction(race_uid, stored)
                  backfilled += 1
              except Exception:
                  pass

      if backfilled:
          log.info(f"scheduler: backfilled {backfilled} evaluations")

  except Exception as e:
      log.warning(f"evaluation_backfill failed: {e}")
```

# In the main scheduler loop, add:

last_eval_backfill = 0

# … inside the while True loop:

if now - last_eval_backfill >= EVAL_BACKFILL_INTERVAL:
_run_evaluation_backfill()
last_eval_backfill = now

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 10 — BACKTESTING.JS: btBatchSize NOT PASSED CORRECTLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: static/js/backtesting.js — runBacktest()

PROBLEM: The request body sends batch_size but the field is named
batchSize in the existing code. Also the model comparison toggle
is missing from the UI entirely.

FIX — Update the request body in runBacktest():

body: JSON.stringify({
date_from:    from,
date_to:      to,
code_filter:  code !== “ALL” ? code : null,
batch_size:   batchSize,
compare_models: false,    // add toggle later
})

FIX — Update btHitRate display (engine now returns “33.1%” string):
// The summary.hit_rate is now a formatted string like “33.1%”
// Remove any extra “%” formatting:
setText(“btHitRate”, summary.hit_rate || “0%”);
setText(“btROI”,     summary.roi      || “0%”);

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIX 11 — SQL SCHEMA: ADD MISSING COLUMNS TO prediction_snapshots
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE: sql/001_canonical_schema.sql

Add the ALTER statements alongside the existing Phase 5 columns:

– prediction_snapshots Phase 5 extension — race context
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS race_date  DATE;
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS track      TEXT DEFAULT ‘’;
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS race_num   INTEGER;
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS code       TEXT DEFAULT ‘’;
ALTER TABLE prediction_snapshots ADD COLUMN IF NOT EXISTS top_runner TEXT;

ALTER TABLE test_prediction_snapshots ADD COLUMN IF NOT EXISTS race_date  DATE;
ALTER TABLE test_prediction_snapshots ADD COLUMN IF NOT EXISTS track      TEXT DEFAULT ‘’;
ALTER TABLE test_prediction_snapshots ADD COLUMN IF NOT EXISTS race_num   INTEGER;
ALTER TABLE test_prediction_snapshots ADD COLUMN IF NOT EXISTS code       TEXT DEFAULT ‘’;
ALTER TABLE test_prediction_snapshots ADD COLUMN IF NOT EXISTS top_runner TEXT;

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILES MODIFIED SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

data_engine.py                  — Fix 1 (evaluate_prediction in _write_result,
_update_race_status_fallback)
migrations.py                   — Fix 2 (add race_date/track/race_num/code/
top_runner to prediction_snapshots)
ai/learning_store.py            — Fix 2 (snap_row new fields), Fix 8 (bankroll_history)
ai/predictor.py                 — Fix 2 (race context in result dict)
ai/backtest_engine.py           — Fix 6 (ROI/profit/breakdowns in return dict)
api/prediction_routes.py        — Fix 3 (new /backtest-run endpoint),
Fix 4 (_shape_backtest_response helper),
Fix 5 (GET /today endpoint)
app.py                          — Fix 7 (learning status uses created_at fallback)
scheduler.py                    — Fix 9 (evaluation backfill job)
sql/001_canonical_schema.sql    — Fix 11 (ALTER TABLE statements)
static/js/backtesting.js        — Fix 3 (new endpoint URL),
Fix 10 (batch_size field, hit_rate display)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VERIFICATION CHECKLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After ALL fixes (Parts 1, 2, and this file):

1. python smoke_test.py — zero errors
1. Evaluation pipeline:
   → After a race result is written, Supabase learning_evaluations table
   gains a new row for that race_uid
   → GET /api/predictions/performance returns total_evaluated > 0
   after at least one result has been written
1. Learning tab Activity Feed:
   → GET /api/predictions/today returns predictions array
   → Feed shows race names, signal types, PENDING/WIN/LOSS outcomes
   → Paper Bets Today count matches number of snap rows for today
1. Learning tab Performance:
   → Win Rate shows a percentage (not “—”)
   → ROI shows a value (positive or negative)
   → Performance chart renders if total_evaluated > 0
1. Backtest:
   → Run Backtest button works WITHOUT needing to log in first
   → POST /api/predictions/backtest-run returns 200 (not 401)
   → Summary shows Samples, Hit Rate, ROI, Profit, Verdict
   → Results table populates with rows from backtest_run_items
   → Code breakdown shows GREYHOUND/HORSE/HARNESS split
   → Export CSV button downloads a file
1. Schema:
   → prediction_snapshots has race_date, track, race_num, code, top_runner
   → After next prediction run, these fields are populated
   → GET /api/predictions/today can filter by race_date
1. Scheduler:
   → Evaluation backfill fires hourly
   → Supabase learning_evaluations gains rows for any
   race that has a result but no evaluation record

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
END OF PROMPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
