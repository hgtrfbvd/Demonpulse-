(function () {
    let liveRace = null;
    let liveAnalysis = null;
    let liveSignal = null;
    let liveSimulation = null;
    let countdownTimer = null;
    let raceLocked = false;

    const q = (id) => document.getElementById(id);

    function getRaceUid() {
        const params = new URLSearchParams(window.location.search);
        return params.get("race_uid") || "";
    }

    function parseJumpTimeToDate(jumpTime) {
        if (!jumpTime || typeof jumpTime !== "string") return null;

        // ISO datetime strings: "2026-04-07T10:30:00+10:00", "2026-04-07T10:30:00Z", etc.
        if (/^\d{4}-\d{2}-\d{2}T/.test(jumpTime) || /^\d{4}-\d{2}-\d{2} /.test(jumpTime)) {
            const dt = new Date(jumpTime);
            return isNaN(dt.getTime()) ? null : dt;
        }

        // HH:MM or HH:MM:SS — interpreted as local time for today
        const parts = jumpTime.split(":");
        if (parts.length < 2) return null;

        const hour = parseInt(parts[0], 10);
        const minute = parseInt(parts[1], 10);

        if (Number.isNaN(hour) || Number.isNaN(minute)) return null;

        const now = new Date();
        return new Date(now.getFullYear(), now.getMonth(), now.getDate(), hour, minute, 0, 0);
    }

    function formatCountdownText(jumpTime) {
        // Accept Date object or string
        const target = (jumpTime instanceof Date) ? jumpTime : parseJumpTimeToDate(jumpTime);
        if (!target) return "—";

        const diffSeconds = Math.floor((target.getTime() - Date.now()) / 1000);
        if (diffSeconds <= 0) return "Jumped / due";

        const mins = Math.floor(diffSeconds / 60);
        const secs = diffSeconds % 60;

        if (mins >= 60) {
            const hrs = Math.floor(mins / 60);
            const remMins = mins % 60;
            return `${hrs}h ${remMins}m`;
        }

        return `${mins}m ${String(secs).padStart(2, "0")}s`;
    }

    // Resolve the best available jump datetime for countdown purposes
    function getRaceJumpDate(race) {
        if (!race) return null;
        if (race.jump_dt_iso) {
            const dt = new Date(race.jump_dt_iso);
            if (!isNaN(dt.getTime())) return dt;
        }
        return parseJumpTimeToDate(race.jump_time || "");
    }

    // Format a readable local jump time for display
    function formatJumpTimeDisplay(race) {
        if (!race) return "—";
        if (race.jump_dt_iso) {
            const dt = new Date(race.jump_dt_iso);
            if (!isNaN(dt.getTime())) {
                return dt.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", timeZone: "Australia/Sydney" });
            }
        }
        return race.jump_time || "—";
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

    function decisionClass(decision) {
        const d = String(decision || "").toUpperCase();
        if (["BET", "SMALL_BET", "SAVE_BET"].includes(d)) return "decision-bet";
        if (d === "CAUTION" || d === "SESSION") return "decision-caution";
        if (d === "PASS") return "decision-pass";
        return "decision-none";
    }

    function renderDecisionPill(decision) {
        const el = q("liveDecisionPill");
        if (!el) return;
        el.className = `decision-pill ${decisionClass(decision)}`;
        el.textContent = String(decision || "—").toUpperCase();
    }

    function updateRaceLock() {
        const jumpDate = getRaceJumpDate(liveRace);
        const countdown = jumpDate ? formatCountdownText(jumpDate) : "—";
        const status = String(liveRace?.status || "").toLowerCase();
        raceLocked = countdown === "Jumped / due" || ["closed", "completed", "resulted"].includes(status);

        ["livePlaceBetBtn", "liveBetRaceUid", "liveBetRunner", "liveBetBox", "liveBetOdds", "liveBetStake", "liveBetType"]
            .forEach(id => {
                const el = q(id);
                if (el) el.disabled = raceLocked;
            });

        setText("liveBetMeta", raceLocked ? "Betting locked for this race" : "Auto-fills from selection");
    }

    function renderRaceMeta() {
        if (!liveRace) {
            setText("liveRaceTitle", "No race selected");
            setText("liveRaceMeta", "Open from Home board or use ?race_uid=...");
            return;
        }

        const code = normaliseCode(liveRace.code);
        const jumpDisplay = formatJumpTimeDisplay(liveRace);
        setText("liveRaceTitle", `${liveRace.track || "Unknown"} R${liveRace.race_num || "?"}`);
        setText("liveRaceMeta", `${code} • ${jumpDisplay} • ${liveRace.status || "upcoming"}`);

        setText("liveTrack", liveRace.track || "—");
        setText("liveRaceNum", liveRace.race_num ? `R${liveRace.race_num}` : "—");
        setText("liveDistance", liveRace.distance || "—");
        setText("liveGrade", liveRace.grade || "—");
        setText("liveJump", jumpDisplay);
        setText("liveStatus", String(liveRace.status || "upcoming").toUpperCase());

        setText("liveHeroCode", code);
        setText("liveHeroCountdown", formatCountdownText(getRaceJumpDate(liveRace) || liveRace.jump_time));
        updateRaceLock();
    }

    function autofillBetForm() {
        q("liveBetRaceUid").value = liveRace?.race_uid || getRaceUid() || "";
        q("liveBetRunner").value = liveAnalysis?.selection || liveSignal?.top_runner || "";
        q("liveBetBox").value = liveAnalysis?.box || liveSignal?.top_box || "";
        q("liveBetOdds").value = liveSignal?.top_odds || liveAnalysis?.odds || "";
        if (!q("liveBetStake").value) q("liveBetStake").value = "10";
    }

    function renderAnalysis() {
        const decision = liveAnalysis?.decision || "—";
        const signal = liveSignal?.signal || liveAnalysis?.signal || "—";
        const confidence = liveAnalysis?.confidence || liveSignal?.confidence || "—";
        const ev = liveSignal?.ev ?? liveAnalysis?.ev ?? "—";
        const selection = liveAnalysis?.selection || liveSignal?.top_runner || "—";
        const box = liveAnalysis?.box || liveSignal?.top_box || "—";
        const collapseRisk = liveAnalysis?.collapse_risk || "—";

        setText("liveHeroSignal", String(signal).toUpperCase());
        setText("liveHeroDecision", String(decision).toUpperCase());

        renderDecisionPill(decision);
        setText("liveSelection", selection);
        setText("liveBox", box);
        setText("liveSignal", String(signal).toUpperCase());
        setText("liveConfidence", confidence);
        setText("liveEV", ev);
        setText("liveCollapseRisk", collapseRisk);

        const reason = liveAnalysis?.pass_reason
            || liveAnalysis?.race_shape
            || "No detailed reasoning available yet.";
        setText("liveReasonBox", reason);

        setText("livePace", liveAnalysis?.pace_type || "—");
        setText("livePressure", liveAnalysis?.pressure_score != null ? `${liveAnalysis.pressure_score}/10` : "—");
        setText("liveBeneficiary", liveAnalysis?.beneficiary || "—");
        setText("liveSeparation", liveAnalysis?.separation || "—");
        setText("liveShapeMeta", liveAnalysis ? "Local scoring loaded" : "Pending analysis");

        autofillBetForm();
    }

    function renderFilters() {
        const filters = liveAnalysis?.filters || {};
        const keys = Object.keys(filters);

        if (!keys.length) {
            q("liveFilterList").innerHTML = `<div class="quick-feed-empty">No filter data yet.</div>`;
            return;
        }

        q("liveFilterList").innerHTML = keys.map(key => {
            const row = filters[key] || {};
            return `
                <div class="live-filter-row">
                    <div class="live-filter-key">${key}</div>
                    <div class="live-filter-score">${row.score ?? "—"}</div>
                    <div class="live-filter-action">${row.action ?? "—"}</div>
                </div>
            `;
        }).join("");
    }

    function renderRunners() {
        const runners = liveAnalysis?.all_runners || [];

        if (!runners.length) {
            q("liveRunnerRows").innerHTML = `
                <tr>
                    <td colspan="7" class="board-empty">No race loaded.</td>
                </tr>
            `;
            setText("liveRunnerMeta", "No runner data");
            return;
        }

        setText("liveRunnerMeta", `${runners.length} runners loaded`);

        q("liveRunnerRows").innerHTML = runners.map(r => `
            <tr>
                <td>${r.box ?? "—"}</td>
                <td>${r.name || "—"}</td>
                <td>${r.odds || "—"}</td>
                <td>${r.speed || "—"}</td>
                <td>${r.style || "—"}</td>
                <td>${r.score ?? "—"}</td>
                <td>${r.crash_map || "—"}</td>
            </tr>
        `).join("");
    }

    function renderSimulation() {
        if (!liveSimulation) {
            q("liveSimIdle").style.display = "block";
            q("liveSimResults").style.display = "none";
            setText("liveSimMeta", "Idle");
            return;
        }

        q("liveSimIdle").style.display = "none";
        q("liveSimResults").style.display = "block";

        const top = liveSimulation.top_runner || {};
        setText("liveSimMeta", "Simulation loaded");
        setText("liveSimDecision", liveSimulation.decision || "—");
        setText("liveSimConfidence", liveSimulation.confidence_score ?? "—");
        setText("liveSimChaos", liveSimulation.chaos_rating || "—");
        setText("liveSimTopWin", top.win_pct != null ? `${top.win_pct}%` : "—");
        setText("liveSimSummary", liveSimulation.simulation_summary || "No simulation summary.");

        const runners = liveSimulation.runners || [];
        if (!runners.length) {
            q("liveSimRows").innerHTML = `
                <tr>
                    <td colspan="6" class="board-empty">No simulation rows returned.</td>
                </tr>
            `;
            return;
        }

        q("liveSimRows").innerHTML = runners.map(r => `
            <tr>
                <td>${r.name || "—"}</td>
                <td>${r.barrier_or_box ?? "—"}</td>
                <td>${r.win_pct != null ? `${r.win_pct}%` : "—"}</td>
                <td>${r.place_pct != null ? `${r.place_pct}%` : "—"}</td>
                <td>${r.avg_finish ?? "—"}</td>
                <td>${r.sim_edge ?? "—"}</td>
            </tr>
        `).join("");
    }

    function updateCountdown() {
        if (!liveRace) return;
        setText("liveHeroCountdown", formatCountdownText(getRaceJumpDate(liveRace) || liveRace.jump_time));
        updateRaceLock();
    }

    async function loadLiveRace() {
        const raceUid = getRaceUid();
        if (!raceUid) {
            renderRaceMeta();
            renderAnalysis();
            renderFilters();
            renderRunners();
            renderSimulation();
            return;
        }

        try {
            const data = await api(`/api/live/race/${encodeURIComponent(raceUid)}`);
            liveRace = data.race || null;
            liveAnalysis = data.analysis || null;
            liveSignal = data.signal || null;

            renderRaceMeta();
            renderAnalysis();
            renderFilters();
            renderRunners();
            renderSimulation();
        } catch (error) {
            console.error("Live race load failed:", error);
            setText("liveRaceTitle", "Failed to load race");
            setText("liveRaceMeta", "Check API wiring for /api/live/race/<race_uid>");
        }
    }

    async function runLiveSimulation() {
        const raceUid = getRaceUid();
        if (!raceUid) return;

        setText("liveSimMeta", "Running simulation...");

        try {
            const data = await api(`/api/live/watch-sim/${encodeURIComponent(raceUid)}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" }
            });

            liveSimulation = data.simulation || null;
            renderSimulation();
        } catch (error) {
            console.error("Live simulation failed:", error);
            setText("liveSimMeta", "Simulation failed");
        }
    }

    async function placeLiveBet() {
        if (raceLocked) return;

        const payload = {
            race_uid: q("liveBetRaceUid").value.trim(),
            runner: q("liveBetRunner").value.trim(),
            box_num: q("liveBetBox").value.trim(),
            odds: q("liveBetOdds").value.trim(),
            stake: q("liveBetStake").value.trim(),
            bet_type: q("liveBetType").value
        };

        if (!payload.race_uid || !payload.runner || !payload.odds || !payload.stake) {
            setText("liveBetMeta", "Complete race, runner, odds and stake first");
            return;
        }

        setText("liveBetMeta", "Placing bet...");

        try {
            await api("/api/betting/place", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            setText("liveBetMeta", `Bet placed: ${payload.runner} ${payload.bet_type}`);
        } catch (error) {
            console.error("Place bet failed:", error);
            setText("liveBetMeta", "Bet place failed");
        }
    }

    function clearBetForm() {
        ["liveBetRunner", "liveBetBox", "liveBetOdds", "liveBetStake"].forEach(id => {
            q(id).value = "";
        });
        q("liveBetType").value = "WIN";
        setText("liveBetMeta", raceLocked ? "Betting locked for this race" : "Bet form cleared");
    }

    function openInSimulator() {
        const raceUid = getRaceUid();
        if (!raceUid) return;
        window.location.href = `/simulator?race_uid=${encodeURIComponent(raceUid)}`;
    }

    async function markWatched() {
        const raceUid = getRaceUid();
        if (!raceUid) return;

        try {
            await api("/api/live/mark-watched", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ race_uid: raceUid })
            });
            setText("liveShapeMeta", "Marked watched");
        } catch (error) {
            console.error("Mark watched failed:", error);
            setText("liveShapeMeta", "Mark watched failed");
        }
    }

    function bindEvents() {
        const watchBtn = q("liveWatchSimBtn");
        const refreshBtn = q("liveRefreshBtn");
        const placeBetBtn = q("livePlaceBetBtn");
        const clearBetBtn = q("liveClearBetBtn");
        const sendToSimulatorBtn = q("liveSendToSimulatorBtn");
        const markWatchedBtn = q("liveMarkWatchedBtn");

        if (watchBtn) watchBtn.addEventListener("click", runLiveSimulation);
        if (refreshBtn) refreshBtn.addEventListener("click", loadLiveRace);
        if (placeBetBtn) placeBetBtn.addEventListener("click", placeLiveBet);
        if (clearBetBtn) clearBetBtn.addEventListener("click", clearBetForm);
        if (sendToSimulatorBtn) sendToSimulatorBtn.addEventListener("click", openInSimulator);
        if (markWatchedBtn) markWatchedBtn.addEventListener("click", markWatched);
    }

    function startCountdownLoop() {
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = setInterval(updateCountdown, 1000);
    }

    document.addEventListener("DOMContentLoaded", () => {
        bindEvents();
        startCountdownLoop();
        loadLiveRace();
    });
})();
