(function () {
    const q = (id) => document.getElementById(id);

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    // -------------------------------------------------------
    // Tab switching (Section 5a fix: use document.getElementById via q())
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

                const target = document.getElementById(`settings-${tab.dataset.tab}`);
                if (target) target.style.display = "block";

                if (tab.dataset.tab === "visibility") loadVisibility();
                if (tab.dataset.tab === "data") loadSourceStatus();
            });
        });
    }

    // -------------------------------------------------------
    // Load header stats bar
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
                        if (x.seconds_to_jump != null) return x.seconds_to_jump;
                        const p = String(x.jump_time || "").split(":");
                        if (p.length < 2) return Infinity;
                        return parseInt(p[0], 10) * 60 + parseInt(p[1], 10);
                    };
                    return t(b) - t(a); // descending to get furthest
                });
                const furthest = sorted[0];
                setText("settingsLastSync", furthest ? furthest.jump_time || "—" : "—");
            }
        } catch (_) {
            setText("settingsDataStatus", "ERR");
        }
    }

    // -------------------------------------------------------
    // Load visibility / counters (Section 5c)
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
            setText("visDiscovered", ff.races_discovered ?? ff.total_races ?? "—");
            setText("visDomestic", ff.domestic ?? ff.au_nz_eligible ?? "—");
            setText("visIntlExcluded", ff.international_excluded ?? ff.skipped_international ?? "—");
        } catch (_) {
            const pre = q("formfavDebugPre");
            if (pre) pre.textContent = "FormFav debug not available.";
        }
    }

    // -------------------------------------------------------
    // Source status (Section 5b)
    // -------------------------------------------------------
    async function loadSourceStatus() {
        try {
            const health = await api("/api/health/connectors");
            const op = health.oddspro || health.OddsPro || {};
            const ff = health.formfav || health.FormFav || {};

            const opStatus = op.status || (op.enabled ? "OK" : "DISABLED");
            const opDetail = op.base_url ? ` — ${op.base_url}${op.authenticated ? " (auth)" : " (public)"}` : "";
            setText("srcOddsPro", opStatus + opDetail);

            const ffEnabled = ff.enabled ?? (ff.status === "OK");
            setText("srcFormFav", ffEnabled ? "ENABLED" : "DISABLED");
            setText("srcFormFavLastSync", ff.last_sync || ff.last_sync_time || "—");
        } catch (_) {
            // Fallback to generic health endpoint
            try {
                const h = await api("/api/health");
                setText("srcOddsPro", h.oddspro_enabled ? "ENABLED" : "UNKNOWN");
                setText("srcFormFav", h.formfav_enabled ? "ENABLED" : "UNKNOWN");
            } catch (__) {
                setText("srcOddsPro", "UNKNOWN");
                setText("srcFormFav", "UNKNOWN");
            }
        }
    }

    // -------------------------------------------------------
    // Maintenance actions (Section 5d)
    // -------------------------------------------------------
    function bindMaintenance() {
        const forceRefreshBtn = q("forceRefreshBtn");
        if (forceRefreshBtn) {
            forceRefreshBtn.addEventListener("click", async () => {
                setText("forceRefreshMeta", "Running sweep...");
                try {
                    const r = await api("/api/admin/sweep", { method: "POST" });
                    setText("forceRefreshMeta", r.ok !== false
                        ? `✓ Done — ${r.races_stored || 0} races stored`
                        : `✗ Error: ${r.error || "Unknown"}`);
                } catch (_) {
                    setText("forceRefreshMeta", "✗ Sweep endpoint not available");
                }
            });
        }

        const formfavSyncBtn = q("formfavSyncBtn");
        if (formfavSyncBtn) {
            formfavSyncBtn.addEventListener("click", async () => {
                setText("formfavSyncMeta", "Syncing...");
                try {
                    const r = await api("/api/formfav/sync", { method: "POST" });
                    setText("formfavSyncMeta", r.ok !== false
                        ? `✓ ${r.races_enriched || 0} races enriched`
                        : `✗ ${r.error || "Failed"}`);
                } catch (_) {
                    setText("formfavSyncMeta", "✗ FormFav sync not available");
                }
            });
        }

        const healthCheckBtn = q("healthCheckBtn");
        if (healthCheckBtn) {
            healthCheckBtn.addEventListener("click", async () => {
                setText("healthCheckMeta", "Checking…");
                const pre = q("healthDetailPre");
                try {
                    const r = await api("/api/health/live");
                    if (pre) {
                        pre.textContent = JSON.stringify(r, null, 2);
                        pre.style.display = "block";
                    }
                    setText("healthCheckMeta", r.ok !== false ? "✓ Healthy" : "✗ Issues found");
                } catch (_) {
                    setText("healthCheckMeta", "✗ Health check failed");
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
        bindMaintenance();
    });
})();
