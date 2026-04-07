Rework the DemonPulse UI into a professional racing intelligence platform. The goal is a clean, dark, premium interface similar to Sportsbet/Betfair in usability but with DemonPulse’s AI analysis layered on top. Two pages get completely rebuilt: the Home page (race selection board) and the Live/Race page (full form guide). All other pages (Betting, Reports, etc.) are untouched.

The existing design tokens in tokens.css are kept:
–bg-0: #050505    (deepest background)
–bg-1: #090909
–bg-2: #101010
–bg-3: #161616
–red-1: #ff1f1f   (primary accent)
–amber: #ffb347   (warning/near-jump)
–blue: #69b7ff    (info)
–green: #49d17d   (positive/win)
–text: #f2f2f2
–text-soft: #cfcfcf
–text-dim: #8c8c8c

-----

## PAGE 1 — HOME (templates/home.html + static/js/home.js)

### Layout Vision

Sportsbet-style race board. No hero section, no “MASTER CONTROL BOARD” copy.
Clean, dense, functional. User lands and immediately sees upcoming races.

### Structure

```
┌─────────────────────────────────────────────┐
│  TOP BAR (already in base.html — keep)      │
├─────────────────────────────────────────────┤
│  FILTER STRIP                               │
│  [ALL] [🐕 GREYHOUND] [🐴 HORSE] [🏇 HARNESS] │
│  Auto-refresh toggle  Last updated: 10:52am │
├─────────────────────────────────────────────┤
│  NEXT TO JUMP — horizontal scroll strip     │
│  [AnglePark R1 • 38s] [Launceston R1 • 2m] │
├─────────────────────────────────────────────┤
│  RACE BOARD — grouped by meeting            │
│  ┌──────────────────────────────────────┐  │
│  │ 🐕 ANGLE PARK  •  SA  •  Greyhound   │  │
│  │ R1 • 319m • Grade 5 •  38s  IMMINENT │  │
│  │ R2 • 395m • Grade 6 • 12m  UPCOMING  │  │
│  │ R3 • 520m • Grade 5 • 26m  UPCOMING  │  │
│  └──────────────────────────────────────┘  │
│  ┌──────────────────────────────────────┐  │
│  │ 🐴 HAMILTON  •  VIC  •  Thoroughbred │  │
│  │ R1 • 1100m • Maiden •  58m UPCOMING  │  │
│  └──────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

### Filter Strip

Replace the current toolbar. One row, full width.
Left side: code filter buttons styled as pill tabs — ALL | GREYHOUND | HORSE | HARNESS
Right side: small text “Updated 10:52:14am” and an auto-refresh indicator dot (green = live).

HTML:

```html
<div class="race-filter-strip">
  <div class="filter-tabs">
    <button class="filter-tab active" data-code="ALL">All Races</button>
    <button class="filter-tab" data-code="GREYHOUND">Greyhounds</button>
    <button class="filter-tab" data-code="HORSE">Horses</button>
    <button class="filter-tab" data-code="HARNESS">Harness</button>
  </div>
  <div class="filter-strip-right">
    <span class="refresh-dot" id="refreshDot"></span>
    <span class="filter-meta" id="filterMeta">Loading…</span>
  </div>
</div>
```

CSS for filter tabs: dark pill buttons, red accent on active, no borders on inactive. Hover lifts slightly. Active tab has –red-1 bottom border 2px and slightly lighter background.

### Next to Jump Strip

Horizontal scrollable strip of the next 6-8 races by countdown. Each chip shows:

- Track name (short)
- Race number
- Countdown (red/amber/white depending on urgency)
- Click navigates to that race’s form guide

```html
<div class="ntj-strip" id="ntjStrip">
  <!-- populated by JS -->
  <div class="ntj-chip ntj-imminent">
    <span class="ntj-track">Angle Park</span>
    <span class="ntj-race">R1</span>
    <span class="ntj-time">38s</span>
  </div>
