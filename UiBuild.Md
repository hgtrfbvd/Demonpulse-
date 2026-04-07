# DemonPulse — Phase 2 Feature Build

Read this entire file before writing any code. Work through each section in order.

-----

## SECTION 1 — RACE STATUS: JUMPED RACES STAY UNTIL RESULTS ARRIVE

### Problem

Races disappear from the board when they jump. They should stay visible as “Pending Result” until OddsPro confirms the result, then show the result. Old races stay on the board until midnight resets.

### Fix: board_builder.py

Change `is_race_expired_by_time()` so it NEVER removes a race based on time alone. Only remove races that have a confirmed settled status (final, paying, abandoned, result_posted).

```python
# In board_builder.py — remove the is_race_expired_by_time() check entirely from build_board().
# Replace the expired filter block with nothing — let the status machine handle removal.
# A race only leaves the board when its status is in SETTLED_STATUSES.
```

### Fix: race_status.py

`BOARD_EXPIRED_GRACE_SECS` is currently 1800 (30 min). Change to 86400 (24 hours) so time-based expiry only triggers as a last resort.

```python
BOARD_EXPIRED_GRACE_SECS = 86400  # keep on board until midnight
```

### Fix: home.js / live.js — display jumped races clearly

In the countdown formatting function, when a race has jumped but has no result yet:

- Show status badge as “PENDING” with amber colour (not IMMINENT red)
- Show countdown as “Awaiting Result” not “Jumped”
- When result arrives (status = result_posted / final / paying), show “RESULT: [winner]”

```javascript
function formatCountdown(secs, status) {
    const st = (status || "").toLowerCase();
    if (["final","paying","result_posted","abandoned"].includes(st)) return "Resulted";
    if (secs == null) return "—";
    if (secs < 0) return "Awaiting Result";
    if (secs < 60)   return `${secs}s`;
    if (secs < 3600) return `${Math.floor(secs/60)}m ${secs%60}s`;
    return `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`;
}

function getStatusBadgeClass(secs, status) {
    const st = (status || "").toLowerCase();
    if (["final","paying","result_posted"].includes(st)) return "badge-resulted";
    if (["jumped_estimated","awaiting_result"].includes(st) || secs < 0) return "badge-pending";
    if (secs < 120) return "badge-imminent";
    if (secs < 600) return "badge-near";
    return "badge-upcoming";
}

function getStatusLabel(secs, status) {
    const st = (status || "").toLowerCase();
    if (["final","paying","result_posted"].includes(st)) return "RESULTED";
    if (["jumped_estimated","awaiting_result"].includes(st) || secs < 0) return "PENDING";
    if (secs < 120) return "IMMINENT";
    if (secs < 600) return "";
    return "";
}
```

Add CSS:

```css
.badge-pending  { background: rgba(255,179,71,0.18); color: var(--amber); border: 1px solid rgba(255,179,71,0.3); }
.badge-resulted { background: rgba(61,214,140,0.14); color: var(--green); }
```

-----

## SECTION 2 — RUNNER EXPAND CARDS: SHOW ALL DB DATA + FORMFAV ENRICHMENT

### Problem

The runner expand currently shows dashes for everything because the API endpoint at `/api/live/race/<uid>` returns runners from `today_runners` table but does NOT merge the FormFav runner enrichment data from `formfav_runner_enrichment` table.

### Fix: app.py — api_live_race endpoint

In the `api_live_race` function, after fetching `runners = get_runners_for_race(race_uid)`, also fetch FormFav runner enrichment and merge it:

```python
# After: runners = get_runners_for_race(race_uid)
# Add:
from database import get_formfav_runner_enrichments
ff_runner_rows = get_formfav_runner_enrichments(race_uid)
ff_runner_map = {}
for ff_r in ff_runner_rows:
    key = ff_r.get("number") or ff_r.get("box_num")
    if key is not None:
        ff_runner_map[int(key)] = ff_r

# Merge FormFav data into each runner
enriched_runners = []
for r in runners:
    box = r.get("box_num") or r.get("number") or r.get("barrier")
    ff = ff_runner_map.get(int(box)) if box is not None else {}
    merged = {**r}
    if ff:
        # Only fill fields that are missing in OddsPro data
        for field in ["form_string","trainer","jockey","driver","weight","career",
                      "best_time","win_prob","place_prob","model_rank","confidence",
                      "decorators","speed_map","class_profile","race_class_fit",
                      "stats_overall","stats_track","stats_distance","stats_condition",
                      "stats_track_distance","stats_full","model_version"]:
            if not merged.get(field) and ff.get(field):
                merged[field] = ff[field]
        # Always include these FormFav-specific fields
        merged["ff_win_prob"]     = ff.get("win_prob")
        merged["ff_model_rank"]   = ff.get("model_rank")
        merged["ff_confidence"]   = ff.get("confidence")
        merged["ff_decorators"]   = ff.get("decorators") or []
        merged["ff_speed_map"]    = ff.get("speed_map")
        merged["ff_class_profile"]= ff.get("class_profile")
        merged["ff_stats_full"]   = ff.get("stats_full") or {}
        merged["ff_career_stats"] = ff.get("stats_overall")
    enriched_runners.append(merged)
runners = enriched_runners
```

