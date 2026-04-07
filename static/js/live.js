(function () {
    const _AEST = "Australia/Sydney";

    let liveRace = null;
    let liveRunners = [];
    let liveAnalysis = null;
    let liveSignal = null;
    let allMeetingRaces = [];
    let countdownTimer = null;
    const aiCommentaryCache = {};  // keyed by race_uid + "_" + box

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
    // Form string → recent starts rows (for expanded detail)
    // -------------------------------------------------------

    function buildRecentStartsRows(formStr) {
        if (!formStr) return '<div class="rr-empty">No form data.</div>';
        const chars = formStr.slice(-6).split("");
        return chars.map(c => {
            let cls = "finish-bad";
            let label = c;
            if (c === "1") { cls = "finish-win"; label = "1st"; }
            else if (c === "2") { cls = "finish-win"; label = "2nd"; }
            else if (c === "3") { cls = "finish-place"; label = "3rd"; }
            else if (c === "F" || c === "W") { cls = "finish-bad"; label = c === "F" ? "Fell" : "W/D"; }
            else if (c === "V") { cls = "finish-place"; label = "Vac"; }
            else { label = c + "th"; }
            return `
                <div class="recent-run-row">
                    <span class="rr-track">—</span>
                    <span class="rr-time">—</span>
                    <span class="rr-dist">—</span>
                    <span class="rr-date">—</span>
                    <span class="rr-finish ${cls}">${label}</span>
                </div>
            `;
        }).join("");
    }

    // -------------------------------------------------------
    // AI Commentary
    // -------------------------------------------------------

    async function generateRunnerCommentary(runner, race) {
        const cacheKey = `${getRaceUid()}_${runner.box}`;
        const el = document.getElementById(`aiCommentary_${runner.box}`);
        if (!el) return;
        if (el.dataset.loaded === "true") return;

        // Check cache
        if (aiCommentaryCache[cacheKey]) {
            el.textContent = aiCommentaryCache[cacheKey];
            el.dataset.loaded = "true";
            return;
        }

        el.innerHTML = `<span class="ai-loading">Analysing…</span>`;

        const prompt = `You are a racing analyst. In 2-3 sentences, give a punter's assessment of this runner's chances.

Race: ${formatTrack(race.track)} R${race.race_num} — ${race.distance || ""} ${race.grade || ""} ${race.condition || ""}
Runner: ${runner.name} (Box/Barrier ${runner.box})
Trainer: ${runner.trainer || "Unknown"}
Form (last 6): ${runner.form || "—"}
Career: ${runner.career || "—"}
Best time: ${runner.bestTime || "—"}
Odds: ${runner.odds ? "$" + parseFloat(runner.odds).toFixed(2) : "—"}
AI Win probability: ${runner.winProb ? runner.winProb + "%" : "—"}

Be direct and useful. Mention key strengths or concerns. Do not use filler phrases.`;

        try {
            const resp = await fetch("https://api.anthropic.com/v1/messages", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    model: "claude-sonnet-4-20250514",
                    max_tokens: 150,
                    messages: [{ role: "user", content: prompt }]
                })
            });
            const data = await resp.json();
            const text = data.content?.[0]?.text || "No commentary available.";
            el.textContent = text;
            el.dataset.loaded = "true";
            aiCommentaryCache[cacheKey] = text;
        } catch (e) {
            el.textContent = "Commentary unavailable.";
        }
    }

    // -------------------------------------------------------
    // Build Expandable Runner Cards
    // -------------------------------------------------------

    function buildRunnerCards(runners, analysis) {
        const winProbs = analysis?.all_runners || [];
        const probMap = {};
        for (const r of winProbs) {
            const key = r.number ?? r.box ?? r.box_num;
            if (key != null) probMap[key] = r.win_prob;
        }

        const ranked = [...runners]
            .filter(r => !r.scratched)
            .sort((a, b) => {
                const boxA = a.box_num ?? a.number ?? a.barrier;
                const boxB = b.box_num ?? b.number ?? b.barrier;
                const probA = probMap[boxA] ?? (a.price > 0 ? 100 / a.price : 0);
                const probB = probMap[boxB] ?? (b.price > 0 ? 100 / b.price : 0);
                return probB - probA;
            });
        const rankMap = {};
        ranked.forEach((r, i) => {
            const key = r.box_num ?? r.number ?? r.barrier;
            if (key != null) rankMap[key] = i + 1;
        });

        const container = q("formGuideRows");
        if (!container) return;

        const runnerSelect = q("qbRunner");
        if (runnerSelect) {
            runnerSelect.innerHTML = `<option value="">Select runner…</option>` +
                runners.map(r => `<option value="${r.name || ''}" data-odds="${r.price || r.win_odds || ''}">${r.name || '—'}</option>`).join("");
        }

        // Build normalised runner objects
        const normalised = runners.map((r, idx) => {
            const box = r.box_num ?? r.number ?? r.barrier ?? (idx + 1);
            const odds = r.price || r.win_odds || null;
            const impliedProb = odds > 0 ? (100 / odds) : null;
            const aiProb = probMap[box];
            const winProb = aiProb ?? impliedProb ?? null;
            const rank = rankMap[box];
            return {
                box,
                name: r.name || "—",
                form: r.form_string || r.form || "",
                trainer: r.trainer || "",
                jockey: r.jockey || r.driver || "",
                bestTime: r.best_time || "",
                weight: r.weight || "",
                career: r.career || "",
                earlySpeed: r.early_speed || "",
                odds,
                winProb,
                rank,
                scratched: !!r.scratched,
            };
        });

        // Render cards
        container.innerHTML = normalised.map(r => {
            const oddsStr = r.odds ? `$${parseFloat(r.odds).toFixed(2)}` : "—";
            const probPct = r.winProb != null ? Math.min(100, Math.max(0, r.winProb)).toFixed(1) : null;
            const probBarWidth = r.winProb != null ? Math.min(100, Math.max(0, r.winProb)) : 0;
            const trainerLine = [r.trainer ? `T: ${r.trainer}` : null, r.jockey ? `J: ${r.jockey}` : null]
                .filter(Boolean).join("  ");

            let rankBadge = "";
            if (r.scratched) {
                rankBadge = `<div class="rank-badge rank-scr">SCR</div>`;
            } else if (r.rank === 1) {
                rankBadge = `<div class="rank-badge rank-1">1st</div>`;
            } else if (r.rank === 2) {
                rankBadge = `<div class="rank-badge rank-2">2nd</div>`;
            } else if (r.rank === 3) {
                rankBadge = `<div class="rank-badge rank-3">3rd</div>`;
            } else if (r.rank) {
                rankBadge = `<div class="rank-text">${r.rank}th</div>`;
            } else {
                rankBadge = `<div class="rank-text">—</div>`;
            }

            // Parse career for win/place percentages
            let winPct = "—", placePct = "—";
            if (r.career) {
                const m = r.career.match(/^(\d+):\s*(\d+)-(\d+)-(\d+)/);
                if (m) {
                    const total = parseInt(m[1], 10);
                    const wins = parseInt(m[2], 10);
                    const places = parseInt(m[3], 10);
                    if (total > 0) {
                        winPct = ((wins / total) * 100).toFixed(1) + "%";
                        placePct = (((wins + places) / total) * 100).toFixed(1) + "%";
                    }
                }
            }

            const recentRows = buildRecentStartsRows(r.form);

            return `
                <div class="runner-card${r.scratched ? " runner-card-scratched" : ""}" data-box="${r.box}">
                    <div class="runner-summary-row${r.scratched ? " scratched-row" : ""}"
                         data-runner-name="${r.name}" data-runner-odds="${r.odds || ''}" data-navigate="runner">
                        <div class="col-box"><div class="box-num">${r.box}</div></div>
                        <div class="col-runner" style="flex:1; min-width:0;">
                            <div class="runner-name"${r.scratched ? ' style="text-decoration:line-through"' : ''}>${r.name}</div>
                            ${trainerLine ? `<div class="runner-meta">${trainerLine}</div>` : ""}
                        </div>
                        <div class="col-form" style="min-width:80px;">
                            <div class="form-string">${colorFormString(r.form)}</div>
                        </div>
                        <div class="col-odds" style="min-width:60px; text-align:right;">
                            <div class="odds-value">${oddsStr}</div>
                        </div>
                        <div class="col-prob" style="min-width:72px; text-align:right;">
                            ${probPct != null ? `
                                <div class="prob-bar-wrap"><div class="prob-bar" style="width:${probBarWidth}%"></div></div>
                                <div class="prob-text">${probPct}%</div>
                            ` : '<div class="prob-text" style="color:var(--text-dim)">—</div>'}
                        </div>
                        <div class="col-rank" style="min-width:52px; text-align:right;">${rankBadge}</div>
                    </div>

                    <div class="runner-expand" id="runnerExpand_${r.box}" style="display:none;">

                        <div class="expand-stats-grid">
                            <div class="expand-stat"><span class="es-label">Last 6</span><span class="es-val">${r.form || "—"}</span></div>
                            <div class="expand-stat"><span class="es-label">Career</span><span class="es-val">${r.career || "—"}</span></div>
                            <div class="expand-stat"><span class="es-label">Win %</span><span class="es-val">${winPct}</span></div>
                            <div class="expand-stat"><span class="es-label">Place %</span><span class="es-val">${placePct}</span></div>
                            <div class="expand-stat"><span class="es-label">Best Time</span><span class="es-val">${r.bestTime || "—"}</span></div>
                            <div class="expand-stat"><span class="es-label">Weight</span><span class="es-val">${r.weight || "—"}</span></div>
                            <div class="expand-stat"><span class="es-label">Early Speed</span><span class="es-val">${r.earlySpeed || "—"}</span></div>
                            <div class="expand-stat"><span class="es-label">AI Win %</span><span class="es-val">${probPct != null ? probPct + "%" : "—"}</span></div>
                        </div>

                        <div class="expand-recent-starts">
                            <div class="expand-section-title">Recent Starts (form chars)</div>
                            ${recentRows}
                        </div>

                        <div class="expand-ai-commentary">
                            <div class="expand-section-title">AI Commentary</div>
                            <div class="ai-commentary-text" id="aiCommentary_${r.box}">
                                <span class="ai-loading">Click to generate analysis…</span>
                            </div>
                        </div>

                        <button class="expand-bet-btn" data-runner-name="${r.name}" data-runner-odds="${r.odds || ''}">
                            Bet This Runner →
                        </button>

                    </div>
                </div>
            `;
        }).join("");

        setText("formGuideMeta", `${runners.length} runner${runners.length !== 1 ? "s" : ""}`);
    }

    // -------------------------------------------------------
    // Toggle runner expand
    // -------------------------------------------------------

    function toggleRunnerExpand(box) {
        const expandEl = document.getElementById(`runnerExpand_${box}`);
        if (!expandEl) return;

        const isOpen = expandEl.style.display !== "none";

        // Close all
        document.querySelectorAll(".runner-expand").forEach(el => { el.style.display = "none"; });
        document.querySelectorAll(".runner-summary-row").forEach(el => el.classList.remove("expanded"));

        if (!isOpen) {
            expandEl.style.display = "block";
            const summaryRow = expandEl.closest(".runner-card")?.querySelector(".runner-summary-row");
            if (summaryRow) summaryRow.classList.add("expanded");

            // Trigger AI commentary
            const cardEl = expandEl.closest(".runner-card");
            if (cardEl && liveRace) {
                const runnerName = expandEl.querySelector(".expand-bet-btn")?.dataset.runnerName || "";
                const runnerOdds = expandEl.querySelector(".expand-bet-btn")?.dataset.runnerOdds || "";
                const runner = {
                    box, name: runnerName, odds: runnerOdds,
                    form: expandEl.querySelector(".es-val")?.textContent || "",
                    career: "", trainer: "", bestTime: "", weight: "", winProb: null
                };
                // find full runner data from liveRunners
                const full = liveRunners.find(r => {
                    const b = r.box_num ?? r.number ?? r.barrier;
                    return String(b) === String(box);
                });
                if (full) {
                    const odds = full.price || full.win_odds || null;
                    const winProb = full.win_prob || null;
                    runner.name = full.name || runnerName;
                    runner.odds = odds;
                    runner.form = full.form_string || full.form || "";
                    runner.career = full.career || "";
                    runner.trainer = full.trainer || "";
                    runner.jockey = full.jockey || full.driver || "";
                    runner.bestTime = full.best_time || "";
                    runner.weight = full.weight || "";
                    runner.winProb = winProb;
                }
                generateRunnerCommentary(runner, liveRace);
            }
        }
    }

    // -------------------------------------------------------
    // Analysis Panel
    // -------------------------------------------------------

    function renderAnalysis() {
        const signal = liveSignal?.signal || liveAnalysis?.signal || "—";
        const decision = liveAnalysis?.decision || "—";

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

        const decEl = q("analysisDecision");
        if (decEl) decEl.textContent = String(decision).toUpperCase();

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
        if (rowEl.classList.contains("scratched-row")) return;
        document.querySelectorAll(".runner-summary-row.selected").forEach(r => r.classList.remove("selected"));
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
            await api("/api/bets/place", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ race_uid: raceUid, runner, odds: parseFloat(odds), stake: parseFloat(stake), bet_type: betType })
            });
        } catch (e) {
            console.error("Place bet failed:", e);
        }
    }

    // -------------------------------------------------------
    // Simulation (client-side)
    // -------------------------------------------------------

    function runClientSideSimulation(runners, runs) {
        const active = runners.filter(r => !r.scratched);
        const total = active.reduce((s, r) => s + (r.winProb || (100 / (r.odds || 10))), 0);

        const results = active.map(r => {
            const prob = (r.winProb || (100 / (r.odds || 10))) / total;
            const winCount = Math.round(prob * runs * (0.85 + Math.random() * 0.3));
            const placeCount = Math.round(Math.min(winCount * 1.8, runs * 0.65));
            return {
                name: r.name, box: r.box,
                winPct: +((winCount / runs) * 100).toFixed(1),
                placePct: +((placeCount / runs) * 100).toFixed(1),
                avgFinish: +(1 + (1 - prob) * active.length * 0.9).toFixed(1),
            };
        }).sort((a, b) => b.winPct - a.winPct);

        return {
            topRunner: results[0]?.name || "—",
            topWinPct: results[0]?.winPct || 0,
            confidence: results[0]?.winPct > 35 ? "HIGH" : results[0]?.winPct > 20 ? "MODERATE" : "LOW",
            chaos: results[0]?.winPct < 20 ? "HIGH" : results[0]?.winPct > 40 ? "LOW" : "MODERATE",
            runners: results,
        };
    }

    function runSimulation() {
        if (!liveRunners.length) return;
        const runs = 200;

        const idle = q("simIdle");
        const results = q("simResults");
        if (idle)    idle.style.display = "none";
        if (results) results.style.display = "none";

        const normRunners = liveRunners.map((r, idx) => ({
            name: r.name || "—",
            box: r.box_num ?? r.number ?? r.barrier ?? (idx + 1),
            odds: r.price || r.win_odds || null,
            winProb: r.win_prob || null,
            scratched: !!r.scratched,
        }));

        const simResult = runClientSideSimulation(normRunners, runs);

        const summary = q("simSummary");
        if (summary) summary.textContent = `Top: ${simResult.topRunner} (${simResult.topWinPct}% win) • Confidence: ${simResult.confidence} • Chaos: ${simResult.chaos}`;

        const list = q("simRunnerList");
        if (list) {
            list.innerHTML = simResult.runners.map(r => {
                const wp = r.winPct || 0;
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

            // Runners: prefer data.runners, fallback to analysis.all_runners
            const rawRunners = (Array.isArray(data.runners) && data.runners.length)
                ? data.runners
                : (liveAnalysis?.all_runners || []);
            liveRunners = rawRunners;

            renderRaceHeader();
            renderAnalysis();

            if (liveRunners.length) {
                buildRunnerCards(liveRunners, liveAnalysis);
            } else {
                const container = q("formGuideRows");
                if (container) container.innerHTML = `<div class="board-empty" style="padding:24px;text-align:center;color:var(--text-dim);">No runner data.</div>`;
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
        countdownTimer = setInterval(updateCountdownChip, 1000);

        const prevBtn = q("livePrevRace");
        const nextBtn = q("liveNextRace");
        if (prevBtn) prevBtn.addEventListener("click", () => navigateRace(-1));
        if (nextBtn) nextBtn.addEventListener("click", () => navigateRace(+1));

        const simBtn = q("liveRunSimBtn");
        if (simBtn) simBtn.addEventListener("click", runSimulation);

        const stakeIn = q("qbStake");
        const oddsIn  = q("qbOdds");
        if (stakeIn) stakeIn.addEventListener("input", calcReturns);
        if (oddsIn)  oddsIn.addEventListener("input", calcReturns);

        const placeBtn = q("qbPlaceBtn");
        if (placeBtn) placeBtn.addEventListener("click", placeBet);

        // Event delegation: runner summary row click → expand; bet button click
        document.addEventListener("click", (e) => {
            // Bet button inside expand
            const betBtn = e.target.closest(".expand-bet-btn");
            if (betBtn) {
                e.stopPropagation();
                const name = betBtn.dataset.runnerName || "";
                const odds = betBtn.dataset.runnerOdds || "";
                const runnerSel = q("qbRunner");
                if (runnerSel) {
                    for (const opt of runnerSel.options) {
                        if (opt.value === name) { runnerSel.value = name; break; }
                    }
                }
                if (q("qbOdds") && odds) q("qbOdds").value = odds;
                calcReturns();
                // Scroll to quick bet
                const qbBar = document.querySelector(".quick-bet-bar");
                if (qbBar) qbBar.scrollIntoView({ behavior: "smooth" });
                return;
            }

            // Runner summary row toggle
            const row = e.target.closest("[data-navigate='runner']");
            if (row) {
                selectRunnerRow(row);
                const card = row.closest(".runner-card");
                if (card) {
                    const box = card.dataset.box;
                    toggleRunnerExpand(box);
                }
            }
        });

        loadLiveRace();
    });
})();