</div>
```

CSS: horizontal flex, overflow-x auto, no scrollbar visible (scrollbar-width: none). Each chip is a rounded pill ~140px wide. IMMINENT = red background pulse animation. NEAR = amber. UPCOMING = dark with white text.

Pulse animation for imminent races:

```css
@keyframes pulse-red {
  0%, 100% { box-shadow: 0 0 0 0 rgba(255,31,31,0.4); }
  50%       { box-shadow: 0 0 0 6px rgba(255,31,31,0); }
}
.ntj-imminent { animation: pulse-red 1.5s ease infinite; }
```

### Race Board — Meeting Groups

Replace the flat table with grouped meeting cards.

Races are grouped by (track + code). Each group = one meeting card.

Meeting card structure:

```html
<div class="meeting-card" data-code="GREYHOUND">
  <div class="meeting-header">
    <div class="meeting-header-left">
      <span class="meeting-code-badge badge-greyhound">GH</span>
      <div class="meeting-info">
        <span class="meeting-name">Angle Park</span>
        <span class="meeting-sub">South Australia • Greyhound</span>
      </div>
    </div>
    <span class="meeting-race-count">8 races</span>
  </div>

  <div class="meeting-races">
    <!-- one row per race -->
    <div class="race-row" data-race-uid="..." onclick="openRace(this)">
      <div class="race-row-left">
        <span class="race-num">R1</span>
        <div class="race-details">
          <span class="race-name">Grade 5 • 319m</span>
        </div>
      </div>
      <div class="race-row-right">
        <span class="race-countdown imminent">38s</span>
        <span class="race-status-badge status-imminent">IMMINENT</span>
        <span class="race-arrow">›</span>
      </div>
    </div>
    <!-- more races -->
  </div>
</div>
```

Race row hover: background lifts to –bg-3, left border flashes red, cursor pointer. Clicking the row navigates to /live?race_uid=XXX.

Code badge colors:

- GREYHOUND: background rgba(255,31,31,0.15), color –red-1, text “GH”
- HORSE: background rgba(105,183,255,0.15), color –blue, text “HR”
- HARNESS: background rgba(255,179,71,0.15), color –amber, text “HN”

Status badge colors:

- IMMINENT: –red-1 background
- NEAR: –amber color, transparent bg with amber border
- UPCOMING: –text-dim color, transparent bg

Countdown color rules:

- < 2 min: –red-1, bold
- 2-10 min: –amber
- 10 min: –text-soft

### JS Changes (home.js)

Replace the table-rendering logic entirely.

Group the board items by track+code:

```javascript
function groupByMeeting(items) {
    const groups = {};
    for (const item of items) {
        const key = `${item.track}_${item.code}`;
        if (!groups[key]) groups[key] = { track: item.track, code: item.code, races: [] };
        groups[key].races.push(item);
    }
    // sort each meeting's races by race_num
    for (const g of Object.values(groups)) {
        g.races.sort((a, b) => (a.race_num || 0) - (b.race_num || 0));
    }
    // sort meetings by their soonest race
    return Object.values(groups).sort((a, b) => {
        const aMin = Math.min(...a.races.map(r => r.seconds_to_jump ?? 99999));
        const bMin = Math.min(...b.races.map(r => r.seconds_to_jump ?? 99999));
        return aMin - bMin;
    });
}
```

Countdown formatting:

```javascript
function formatCountdown(secs) {
    if (secs == null) return "—";
    if (secs < 0)   return "Jumped";
    if (secs < 60)  return `${secs}s`;
    if (secs < 3600) return `${Math.floor(secs/60)}m ${secs%60}s`;
    return `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`;
}
```

Track name formatting:

```javascript
function formatTrack(slug) {
    if (!slug) return "—";
    return slug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}