### Fix: live.js — buildRunnerCards — show ALL available fields

In `buildRunnerCards`, the expand section must show every field that exists. Replace the current expand-stats-grid with:

```javascript
function buildExpandGrid(r) {
    // Parse career stats from the career string OR from ff_career_stats
    let wins = "—", places = "—", winPct = "—", placePct = "—", starts = "—";
    if (r.career) {
        const m = r.career.match(/^(\d+):\s*(\d+)-(\d+)-(\d+)/);
        if (m) {
            starts = m[1]; wins = m[2]; places = m[3];
            winPct  = ((parseInt(m[2])/parseInt(m[1]))*100).toFixed(1)+"%";
            placePct= (((parseInt(m[2])+parseInt(m[3]))/parseInt(m[1]))*100).toFixed(1)+"%";
        }
    }
    const stats = r.ff_career_stats || {};
    if (stats.wins !== undefined) {
        winPct   = stats.win_pct   ? stats.win_pct.toFixed(1)+"%" : winPct;
        placePct = stats.place_pct ? stats.place_pct.toFixed(1)+"%" : placePct;
    }

    const fields = [
        ["Last 6",       r.form_string || r.form || "—"],
        ["Career",       r.career || "—"],
        ["Win %",        winPct],
        ["Place %",      placePct],
        ["Best Time",    r.best_time || r.bestTime || "—"],
        ["Weight",       r.weight || "—"],
        ["Early Speed",  r.early_speed || r.earlySpeed || "—"],
        ["AI Win %",     r.ff_win_prob != null ? r.ff_win_prob.toFixed(1)+"%" : (r.winProb != null ? r.winProb.toFixed(1)+"%" : "—")],
        ["AI Rank",      r.ff_model_rank != null ? `#${r.ff_model_rank}` : "—"],
        ["AI Confidence",r.ff_confidence || "—"],
        ["Race Class Fit",r.ff_class_profile ? JSON.stringify(r.ff_class_profile).slice(0,30) : "—"],
        ["Trainer",      r.trainer || "—"],
        ["Jockey/Driver",r.jockey || r.driver || "—"],
    ].filter(([,v]) => v && v !== "—");

    return `<div class="expand-stats-grid">${
        fields.map(([k,v]) => `<div class="expand-stat"><span class="es-label">${k}</span><span class="es-val">${esc(String(v))}</span></div>`).join("")
    }</div>`;
}
```

### FormFav Decorator Badges

After the stats grid, show any FormFav decorator badges (e.g. “In Form”, “Top Rated”, “Place Rate”):

```javascript
function buildDecoratorBadges(decorators) {
    if (!decorators || !decorators.length) return "";
    const badgeColors = {
        "In Form":    "var(--green)",
        "Top Rated":  "var(--blue)",
        "Fav":        "var(--amber)",
        "Place Rate": "var(--blue)",
    };
    const badges = Array.isArray(decorators) ? decorators : [];
    if (!badges.length) return "";
    return `<div class="decorator-badges">${
        badges.map(d => {
            const label = typeof d === "string" ? d : (d.label || d.name || JSON.stringify(d));
            const color = badgeColors[label] || "var(--text-dim)";
            return `<span class="decorator-badge" style="border-color:${color};color:${color}">${esc(label)}</span>`;
        }).join("")
    }</div>`;
}
```

Add CSS for badges:

```css
.decorator-badges { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }
.decorator-badge {
    font-size:0.68rem; font-weight:700; padding:3px 9px; border-radius:20px;
    border:1px solid; background:transparent; letter-spacing:0.04em;
}
```

### Race-level FormFav badges on the runner summary row

In the non-expanded runner row, show the top 2 decorator badges inline next to the runner name:

```javascript
// In buildRunnerCards, in the summary row:
const topBadges = (r.ff_decorators || []).slice(0,2).map(d => {
    const label = typeof d === "string" ? d : (d.label || "");
    return label ? `<span class="runner-badge">${esc(label)}</span>` : "";
}).join("");
```

Add CSS:

```css
.runner-badge {
    font-size:0.6rem; font-weight:700; padding:1px 6px; border-radius:4px;
    background:rgba(90,180,255,0.12); color:var(--blue);
    margin-left:5px; vertical-align:middle;
}
```

-----

## SECTION 3 — ANIMATED RACE SIMULATION

### Problem

The current simulation in live.js just shows a static list of win percentages. It needs to show an animated race run matching DemonPulse’s simulation engine output.

### Fix: live.js — replace runSimulation with animated canvas race

When “Run Sim” is clicked, run a 500-iteration simulation, then animate the most likely race scenario on a canvas:

```javascript
function runSimulation() {
    if (!liveRunners.length) return;
    const RUNS = 500;
    const active = liveRunners.filter(r => !r.scratched);
    const code = (liveRace?.code || "GREYHOUND").toUpperCase();

    // 1. Run probability model
    const total = active.reduce((s,r) => s + (r.ff_win_prob || r.win_prob || (100/(r.price||10))), 0);
    const simRunners = active.map((r,i) => {
        const prob = (r.ff_win_prob || r.win_prob || (100/(r.price||10))) / total;
        const winCount = Math.round(prob * RUNS * (0.8 + Math.random()*0.4));
        return {
            name: r.name, box: r.box_num ?? r.number ?? i+1,
            prob, winCount,
            winPct: +((winCount/RUNS)*100).toFixed(1),
            placePct: +(Math.min(95, (winCount/RUNS)*100 * 2.2)).toFixed(1),
            earlySpeed: r.early_speed || r.ff_speed_map?.early || Math.random(),
            color: BOX_COLORS[i % BOX_COLORS.length],
        };
    }).sort((a,b) => b.winPct - a.winPct);

    renderSimResults(simRunners);
    runRaceAnimation(simRunners, code);
}

