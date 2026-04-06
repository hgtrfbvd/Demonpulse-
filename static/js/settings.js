(function () {
    const q = (id) => document.getElementById(id);

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    // -------------------------------------------------------
    // Tab switching
    // -------------------------------------------------------
    function initTabs() {
        const tabs = document.querySelectorAll(".settings-tab");
        tabs.forEach(tab => {
            tab.addEventListener("click", () => {
                tabs.forEach(t => t.classList.remove("active"));
                tab.classList.add("active");

                document.querySelectorAll(".settings-section").forEach(sec => {
                    sec.style.display = "none";
                });

                const target = q(`settings-${tab.dataset.tab}`);
                if (target) target.style.display = "block";

                if (tab.dataset.tab === "visibility") loadVisibility();
            });
        });
    }

    // -------------------------------------------------------
    // Load hero stats
    // -------------------------------------------------------
    async function loadHeroStats() {
        try {
            const status = await api("/api/system/status");
            setText("settingsEnv", status.env || "—");
            setText("genEnvDisplay", status.env || "—");
        } catch (_) {}

        try {
            const board = await api("/api/home/board");
            const items = Array.isArray(board.items) ? board.items : [];
            setText("settingsBoardCount", String(items.length));
            setText("settingsDataStatus", items.length > 0 ? "OK" : "NO DATA");

            if (items.length > 0) {
                const sorted = [...items].sort((a, b) => {
                    const t = x => {
                        const p = String(x.jump_time || "").split(":");
                        if (p.length < 2) return Infinity;
                        return parseInt(p[0], 10) * 60 + parseInt(p[1], 10);
                    };
                    return t(a) - t(b);
                });
                const furthestRace = sorted[sorted.length - 1];
                setText("settingsLastSync", furthestRace ? furthestRace.jump_time || "—" : "—");
            }
        } catch (_) {
            setText("settingsDataStatus", "ERR");
        }
    }

    // -------------------------------------------------------
    // Load visibility / counters
    // -------------------------------------------------------
    async function loadVisibility() {
        try {
            const board = await api("/api/home/board");
            const items = Array.isArray(board.items) ? board.items : [];

            const normalise = (code) => {
                const raw = String(code || "").toUpperCase();
                if (raw === "THOROUGHBRED") return "HORSE";
                return raw;
            };

            setText("visBoardRaces", String(items.length));
            setText("visGH", String(items.filter(i => normalise(i.code) === "GREYHOUND").length));
            setText("visHorse", String(items.filter(i => normalise(i.code) === "HORSE").length));
            setText("visHarness", String(items.filter(i => normalise(i.code) === "HARNESS").length));
        } catch (_) {
            setText("visBoardRaces", "ERR");
        }

        try {
            const ff = await api("/api/debug/formfav");
            const pre = q("formfavDebugPre");
            if (pre) pre.textContent = JSON.stringify(ff, null, 2);
            setText("visFormfavSynced", ff.races_enriched ?? ff.enriched ?? "—");
        } catch (_) {
            const pre = q("formfavDebugPre");
            if (pre) pre.textContent = "FormFav debug not available.";
        }
    }

    // -------------------------------------------------------
    // Source status
    // -------------------------------------------------------
    async function loadSourceStatus() {
        try {
            const health = await api("/api/health");
            setText("srcOddsPro", health.oddspro_enabled ? "ENABLED" : "DISABLED");
            setText("srcFormFav", health.formfav_enabled ? "ENABLED" : "DISABLED");
        } catch (_) {
            setText("srcOddsPro", "UNKNOWN");
            setText("srcFormFav", "UNKNOWN");
        }
    }

    // -------------------------------------------------------
    // Maintenance actions
    // -------------------------------------------------------
    function bindMaintenance() {
        const forceRefreshBtn = q("forceRefreshBtn");
        if (forceRefreshBtn) {
            forceRefreshBtn.addEventListener("click", async () => {
                setText("forceRefreshMeta", "Running…");
                try {
                    const data = await api("/api/admin/force-refresh", { method: "POST" });
                    setText("forceRefreshMeta", data.ok ? "Refresh triggered" : (data.error || "Failed"));
                } catch (_) {
                    setText("forceRefreshMeta", "Force refresh not available");
                }
            });
        }

        const formfavSyncBtn = q("formfavSyncBtn");
        if (formfavSyncBtn) {
            formfavSyncBtn.addEventListener("click", async () => {
                setText("formfavSyncMeta", "Running…");
                try {
                    const data = await api("/api/admin/formfav-sync", { method: "POST" });
                    setText("formfavSyncMeta", data.ok ? "Sync triggered" : (data.error || "Failed"));
                } catch (_) {
                    setText("formfavSyncMeta", "FormFav sync not available");
                }
            });
        }

        const healthCheckBtn = q("healthCheckBtn");
        if (healthCheckBtn) {
            healthCheckBtn.addEventListener("click", async () => {
                setText("healthCheckMeta", "Checking…");
                const pre = q("healthDetailPre");
                try {
                    const data = await api("/api/health");
                    setText("healthCheckMeta", data.ok ? "Healthy" : "Issues found");
                    if (pre) {
                        pre.textContent = JSON.stringify(data, null, 2);
                        pre.style.display = "block";
                    }
                } catch (_) {
                    setText("healthCheckMeta", "Health check failed");
                    if (pre) {
                        pre.textContent = "Health endpoint not available.";
                        pre.style.display = "block";
                    }
                }
            });
        }

        const refreshVisBtn = q("refreshVisibilityBtn");
        if (refreshVisBtn) {
            refreshVisBtn.addEventListener("click", loadVisibility);
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        initTabs();
        loadHeroStats();
        loadSourceStatus();
        bindMaintenance();
    });
})();
