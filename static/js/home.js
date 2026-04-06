(function () {
    let allBoardItems = [];
    let activeCodeFilter = "ALL";
    let countdownTimer = null;

    const el = {
        refreshBtn: document.getElementById("refreshHomeBoardBtn"),
        clearBtn: document.getElementById("clearHomeFilterBtn"),
        filterButtons: Array.from(document.querySelectorAll(".terminal-filter-btn")),
        boardRows: document.getElementById("homeBoardRows"),
        boardMeta: document.getElementById("boardTerminalMeta"),
        visibleRaceCount: document.getElementById("visibleRaceCount"),
        hotRaceCount: document.getElementById("hotRaceCount"),
        nextUpMain: document.getElementById("nextUpMain"),
        nextUpSub: document.getElementById("nextUpSub"),
        codeMixValue: document.getElementById("codeMixValue"),
        quickFeedList: document.getElementById("quickFeedList"),
        priorityFocusBox: document.getElementById("priorityFocusBox"),
    };

    function normaliseCode(code) {
        const raw = String(code || "GREYHOUND").toUpperCase();
        if (raw === "THOROUGHBRED") return "HORSE";
        return raw;
    }

    function codeClass(code) {
        const c = normaliseCode(code);
        if (c === "GREYHOUND") return "code-gh";
        if (c === "HORSE") return "code-horse";
        if (c === "HARNESS") return "code-harness";
        return "code-default";
    }

    function signalClass(signal) {
        const s = String(signal || "").toUpperCase();
        if (s === "SNIPER") return "signal-sniper";
        if (s === "VALUE") return "signal-value";
        if (s === "GEM") return "signal-gem";
        if (s === "WATCH") return "signal-watch";
        if (s === "RISK") return "signal-risk";
        if (s === "NO_BET") return "signal-no-bet";
        return "signal-none";
    }

    function parseJumpTimeToDate(jumpTime) {
        if (!jumpTime || typeof jumpTime !== "string") return null;

        // ISO datetime strings: "2026-04-07T10:30:00+10:00", "2026-04-07T10:30:00Z", etc.
        // Detect by full date pattern before the "T" separator
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

    // Use jump_dt_iso (UTC ISO from backend) when available, else fall back to jump_time
    function getJumpDate(item) {
        if (item && item.jump_dt_iso) {
            const dt = new Date(item.jump_dt_iso);
            if (!isNaN(dt.getTime())) return dt;
        }
        return parseJumpTimeToDate(item && item.jump_time ? item.jump_time : (typeof item === "string" ? item : null));
    }

    function formatCountdownText(jumpTimeOrDate) {
        let target;
        if (jumpTimeOrDate instanceof Date) {
            target = jumpTimeOrDate;
        } else {
            target = parseJumpTimeToDate(jumpTimeOrDate);
        }
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

    function setFilter(nextFilter) {
        activeCodeFilter = nextFilter;
        el.filterButtons.forEach(btn => {
            btn.classList.toggle("active", btn.dataset.code === nextFilter);
        });
        renderHomeBoard();
    }

    function getFilteredItems() {
        if (activeCodeFilter === "ALL") return [...allBoardItems];
        return allBoardItems.filter(item => normaliseCode(item.code) === activeCodeFilter);
    }

    function sortByJumpTime(items) {
        return items.sort((a, b) => {
            // Prefer backend-computed seconds_to_jump (already server-side calculated)
            const aHasSecs = a.seconds_to_jump != null;
            const bHasSecs = b.seconds_to_jump != null;
            if (aHasSecs && bHasSecs) return a.seconds_to_jump - b.seconds_to_jump;

            // Fall back to jump_dt_iso (UTC ISO) or jump_time (HH:MM)
            const aDate = getJumpDate(a);
            const bDate = getJumpDate(b);

            if (!aDate && !bDate) return 0;
            if (!aDate) return 1;
            if (!bDate) return -1;
            return aDate.getTime() - bDate.getTime();
        });
    }

    function isHotSignal(signal) {
        const s = String(signal || "").toUpperCase();
        return s === "SNIPER" || s === "VALUE";
    }

    function renderBoardRows(items) {
        if (!items.length) {
            el.boardRows.innerHTML = `
                <tr>
                    <td colspan="10" class="board-empty">No races available for this filter.</td>
                </tr>
            `;
            return;
        }

        el.boardRows.innerHTML = items.map((item, idx) => {
            const code = normaliseCode(item.code);
            const signal = item.signal || "—";
            const confidence = item.confidence || "—";
            const liveHref = item.race_uid
                ? `/live?race_uid=${encodeURIComponent(item.race_uid)}`
                : "/live";
            const raceHref = item.race_uid
                ? `/race?race_uid=${encodeURIComponent(item.race_uid)}`
                : "/race";
            const detailId = `home-detail-${idx}`;

            // Display the jump time: prefer formatted local time from jump_dt_iso
            const jumpDisplay = (() => {
                if (item.jump_dt_iso) {
                    const d = new Date(item.jump_dt_iso);
                    if (!isNaN(d.getTime())) {
                        return d.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", timeZone: "Australia/Sydney" });
                    }
                }
                return item.jump_time || "—";
            })();
            const jumpIso = item.jump_dt_iso || "";

            return `
                <tr class="home-board-row" data-detail="${detailId}" style="cursor:pointer;" title="Click to expand timing detail">
                    <td><span class="code-badge ${codeClass(code)}">${code}</span></td>
                    <td>${item.track || "—"}</td>
                    <td>R${item.race_num || "—"}</td>
                    <td>${jumpDisplay}</td>
                    <td class="countdown-cell" data-jump-iso="${jumpIso}" data-jump="${item.jump_time || ""}">${formatCountdownText(item.jump_dt_iso ? new Date(item.jump_dt_iso) : item.jump_time)}</td>
                    <td><span class="status-pill">${(item.status || "upcoming").toUpperCase()}</span></td>
                    <td><span class="signal-pill ${signalClass(signal)}">${signal}</span></td>
                    <td>${confidence}</td>
                    <td>
                        <a class="dp-btn dp-btn-small" href="${liveHref}">Live</a>
                        <a class="dp-btn dp-btn-small" href="${raceHref}" style="margin-left:4px;">Race View</a>
                    </td>
                    <td style="width:24px;text-align:center;color:var(--text-dim);font-size:12px;">▶</td>
                </tr>
                <tr class="home-detail-row" id="${detailId}" style="display:none;">
                    <td colspan="10">
                        <div class="home-detail-panel">
                            <div class="home-detail-grid">
                                <div class="home-detail-item"><span class="home-detail-key">race_uid</span><span class="home-detail-val">${item.race_uid || "—"}</span></div>
                                <div class="home-detail-item"><span class="home-detail-key">source jump</span><span class="home-detail-val">${item.source_jump_time || item.jump_time || "—"}</span></div>
                                <div class="home-detail-item"><span class="home-detail-key">jump_dt_iso</span><span class="home-detail-val">${item.jump_dt_iso || "—"}</span></div>
                                <div class="home-detail-item"><span class="home-detail-key">ntj_label</span><span class="home-detail-val">${item.ntj_label || "—"}</span></div>
                                <div class="home-detail-item"><span class="home-detail-key">seconds_to_jump</span><span class="home-detail-val">${item.seconds_to_jump != null ? item.seconds_to_jump : "—"}</span></div>
                                <div class="home-detail-item"><span class="home-detail-key">status</span><span class="home-detail-val">${item.status || "—"}</span></div>
                                <div class="home-detail-item"><span class="home-detail-key">country</span><span class="home-detail-val">${item.country || "—"}</span></div>
                            </div>
                        </div>
                    </td>
                </tr>
            `;
        }).join("");

        // bind expand/collapse
        document.querySelectorAll(".home-board-row").forEach(row => {
            row.addEventListener("click", (e) => {
                if (e.target.closest("a")) return;
                const detailId = row.dataset.detail;
                const detail = document.getElementById(detailId);
                const arrow = row.querySelector("td:last-child");
                if (!detail) return;
                const open = detail.style.display !== "none";
                detail.style.display = open ? "none" : "table-row";
                if (arrow) arrow.textContent = open ? "▶" : "▼";
            });
        });
    }

    function renderQuickFeed(items) {
        if (!items.length) {
            el.quickFeedList.innerHTML = `<div class="quick-feed-empty">No board data yet.</div>`;
            return;
        }

        const top = items.slice(0, 6);
        el.quickFeedList.innerHTML = top.map(item => {
            const code = normaliseCode(item.code);
            const signal = item.signal || "—";
            return `
                <a class="quick-feed-row" href="${item.race_uid ? `/live?race_uid=${encodeURIComponent(item.race_uid)}` : '/live'}">
                    <div class="quick-feed-main">
                        <span class="code-badge ${codeClass(code)}">${code}</span>
                        <span class="quick-feed-track">${item.track || "—"} R${item.race_num || "—"}</span>
                    </div>
                    <div class="quick-feed-side">
                        <span class="quick-feed-time">${item.jump_time || "—"}</span>
                        <span class="signal-pill ${signalClass(signal)}">${signal}</span>
                    </div>
                </a>
            `;
        }).join("");
    }

    function renderPriority(items) {
        const hot = items.find(item => isHotSignal(item.signal));
        if (!hot) {
            el.priorityFocusBox.innerHTML = `No SNIPER or VALUE race visible right now.`;
            return;
        }

        el.priorityFocusBox.innerHTML = `
            <div class="priority-code-line">
                <span class="code-badge ${codeClass(hot.code)}">${normaliseCode(hot.code)}</span>
                <span class="signal-pill ${signalClass(hot.signal)}">${hot.signal || "—"}</span>
            </div>
            <div class="priority-race-line">${hot.track || "—"} R${hot.race_num || "—"}</div>
            <div class="priority-sub-line">Jump ${hot.jump_time || "—"} • Confidence ${hot.confidence || "—"}</div>
            <a class="dp-btn dp-btn-primary priority-open-btn" href="${hot.race_uid ? `/live?race_uid=${encodeURIComponent(hot.race_uid)}` : '/live'}">Open Live Race</a>
        `;
    }

    function updateSummary(items) {
        const visibleCount = items.length;
        const hotCount = items.filter(item => isHotSignal(item.signal)).length;
        const next = items[0] || null;

        const ghCount = items.filter(item => normaliseCode(item.code) === "GREYHOUND").length;
        const horseCount = items.filter(item => normaliseCode(item.code) === "HORSE").length;
        const harnessCount = items.filter(item => normaliseCode(item.code) === "HARNESS").length;

        el.visibleRaceCount.textContent = String(visibleCount);
        el.hotRaceCount.textContent = String(hotCount);
        el.codeMixValue.textContent = `${ghCount} / ${horseCount} / ${harnessCount}`;

        if (next) {
            el.nextUpMain.textContent = `${next.track || "—"} R${next.race_num || "—"}`;
            const nextJump = (() => {
                if (next.jump_dt_iso) {
                    const d = new Date(next.jump_dt_iso);
                    if (!isNaN(d.getTime())) {
                        return d.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", timeZone: "Australia/Sydney" });
                    }
                }
                return next.jump_time || "—";
            })();
            el.nextUpSub.textContent = `${normaliseCode(next.code)} • ${nextJump}`;
        } else {
            el.nextUpMain.textContent = "—";
            el.nextUpSub.textContent = "Waiting for board";
        }

        el.boardMeta.textContent = visibleCount
            ? `${visibleCount} race${visibleCount === 1 ? "" : "s"} visible`
            : "No races visible";
    }

    function refreshCountdowns() {
        document.querySelectorAll(".countdown-cell").forEach(cell => {
            // Prefer jump_dt_iso (UTC ISO) for precise countdown
            const iso = cell.dataset.jumpIso || "";
            if (iso) {
                const dt = new Date(iso);
                if (!isNaN(dt.getTime())) {
                    cell.textContent = formatCountdownText(dt);
                    return;
                }
            }
            // Fall back to jump_time (HH:MM)
            cell.textContent = formatCountdownText(cell.dataset.jump || "");
        });
    }

    function renderHomeBoard() {
        const items = sortByJumpTime(getFilteredItems());
        renderBoardRows(items);
        renderQuickFeed(items);
        renderPriority(items);
        updateSummary(items);
        refreshCountdowns();
    }

    async function loadHomeBoard() {
        el.boardMeta.textContent = "Loading board…";
        el.boardRows.innerHTML = `
            <tr>
                <td colspan="9" class="board-empty">Loading board…</td>
            </tr>
        `;

        try {
            const data = await api("/api/home/board");
            allBoardItems = Array.isArray(data.items) ? data.items : [];
            renderHomeBoard();
        } catch (error) {
            console.error("Home board load failed:", error);
            el.boardMeta.textContent = "Board load failed";
            el.boardRows.innerHTML = `
                <tr>
                    <td colspan="9" class="board-empty">Failed to load board.</td>
                </tr>
            `;
            el.quickFeedList.innerHTML = `<div class="quick-feed-empty">Failed to load board.</div>`;
            el.priorityFocusBox.innerHTML = `Board load failed.`;
        }
    }

    function bindHomeEvents() {
        if (el.refreshBtn) {
            el.refreshBtn.addEventListener("click", loadHomeBoard);
        }

        if (el.clearBtn) {
            el.clearBtn.addEventListener("click", () => setFilter("ALL"));
        }

        el.filterButtons.forEach(btn => {
            btn.addEventListener("click", () => setFilter(btn.dataset.code));
        });
    }

    function startHomeCountdownLoop() {
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = setInterval(refreshCountdowns, 1000);
    }

    document.addEventListener("DOMContentLoaded", () => {
        bindHomeEvents();
        startHomeCountdownLoop();
        loadHomeBoard();
    });
})();