```

openRace function:

```javascript
function openRace(el) {
    const uid = el.dataset.raceUid;
    if (uid) window.location.href = `/live?race_uid=${encodeURIComponent(uid)}`;
}
```

Auto-refresh: poll /api/home/board every 30 seconds. Show the refresh dot as green solid when live, grey when stale (>60s since last success).

Remove: the hero stats section, quick-feed sidebar, priority focus box. These are replaced by the NTJ strip and grouped board.

-----

## PAGE 2 — LIVE / RACE FORM GUIDE (templates/live.html + static/js/live.js)

This becomes the full form guide for a selected race. Think Sportsbet race page but with DemonPulse AI overlaid.

### Layout Vision

```
┌────────────────────────────────────────────────────┐
│ RACE HEADER                                         │
│ Angle Park R1 • Grade 5 • 319m • Good • 10:52am    │
│ [GREYHOUND] [38s IMMINENT] [← Prev Race] [Next →]  │
├──────────────────────────────┬─────────────────────┤
│ FORM GUIDE TABLE (main)      │ RACE ANALYSIS       │
│ Box | Runner | Form | Odds   │ Signal: VALUE       │
│ ————————————————————————     │ Decision: BET       │
│  1  Rapid Fire   FVFF1  2.80 │ Pace: EVEN          │
│     ████░░░░░ 62% win        │ Shape: FRONT RUNNER │
│  2  Midnight Run FVVV2  4.20 │ Condition: Good     │
│  ...                         │                     │
│                              │ ── SIMULATION ──    │
│                              │ [Run Sim]           │
│                              │ Top: Rapid Fire 62% │
├──────────────────────────────┴─────────────────────┤
│ QUICK BET MODULE                                    │
│ [Win ▼] Runner: [Rapid Fire ▼]  $[  10  ]  [BET]  │
└────────────────────────────────────────────────────┘
```

### Race Header Section

Clean single-row header strip with all race metadata:

```html
<div class="race-header-bar">
  <div class="race-header-title">
    <span class="race-header-track" id="liveTrack">—</span>
    <span class="race-header-sep">•</span>
    <span class="race-header-racenum" id="liveRaceNum">R—</span>
    <span class="race-header-name" id="liveRaceName">—</span>
  </div>
  <div class="race-header-chips">
    <span class="race-code-chip" id="liveCode">—</span>
    <span class="race-meta-chip" id="liveDistance">—</span>
    <span class="race-meta-chip" id="liveGrade">—</span>
    <span class="race-meta-chip" id="liveCondition">—</span>
    <span class="race-time-chip" id="liveJump">—</span>
    <span class="race-countdown-chip" id="liveCountdownChip">—</span>
  </div>
  <div class="race-header-nav">
    <button class="race-nav-btn" id="livePrevRace">‹ Prev</button>
    <button class="race-nav-btn" id="liveNextRace">Next ›</button>
  </div>
</div>
```

The countdown chip auto-updates every second. Color shifts: >10min = white, 2-10min = amber, <2min = red with pulse.

### Main Content — Two Column Layout

Left column (65%): Form Guide Table
Right column (35%): Race Analysis + Simulation + Quick Bet

### Form Guide Table (LEFT COLUMN)

This is the centrepiece. Replace the plain runner table with a proper form guide.

```html
<div class="form-guide-card card">
  <div class="form-guide-header">
    <h2 class="form-guide-title">Form Guide</h2>
    <span class="form-guide-meta" id="formGuideMeta">— runners</span>
  </div>
  <div class="form-guide-table-wrap">
    <table class="form-guide-table">
      <thead>
        <tr>
          <th class="col-box">#</th>
          <th class="col-runner">Runner</th>
          <th class="col-form">Form</th>
          <th class="col-odds">Odds</th>
          <th class="col-prob">AI Win%</th>
          <th class="col-rank">Rank</th>
        </tr>
      </thead>
      <tbody id="formGuideRows">
      </tbody>
    </table>
  </div>
</div>
```

Each runner row:

```html
<tr class="runner-row" data-scratched="false">
  <td class="col-box">
    <div class="box-num">1</div>
  </td>
  <td class="col-runner">
    <div class="runner-name">Rapid Fire</div>
    <div class="runner-meta">T: J Smith  J: M Jones</div>
  </td>
  <td class="col-form">
    <div class="form-string">FVFF1</div>
    <div class="form-career">14: 4-3-2</div>
  </td>
  <td class="col-odds">
    <div class="odds-value">$2.80</div>
    <div class="odds-imp">35.7% imp</div>
  </td>
  <td class="col-prob">
    <div class="prob-bar-wrap">
      <div class="prob-bar" style="width:62%"></div>
    </div>
    <div class="prob-text">62%</div>
  </td>
  <td class="col-rank">
    <div class="rank-badge rank-1">1st</div>
  </td>
