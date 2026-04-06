async function api(url, options = {}) {
    const res = await fetch(url, options);
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
            const parseT = (t) => {
                if (!t) return Infinity;
                const parts = String(t).split(":");
                if (parts.length < 2) return Infinity;
                return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
            };
            return parseT(a.jump_time) - parseT(b.jump_time);
        });
        const next = sorted[0];
        const nextEl = document.getElementById("headerNextUp");
        if (nextEl) {
            nextEl.textContent = next
                ? `${next.track || "—"} R${next.race_num || "?"} ${next.jump_time || ""}`
                : "—";
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
});
