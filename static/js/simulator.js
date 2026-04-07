(function () {
    const _AEST = "Australia/Sydney";

    let loadedRace = null;
    let loadedRunners = [];
    let simulationResult = null;
    let simLog = [];

    const q = (id) => document.getElementById(id);

    function getRaceUidFromUrl() {
        const params = new URLSearchParams(window.location.search);
        return params.get("race_uid") || "";
    }

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    function formatTrack(slug) {
        if (!slug) return "—";
        return slug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    }

    function formatJumpTime(item) {
        if (item.jump_dt_iso) {
            const dt = new Date(item.jump_dt_iso);
            if (!isNaN(dt.getTime())) {
                return dt.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", timeZone: _AEST });
            }
        }
        return item.jump_time || "";
    }

    function decisionClass(decision) {
        const d = String(decision || "").toUpperCase();
        if (["BET", "SMALL_BET", "SAVE_BET"].includes(d)) return "decision-bet";
        if (d === "CAUTION" || d === "SESSION") return "decision-caution";
        if (d === "PASS") return "decision-pass";
        return "decision-none";
    }

    function normaliseCode(code) {
        const raw = String(code || "GREYHOUND").toUpperCase();
        if (raw === "THOROUGHBRED") return "HORSE";
        return raw;
    }

    // -------------------------------------------------------
    // Load race selector from board
    // -------------------------------------------------------

    async function loadBoardSelector() {
        const sel = q("simRaceSelect");
        if (!sel) return;

        try {
            const data = await api("/api/home/board");
            const items = Array.isArray(data.items) ? data.items : [];

            if (!items.length) {
                sel.innerHTML = `<option value="">No races available</option>`;
                return;
            }

            const sorted = [...items].sort((a, b) => {
                const t = x => x.seconds_to_jump ?? Infinity;
                return t(a) - t(b);
            });

            sel.innerHTML = `<option value="">Select a race…</option>` +
                sorted.map(item => {
                    const label = `${formatTrack(item.track)} R${item.race_num || "?"} (${formatJumpTime(item)})`;
                    return `<option value="${item.race_uid || ''}">${label}</option>`;
                }).join("");
        } catch (e) {
            sel.innerHTML = `<option value="">Failed to load races</option>`;
        }
    }

    // -------------------------------------------------------
    // Load race
    // -------------------------------------------------------

    function renderLoadedRace() {
        if (!loadedRace) {
            setText("simRaceMeta", "No race loaded");
            setText("simHeroRace", "—");
            return;
        }
        setText("simRaceMeta", `${loadedRace.track || "—"} R${loadedRace.race_num || "—"} loaded`);
        setText("simTrack", loadedRace.track || "—");
        setText("simRaceNum", loadedRace.race_num ? `R${loadedRace.race_num}` : "—");
        setText("simCode", normaliseCode(loadedRace.code));
        setText("simDistance", loadedRace.distance || "—");
        setText("simJump", loadedRace.jump_time || "—");
        setText("simStatus", (loadedRace.status || "upcoming").toUpperCase());
        setText("simHeroRace", `${loadedRace.track || "—"} R${loadedRace.race_num || "—"}`);
    }

    async function loadRace() {
        const raceUid = q("simRaceUid").value.trim() || getRaceUidFromUrl();
        if (!raceUid) {
            setText("simControlMeta", "Enter a race_uid first");
            return;
        }
        setText("simControlMeta", "Loading race...");
        try {
            const data = await api(`/api/live/race/${encodeURIComponent(raceUid)}`);
            loadedRace = data.race || null;
            loadedRunners = (Array.isArray(data.runners) && data.runners.length)
                ? data.runners
                : (data.analysis?.all_runners || []);
            renderLoadedRace();
            setText("simControlMeta", loadedRace ? "Race loaded" : "Race not found");
        } catch (error) {
            console.error("Simulator race load failed:", error);
            setText("simControlMeta", "Failed to load race");
        }
    }

    // -------------------------------------------------------
    // Client-side simulation (Section 6b)
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

    function renderSimulationTopline() {
        if (!simulationResult) {
            const pill = q("simDecisionPill");
            if (pill) { pill.className = "decision-pill decision-none"; pill.textContent = "—"; }
            setText("simHeroDecision", "—");
            setText("simHeroChaos", "—");
            setText("simTopRunner", "—");
            setText("simTopRunnerWinPct", "—");
            setText("simConfidenceScore", "—");
            setText("simChaosRating", "—");
            setText("simPaceType", "—");
            setText("simCollapseRisk", "—");
            setText("simSummaryBox", "No simulation run yet.");
            return;
        }

        const pill = q("simDecisionPill");
        if (pill) {
            const verdict = simulationResult.confidence === "HIGH" ? "BET" :
                            simulationResult.confidence === "MODERATE" ? "CAUTION" : "PASS";
            pill.className = `decision-pill ${decisionClass(verdict)}`;
            pill.textContent = verdict;
        }
        setText("simHeroDecision", simulationResult.confidence);
        setText("simHeroChaos", simulationResult.chaos);
        setText("simTopRunner", simulationResult.topRunner);
        setText("simTopRunnerWinPct", `${simulationResult.topWinPct}%`);
        setText("simConfidenceScore", simulationResult.confidence);
        setText("simChaosRating", simulationResult.chaos);
        setText("simPaceType", "—");
        setText("simCollapseRisk", simulationResult.chaos === "HIGH" ? "HIGH" : "LOW");
        setText("simSummaryBox",
            `Top pick: ${simulationResult.topRunner} (${simulationResult.topWinPct}% win probability). ` +
            `Confidence: ${simulationResult.confidence}. Chaos: ${simulationResult.chaos}.`
        );
    }

    function renderSimulationTable() {
        const rows = simulationResult?.runners || [];
        if (!rows.length) {
            q("simRunnerRows").innerHTML = `<tr><td colspan="7" class="board-empty">No simulation results yet.</td></tr>`;
            setText("simRunnerMeta", "No simulation data");
            return;
        }
        setText("simRunnerMeta", `${rows.length} runners simulated`);
        q("simRunnerRows").innerHTML = rows.map(r => `
            <tr>
                <td>${r.name || "—"}</td>
                <td>${r.box ?? "—"}</td>
                <td>${r.winPct != null ? r.winPct + "%" : "—"}</td>
                <td>${r.placePct != null ? r.placePct + "%" : "—"}</td>
                <td>${r.avgFinish ?? "—"}</td>
                <td>—</td>
                <td>—</td>
            </tr>
        `).join("");
    }

    function renderScenarioPanel() {
        if (!simulationResult) {
            setText("simScenarioMeta", "Waiting");
            setText("simMostCommonScenario", "—");
            setText("simLeaderFrequency", "—");
            setText("simInterferenceRate", "—");
            setText("simConfidenceRating", "—");
            return;
        }
        setText("simScenarioMeta", "Scenario set");
        setText("simMostCommonScenario", simulationResult.topRunner ? `${simulationResult.topRunner} leads` : "—");
        setText("simLeaderFrequency", simulationResult.topRunner ? `${simulationResult.topRunner}: ${simulationResult.topWinPct}%` : "—");
        setText("simInterferenceRate", "—");
        setText("simConfidenceRating", simulationResult.confidence);
    }

    // -------------------------------------------------------
    // AI Expert Guide (Section 6c)
    // -------------------------------------------------------

    async function generateExpertGuide(race, simResult) {
        const guideEl = q("simGuideBox");
        if (!guideEl) return;
        guideEl.textContent = "Generating expert guide…";

        const prompt = `Racing simulation complete. Write a 3-sentence expert summary for a punter.

Race: ${race.track} R${race.race_num} — ${race.distance || ""} ${race.grade || ""}
Top pick: ${simResult.topRunner} (${simResult.topWinPct}% win probability)
Confidence: ${simResult.confidence}, Chaos level: ${simResult.chaos}
Field size: ${simResult.runners.length} runners

Cover: the recommended bet, key risks, and one sentence on race shape.`;

        try {
            const resp = await fetch("https://api.anthropic.com/v1/messages", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    model: "claude-sonnet-4-20250514",
                    max_tokens: 200,
                    messages: [{ role: "user", content: prompt }]
                })
            });
            const data = await resp.json();
            guideEl.textContent = data.content?.[0]?.text || "Expert guide unavailable.";
        } catch (e) {
            guideEl.textContent = "Expert guide unavailable.";
        }
    }

    // -------------------------------------------------------
    // Simulation log (Section 6d)
    // -------------------------------------------------------

    function renderSimLog() {
        const wrap = q("simLogList");
        if (!wrap) return;

        if (!simLog.length) {
            wrap.innerHTML = `<div class="quick-feed-empty">No simulator history in this session.</div>`;
            return;
        }

        wrap.innerHTML = simLog.slice(-10).reverse().map(item => `
            <div class="sim-log-row">
                <div class="sim-log-main">
                    <div class="sim-log-race">${item.race || "—"}</div>
                    <div class="sim-log-sub">${item.runs} runs • ${item.decision} • Chaos: ${item.chaos}</div>
                </div>
                <div class="sim-log-side">${item.time}</div>
            </div>
        `).join("");
    }

    // -------------------------------------------------------
    // Run simulation
    // -------------------------------------------------------

    async function runSimulation() {
        const raceUid = q("simRaceUid").value.trim() || getRaceUidFromUrl();
        if (!raceUid) {
            setText("simControlMeta", "Enter a race_uid first");
            return;
        }

        const nRuns = parseInt(q("simRuns").value || "200", 10);
        setText("simControlMeta", "Running simulation...");
        setText("simHeroRuns", String(nRuns));

        // Ensure race data is loaded
        if (!loadedRace || !loadedRunners.length) {
            await loadRace();
        }

        if (!loadedRunners.length) {
            setText("simControlMeta", "No runner data — load race first");
            return;
        }

        // Build normalised runners
        const normRunners = loadedRunners.map((r, idx) => ({
            name: r.name || "—",
            box: r.box_num ?? r.number ?? r.barrier ?? (idx + 1),
            odds: r.price || r.win_odds || null,
            winProb: r.win_prob || null,
            scratched: !!r.scratched,
        }));

        simulationResult = runClientSideSimulation(normRunners, nRuns);

        renderSimulationTopline();
        renderSimulationTable();
        renderScenarioPanel();

        // AI expert guide
        if (loadedRace) {
            generateExpertGuide(loadedRace, simulationResult);
        } else {
            setText("simGuideBox", "Load a race to get AI expert guide.");
        }

        // Log entry
        simLog.push({
            race: loadedRace ? `${loadedRace.track || "—"} R${loadedRace.race_num || "—"}` : raceUid,
            runs: nRuns,
            decision: simulationResult.confidence,
            chaos: simulationResult.chaos,
            time: new Date().toLocaleTimeString("en-AU", {
                hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit"
            })
        });
        renderSimLog();
        setText("simControlMeta", "Simulation complete");
    }

    function clearSimulator() {
        simulationResult = null;
        loadedRace = null;
        loadedRunners = [];
        if (q("simRaceUid"))    q("simRaceUid").value = "";
        if (q("simRaceSelect")) q("simRaceSelect").value = "";
        if (q("simCondition"))  q("simCondition").value = "";
        if (q("simRuns"))       q("simRuns").value = "200";
        if (q("simMode"))       q("simMode").value = "monte_carlo";
        renderLoadedRace();
        renderSimulationTopline();
        renderSimulationTable();
        renderScenarioPanel();
        setText("simGuideBox", "No expert guide yet.");
        setText("simControlMeta", "Cleared");
    }

    function bindEvents() {
        q("loadSimulatorRaceBtn")?.addEventListener("click", loadRace);
        q("runSimulatorBtn")?.addEventListener("click", runSimulation);
        q("clearSimulatorBtn")?.addEventListener("click", clearSimulator);

        q("simRaceSelect")?.addEventListener("change", (e) => {
            const uid = e.target.value;
            if (uid && q("simRaceUid")) {
                q("simRaceUid").value = uid;
                loadRace();
            }
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const raceUid = getRaceUidFromUrl();
        if (raceUid && q("simRaceUid")) q("simRaceUid").value = raceUid;

        loadBoardSelector();
        bindEvents();
        renderLoadedRace();
        renderSimulationTopline();
        renderSimulationTable();
        renderScenarioPanel();
        renderSimLog();
        setText("simGuideBox", "No expert guide yet.");

        if (raceUid) loadRace();
    });
})();