</tr>
```

Scratched runners: row gets opacity 0.4, runner-name gets strikethrough, rank badge replaced with “SCR” badge in red.

Win probability bar: a thin horizontal bar inside the cell, colored from –red-1 (high prob) to –text-dim (low prob). Width = win_prob percentage. If no AI data available, show implied probability from odds instead.

Rank badges:

- 1st: –red-1 background
- 2nd: –amber background with dark text
- 3rd: –bg-3 background
- 4th+: just plain text number

Form string: each character is a colored span:

- 1/2/3: –green
- F/W: –red-1
- V: –amber
- x/0: –text-dim

Implied probability: calculated as (1/odds × 100).toFixed(1) + “%”

Row click: expands an inline detail row showing best_time, weight, jockey full name, and any FormFav decorators (badges like “FIRST UP”, “TRACK SPECIALIST”, etc.)

### Race Analysis Panel (RIGHT COLUMN, top section)

```html
<div class="race-analysis-card card">
  <div class="analysis-section">
    <div class="analysis-label">SIGNAL</div>
    <div class="analysis-signal" id="analysisSignal">—</div>
  </div>
  <div class="analysis-section">
    <div class="analysis-label">DECISION</div>
    <div class="analysis-decision" id="analysisDecision">—</div>
  </div>
  <div class="analysis-divider"></div>
  <div class="analysis-grid">
    <div class="analysis-item">
      <span class="analysis-key">Pace</span>
      <span class="analysis-val" id="analysisPace">—</span>
    </div>
    <div class="analysis-item">
      <span class="analysis-key">Race Shape</span>
      <span class="analysis-val" id="analysisShape">—</span>
    </div>
    <div class="analysis-item">
      <span class="analysis-key">Condition</span>
      <span class="analysis-val" id="analysisCondition">—</span>
    </div>
    <div class="analysis-item">
      <span class="analysis-key">Weather</span>
      <span class="analysis-val" id="analysisWeather">—</span>
    </div>
    <div class="analysis-item">
      <span class="analysis-key">Confidence</span>
      <span class="analysis-val" id="analysisConfidence">—</span>
    </div>
    <div class="analysis-item">
      <span class="analysis-key">EV</span>
      <span class="analysis-val" id="analysisEV">—</span>
    </div>
  </div>
</div>
```

Signal display: large text, colored by signal type:

- SNIPER: –green, font-weight 700
- VALUE: –blue
- GEM: –amber
- WATCH: –text-soft
- RISK: –red-1
- NO_BET: –text-dim

### Simulation Panel (RIGHT COLUMN, middle section)

```html
<div class="sim-panel card">
  <div class="sim-header">
    <span class="sim-title">Simulation</span>
    <button class="sim-run-btn" id="liveRunSimBtn">Run Sim</button>
  </div>

  <div class="sim-idle" id="simIdle">
    Click Run Sim to simulate this race.
  </div>

  <div class="sim-results" id="simResults" style="display:none;">
    <div class="sim-summary" id="simSummary"></div>
    <div class="sim-runner-list" id="simRunnerList">
      <!-- compact list: runner name, win%, place% -->
    </div>
  </div>
</div>
```

Sim results display as compact rows: runner name left, win% right as colored bar. No table — just a clean stacked list.

### Quick Bet Module (BOTTOM, full width)

```html
<div class="quick-bet-bar card">
  <div class="qb-field">
    <label>Type</label>
    <select id="qbType">
      <option value="WIN">Win</option>
      <option value="PLACE">Place</option>
      <option value="EACHWAY">Each Way</option>
    </select>
  </div>
  <div class="qb-field qb-runner">
    <label>Runner</label>
    <select id="qbRunner">
      <option value="">Select runner…</option>
    </select>
  </div>
  <div class="qb-field qb-odds">
    <label>Odds</label>
    <input id="qbOdds" type="text" placeholder="—">
  </div>
  <div class="qb-field qb-stake">
    <label>Stake $</label>
    <input id="qbStake" type="number" value="10" min="1">
  </div>
  <div class="qb-returns">
    <label>Returns</label>
    <span id="qbReturns">—</span>
  </div>
  <button class="qb-place-btn" id="qbPlaceBtn">Place Bet</button>
