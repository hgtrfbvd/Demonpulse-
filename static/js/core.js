async function api(url, options = {}) {
    const res = await fetch(url, options);
    if (!res.ok) throw new Error("API error");
    return res.json();
}

function formatCountdown(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
}

async function loadSystemStatus() {
    try {
        const data = await api('/api/system/status');

        document.getElementById('envBadge').innerText = data.env;
        document.getElementById('shadowStatus').innerText =
            data.shadow_active ? 'SHADOW ON' : 'SHADOW OFF';
    } catch (e) {
        console.error(e);
    }
}

loadSystemStatus();