const BOX_COLORS = ["#ff2d2d","#3dd68c","#5ab4ff","#ffb340","#cc44ff","#ff6b6b","#44ddff","#ffdd44"];

function runRaceAnimation(simRunners, code) {
    const container = document.getElementById("simAnimContainer");
    if (!container) return;

    // Build canvas
    container.innerHTML = `
        <canvas id="raceCanvas" width="600" height="${simRunners.length * 36 + 40}" style="width:100%;border-radius:8px;background:var(--bg-3)"></canvas>
        <div class="sim-result-banner" id="simResultBanner" style="display:none;"></div>
    `;
    const canvas = document.getElementById("raceCanvas");
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    const TRACK_START = 80, TRACK_END = W - 30;
    const TRACK_LEN = TRACK_END - TRACK_START;
    const ROW_H = 36;

    // Runner state: position 0..1
    const state = simRunners.map((r, i) => ({
        ...r, x: 0, y: TRACK_START + i * ROW_H + 18,
        speed: 0.004 + r.prob * 0.006 + Math.random() * 0.002,
        finished: false, finishOrder: null,
    }));

    let frame = 0, finishCount = 0, animId = null;

    function draw() {
        ctx.clearRect(0, 0, W, H);

        // Track lanes
        state.forEach((r, i) => {
            ctx.fillStyle = i % 2 === 0 ? "rgba(255,255,255,0.02)" : "transparent";
            ctx.fillRect(TRACK_START, i*ROW_H + TRACK_START - 36, TRACK_LEN, ROW_H);

            // Box label
            ctx.fillStyle = r.color;
            ctx.font = "bold 11px Barlow Condensed, sans-serif";
            ctx.fillText(`${r.box}. ${r.name.split(" ")[0]}`, 4, r.y + 4);

            // Runner dot
            const rx = TRACK_START + r.x * TRACK_LEN;
            ctx.beginPath();
            ctx.arc(rx, r.y, 8, 0, Math.PI*2);
            ctx.fillStyle = r.color;
            ctx.fill();

            if (r.finished) {
                ctx.fillStyle = "rgba(255,255,255,0.6)";
                ctx.font = "9px sans-serif";
                ctx.fillText(`${r.finishOrder}`, rx - 3, r.y + 3);
            }
        });

        // Finish line
        ctx.strokeStyle = "rgba(255,255,255,0.3)";
        ctx.setLineDash([4,4]);
        ctx.beginPath();
        ctx.moveTo(TRACK_END, 0);
        ctx.lineTo(TRACK_END, H);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    function tick() {
        frame++;
        state.forEach(r => {
            if (r.finished) return;
            // Vary speed slightly each frame (simulate jostling)
            r.speed += (Math.random() - 0.5) * 0.0004;
            r.speed = Math.max(0.002, Math.min(0.014, r.speed));
            r.x = Math.min(1, r.x + r.speed);
            if (r.x >= 1 && !r.finished) {
                r.finished = true;
                r.finishOrder = ++finishCount;
            }
        });

        draw();

        if (finishCount < state.length) {
            animId = requestAnimationFrame(tick);
        } else {
            const winner = state.find(r => r.finishOrder === 1);
            const banner = document.getElementById("simResultBanner");
            if (banner && winner) {
                banner.style.display = "block";
                banner.innerHTML = `<span style="color:var(--green);font-weight:700">🏆 ${winner.name}</span> wins the simulation`;
            }
        }
    }

    // Clear any existing animation
    if (window._raceAnimId) cancelAnimationFrame(window._raceAnimId);
    window._raceAnimId = animId;
    requestAnimationFrame(tick);
}

function renderSimResults(simRunners) {
    const idle = document.getElementById("simIdle");
    const results = document.getElementById("simResults");
    if (idle) idle.style.display = "none";
    if (results) results.style.display = "block";

    const summary = document.getElementById("simSummary");
    if (summary) summary.textContent = `Top: ${simRunners[0]?.name} (${simRunners[0]?.winPct}% from 500 sims) • ${simRunners[0]?.winPct > 35 ? "HIGH" : "MODERATE"} confidence`;

    const list = document.getElementById("simRunnerList");
    if (list) list.innerHTML = simRunners.map(r => `
        <div class="sim-runner-row">
            <span class="sim-runner-name" style="color:${r.color}">${r.box}. ${r.name}</span>
            <div class="sim-win-bar-wrap"><div class="sim-win-bar" style="width:${r.winPct}%;background:${r.color}"></div></div>
            <span class="sim-win-pct">${r.winPct}%</span>
        </div>
    `).join("");
}
```

### Add to live.html — simulation container

In the sim panel section, add a container for the animation above the runner list:

```html
<div id="simAnimContainer" style="margin-bottom:12px;"></div>
<div class="sim-result-banner" id="simResultBanner" style="display:none; padding:8px 0; font-size:0.85rem;"></div>
```

Add CSS:

```css
.sim-result-banner {
    text-align:center; padding:8px 12px; border-radius:8px;
    background:rgba(61,214,140,0.08); border:1px solid rgba(61,214,140,0.2);
    font-size:0.82rem; margin-top:8px;
}
```

-----

## SECTION 4 — AI LEARNING PAGE FULL REWORK

### What it needs to do

1. Display current AI model settings and let admin configure them
1. Show the AI’s autonomous paper betting activity (it bets on every race automatically)
1. Show results review when results come in
1. Let admin set the learning objective and goals
1. Show learning progress over time

### 4a. New backend endpoint needed — add to app.py

```python
@app.route("/api/ai/learning/status", methods=["GET"])
def api_ai_learning_status():
    """AI learning engine status — paper bets placed, results reviewed, model progress."""
    try:
        from database import get_races_for_date, get_result
        from ai.learning_store import get_performance_summary
        from datetime import date
        today = date.today().isoformat()

        perf = get_performance_summary(limit=200)

        # Fetch today's prediction snapshots and results
        all_races = get_races_for_date(today)
        predicted = [r for r in all_races if r.get("status") in ("final","result_posted","paying")]

        return jsonify({
            "ok": True,
            "model_version": perf.get("model_version", "baseline_v1"),
            "total_predictions": perf.get("total_predictions", 0),
            "total_evaluated": perf.get("total_evaluated", 0),
            "win_rate": perf.get("win_rate", 0),
            "roi": perf.get("roi", 0),
            "paper_bets_today": len(predicted),
            "results_reviewed_today": len([r for r in predicted if r.get("status") in ("final","paying","result_posted")]),
            "enrichment_rate": perf.get("enrichment_rate", 0),
            "active": True,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
```

### 4b. New learning.html layout

Replace the current learning.html content entirely. Structure:

```
┌─────────────────────────────────────────────┐
│ AI LEARNING ENGINE                          │
│ Status: ACTIVE  Model: baseline_v1          │
│ Today: 45 paper bets  |  12 reviewed        │
├──────────────────────┬──────────────────────┤
│ CONFIGURATION        │ LIVE ACTIVITY        │
│ Learning objective   │ Paper bets feed      │
│ Model version        │ Recent results       │
│ Confidence threshold │ Win/Loss review      │
│ EV threshold         │                      │
├──────────────────────┴──────────────────────┤
│ PERFORMANCE CHARTS (bankroll curve over time)│
└─────────────────────────────────────────────┘
```

**Configuration panel** — let admin set:

- Learning objective (dropdown: “Maximise ROI”, “High Strike Rate”, “Value Finding”, “Conservative”)
- Confidence threshold (slider: 0.50 → 0.90)
- EV threshold (slider: 0.02 → 0.20)
- Auto-bet on all races? (toggle, default ON)
- Codes to include (checkboxes: GREYHOUND, HORSE, HARNESS)

Save config via `POST /api/admin/sweep` (repurpose or add `POST /api/ai/learning/config`).

**Live Activity feed** — polls `GET /api/predictions/today` every 30s.
Shows a live scrolling list:

```
✓ Angle Park R4 — PREDICTION: Rapid Fire (62%) — [PENDING]
✓ Hamilton R1   — PREDICTION: Desert King  (41%) — WIN $3.80 ✓
✗ Launceston R2 — PREDICTION: Blue Thunder (38%) — LOSS
```

**Performance section** — Chart.js line chart showing paper bankroll over time.

### 4c. learning.js — full rewrite

```javascript
// Poll learning status every 30s
async function pollLearningStatus() {
    try {
        const data = await api("/api/ai/learning/status");
        document.getElementById("learnModelVersion").textContent = data.model_version || "—";
        document.getElementById("learnTotalPredictions").textContent = data.total_predictions || 0;
        document.getElementById("learnTotalEvaluated").textContent = data.total_evaluated || 0;
        document.getElementById("learnWinRate").textContent = data.win_rate ? data.win_rate.toFixed(1)+"%" : "—";
        document.getElementById("learnROI").textContent = data.roi != null ? (data.roi > 0 ? "+" : "") + data.roi.toFixed(1)+"%" : "—";
        document.getElementById("learnPaperBetsToday").textContent = data.paper_bets_today || 0;
        document.getElementById("learnResultsReviewed").textContent = data.results_reviewed_today || 0;
        document.getElementById("learnStatusDot").style.background = data.active ? "var(--green)" : "var(--text-dim)";
    } catch (_) {}
}

// Load activity feed
async function loadActivityFeed() {
    try {
        const data = await api("/api/predictions/today");
        const feed = document.getElementById("learnActivityFeed");
        const items = data.predictions || data.items || [];
        if (!items.length) {
            feed.innerHTML = `<div class="activity-empty">No predictions yet today — system will auto-predict all upcoming races.</div>`;
            return;
        }
        feed.innerHTML = items.slice(0,20).map(p => {
            const won = p.result === "WIN";
            const lost = p.result === "LOSS";
            const icon = won ? "✓" : lost ? "✗" : "·";
            const cls  = won ? "activity-win" : lost ? "activity-loss" : "activity-pending";
            return `<div class="activity-row ${cls}">
                <span class="activity-icon">${icon}</span>
                <span class="activity-race">${p.track || "?"} R${p.race_num || "?"}</span>
                <span class="activity-pick">${p.selection || p.top_runner || "—"}</span>
                <span class="activity-result">${p.result || "PENDING"}</span>
            </div>`;
        }).join("");
    } catch (_) {}
}

setInterval(() => { pollLearningStatus(); loadActivityFeed(); }, 30000);
document.addEventListener("DOMContentLoaded", () => {
    bindTabs();
    pollLearningStatus();
    loadActivityFeed();
});
```

Add CSS:

```css
.activity-row { display:flex; align-items:center; gap:10px; padding:7px 0; border-bottom:1px solid var(--border); font-size:0.8rem; }
.activity-icon { width:16px; font-weight:700; }
.activity-win  .activity-icon { color:var(--green); }
.activity-loss .activity-icon { color:var(--red-1); }
.activity-race { min-width:110px; color:var(--text-soft); }
.activity-pick { flex:1; color:var(--text); }
.activity-result { font-weight:600; min-width:70px; text-align:right; }
.activity-win  .activity-result { color:var(--green); }
.activity-loss .activity-result { color:var(--red-1); }
.activity-pending .activity-result { color:var(--text-dim); }
.activity-empty { color:var(--text-dim); font-size:0.82rem; padding:16px 0; text-align:center; }
```

-----

## SECTION 5 — BETTING MERGED INTO REPORTS PAGE

### 5a. Reports page changes (templates/reports.html + static/js/reports.js)

Remove the “place bet” functionality from reports entirely — that stays only on the live race page.

Add a “Bankroll” tab to the reports page:

```html
<button class="report-tab" data-tab="bankroll">Bankroll</button>
```

Bankroll tab content:

```html
<section class="card report-section" id="report-bankroll" style="display:none;">
    <div class="page-stats-bar">
        <div class="stats-bar-item">
            <span class="stats-bar-label">Bank</span>
            <span class="stats-bar-value" id="rpBank">—</span>
        </div>
        <div class="stats-bar-item">
            <span class="stats-bar-label">Today P/L</span>
            <span class="stats-bar-value" id="rpTodayPL">—</span>
        </div>
        <div class="stats-bar-item">
            <span class="stats-bar-label">Open Bets</span>
            <span class="stats-bar-value" id="rpOpenBets">—</span>
        </div>
        <div class="stats-bar-item">
            <span class="stats-bar-label">Exposure</span>
            <span class="stats-bar-value" id="rpExposure">—</span>
        </div>
        <div class="stats-bar-item">
            <span class="stats-bar-label">Strike Rate</span>
            <span class="stats-bar-value" id="rpStrike">—</span>
        </div>
    </div>
    <!-- P/L chart -->
    <canvas id="bankrollChart" height="100" style="margin:12px 0;"></canvas>
    <!-- Bet history table -->
    <table class="board-terminal-table">
        <thead><tr><th>Date</th><th>Race</th><th>Runner</th><th>Type</th><th>Odds</th><th>Stake</th><th>Result</th><th>P/L</th></tr></thead>
        <tbody id="rpBetHistoryRows"><tr><td colspan="8" class="board-empty">No history</td></tr></tbody>
    </table>
</section>
```

### 5b. Remove betting.html from nav entirely

In base.html, remove the Betting nav link. The page still exists but is no longer in the nav.

-----

## SECTION 6 — SETTINGS: FULL USER MANAGEMENT + ADMIN CONTROL

### 6a. Add User Management tab to settings.html

Add a new tab:

```html
<button class="settings-tab" data-tab="users">User Management</button>
```

User management section:

```html
<section class="card settings-section" id="settings-users" style="display:none;">
    <div class="section-head">
        <div>
            <div class="section-kicker">USER MANAGEMENT</div>
            <h2 class="section-title">Access Control</h2>
        </div>
        <button class="dp-btn dp-btn-primary" id="openAddUserBtn">+ Add User</button>
    </div>

    <!-- Users table -->
    <div class="table-wrap">
        <table class="board-terminal-table">
            <thead>
                <tr>
                    <th>Username</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Last Login</th>
                    <th>Bankroll</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="userTableRows">
                <tr><td colspan="6" class="board-empty">Loading…</td></tr>
            </tbody>
        </table>
    </div>

    <!-- Add User Modal -->
    <div class="modal-backdrop" id="addUserModal" style="display:none;">
        <div class="modal-box">
            <div class="modal-title">Add New User</div>
            <div class="modal-fields">
                <div class="settings-field">
                    <label class="settings-label">Username</label>
                    <input class="bet-input" id="newUsername" placeholder="username">
                </div>
                <div class="settings-field">
                    <label class="settings-label">Password</label>
                    <input class="bet-input" id="newPassword" type="password" placeholder="min 8 characters">
                </div>
                <div class="settings-field">
                    <label class="settings-label">Role</label>
                    <select class="bet-input" id="newRole">
                        <option value="viewer">Viewer — board + reports only</option>
                        <option value="operator" selected>Operator — home, live, betting, reports</option>
                        <option value="admin">Admin — full access</option>
                    </select>
                </div>
                <div class="settings-field">
                    <label class="settings-label">Starting Bankroll ($)</label>
                    <input class="bet-input" id="newBankroll" type="number" value="1000">
                </div>
            </div>
            <div class="modal-actions">
                <button class="dp-btn dp-btn-primary" id="confirmAddUserBtn">Create User</button>
                <button class="dp-btn" onclick="document.getElementById('addUserModal').style.display='none'">Cancel</button>
            </div>
            <div class="section-meta" id="addUserMeta"></div>
        </div>
    </div>
</section>
```

### 6b. settings.js — user management functions

```javascript
async function loadUsers() {
    try {
        const data = await api("/api/admin/users");
        const rows = document.getElementById("userTableRows");
        const users = data.users || [];
        if (!users.length) {
            rows.innerHTML = `<tr><td colspan="6" class="board-empty">No users yet</td></tr>`;
            return;
        }
        rows.innerHTML = users.map(u => `
            <tr>
                <td><strong>${u.username}</strong></td>
                <td><span class="role-badge role-${u.role}">${u.role}</span></td>
                <td><span style="color:${u.active ? 'var(--green)' : 'var(--text-dim)'}">${u.active ? "Active" : "Disabled"}</span></td>
                <td>${u.last_login ? new Date(u.last_login).toLocaleDateString("en-AU") : "Never"}</td>
                <td>$${(u.bankroll ?? 1000).toLocaleString()}</td>
                <td>
                    <button class="dp-btn dp-btn-small" onclick="toggleUser('${u.id}', ${u.active})">${u.active ? "Disable" : "Enable"}</button>
                    <button class="dp-btn dp-btn-small" onclick="resetPassword('${u.id}', '${u.username}')">Reset PW</button>
                    ${u.role !== "admin" ? `<button class="dp-btn dp-btn-small" style="color:var(--red-1)" onclick="deleteUser('${u.id}', '${u.username}')">Remove</button>` : ""}
                </td>
            </tr>
        `).join("");
    } catch (e) {
        console.error("Load users failed", e);
    }
}

async function createUser() {
    const username = document.getElementById("newUsername").value.trim();
    const password = document.getElementById("newPassword").value;
    const role     = document.getElementById("newRole").value;
    const bankroll = parseFloat(document.getElementById("newBankroll").value) || 1000;
    const meta     = document.getElementById("addUserMeta");

    if (!username || !password) { meta.textContent = "Username and password required"; return; }
    if (password.length < 8)   { meta.textContent = "Password must be 8+ characters"; return; }

    try {
        const r = await api("/api/admin/users/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password, role, starting_bankroll: bankroll })
        });
        if (r.ok) {
            document.getElementById("addUserModal").style.display = "none";
            loadUsers();
        } else {
            meta.textContent = r.error || "Failed to create user";
        }
    } catch (e) {
        meta.textContent = "Error: " + e.message;
    }
}

async function toggleUser(userId, currentActive) {
    await api(`/api/admin/users/${userId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: !currentActive })
    });
    loadUsers();
}

async function deleteUser(userId, username) {
    if (!confirm(`Remove user "${username}"? This cannot be undone.`)) return;
    await api(`/api/admin/users/${userId}`, { method: "DELETE" });
    loadUsers();
}

async function resetPassword(userId, username) {
    const newPw = prompt(`New password for "${username}" (min 8 chars):`);
    if (!newPw || newPw.length < 8) { alert("Password must be 8+ characters"); return; }
    const r = await api(`/api/admin/users/${userId}/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: newPw })
    });
    alert(r.ok ? "Password reset successfully" : (r.error || "Failed"));
}
```

Add CSS:

```css
.modal-backdrop {
    position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:300;
    display:flex; align-items:center; justify-content:center;
    backdrop-filter:blur(4px);
}
.modal-box {
    background:var(--bg-2); border:1px solid var(--border-strong);
    border-radius:16px; padding:24px; min-width:360px; max-width:480px;
}
.modal-title { font-size:1.1rem; font-weight:700; margin-bottom:16px; }
.modal-fields { display:flex; flex-direction:column; gap:12px; margin-bottom:20px; }
.modal-actions { display:flex; gap:8px; }
.role-badge { font-size:0.68rem; font-weight:700; padding:2px 8px; border-radius:4px; }
.role-admin    { background:rgba(255,45,45,0.15); color:var(--red-1); }
.role-operator { background:rgba(90,180,255,0.12); color:var(--blue); }
.role-viewer   { background:var(--bg-4); color:var(--text-dim); }
```

### 6c. Backend: add user management API routes — add to app.py or api/admin_routes.py

```python
@admin_bp.route("/users", methods=["GET"])
@require_role("admin")
def list_users():
    from users import get_all_users
    return jsonify({"ok": True, "users": get_all_users()})

