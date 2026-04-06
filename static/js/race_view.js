(function () {
    const _AEST = "Australia/Sydney";

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

    // Resolve the best available jump datetime for countdown purposes
    function getRaceJumpDate(race) {
        if (!race) return null;
        if (race.jump_dt_iso) {
            const dt = new Date(race.jump_dt_iso);
            if (!isNaN(dt.getTime())) return dt;
        }
        return parseJumpTimeToDate(race.jump_time || "");
    }

    // Format a readable local jump time for display (AEST)
    function formatJumpTimeDisplay(race) {
        if (!race) return "—";
        if (race.jump_dt_iso) {
            const dt = new Date(race.jump_dt_iso);
            if (!isNaN(dt.getTime())) {
                return dt.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", timeZone: _AEST });
            }
        }
        return race.jump_time || "—";
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

    function renderRaceHeader() {
        if (!raceData) {
            setText("rvRaceTitle", "No race selected");
            setText("rvRaceMeta", "Open from Home or Live board, or use ?race_uid=...");
            const strip = q("rvInfoStrip");
            if (strip) strip.style.display = "none";
            return;
        }

        const code = normaliseCode(raceData.code);
        const jumpDisplay = formatJumpTimeDisplay(raceData);
        setText("rvRaceTitle", `${raceData.track || "Unknown"} R${raceData.race_num || "?"}`);
        setText("rvRaceMeta", `${code} • ${jumpDisplay} • ${(raceData.status || "upcoming").toUpperCase()}`);
        setText("rvHeroCode", code);
        setText("rvHeroStatus", (raceData.status || "upcoming").toUpperCase());
        setText("rvHeroJump", jumpDisplay);
        setText("rvHeroCountdown", formatCountdownText(getRaceJumpDate(raceData) || raceData.jump_time));

        // Populate the race info strip
        const strip = q("rvInfoStrip");
        if (strip) strip.style.display = "";
        setText("rvInfoTrack", raceData.track || "—");
        setText("rvInfoRaceNum", raceData.race_num ? `Race ${raceData.race_num}` : "—");
        setText("rvInfoCode", code);
        setText("rvInfoDistance", raceData.distance || "—");
        setText("rvInfoGrade", raceData.grade || "—");
        setText("rvInfoCondition", raceData.condition || "—");
        setText("rvInfoJump", jumpDisplay);
        setText("rvInfoStatus", (raceData.status || "upcoming").toUpperCase());
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
            tbody.innerHTML = runners.map(r => {
                const box = r.box_num ?? r.box ?? r.barrier ?? r.number ?? "—";
                const name = r.name || r.runner_name || "—";
                const odds = r.price != null ? r.price : (r.odds ?? r.win_odds ?? "—");
                const trainer = r.trainer || r.driver || r.jockey || "—";
                const confidence = r.confidence ?? r.score ?? "—";
                const notes = r.ai_notes || r.notes || "—";
                const status = r.scratched ? "SCR" : (r.status || "OK");
                return `
                    <tr>
                        <td>${box}</td>
                        <td>${name}</td>
                        <td>${odds}</td>
                        <td>${trainer}</td>
                        <td>${confidence}</td>
                        <td>${notes}</td>
                        <td>${status}</td>
                    </tr>
                `;
            }).join("");
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
        setText("rvTruthParsedDt", r.jump_dt_iso || r.jump_time || "—");
        setText("rvTruthTz", r.jump_dt_iso ? "UTC (ISO)" : (r.timezone || "AEST"));
        setText("rvTruthCountry", r.country || "—");
        setText("rvTruthCode", normaliseCode(r.code));
        setText("rvTruthTrack", r.track || "—");
        setText("rvTruthMerge", r.merge_status || "—");
        setText("rvTruthFormfav", r.formfav ? "enriched" : "none");
        setText("rvTruthBoard", r.board_reason || "standard");
    }

    function updateCountdown() {
        if (!raceData) return;
        setText("rvHeroCountdown", formatCountdownText(getRaceJumpDate(raceData) || raceData.jump_time));
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