</div>
```

Returns auto-calculates: stake × odds on each keystroke.
When a runner row is clicked in the form guide, the runner dropdown auto-selects that runner and pre-fills odds.

### JS Changes (live.js)

Populate form guide from /api/live/race/<race_uid> response.

Build runner rows:

```javascript
function buildRunnerRows(runners, analysis) {
    const winProbs = analysis?.all_runners || [];
    const probMap = {};
    for (const r of winProbs) {
        probMap[r.number || r.box] = r.win_prob;
    }

    return runners.map((r, idx) => {
        const isScratched = r.scratched;
        const odds = r.price || r.win_odds;
        const impliedProb = odds ? (100 / odds).toFixed(1) : null;
        const winProb = probMap[r.number || r.box_num] ?? (impliedProb ? parseFloat(impliedProb) : null);
        const formStr = r.form_string || "";

        return buildRunnerRow(r, winProb, impliedProb, idx + 1, isScratched);
    });
}
```

Form string coloring:

```javascript
function colorFormString(form) {
    if (!form) return "—";
    return form.split("").map(c => {
        if ("123".includes(c)) return `<span class="form-win">${c}</span>`;
        if ("FW".includes(c))  return `<span class="form-fail">${c}</span>`;
        if (c === "V")         return `<span class="form-mid">${c}</span>`;
        return `<span class="form-dim">${c}</span>`;
    }).join("");
}
```

Auto-populate bet module when runner clicked:

```javascript
function selectRunner(runner) {
    document.getElementById("qbRunner").value = runner.name;
    document.getElementById("qbOdds").value = runner.price || "";
    calcReturns();
}
```

Returns calculation:

```javascript
function calcReturns() {
    const stake = parseFloat(document.getElementById("qbStake").value) || 0;
    const odds = parseFloat(document.getElementById("qbOdds").value) || 0;
    const ret = odds > 0 ? (stake * odds).toFixed(2) : "—";
    document.getElementById("qbReturns").textContent = ret > 0 ? `$${ret}` : "—";
}
```

Prev/Next race navigation: when race loads, call /api/home/board to get all races for today, filter to same meeting (same track + code), sort by race_num. Prev/Next buttons navigate within the meeting.

-----

## CSS ADDITIONS (add to pages.css or a new race.css file)

Key new CSS classes needed:

```css
/* Filter strip */
.race-filter-strip {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
    background: var(--bg-2);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 10;
}
.filter-tab {
    background: transparent;
    border: none;
    color: var(--text-dim);
    padding: 8px 16px;
    border-radius: 20px;
    cursor: pointer;
    font-size: 0.85rem;
    font-weight: 500;
    letter-spacing: 0.02em;
    transition: all 0.15s;
}
.filter-tab.active {
    background: rgba(255,31,31,0.12);
    color: var(--red-1);
    border-bottom: 2px solid var(--red-1);
}
.filter-tab:hover:not(.active) { color: var(--text); background: var(--bg-3); }

/* NTJ strip */
.ntj-strip {
    display: flex;
    gap: 8px;
    padding: 12px 20px;
    overflow-x: auto;
    scrollbar-width: none;
    background: var(--bg-1);
    border-bottom: 1px solid var(--border);
}
.ntj-chip {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 8px 14px;
    background: var(--bg-3);
    border-radius: 10px;
    cursor: pointer;
    min-width: 110px;
    border: 1px solid var(--border);
    transition: transform 0.15s;
}
.ntj-chip:hover { transform: translateY(-2px); border-color: rgba(255,31,31,0.3); }
.ntj-track { font-size: 0.78rem; color: var(--text-soft); font-weight: 600; }
.ntj-race  { font-size: 0.72rem; color: var(--text-dim); }
.ntj-time  { font-size: 0.9rem; font-weight: 700; margin-top: 4px; }
.ntj-imminent .ntj-time { color: var(--red-1); }
.ntj-near .ntj-time     { color: var(--amber); }
.ntj-upcoming .ntj-time { color: var(--text-soft); }

/* Meeting cards */
.meeting-card {
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    overflow: hidden;
    margin-bottom: 10px;
}
.meeting-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    background: var(--bg-3);
    border-bottom: 1px solid var(--border);
}
.meeting-name { font-weight: 700; font-size: 0.95rem; }
.meeting-sub  { font-size: 0.75rem; color: var(--text-dim); }
.meeting-code-badge {
    width: 32px; height: 32px;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.7rem; font-weight: 700;
    margin-right: 10px;
}
.badge-greyhound { background: rgba(255,31,31,0.15); color: var(--red-1); }
.badge-horse     { background: rgba(105,183,255,0.15); color: var(--blue); }
.badge-harness   { background: rgba(255,179,71,0.15); color: var(--amber); }

