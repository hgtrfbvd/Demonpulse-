(function () {
    const _AEST = "Australia/Sydney";

    let allBoardItems = [];
    let activeCodeFilter = "ALL";
    let countdownTimer = null;
    let refreshTimer = null;
    let lastSuccessAt = null;

    // -------------------------------------------------------
    // Helpers
    // -------------------------------------------------------

    function normaliseCode(code) {
        const raw = String(code || "GREYHOUND").toUpperCase();
        if (raw === "THOROUGHBRED") return "HORSE";
        return raw;
    }

    function formatTrack(slug) {
        if (!slug) return "—";
        return slug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    }

    function formatCountdown(secs) {
        if (secs == null) return "—";
        if (secs < 0)    return "Jumped";
        if (secs < 60)   return `${secs}s`;
        if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
        return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
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

    function getSecondsToJump(item) {
        if (item.seconds_to_jump != null) return item.seconds_to_jump;
        const dt = item.jump_dt_iso ? new Date(item.jump_dt_iso) : parseJumpTimeToDate(item.jump_time);
        if (!dt) return null;
        return Math.floor((dt.getTime() - Date.now()) / 1000);
    }

    function countdownClass(secs) {
        if (secs == null) return "";
        if (secs < 0)    return "jumped";
        if (secs < 120)  return "imminent";
        if (secs < 600)  return "near";
        return "";
    }

    function chipClass(secs) {
        if (secs == null) return "ntj-upcoming";
        if (secs < 0)    return "ntj-jumped";
        if (secs < 120)  return "ntj-imminent";
        if (secs < 600)  return "ntj-near";
        return "ntj-upcoming";
    }

    function statusClass(secs) {
        if (secs == null)  return "status-upcoming";
        if (secs < -1800)  return "status-awaiting";
        if (secs < 0)      return "status-pending";
        if (secs < 120)    return "status-imminent";
        if (secs < 600)    return "status-near";
        return "status-upcoming";
    }

    function statusLabel(secs) {
        if (secs == null)  return "UPCOMING";
        if (secs < -1800)  return "AWAITING";
        if (secs < 0)      return "PENDING";
        if (secs < 120)    return "IMMINENT";
        if (secs < 600)    return "NEAR";
        return "UPCOMING";
    }

    function codeBadgeClass(code) {
        const c = normaliseCode(code);
        if (c === "GREYHOUND") return "badge-greyhound";
        if (c === "HORSE")     return "badge-horse";
        if (c === "HARNESS")   return "badge-harness";
        return "";
    }

    function codeShort(code) {
        const c = normaliseCode(code);
        if (c === "GREYHOUND") return "GH";
        if (c === "HORSE")     return "HR";
        if (c === "HARNESS")   return "HN";
        return c.slice(0, 2);
    }

    // -------------------------------------------------------
    // Grouping
    // -------------------------------------------------------

    function groupByMeeting(items) {
        const groups = {};
        for (const item of items) {
            const key = `${item.track}_${normaliseCode(item.code)}`;
            if (!groups[key]) {
                groups[key] = { track: item.track, code: normaliseCode(item.code), races: [] };
            }
            groups[key].races.push(item);
        }
        for (const g of Object.values(groups)) {
            g.races.sort((a, b) => (a.race_num || 0) - (b.race_num || 0));
        }
        return Object.values(groups).sort((a, b) => {
            const aMin = Math.min(...a.races.map(r => getSecondsToJump(r) ?? 99999));
            const bMin = Math.min(...b.races.map(r => getSecondsToJump(r) ?? 99999));
            return aMin - bMin;
        });
    }

    // -------------------------------------------------------
    // Render NTJ Strip
    // -------------------------------------------------------

    function renderNtjStrip(items) {
        const strip = document.getElementById("ntjStrip");
        if (!strip) return;

        // Sort by soonest and take first 8
        const sorted = [...items]
            .filter(item => (getSecondsToJump(item) ?? -1) >= -30)
            .sort((a, b) => (getSecondsToJump(a) ?? 99999) - (getSecondsToJump(b) ?? 99999))
            .slice(0, 8);

        if (!sorted.length) {
            strip.innerHTML = `<div class="ntj-chip ntj-upcoming"><span class="ntj-track">No races</span><span class="ntj-time">—</span></div>`;
            return;
        }

        strip.innerHTML = sorted.map(item => {
            const secs = getSecondsToJump(item);
            const cls = chipClass(secs);
            const uid = item.race_uid || "";
            return `
                <div class="ntj-chip ${cls}" data-race-uid="${uid}" data-navigate="race" title="${formatTrack(item.track)} R${item.race_num || '?'}">
                    <span class="ntj-track">${formatTrack(item.track)}</span>
                    <span class="ntj-race">R${item.race_num || "?"}</span>
                    <span class="ntj-time">${formatCountdown(secs)}</span>
                </div>
            `;
        }).join("");
    }

    // -------------------------------------------------------
    // Render Meeting Cards
    // -------------------------------------------------------

    function renderMeetingCards(filteredItems) {
        const board = document.getElementById("raceBoard");
        if (!board) return;

        if (!filteredItems.length) {
            board.innerHTML = `<div class="board-empty" style="padding:32px;text-align:center;color:var(--text-dim);">No races available for this filter.</div>`;
            return;
        }

        const meetings = groupByMeeting(filteredItems);

        board.innerHTML = meetings.map(meeting => {
            const badgeCls = codeBadgeClass(meeting.code);
            const short = codeShort(meeting.code);
            const racesHtml = meeting.races.map(race => {
                const secs = getSecondsToJump(race);
                const cdCls = countdownClass(secs);
                const sCls = statusClass(secs);
                const sLabel = statusLabel(secs);
                const uid = race.race_uid || "";
                const gradeDist = [race.grade, race.distance ? race.distance + "m" : null]
                    .filter(Boolean).join(" • ") || "—";
                return `
                    <div class="race-row" data-race-uid="${uid}" data-navigate="race">
                        <div class="race-row-left">
                            <span class="race-num">R${race.race_num || "?"}</span>
                            <div class="race-details">
                                <span class="race-name">${gradeDist}</span>
                            </div>
                        </div>
                        <div class="race-row-right">
                            <span class="race-countdown ${cdCls}">${formatCountdown(secs)}</span>
                            <span class="race-status-badge ${sCls}">${sLabel}</span>
                            <span class="race-arrow">›</span>
                        </div>
                    </div>
                `;
            }).join("");

            const stateStr = meeting.races[0]?.state || meeting.races[0]?.country || "";
            const subText = [stateStr, meeting.code.charAt(0) + meeting.code.slice(1).toLowerCase()]
                .filter(Boolean).join(" • ");

            return `
                <div class="meeting-card" data-code="${meeting.code}">
                    <div class="meeting-header">
                        <div class="meeting-header-left">
                            <div class="meeting-code-badge ${badgeCls}">${short}</div>
                            <div class="meeting-info">
                                <span class="meeting-name">${formatTrack(meeting.track)}</span>
                                <span class="meeting-sub">${subText}</span>
                            </div>
                        </div>
                        <span class="meeting-race-count">${meeting.races.length} race${meeting.races.length !== 1 ? "s" : ""}</span>
                    </div>
                    <div class="meeting-races">
                        ${racesHtml}
                    </div>
                </div>
            `;
        }).join("");
    }

    // -------------------------------------------------------
    // Filter
    // -------------------------------------------------------

    function getFilteredItems() {
        if (activeCodeFilter === "ALL") return [...allBoardItems];
        return allBoardItems.filter(item => normaliseCode(item.code) === activeCodeFilter);
    }

    function setFilter(code) {
        activeCodeFilter = code;
        document.querySelectorAll(".filter-tab").forEach(btn => {
            btn.classList.toggle("active", btn.dataset.code === code);
        });
        renderBoard();
    }

    function renderBoard() {
        const filtered = getFilteredItems();
        renderNtjStrip(allBoardItems); // NTJ strip always shows all
        renderMeetingCards(filtered);
        updateFilterMeta();
    }

    // -------------------------------------------------------
    // Meta / status dot
    // -------------------------------------------------------

    function updateFilterMeta() {
        const meta = document.getElementById("filterMeta");
        const dot  = document.getElementById("refreshDot");
        if (!meta) return;

        const count = getFilteredItems().length;
        const now = Date.now();
        const stale = lastSuccessAt ? (now - lastSuccessAt) > 60000 : true;

        const timeStr = lastSuccessAt
            ? new Date(lastSuccessAt).toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: _AEST })
            : "—";

        meta.textContent = `${count} race${count !== 1 ? "s" : ""} • Updated ${timeStr}`;
        if (dot) dot.className = `refresh-dot ${stale ? "dot-stale" : "dot-live"}`;
    }

    // -------------------------------------------------------
    // Countdown refresh (every second in-DOM)
    // -------------------------------------------------------

    function refreshCountdowns() {
        document.querySelectorAll(".race-row").forEach(row => {
            const uid = row.dataset.raceUid;
            if (!uid) return;
            const item = allBoardItems.find(i => i.race_uid === uid);
            if (!item) return;

            const secs = getSecondsToJump(item);
            const cdEl = row.querySelector(".race-countdown");
            const sbEl = row.querySelector(".race-status-badge");

            if (cdEl) {
                cdEl.textContent = formatCountdown(secs);
                cdEl.className = `race-countdown ${countdownClass(secs)}`;
            }
            if (sbEl) {
                sbEl.textContent = statusLabel(secs);
                sbEl.className = `race-status-badge ${statusClass(secs)}`;
            }
        });

        document.querySelectorAll(".ntj-chip[data-race-uid]").forEach(chip => {
            const uid = chip.dataset.raceUid;
            const item = allBoardItems.find(i => i.race_uid === uid);
            if (!item) return;
            const secs = getSecondsToJump(item);
            const timeEl = chip.querySelector(".ntj-time");
            if (timeEl) timeEl.textContent = formatCountdown(secs);
            chip.className = `ntj-chip ${chipClass(secs)}`;
        });

        updateFilterMeta();
    }

    // -------------------------------------------------------
    // Data load
    // -------------------------------------------------------

    async function loadHomeBoard() {
        const meta = document.getElementById("filterMeta");
        if (meta) meta.textContent = "Loading…";

        try {
            const data = await api("/api/home/board");
            allBoardItems = Array.isArray(data.items) ? data.items : [];
            lastSuccessAt = Date.now();
            renderBoard();
        } catch (error) {
            console.error("Home board load failed:", error);
            const board = document.getElementById("raceBoard");
            if (board) board.innerHTML = `<div class="board-empty" style="padding:32px;text-align:center;color:var(--text-dim);">Failed to load board.</div>`;
            if (meta) meta.textContent = "Load failed";
        }
    }

    // -------------------------------------------------------
    // Boot
    // -------------------------------------------------------

    document.addEventListener("DOMContentLoaded", () => {
        // Filter tab clicks
        document.querySelectorAll(".filter-tab").forEach(btn => {
            btn.addEventListener("click", () => setFilter(btn.dataset.code));
        });

        // Event delegation for race navigation (NTJ chips + race rows)
        document.addEventListener("click", (e) => {
            const target = e.target.closest("[data-navigate='race']");
            if (!target) return;
            const uid = target.dataset.raceUid;
            if (uid) window.location.href = `/live?race_uid=${encodeURIComponent(uid)}`;
        });

        // Countdown refresh loop
        countdownTimer = setInterval(refreshCountdowns, 1000);

        // Auto-refresh board every 30 seconds
        refreshTimer = setInterval(loadHomeBoard, 30000);

        loadHomeBoard();
    });
})();
