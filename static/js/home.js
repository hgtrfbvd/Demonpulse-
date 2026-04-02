(function () {
    let allBoardItems = [];
    let activeCodeFilter = "ALL";
    let countdownTimer = null;

    const els = {
        envValue: document.getElementById("homeEnvValue"),
        shadowValue: document.getElementById("homeShadowValue"),
        raceCount: document.getElementById("homeRaceCount"),
        nextUp: document.getElementById("summaryNextUp"),
        nextUpSub: document.getElementById("summaryNextUpSub"),
        hotCount: document.getElementById("summaryHotCount"),
        codeMix: document.getElementById("summaryCodeMix"),
        boardMeta: document.getElementById("boardMeta"),
        boardBody: document.getElementById("homeBoardBody"),
        refreshBtn: document.getElementById("refreshBoardBtn"),
        filterButtons: document.querySelectorAll(".dp-filter-btn"),
    };

    function normaliseCode(code) {
        const raw = String(code || "GREYHOUND").toUpperCase();
        if (raw === "THOROUGHBRED") return "HORSE";
        return raw;
    }

    function codeBadgeClass(code) {
        const c = normaliseCode(code);
        if (c === "GREYHOUND") return "code-gh";
        if (c === "HORSE") return "code-horse";
        if (c === "HARNESS") return "code-harness";
        return "code-default";
    }

    function signalBadgeClass(signal) {
        const s = String(signal || "").toUpperCase();
        if (s === "SNIPER") return "signal-sniper";
        if (s === "VALUE") return "signal-value";
        if (s === "GEM") return "signal-gem";
        if (s === "WATCH") return "signal-watch";
        if (s === "RISK") return "signal-risk";
        if (s === "NO_BET") return "signal-no-bet";
        return "signal-none";
    }

    function parseJumpToDate(jumpTime) {
        if (!jumpTime || typeof jumpTime !== "string") return null;

        const parts = jumpTime.split(":");
        if (parts.length < 2) return null;

        const hour = parseInt(parts[0], 10);
        const minute = parseInt(parts[1], 10);

        if (Number.isNaN(hour) || Number.isNaN(minute)) return null;

        const now = new Date();
        const target = new Date(
            now.getFullYear(),
            now.getMonth(),
            now.getDate(),
            hour,
            minute,
            0,
            0
        );

        return target;
    }

    function formatCountdownText(jumpTime) {
        const target = parseJumpToDate(jumpTime);
        if (!target) return "—";

        const now = new Date();
        const diffMs = target.getTime() - now.getTime();
        const diffSec = Math.floor(diffMs / 1000);

        if (diffSec <= 0) return "Jumped / due";

        const mins = Math.floor(diffSec / 60);
        const secs = diffSec % 60;

        if (mins >= 60) {
            const hrs = Math.floor(mins / 60);
            const remMins = mins % 60;
            return `${hrs}h ${remMins}m`;
        }

        return `${mins}m ${String(secs).padStart(2, "0")}s`;
    }

    function filteredItems() {
        if (activeCodeFilter === "ALL") return [...allBoardItems];
        return allBoardItems.filter(item => normaliseCode(item.code) === activeCodeFilter);
    }

    function sortItems(items) {
        return items.sort((a, b) => {
            const aDate = parseJumpToDate(a.jump_time);
            const bDate = parseJumpToDate(b.jump_time);

            if (!aDate && !bDate) return 0;
            if (!aDate) return 1;
            if (!bDate) return -1;

            return aDate.getTime() - bDate.getTime();
        });
    }

    function getActionHref(item) {
        return item.race_uid ? `/live?race_uid=${encodeURIComponent(item.race_uid)}` : "/live";
    }

    function renderBoard() {
        const items = sortItems(filteredItems());

        if (!items.length) {
            els.boardBody.innerHTML = `
                <tr>
                    <td colspan="9" class="board-empty">No races for this filter.</td>
                </tr>
            `;
            els.boardMeta.textContent = "No visible races";
            els.raceCount.textContent = "0";
            els.nextUp.textContent = "—";
            els.nextUpSub.textContent = "No race loaded";
            els.hotCount.textContent = "0";
            els.codeMix.textContent = "0 / 0 / 0";
            return;
        }

        const hotCount = items.filter(item => {
            const signal = String(item.signal || "").toUpperCase();
            return signal === "SNIPER" || signal === "VALUE";
        }).length;

        const gh = items.filter(i => normaliseCode(i.code) === "GREYHOUND").length;
        const horse = items.filter(i => normaliseCode(i.code) === "HORSE").length;
        const harness = items.filter(i => normaliseCode(i.code) === "HARNESS").length;

        const next = items[0];

        els.raceCount.textContent = String(items.length);
        els.hotCount.textContent = String(hotCount);
        els.codeMix.textContent = `${gh} / ${horse} / ${harness}`;
        els.boardMeta.textContent = `${items.length} visible race${items.length === 1 ? "" : "s"}`;
        els.nextUp.textContent = `${next.track || "Unknown"} R${next.race_num || "?"}`;
        els.nextUpSub.textContent = `${normaliseCode(next.code)} • ${next.jump_time || "No jump time"}`;

        els.boardBody.innerHTML = items.map(item => {
            const code = normaliseCode(item.code);
            const signal = item.signal || "—";
            const confidence = item.confidence || "—";
            const status = item.status || "upcoming";

            return `
                <tr>
                    <td>
                        <span class="code-badge ${codeBadgeClass(code)}">${code}</span>
                    </td>
                    <td>${item.track || "—"}</td>
                    <td>R${item.race_num || "—"}</td>
                    <td>${item.jump_time || "—"}</td>
                    <td class="countdown-cell" data-jump-time="${item.jump_time || ""}">
                        ${formatCountdownText(item.jump_time)}
                    </td>
                    <td><span class="status-pill">${status}</span></td>
                    <td><span class="signal-pill ${signalBadgeClass(signal)}">${signal}</span></td>
                    <td>${confidence}</td>
                    <td>
                        <a class="dp-btn dp-btn-small" href="${getActionHref(item)}">Open Live</a>
                    </td>
                </tr>
            `;
        }).join("");

        refreshCountdowns();
    }

    function refreshCountdowns() {
        const countdownEls = document.querySelectorAll(".countdown-cell");
        countdownEls.forEach(el => {
            const jumpTime = el.getAttribute("data-jump-time");
            el.textContent = formatCountdownText(jumpTime);
        });
    }

    function setFilter(nextFilter) {
        activeCodeFilter = nextFilter;
        els.filterButtons.forEach(btn => {
            btn.classList.toggle("active", btn.dataset.code === nextFilter);
        });
        renderBoard();
    }

    async function loadSystemSummary() {
        try {
            const status = await api("/api/system/status");
            els.envValue.textContent = status.env || "—";
            els.shadowValue.textContent = status.shadow_active ? "ON" : "OFF";
        } catch (_err) {
            els.envValue.textContent = "ERR";
            els.shadowValue.textContent = "ERR";
        }
    }

    async function loadBoard() {
        els.boardMeta.textContent = "Loading board…";
        els.boardBody.innerHTML = `
            <tr>
                <td colspan="9" class="board-empty">Loading board…</td>
            </tr>
        `;

        try {
            const data = await api("/api/home/board");
            allBoardItems = Array.isArray(data.items) ? data.items : [];
            renderBoard();
        } catch (err) {
            console.error("Board load failed:", err);
            els.boardMeta.textContent = "Board load failed";
            els.boardBody.innerHTML = `
                <tr>
                    <td colspan="9" class="board-empty">Failed to load board.</td>
                </tr>
            `;
        }
    }

    function bindEvents() {
        if (els.refreshBtn) {
            els.refreshBtn.addEventListener("click", loadBoard);
        }

        els.filterButtons.forEach(btn => {
            btn.addEventListener("click", () => setFilter(btn.dataset.code));
        });
    }

    function startCountdownLoop() {
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = setInterval(refreshCountdowns, 1000);
    }

    async function initHomePage() {
        bindEvents();
        startCountdownLoop();
        await loadSystemSummary();
        await loadBoard();
    }

    document.addEventListener("DOMContentLoaded", initHomePage);
})();