.race-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 11px 16px;
    cursor: pointer;
    border-bottom: 1px solid var(--border);
    transition: background 0.12s;
}
.race-row:last-child { border-bottom: none; }
.race-row:hover { background: var(--bg-3); border-left: 2px solid var(--red-1); }
.race-num { font-weight: 700; font-size: 0.9rem; min-width: 28px; }
.race-name { font-size: 0.8rem; color: var(--text-dim); }
.race-countdown { font-weight: 700; font-size: 0.9rem; min-width: 52px; text-align: right; }
.race-countdown.imminent { color: var(--red-1); }
.race-countdown.near     { color: var(--amber); }
.race-status-badge {
    font-size: 0.68rem; font-weight: 600;
    padding: 2px 8px; border-radius: 20px;
    margin-left: 8px;
}
.status-imminent { background: rgba(255,31,31,0.2); color: var(--red-1); }
.status-near     { border: 1px solid var(--amber); color: var(--amber); }
.status-upcoming { color: var(--text-dim); }
.race-arrow { color: var(--text-dim); margin-left: 8px; }

/* Form guide table */
.form-guide-table { width: 100%; border-collapse: collapse; }
.form-guide-table th {
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em;
    color: var(--text-dim); text-transform: uppercase;
    padding: 10px 12px; border-bottom: 1px solid var(--border);
    text-align: left;
}
.runner-row { border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.12s; }
.runner-row:hover { background: var(--bg-3); }
.runner-row.selected { background: rgba(255,31,31,0.06); border-left: 2px solid var(--red-1); }
.runner-row.scratched { opacity: 0.4; pointer-events: none; }
.runner-row td { padding: 12px 12px; vertical-align: middle; }

.box-num {
    width: 28px; height: 28px; border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    background: var(--bg-3); font-weight: 700; font-size: 0.85rem;
}
.runner-name { font-weight: 600; font-size: 0.9rem; }
.runner-meta { font-size: 0.72rem; color: var(--text-dim); margin-top: 2px; }
.form-string { font-family: monospace; letter-spacing: 0.08em; font-size: 0.85rem; }
.form-win  { color: var(--green); }
.form-fail { color: var(--red-1); }
.form-mid  { color: var(--amber); }
.form-dim  { color: var(--text-dim); }
.form-career { font-size: 0.72rem; color: var(--text-dim); margin-top: 2px; }

.odds-value { font-weight: 700; font-size: 0.95rem; color: var(--text); }
.odds-imp   { font-size: 0.72rem; color: var(--text-dim); }

.prob-bar-wrap {
    height: 4px; background: var(--bg-3); border-radius: 2px;
    margin-bottom: 4px; overflow: hidden;
}
.prob-bar { height: 100%; background: var(--red-1); border-radius: 2px; transition: width 0.4s; }
.prob-text { font-size: 0.8rem; font-weight: 600; color: var(--text-soft); }

