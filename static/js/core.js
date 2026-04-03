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
        const shadowEl = document.getElementById("shadowStatus");

        if (envEl) envEl.textContent = data.env || "—";
        if (shadowEl) shadowEl.textContent = data.shadow_active ? "ON" : "OFF";
    } catch (e) {
        console.error(e);
    }
}

document.addEventListener("DOMContentLoaded", () => {
    updateTopClock();
    setInterval(updateTopClock, 1000);
    loadSystemStatus();
});
