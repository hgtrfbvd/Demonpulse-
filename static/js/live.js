(function () {
    const _AEST = "Australia/Sydney";

    let liveRace = null;
    let liveAnalysis = null;
    let liveSignal = null;
    let allMeetingRaces = [];   // races in the same meeting, sorted by race_num
    let countdownTimer = null;

    const q = (id) => document.getElementById(id);

    // -------------------------------------------------------
    // Utility
    // -------------------------------------------------------

    function getRaceUid() {
        const params = new URLSearchParams(window.location.search);
        return params.get("race_uid") || "";
    }

    function parseJumpTimeToDate(jumpTime) {
        if (!jumpTime || typeof jumpTime !== "string") return null;
        if (/^\d{4}-\d{2}-\d{2}T/.test(jumpTime) || /^\d{4}-\d{2}-\d{2} /.test(jumpTime)) {
            const dt = new Date(jumpTime);
            return isNaN(dt.getTime()) ? null : dt;
        }
        const parts = jumpTime.split(":");
        if (parts.length < 2) return null;
        const hour = parseInt(parts[0], 10);
        const minute = parseInt(parts[1], 10);
        if (Number.isNaN(hour) || Number.isNaN(minute)) return null;
        const now = new Date();
        return new Date(now.getFullYear(), now.getMonth(), now.getDate(), hour, minute, 0, 0);
    }

    function getRaceJumpDate(race) {
        if (!race) return null;
        if (race.jump_dt_iso) {
            const dt = new Date(race.jump_dt_iso);
            if (!isNaN(dt.getTime())) return dt;
        }
        return parseJumpTimeToDate(race.jump_time || "");
    }

    function formatCountdown(secs) {
        if (secs == null) return "—";
        if (secs < 0)    return "Jumped";
        if (secs < 60)   return `${secs}s`;
        if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
        return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
    }

    function getSecondsNow(race) {
        const dt = getRaceJumpDate(race);
        if (!dt) return null;
        return Math.floor((dt.getTime() - Date.now()) / 1000);
    }

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    function normaliseCode(code) {
        const raw = String(code || "GREYHOUND").toUpperCase();
        if (raw === "THOROUGHBRED") return "HORSE";
        return raw;
    }

    function formatTrack(slug) {
        if (!slug) return "—";
        return slug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    }

    // -------------------------------------------------------
    // Countdown chip
    // -------------------------------------------------------

    function updateCountdownChip() {
        const chip = q("liveCountdownChip");
        if (!chip || !liveRace) return;
        const secs = getSecondsNow(liveRace);
        chip.textContent = formatCountdown(secs);

        if (secs == null)    { chip.className = "race-countdown-chip"; chip.style.color = "var(--text-soft)"; chip.style.background = "var(--bg-3)"; }
        else if (secs < 120) { chip.className = "race-countdown-chip countdown-imminent"; chip.style.color = "var(--red-1)"; chip.style.background = "rgba(255,31,31,0.12)"; }
        else if (secs < 600) { chip.className = "race-countdown-chip countdown-near";     chip.style.color = "var(--amber)"; chip.style.background = "rgba(255,179,71,0.12)"; }
        else                 { chip.className = "race-countdown-chip"; chip.style.color = "var(--text)"; chip.style.background = "var(--bg-3)"; }
    }

    // -------------------------------------------------------
    // Render Race Header
    // -------------------------------------------------------

    function renderRaceHeader() {
        if (!liveRace) {
            setText("liveTrack", "No race selected");
            setText("liveRaceNum", "");
            setText("liveRaceName", "");
            setText("liveCode", "—");
            setText("liveDistance", "—");
            setText("liveGrade", "—");
            setText("liveCondition", "—");
            setText("liveJump", "—");
            updateCountdownChip();
            return;
        }

        const code = normaliseCode(liveRace.code);
        setText("liveTrack", formatTrack(liveRace.track));
        setText("liveRaceNum", liveRace.race_num ? `R${liveRace.race_num}` : "R—");
        setText("liveRaceName", liveRace.race_name || liveRace.grade || "");
        setText("liveDistance", liveRace.distance ? `${liveRace.distance}m` : "—");
        setText("liveGrade", liveRace.grade || "—");
        setText("liveCondition", liveRace.track_condition || liveRace.condition || "—");

        // Jump time display
        let jumpDisplay = "—";
        if (liveRace.jump_dt_iso) {
            const dt = new Date(liveRace.jump_dt_iso);
            if (!isNaN(dt.getTime())) {
                jumpDisplay = dt.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", timeZone: _AEST });
            }
        } else if (liveRace.jump_time) {
            jumpDisplay = liveRace.jump_time;
        }
        setText("liveJump", jumpDisplay);

        // Code chip
        const codeChip = q("liveCode");
        if (codeChip) {
            codeChip.textContent = code;
            codeChip.className = "race-code-chip race-meta-chip code-chip-" + code.toLowerCase();
        }

        updateCountdownChip();
    }

    // -------------------------------------------------------
    // Form string coloring
    // -------------------------------------------------------

    function colorFormString(form) {
        if (!form) return "—";
        return form.split("").map(c => {
            if ("123".includes(c)) return `<span class="form-win">${c}</span>`;
            if ("FW".includes(c))  return `<span class="form-fail">${c}</span>`;
            if (c === "V")         return `<span class="form-mid">${c}</span>`;
            return `<span class="form-dim">${c}</span>`;
        }).join("");
    }

    // -------------------------------------------------------
    // Build Runner Rows
    // -------------------------------------------------------

    function buildRunnerRows(runners, analysis) {
        const winProbs = analysis?.all_runners || [];
        const probMap = {};
        for (const r of winProbs) {
            probMap[r.number ?? r.box] = r.win_prob;
        }

        // Determine rank order by model_rank or win_prob
        const ranked = [...runners]
            .filter(r => !r.scratched)
            .sort((a, b) => {
                const aProb = probMap[a.number ?? a.box_num] ?? (a.price > 0 ? 100 / a.price : 0);
                const bProb = probMap[b.number ?? b.box_num] ?? (b.price > 0 ? 100 / b.price : 0);
                return bProb - aProb;
            });
        const rankMap = {};
        ranked.forEach((r, i) => { rankMap[r.number ?? r.box_num] = i + 1; });

        const tbody = q("formGuideRows");
        if (!tbody) return;

        const runnerSelect = q("qbRunner");
        if (runnerSelect) {
            runnerSelect.innerHTML = `<option value="">Select runner…</option>` +
                runners.map(r => `<option value="${r.name || ''}" data-odds="${r.price || r.win_odds || ''}">${r.name || '—'}</option>`).join("");
        }

        tbody.innerHTML = runners.map(r => {
            const isScratched = r.scratched;
            const boxNum = r.box_num ?? r.box ?? r.number ?? "?";
            const odds = r.price || r.win_odds;
            const impliedProb = odds > 0 ? (100 / odds) : null;
            const aiProb = probMap[r.number ?? r.box_num];
            const winProb = aiProb ?? impliedProb ?? null;
            const rank = rankMap[r.number ?? r.box_num];
            const formStr = r.form_string || r.form || "";
            const careerStr = r.career || "";

            let rankBadge = "";
            if (isScratched) {
                rankBadge = `<div class="rank-badge rank-scr">SCR</div>`;
            } else if (rank === 1) {
                rankBadge = `<div class="rank-badge rank-1">1st</div>`;
            } else if (rank === 2) {
                rankBadge = `<div class="rank-badge rank-2">2nd</div>`;
            } else if (rank === 3) {
                rankBadge = `<div class="rank-badge rank-3">3rd</div>`;
            } else if (rank) {
                rankBadge = `<div class="rank-text">${rank}th</div>`;
            } else {
                rankBadge = `<div class="rank-text">—</div>`;
            }

            const probPct = winProb != null ? Math.min(100, Math.max(0, winProb)).toFixed(1) : null;
            const probBarWidth = winProb != null ? Math.min(100, Math.max(0, winProb)) : 0;
            const impStr = impliedProb != null ? `${impliedProb.toFixed(1)}% imp` : "";
            const trainerJockey = [r.trainer ? `T: ${r.trainer}` : null, r.jockey ? `J: ${r.jockey}` : null]
                .filter(Boolean).join("  ");

            return `
                <tr class="runner-row${isScratched ? " scratched" : ""}" data-runner-name="${r.name || ''}" data-runner-odds="${odds || ''}" data-navigate="runner">
                    <td class="col-box"><div class="box-num">${boxNum}</div></td>
                    <td class="col-runner">
                        <div class="runner-name"${isScratched ? ' style="text-decoration:line-through"' : ''}>${r.name || "—"}</div>
                        ${trainerJockey ? `<div class="runner-meta">${trainerJockey}</div>` : ""}
                    </td>
                    <td class="col-form">
                        <div class="form-string">${colorFormString(formStr)}</div>
                        ${careerStr ? `<div class="form-career">${careerStr}</div>` : ""}
                    </td>
                    <td class="col-odds">
                        <div class="odds-value">${odds ? `$${parseFloat(odds).toFixed(2)}` : "—"}</div>
                        ${impStr ? `<div class="odds-imp">${impStr}</div>` : ""}
                    </td>
                    <td class="col-prob">
                        ${probPct != null ? `
                            <div class="prob-bar-wrap"><div class="prob-bar" style="width:${probBarWidth}%"></div></div>
                            <div class="prob-text">${probPct}%</div>
                        ` : '<div class="prob-text" style="color:var(--text-dim)">—</div>'}
                    </td>
                    <td class="col-rank">${rankBadge}</td>
                </tr>
            `;
        }).join("");

        setText("formGuideMeta", `${runners.length} runner${runners.length !== 1 ? "s" : ""}`);
    }

    // -------------------------------------------------------
    // Analysis Panel
    // -------------------------------------------------------

    function renderAnalysis() {
        const signal = liveSignal?.signal || liveAnalysis?.signal || "—";
        const decision = liveAnalysis?.decision || "—";

        // Signal display
        const sigEl = q("analysisSignal");
        if (sigEl) {
            sigEl.textContent = String(signal).toUpperCase();
            const s = String(signal).toUpperCase();
            sigEl.className = "analysis-signal signal-" + (
                s === "SNIPER" ? "sniper" :
                s === "VALUE"  ? "value"  :
                s === "GEM"    ? "gem"    :
                s === "WATCH"  ? "watch"  :
                s === "RISK"   ? "risk"   :
                s === "NO_BET" ? "no-bet" : "no-bet"
            );
        }

        // Decision display
        const decEl = q("analysisDecision");
        if (decEl) {
            decEl.textContent = String(decision).toUpperCase();
        }

        setText("analysisPace", liveAnalysis?.pace_type || "—");
        setText("analysisShape", liveAnalysis?.race_shape || liveAnalysis?.beneficiary || "—");
        setText("analysisCondition", liveRace?.track_condition || liveRace?.condition || "—");
        setText("analysisWeather", liveAnalysis?.weather || "—");
        setText("analysisConfidence", liveAnalysis?.confidence || liveSignal?.confidence || "—");
        setText("analysisEV", liveSignal?.ev ?? liveAnalysis?.ev ?? "—");
    }

    // -------------------------------------------------------
    // Quick Bet
    // -------------------------------------------------------

    function calcReturns() {
        const stake = parseFloat(q("qbStake")?.value) || 0;
        const odds  = parseFloat(q("qbOdds")?.value)  || 0;
        const ret   = odds > 0 ? (stake * odds).toFixed(2) : null;
        const el = q("qbReturns");
        if (el) el.textContent = ret && parseFloat(ret) > 0 ? `$${ret}` : "—";
    }

    function selectRunnerRow(rowEl) {
        if (rowEl.classList.contains("scratched")) return;
        document.querySelectorAll(".runner-row.selected").forEach(r => r.classList.remove("selected"));
        rowEl.classList.add("selected");

        const name = rowEl.dataset.runnerName;
        const odds = rowEl.dataset.runnerOdds;

        const runnerSel = q("qbRunner");
        if (runnerSel) {
            for (const opt of runnerSel.options) {
                if (opt.value === name) { runnerSel.value = name; break; }
            }
        }
        if (q("qbOdds") && odds) q("qbOdds").value = odds;
        calcReturns();
    }

    async function placeBet() {
        const raceUid = getRaceUid();
        const runner  = q("qbRunner")?.value || "";
        const odds    = q("qbOdds")?.value   || "";
        const stake   = q("qbStake")?.value  || "";
        const betType = q("qbType")?.value   || "WIN";

        if (!raceUid || !runner || !odds || !stake) return;

        try {
            await api("/api/betting/place", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ race_uid: raceUid, runner, odds, stake, bet_type: betType })
            });
        } catch (e) {
            console.error("Place bet failed:", e);
        }
    }

    // -------------------------------------------------------
    // Simulation
    // -------------------------------------------------------

    async function runSimulation() {
        const raceUid = getRaceUid();
        if (!raceUid) return;

        const idle = q("simIdle");
        const results = q("simResults");
        if (idle)    idle.style.display = "none";
        if (results) results.style.display = "none";

        try {
            const data = await api(`/api/live/watch-sim/${encodeURIComponent(raceUid)}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" }
            });

            const sim = data?.simulation;
            if (!sim) {
                if (idle) { idle.textContent = data?.error || "Simulation not available."; idle.style.display = "block"; }
                return;
            }

            const summary = q("simSummary");
            if (summary) summary.textContent = sim.simulation_summary || "";

            const list = q("simRunnerList");
            if (list) {
                const runners = sim.runners || [];
                list.innerHTML = runners.map(r => {
                    const wp = r.win_pct ?? 0;
                    return `
                        <div class="sim-runner-row">
                            <span class="sim-runner-name">${r.name || "—"}</span>
                            <div class="sim-win-bar-wrap"><div class="sim-win-bar" style="width:${Math.min(100, wp)}%"></div></div>
                            <span class="sim-win-pct">${wp}%</span>
                        </div>
                    `;
                }).join("");
            }

            if (results) results.style.display = "block";
        } catch (e) {
            console.error("Simulation failed:", e);
            if (idle) { idle.textContent = "Simulation not yet available."; idle.style.display = "block"; }
        }
    }

    // -------------------------------------------------------
    // Prev / Next race navigation
    // -------------------------------------------------------

    async function loadMeetingRaces() {
        if (!liveRace) return;
        try {
            const data = await api("/api/home/board");
            const items = Array.isArray(data.items) ? data.items : [];
            const track = liveRace.track;
            const code  = (liveRace.code || "").toUpperCase();
            allMeetingRaces = items
                .filter(i => i.track === track && (i.code || "").toUpperCase() === code)
                .sort((a, b) => (a.race_num || 0) - (b.race_num || 0));
        } catch (e) {
            allMeetingRaces = [];
        }
    }

    function navigateRace(direction) {
        if (!liveRace || !allMeetingRaces.length) return;
        const idx = allMeetingRaces.findIndex(r => r.race_uid === liveRace.race_uid);
        const nextIdx = idx + direction;
        if (nextIdx < 0 || nextIdx >= allMeetingRaces.length) return;
        const next = allMeetingRaces[nextIdx];
        if (next?.race_uid) {
            window.location.href = `/live?race_uid=${encodeURIComponent(next.race_uid)}`;
        }
    }

    // -------------------------------------------------------
    // Load race data
    // -------------------------------------------------------

    async function loadLiveRace() {
        const raceUid = getRaceUid();
        if (!raceUid) {
            renderRaceHeader();
            renderAnalysis();
            return;
        }

        try {
            const data = await api(`/api/live/race/${encodeURIComponent(raceUid)}`);
            liveRace     = data.race     || null;
            liveAnalysis = data.analysis || null;
            liveSignal   = data.signal   || null;

            renderRaceHeader();
            renderAnalysis();

            const runners = liveAnalysis?.all_runners || liveRace?.runners || [];
            if (runners.length) {
                buildRunnerRows(runners, liveAnalysis);
            } else {
                const tbody = q("formGuideRows");
                if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="board-empty">No runner data.</td></tr>`;
                setText("formGuideMeta", "— runners");
            }

            await loadMeetingRaces();
        } catch (error) {
            console.error("Live race load failed:", error);
            setText("liveTrack", "Failed to load race");
        }
    }

    // -------------------------------------------------------
    // Boot
    // -------------------------------------------------------

    document.addEventListener("DOMContentLoaded", () => {
        // Countdown loop
        countdownTimer = setInterval(updateCountdownChip, 1000);

        // Prev/Next nav
        const prevBtn = q("livePrevRace");
        const nextBtn = q("liveNextRace");
        if (prevBtn) prevBtn.addEventListener("click", () => navigateRace(-1));
        if (nextBtn) nextBtn.addEventListener("click", () => navigateRace(+1));

        // Sim button
        const simBtn = q("liveRunSimBtn");
        if (simBtn) simBtn.addEventListener("click", runSimulation);

        // Quick bet
        const stakeIn = q("qbStake");
        const oddsIn  = q("qbOdds");
        if (stakeIn) stakeIn.addEventListener("input", calcReturns);
        if (oddsIn)  oddsIn.addEventListener("input", calcReturns);

        const placeBtn = q("qbPlaceBtn");
        if (placeBtn) placeBtn.addEventListener("click", placeBet);

        // Event delegation for runner row selection
        document.addEventListener("click", (e) => {
            const row = e.target.closest("[data-navigate='runner']");
            if (row) selectRunnerRow(row);
        });

        loadLiveRace();
    });
})();