.rank-badge {
    display: inline-flex; align-items: center; justify-content: center;
    padding: 3px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 700;
}
.rank-1 { background: var(--red-1); color: white; }
.rank-2 { background: var(--amber); color: #1a1a1a; }
.rank-3 { background: var(--bg-3); color: var(--text-soft); }

/* Race header bar */
.race-header-bar {
    display: flex; align-items: center; flex-wrap: wrap; gap: 12px;
    padding: 14px 20px;
    background: var(--bg-2);
    border-bottom: 1px solid var(--border);
}
.race-header-track { font-size: 1.3rem; font-weight: 800; }
.race-header-racenum { font-size: 1.1rem; font-weight: 700; color: var(--red-1); }
.race-header-name { font-size: 0.85rem; color: var(--text-dim); }
.race-header-sep { color: var(--text-dim); }
.race-meta-chip {
    background: var(--bg-3); border: 1px solid var(--border);
    padding: 4px 10px; border-radius: 20px;
    font-size: 0.78rem; color: var(--text-soft);
}
.race-countdown-chip {
    padding: 5px 12px; border-radius: 20px; font-weight: 700; font-size: 0.85rem;
}
.race-nav-btn {
    background: var(--bg-3); border: 1px solid var(--border);
    color: var(--text-soft); padding: 6px 12px; border-radius: 8px;
    cursor: pointer; font-size: 0.8rem; transition: all 0.15s;
}
.race-nav-btn:hover { background: var(--bg-3); border-color: var(--red-1); color: var(--text); }

/* Analysis card */
.race-analysis-card { padding: 16px; }
.analysis-section { margin-bottom: 12px; }
.analysis-label { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.1em; color: var(--text-dim); margin-bottom: 4px; }
.analysis-signal { font-size: 1.5rem; font-weight: 800; }
.signal-sniper  { color: var(--green); }
.signal-value   { color: var(--blue); }
.signal-gem     { color: var(--amber); }
.signal-watch   { color: var(--text-soft); }
.signal-risk    { color: var(--red-1); }
.signal-no-bet  { color: var(--text-dim); }
.analysis-divider { height: 1px; background: var(--border); margin: 12px 0; }
.analysis-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.analysis-item { display: flex; flex-direction: column; }
.analysis-key { font-size: 0.7rem; color: var(--text-dim); }
.analysis-val { font-size: 0.85rem; font-weight: 600; }

/* Quick bet bar */
.quick-bet-bar {
    display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap;
    padding: 14px 20px;
}
.qb-field { display: flex; flex-direction: column; gap: 4px; }
.qb-field label { font-size: 0.7rem; color: var(--text-dim); font-weight: 600; letter-spacing: 0.05em; }
.qb-field select,
.qb-field input {
    background: var(--bg-3); border: 1px solid var(--border);
    color: var(--text); padding: 8px 10px; border-radius: 8px;
    font-size: 0.85rem;
}
.qb-runner select { min-width: 180px; }
.qb-stake input   { width: 80px; }
.qb-returns { display: flex; flex-direction: column; gap: 4px; }
.qb-returns label { font-size: 0.7rem; color: var(--text-dim); }
.qb-returns span { font-size: 1rem; font-weight: 700; color: var(--green); }
.qb-place-btn {
    background: var(--red-1); color: white;
    border: none; padding: 10px 24px; border-radius: 8px;
    font-weight: 700; font-size: 0.9rem; cursor: pointer;
    transition: background 0.15s;
    align-self: flex-end;
}
.qb-place-btn:hover { background: var(--red-2); }
.qb-place-btn:disabled { background: var(--bg-3); color: var(--text-dim); cursor: not-allowed; }

/* Sim panel */
.sim-panel { padding: 16px; }
.sim-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.sim-title { font-weight: 700; font-size: 0.9rem; }
.sim-run-btn {
    background: rgba(255,31,31,0.12); color: var(--red-1);
    border: 1px solid rgba(255,31,31,0.3); padding: 6px 14px;
    border-radius: 8px; cursor: pointer; font-size: 0.8rem; font-weight: 600;
}
.sim-idle { font-size: 0.8rem; color: var(--text-dim); text-align: center; padding: 20px 0; }
.sim-runner-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 0.82rem;
}
.sim-runner-name { flex: 1; }
.sim-win-bar-wrap { flex: 1; height: 4px; background: var(--bg-3); border-radius: 2px; margin: 0 10px; }
.sim-win-bar { height: 100%; background: var(--green); border-radius: 2px; }
.sim-win-pct { width: 40px; text-align: right; font-weight: 600; color: var(--green); }
```

-----

## NAVIGATION CHANGES (templates/base.html)

Simplify the nav to 5 items only. Move the rest to a dropdown or settings:

```html
<nav class="top-nav">
    <a href="/home"      class="top-nav-link ...">Races</a>
    <a href="/betting"   class="top-nav-link ...">Betting</a>
    <a href="/reports"   class="top-nav-link ...">Reports</a>
    <a href="/learning"  class="top-nav-link ...">AI</a>
    <a href="/settings"  class="top-nav-link ...">Settings</a>
</nav>
```

Remove “Live”, “Race View”, “Simulator”, “Backtesting” from the top nav. These are now accessed by clicking a race from the board (opens /live) and from within the live page respectively.

-----

## SUMMARY OF CHANGES

Files to modify:

1. templates/home.html       — complete replacement (grouped meeting cards + NTJ strip)
1. templates/live.html       — complete replacement (form guide + analysis + quick bet)
1. templates/base.html       — simplified top nav (5 items)
1. static/js/home.js         — groupByMeeting, formatTrack, NTJ strip, openRace
1. static/js/live.js         — buildRunnerRows, colorFormString, selectRunner, calcReturns, prev/next nav
1. static/css/pages.css      — add all new CSS classes above (or new race.css file)

Files NOT to change:

- All other templates (betting.html, reports.html, settings.html, etc.)
- All backend Python files
- tokens.css, base.css, layout.css, components.css (keep existing)
- All API routes

Expected outcome:

- Home = clean race board grouped by meeting, click any race row → opens form guide
- Live = professional form guide with odds, form strings, AI win%, simulation, quick bet
- Looks and feels like a proper betting platform (Sportsbet quality) with DemonPulse intelligence on top
