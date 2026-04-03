(function () {
    let loadedRace = null;
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

    function decisionClass(decision) {
        const d = String(decision || "").toUpperCase();
        if (["BET", "SMALL_BET", "SAVE_BET"].includes(d)) return "decision-bet";
        if (d === "CAUTION" || d === "SESSION") return "decision-caution";
        if (d === "PASS") return "decision-pass";
        return "decision-none";
    }

    function renderDecisionPill(decision) {
        const el = q("simDecisionPill");
        if (!el) return;
        el.className = `decision-pill ${decisionClass(decision)}`;
        el.textContent = String(decision || "—").toUpperCase();
    }

    function normaliseCode(code) {
        const raw = String(code || "GREYHOUND").toUpperCase();
        if (raw === "THOROUGHBRED") return "HORSE";
        return raw;
    }

    function flagsForRunner(r) {
        const flags = [];
        if (r.is_false_favourite) flags.push("FALSE_FAV");
        if (r.is_hidden_value) flags.push("HIDDEN_VALUE");
        if (r.is_vulnerable) flags.push("VULNERABLE");
        if (r.is_best_map) flags.push("BEST_MAP");
        return flags.length ? flags.join(", ") : "—";
    }

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

    function renderSimulationTopline() {
        if (!simulationResult) {
            renderDecisionPill("—");
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

        const top = simulationResult.top_runner || {};
        renderDecisionPill(simulationResult.decision || "—");
        setText("simHeroDecision", String(simulationResult.decision || "—").toUpperCase());
        setText("simHeroChaos", simulationResult.chaos_rating || "—");

        setText("simTopRunner", top.name || "—");
        setText("simTopRunnerWinPct", top.win_pct != null ? `${top.win_pct}%` : "—");
        setText("simConfidenceScore", simulationResult.confidence_score ?? "—");
        setText("simChaosRating", simulationResult.chaos_rating || "—");
        setText("simPaceType", simulationResult.pace_type || "—");
        setText("simCollapseRisk", simulationResult.collapse_risk || "—");
        setText("simSummaryBox", simulationResult.simulation_summary || "No simulation summary.");
    }

    function renderSimulationTable() {
        const rows = simulationResult?.runners || [];
        if (!rows.length) {
            q("simRunnerRows").innerHTML = `
                <tr>
                    <td colspan="7" class="board-empty">No simulation results yet.</td>
                </tr>
            `;
            setText("simRunnerMeta", "No simulation data");
            return;
        }

        setText("simRunnerMeta", `${rows.length} runners simulated`);

        q("simRunnerRows").innerHTML = rows.map(r => `
            <tr>
                <td>${r.name || "—"}</td>
                <td>${r.barrier_or_box ?? "—"}</td>
                <td>${r.win_pct != null ? `${r.win_pct}%` : "—"}</td>
                <td>${r.place_pct != null ? `${r.place_pct}%` : "—"}</td>
                <td>${r.avg_finish ?? "—"}</td>
                <td>${r.sim_edge ?? "—"}</td>
                <td>${flagsForRunner(r)}</td>
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
        setText("simMostCommonScenario", simulationResult.most_common_scenario || "—");

        const leaderFrequency = simulationResult.leader_frequency || {};
        const leaderKeys = Object.keys(leaderFrequency);
        const leaderText = leaderKeys.length
            ? leaderKeys.slice(0, 3).map(k => `${k}: ${leaderFrequency[k]}%`).join(" | ")
            : "—";

        setText("simLeaderFrequency", leaderText);
        setText("simInterferenceRate", simulationResult.interference_rate ?? "—");
        setText("simConfidenceRating", simulationResult.confidence_rating || "—");
    }

    function renderGuide() {
        if (!simulationResult) {
            setText("simGuideBox", "No expert guide yet.");
            return;
        }

        const lines = [];
        if (simulationResult.projected_race_run) lines.push(simulationResult.projected_race_run);
        if (simulationResult.race_shape_insights) lines.push(simulationResult.race_shape_insights);
        if (simulationResult.final_decision_note) lines.push(simulationResult.final_decision_note);

        setText("simGuideBox", lines.length ? lines.join(" ") : "No expert guide text returned.");
    }

    function renderSimLog() {
        if (!simLog.length) {
            q("simLogList").innerHTML = `<div class="quick-feed-empty">No simulator history in this session.</div>`;
            return;
        }

        q("simLogList").innerHTML = simLog.slice().reverse().map(item => `
            <div class="sim-log-row">
                <div class="sim-log-main">
                    <div class="sim-log-race">${item.race || "—"}</div>
                    <div class="sim-log-sub">${item.runs} runs • ${item.decision} • ${item.chaos}</div>
                </div>
                <div class="sim-log-side">${item.time}</div>
            </div>
        `).join("");
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
            renderLoadedRace();
            setText("simControlMeta", loadedRace ? "Race loaded" : "Race not found");
        } catch (error) {
            console.error("Simulator race load failed:", error);
            setText("simControlMeta", "Failed to load race");
        }
    }

    async function runSimulation() {
        const raceUid = q("simRaceUid").value.trim() || getRaceUidFromUrl();
        if (!raceUid) {
            setText("simControlMeta", "Enter a race_uid first");
            return;
        }

        const nRuns = parseInt(q("simRuns").value || "200", 10);
        const engine = q("simMode").value || "monte_carlo";
        const condition = q("simCondition").value.trim();

        setText("simControlMeta", "Running simulation...");
        setText("simHeroRuns", String(nRuns));

        try {
            const data = await api(`/api/simulator/run/${encodeURIComponent(raceUid)}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    n_runs: nRuns,
                    engine,
                    condition: condition || null
                })
            });

            simulationResult = data.simulation || null;

            renderSimulationTopline();
            renderSimulationTable();
            renderScenarioPanel();
            renderGuide();

            simLog.push({
                race: loadedRace ? `${loadedRace.track || "—"} R${loadedRace.race_num || "—"}` : raceUid,
                runs: nRuns,
                decision: String(simulationResult?.decision || "—").toUpperCase(),
                chaos: simulationResult?.chaos_rating || "—",
                time: new Date().toLocaleTimeString("en-AU", {
                    hour12: false,
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit"
                })
            });
            renderSimLog();

            setText("simControlMeta", "Simulation complete");
        } catch (error) {
            console.error("Simulation run failed:", error);
            setText("simControlMeta", "Simulation failed");
        }
    }

    function clearSimulator() {
        simulationResult = null;
        loadedRace = null;

        q("simRaceUid").value = "";
        q("simCondition").value = "";
        q("simRuns").value = "200";
        q("simMode").value = "monte_carlo";

        renderLoadedRace();
        renderSimulationTopline();
        renderSimulationTable();
        renderScenarioPanel();
        renderGuide();
        setText("simControlMeta", "Cleared");
    }

    function bindEvents() {
        q("loadSimulatorRaceBtn").addEventListener("click", loadRace);
        q("runSimulatorBtn").addEventListener("click", runSimulation);
        q("clearSimulatorBtn").addEventListener("click", clearSimulator);
    }

    document.addEventListener("DOMContentLoaded", () => {
        const raceUid = getRaceUidFromUrl();
        if (raceUid) {
            q("simRaceUid").value = raceUid;
        }

        bindEvents();
        renderLoadedRace();
        renderSimulationTopline();
        renderSimulationTable();
        renderScenarioPanel();
        renderGuide();
        renderSimLog();

        if (raceUid) {
            loadRace();
        }
    });
})();
