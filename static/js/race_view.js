(function () {
    let raceData = null;
    let raceAnalysis = null;
    let countdownTimer = null;
    let truthVisible = false;
    let rawVisible = false;

    const q = (id) => document.getElementById(id);

    function getRaceUid() {
        const params = new URLSearchParams(window.location.search);
        return params.get("race_uid") || "";
    }

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    function normaliseCode(code) {
        const raw = String(code || "").toUpperCase();
        if (raw === "THOROUGHBRED") return "HORSE";
        return raw;
    }

    function parseJumpTimeToDate(jumpTime) {
        if (!jumpTime || typeof jumpTime !== "string") return null;
        const parts = jumpTime.split(":");
        if (parts.length < 2) return null;
        const hour = parseInt(parts[0], 10);
        const minute = parseInt(parts[1], 10);
        if (Number.isNaN(hour) || Number.isNaN(minute)) return null;
        const now = new Date();
        return new Date(now.getFullYear(), now.getMonth(), now.getDate(), hour, minute, 0, 0);
    }

    function formatCountdownText(jumpTime) {
        const target = parseJumpTimeToDate(jumpTime);
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

    function renderRaceHeader() {
        if (!raceData) {
            setText("rvRaceTitle", "No race selected");
            setText("rvRaceMeta", "Open from Home or Live board, or use ?race_uid=...");
            return;
        }

        const code = normaliseCode(raceData.code);
        setText("rvRaceTitle", `${raceData.track || "Unknown"} R${raceData.race_num || "?"}`);
        setText("rvRaceMeta", `${code} • ${raceData.jump_time || "—"} • ${(raceData.status || "upcoming").toUpperCase()}`);
        setText("rvHeroCode", code);
        setText("rvHeroStatus", (raceData.status || "upcoming").toUpperCase());
        setText("rvHeroJump", raceData.jump_time || "—");
        setText("rvHeroCountdown", formatCountdownText(raceData.jump_time));
    }

    function renderRunners() {
        const runners = raceData?.runners || raceAnalysis?.all_runners || [];
        const tbody = q("rvRunnerRows");

        if (!runners.length) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="board-empty">No runners loaded.</td></tr>`;
            setText("rvRunnerMeta", "No runner data");
            return;
        }

        setText("rvRunnerMeta", `${runners.length} runner${runners.length === 1 ? "" : "s"}`);

        if (tbody) {
            tbody.innerHTML = runners.map(r => `
                <tr>
                    <td>${r.box ?? r.barrier ?? r.number ?? "—"}</td>
                    <td>${r.name || r.runner_name || "—"}</td>
                    <td>${r.odds ?? r.win_odds ?? "—"}</td>
                    <td>${r.trainer || r.driver || r.jockey || "—"}</td>
                    <td>${r.confidence ?? r.score ?? "—"}</td>
                    <td>${r.ai_notes || r.notes || "—"}</td>
                    <td>${r.scratched ? "SCR" : (r.status || "OK")}</td>
                </tr>
            `).join("");
        }
    }

    function renderAiPanel() {
        const analysis = raceAnalysis || {};
        setText("rvAiSignal", (analysis.signal || "—").toString().toUpperCase());
        setText("rvAiDecision", (analysis.decision || "—").toString().toUpperCase());
        setText("rvAiConfidence", analysis.confidence ?? "—");
        setText("rvAiSelection", analysis.selection || analysis.top_runner || "—");
        setText("rvAiEv", analysis.ev ?? "—");
        setText("rvReasonBox", analysis.pass_reason || analysis.race_shape || "No AI analysis available for this race.");
        setText("rvAiMeta", analysis ? "Analysis loaded" : "No analysis");
    }

    function renderFormfavPanel() {
        const ff = raceData?.formfav || {};
        setText("rvFfWeather", ff.weather || "—");
        setText("rvFfPace", ff.paceScenario || "—");
        setText("rvFfTrackCond", ff.trackCondition || "—");
        setText("rvFfStartTime", ff.startTime || "—");
        setText("rvFormfavMeta", Object.keys(ff).length > 0 ? "FormFav loaded" : "No FormFav data");
    }

    function renderTruthPanel() {
        const r = raceData || {};
        setText("rvTruthUid", r.race_uid || "—");
        setText("rvTruthSource", r.source || "oddspro");
        setText("rvTruthRawDt", r.raw_datetime || r.jump_time || "—");
        setText("rvTruthParsedDt", r.jump_time || "—");
        setText("rvTruthTz", r.timezone || "AEST");
        setText("rvTruthCountry", r.country || "—");
        setText("rvTruthCode", normaliseCode(r.code));
        setText("rvTruthTrack", r.track || "—");
        setText("rvTruthMerge", r.merge_status || "—");
        setText("rvTruthFormfav", r.formfav ? "enriched" : "none");
        setText("rvTruthBoard", r.board_reason || "standard");
    }

    function updateCountdown() {
        if (!raceData) return;
        setText("rvHeroCountdown", formatCountdownText(raceData.jump_time));
    }

    function renderAll() {
        renderRaceHeader();
        renderRunners();
        renderAiPanel();
        renderFormfavPanel();
        renderTruthPanel();
    }

    async function loadRaceView() {
        const raceUid = getRaceUid();

        if (!raceUid) {
            renderAll();
            return;
        }

        setText("rvRaceTitle", "Loading…");

        try {
            // Load race from board or races API
            const raceResp = await api(`/api/races/${encodeURIComponent(raceUid)}`);
            raceData = raceResp.race || null;

            // Attempt to load analysis from live endpoint
            try {
                const liveResp = await api(`/api/live/race/${encodeURIComponent(raceUid)}`);
                raceAnalysis = liveResp.analysis || null;
                if (!raceData) raceData = liveResp.race || null;
            } catch (_) {
                // analysis not available — that's fine
            }

            renderAll();

            // Populate raw panel
            const rawEl = q("rvRawPre");
            if (rawEl) rawEl.textContent = JSON.stringify(raceData, null, 2);
        } catch (err) {
            console.error("Race View load failed:", err);
            setText("rvRaceTitle", "Failed to load race");
            setText("rvRaceMeta", `Could not load race_uid: ${raceUid}`);
        }
    }

    function bindEvents() {
        const refreshBtn = q("rvRefreshBtn");
        if (refreshBtn) refreshBtn.addEventListener("click", loadRaceView);

        const truthToggle = q("rvTruthToggle");
        if (truthToggle) {
            truthToggle.addEventListener("click", () => {
                truthVisible = !truthVisible;
                const panel = q("rvTruthPanel");
                if (panel) panel.style.display = truthVisible ? "block" : "none";
                truthToggle.textContent = truthVisible ? "Hide Truth Panel" : "Show Truth Panel";
            });
        }

        const rawToggle = q("rvRawToggle");
        if (rawToggle) {
            rawToggle.addEventListener("click", () => {
                rawVisible = !rawVisible;
                const pre = q("rvRawPre");
                if (pre) pre.style.display = rawVisible ? "block" : "none";
                rawToggle.textContent = rawVisible ? "Hide Raw" : "Show Raw";
            });
        }

        const simBtn = q("rvSimulateBtn");
        if (simBtn) {
            simBtn.addEventListener("click", () => {
                const uid = getRaceUid();
                if (uid) window.location.href = `/simulator?race_uid=${encodeURIComponent(uid)}`;
            });
        }

        const betBtn = q("rvSendToBettingBtn");
        if (betBtn) {
            betBtn.addEventListener("click", () => {
                const uid = getRaceUid();
                if (uid) window.location.href = `/betting?race_uid=${encodeURIComponent(uid)}`;
            });
        }

        const backtestBtn = q("rvBacktestBtn");
        if (backtestBtn) {
            backtestBtn.addEventListener("click", () => {
                const uid = getRaceUid();
                if (uid) window.location.href = `/backtesting?race_uid=${encodeURIComponent(uid)}`;
            });
        }

        const saveNoteBtn = q("rvSaveNoteBtn");
        if (saveNoteBtn) {
            saveNoteBtn.addEventListener("click", async () => {
                const uid = getRaceUid();
                const note = (q("rvNotesInput") || {}).value || "";
                if (!uid) {
                    setText("rvNoteMeta", "No race loaded");
                    return;
                }
                setText("rvNoteMeta", "Saving…");
                try {
                    await api("/api/races/" + encodeURIComponent(uid) + "/note", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ note })
                    });
                    setText("rvNoteMeta", "Saved");
                } catch (_) {
                    setText("rvNoteMeta", "Note save not yet wired");
                }
            });
        }
    }

    function startCountdownLoop() {
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = setInterval(updateCountdown, 1000);
    }

    document.addEventListener("DOMContentLoaded", () => {
        bindEvents();
        startCountdownLoop();
        loadRaceView();
    });
})();