@admin_bp.route("/users/create", methods=["POST"])
@require_role("admin")
def create_user_route():
    from users import create_user_full
    from auth import get_current_user
    data = request.get_json(silent=True) or {}
    actor = get_current_user()
    try:
        user = create_user_full(
            username=data.get("username","").strip(),
            password=data.get("password",""),
            role=data.get("role","operator"),
            starting_bankroll=float(data.get("starting_bankroll",1000)),
            creator_username=actor.get("username","admin") if actor else "admin",
        )
        return jsonify({"ok": True, "user": user})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@admin_bp.route("/users/<user_id>", methods=["PATCH"])
@require_role("admin")
def update_user_route(user_id):
    from users import update_user_profile
    from auth import get_current_user
    data = request.get_json(silent=True) or {}
    actor = get_current_user()
    try:
        update_user_profile(user_id, actor.get("username","admin") if actor else "admin", **data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@admin_bp.route("/users/<user_id>", methods=["DELETE"])
@require_role("admin")
def delete_user_route(user_id):
    from users import delete_user
    from auth import get_current_user
    actor = get_current_user()
    try:
        delete_user(user_id, actor.get("username","admin") if actor else "admin")
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@admin_bp.route("/users/<user_id>/reset-password", methods=["POST"])
@require_role("admin")
def reset_user_password(user_id):
    from users import reset_password
    from auth import get_current_user
    data = request.get_json(silent=True) or {}
    actor = get_current_user()
    try:
        reset_password(user_id, data.get("new_password",""), actor.get("username","admin") if actor else "admin")
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
```

### 6d. Role-based page access enforcement

In base.html, the nav must only show pages the current user’s role allows.
Use the `/api/auth/me` response already stored in sessionStorage (set by core.js on login):

```javascript
// In core.js, after login / on load:
async function enforceNavAccess() {
    try {
        const me = await api("/api/auth/me");
        const role = me.role || "viewer";
        const perms = me.permissions || [];

        // Map nav links to required permissions
        const navRules = {
            "/betting":    ["betting"],
            "/learning":   ["ai_learning"],
            "/backtesting":["backtest"],
            "/settings":   ["settings"],
            "/reports":    ["reports"],
        };

        document.querySelectorAll(".top-nav-link").forEach(link => {
            const path = new URL(link.href, location.origin).pathname;
            const required = navRules[path];
            if (required && !required.some(p => perms.includes(p))) {
                link.style.display = "none";
            }
        });
    } catch (_) {}
}
```

Roles map to permissions (already defined in auth.py):

- `admin`: all pages
- `operator`: home, live, betting, reports
- `viewer`: home, reports only

-----

## SECTION 7 — GENERAL SETTINGS PANEL IMPROVEMENTS

### 7a. Add configurable options to General tab

Replace the static display values in settings-general with actual editable controls:

```html
<!-- Board Refresh Interval -->
<div class="settings-field">
    <label class="settings-label">Board Refresh (seconds)</label>
    <select class="bet-input" id="settingRefreshInterval">
        <option value="15">15s</option>
        <option value="30" selected>30s</option>
        <option value="60">60s</option>
        <option value="120">2 min</option>
    </select>
</div>
<!-- Default Stake -->
<div class="settings-field">
    <label class="settings-label">Default Stake ($)</label>
    <input class="bet-input" id="settingDefaultStake" type="number" value="10" min="1">
</div>
<!-- Scheduler toggle -->
<div class="settings-field">
    <label class="settings-label">Scheduler</label>
    <span class="settings-value" id="settingSchedulerStatus">—</span>
</div>
```

Save to localStorage for client-side settings. Show scheduler status from `GET /api/admin/scheduler`.

-----

## FILES TO MODIFY (in order)

1. `race_status.py` — BOARD_EXPIRED_GRACE_SECS = 86400
1. `board_builder.py` — remove is_race_expired_by_time check
1. `app.py` — api_live_race: merge formfav runner enrichment + add user management routes
1. `api/admin_routes.py` — add user CRUD routes, add require_role decorators
1. `templates/live.html` — add simAnimContainer div
1. `static/js/live.js` — animated simulation, full runner expand with all DB fields + FormFav badges
1. `templates/learning.html` — full rework with config panel + activity feed
1. `static/js/learning.js` — full rewrite per Section 4c
1. `templates/reports.html` — add Bankroll tab, remove Betting nav
1. `static/js/reports.js` — add bankroll tab logic + chart
1. `templates/settings.html` — add User Management tab + editable general settings
1. `static/js/settings.js` — user CRUD functions + nav access enforcement
1. `templates/base.html` — remove Betting from nav, add nav access enforcement call
1. `static/js/core.js` — enforceNavAccess() function
1. `static/css/pages.css` — append all new CSS from Sections 1-6

## FILES NOT TO CHANGE

All other Python files (data_engine, scheduler, connectors, database, etc.).
