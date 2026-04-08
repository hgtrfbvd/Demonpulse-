async function api(url, options = {}) {
    const token = localStorage.getItem("dp_token") || "";
    const headers = { ...(options.headers || {}) };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    if (options.body && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
    }
    const res = await fetch(url, { ...options, headers });
    if (!res.ok) {
        throw new Error(`API error (${res.status})`);
    }
    return res.json();
}

function formatCountdown(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatNextUp(item) {
    if (!item) return "—";
    const track = (item.track || "").replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    const raceNum = `R${item.race_num || "?"}`;
    if (item.jump_dt_iso) {
        const dt = new Date(item.jump_dt_iso);
        if (!isNaN(dt.getTime())) {
            const timeStr = dt.toLocaleTimeString("en-AU", {
                hour: "2-digit", minute: "2-digit", timeZone: "Australia/Sydney"
            });
            return `${track} ${raceNum} ${timeStr}`;
        }
    }
    return `${track} ${raceNum} ${item.jump_time || ""}`;
}

function updateTopClock() {
    const el = document.getElementById("topClockValue");
    if (!el) return;

    const now = new Date();
    const text = now.toLocaleTimeString("en-AU", {
        timeZone: "Australia/Sydney",
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
    });
    el.textContent = text;
}

async function loadSystemStatus() {
    try {
        const data = await api("/api/system/status");

        const envEl = document.getElementById("envBadge");
        if (envEl) envEl.textContent = data.env || "—";
    } catch (e) {
        console.error(e);
    }
}

async function loadHeaderStats() {
    try {
        const data = await api("/api/home/board");
        const items = Array.isArray(data.items) ? data.items : [];

        const normalise = (code) => {
            const raw = String(code || "").toUpperCase();
            if (raw === "THOROUGHBRED") return "HORSE";
            return raw;
        };

        const gh = items.filter(i => normalise(i.code) === "GREYHOUND").length;
        const h  = items.filter(i => normalise(i.code) === "HORSE").length;
        const hr = items.filter(i => normalise(i.code) === "HARNESS").length;

        const countsEl = document.getElementById("headerCounts");
        if (countsEl) countsEl.textContent = `${gh}/${h}/${hr}`;

        // Next up
        const sorted = [...items].sort((a, b) => {
            const parseT = (i) => {
                if (i.seconds_to_jump != null) return i.seconds_to_jump;
                if (i.jump_dt_iso) return (new Date(i.jump_dt_iso).getTime() - Date.now()) / 1000;
                const parts = String(i.jump_time || "").split(":");
                if (parts.length < 2) return Infinity;
                return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
            };
            return parseT(a) - parseT(b);
        });
        const future = sorted.filter(i => (i.seconds_to_jump ?? -1) > 0);
        const next = future[0] || null;
        const nextEl = document.getElementById("headerNextUp");
        if (nextEl) {
            nextEl.textContent = next ? formatNextUp(next) : "—";
        }

        const statusEl = document.getElementById("headerDataStatus");
        if (statusEl) statusEl.textContent = items.length > 0 ? "OK" : "NO DATA";
    } catch (e) {
        const statusEl = document.getElementById("headerDataStatus");
        if (statusEl) statusEl.textContent = "ERR";
    }
}

document.addEventListener("DOMContentLoaded", () => {
    updateTopClock();
    setInterval(updateTopClock, 1000);
    loadSystemStatus();
    loadHeaderStats();
    setInterval(loadHeaderStats, 30000);
    enforceNavAccess();
});

async function enforceNavAccess() {
    try {
        const me = await api("/api/auth/me");
        const role = me.role || "viewer";
        const perms = me.permissions || [];

        const navRules = {
            "/betting":     ["betting"],
            "/learning":    ["ai_learning"],
            "/backtesting": ["backtest"],
            "/settings":    ["settings"],
            "/reports":     ["reports"],
        };

        document.querySelectorAll(".top-nav-link").forEach(link => {
            const path = new URL(link.href, location.origin).pathname;
            const required = navRules[path];
            if (required && !required.some(p => perms.includes(p))) {
                link.style.display = "none";
            }
        });
    } catch (_) {
        // No auth or token expired — show all nav links rather than hiding any
    }
}
