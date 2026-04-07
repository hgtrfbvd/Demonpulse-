(function () {
    const q = (id) => document.getElementById(id);

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    // -------------------------------------------------------
    // Tab switching
    // -------------------------------------------------------

    function bindTabs() {
        const tabs = document.querySelectorAll("[data-learn-tab]");
        tabs.forEach(tab => {
            tab.addEventListener("click", () => {
                tabs.forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                document.querySelectorAll(".learning-section").forEach(s => s.style.display = "none");
                const target = document.getElementById(`learn-${tab.dataset.learnTab}`);
                if (target) target.style.display = "block";
                if (tab.dataset.learnTab === "performance") loadPerformanceChart();
            });
        });
    }

    // -------------------------------------------------------
    // Poll learning status
    // -------------------------------------------------------

    async function pollLearningStatus() {
        try {
            const data = await api("/api/ai/learning/status");
            setText("learnModelVersion", data.model_version || "—");
            setText("learnTotalPredictions", data.total_predictions ?? 0);
            setText("learnTotalEvaluated", data.total_evaluated ?? 0);
            setText("learnPaperBetsToday", data.paper_bets_today ?? 0);
            setText("learnResultsReviewed", data.results_reviewed_today ?? 0);
            setText("learnEnrichmentRate", data.enrichment_rate || "—");
            if (q("learnWinRate")) q("learnWinRate").textContent = data.win_rate != null ? data.win_rate.toFixed(1) + "%" : "—";
            if (q("learnROI")) q("learnROI").textContent = data.roi != null ? (data.roi > 0 ? "+" : "") + data.roi.toFixed(1) + "%" : "—";
            const dot = q("learnStatusDot");
            if (dot) dot.style.background = data.active ? "var(--green)" : "var(--text-dim)";

            // Also update performance tab
            setText("perfTotal", data.total_predictions ?? 0);
            setText("perfEvaluated", data.total_evaluated ?? 0);
            if (q("perfWinRate")) q("perfWinRate").textContent = data.win_rate != null ? data.win_rate.toFixed(1) + "%" : "—";
            if (q("perfROI")) q("perfROI").textContent = data.roi != null ? (data.roi > 0 ? "+" : "") + data.roi.toFixed(1) + "%" : "—";
        } catch (_) {}
    }

    // -------------------------------------------------------
    // Load activity feed
    // -------------------------------------------------------

    async function loadActivityFeed() {
        try {
            const data = await api("/api/predictions/today");
            const feed = q("learnActivityFeed");
            if (!feed) return;
            const items = data.predictions || data.items || [];
            if (!items.length) {
                feed.innerHTML = `<div class="activity-empty">No predictions yet today — system will auto-predict all upcoming races.</div>`;
                return;
            }
            feed.innerHTML = items.slice(0, 20).map(p => {
                const won  = p.result === "WIN";
                const lost = p.result === "LOSS";
                const icon = won ? "✓" : lost ? "✗" : "·";
                const cls  = won ? "activity-win" : lost ? "activity-loss" : "activity-pending";
                return `<div class="activity-row ${cls}">
                    <span class="activity-icon">${icon}</span>
                    <span class="activity-race">${p.track || "?"} R${p.race_num || "?"}</span>
                    <span class="activity-pick">${p.selection || p.top_runner || "—"}</span>
                    <span class="activity-result">${p.result || "PENDING"}</span>
                </div>`;
            }).join("");
            setText("learnActivityMeta", `${items.length} predictions loaded`);
        } catch (_) {}
    }

    // -------------------------------------------------------
    // Performance chart
    // -------------------------------------------------------

    let bankrollChart = null;

    async function loadPerformanceChart() {
        try {
            const data = await api("/api/predictions/performance");
            const ctx = q("learnBankrollChart");
            if (!ctx) return;

            const history = data.bankroll_history || data.history || [];
            if (!history.length) return;

            const labels = history.map((_, i) => `Race ${i + 1}`);
            const values = history.map(v => typeof v === "number" ? v : v.bankroll || 0);

            if (bankrollChart) bankrollChart.destroy();
            bankrollChart = new Chart(ctx, {
                type: "line",
                data: {
                    labels,
                    datasets: [{
                        data: values,
                        borderColor: values[values.length - 1] >= values[0] ? "#3dd68c" : "#ff2d2d",
                        backgroundColor: "transparent",
                        tension: 0.3,
                        pointRadius: 2,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { display: false },
                        y: { grid: { color: "rgba(255,255,255,0.05)" }, ticks: { color: "#606070" } }
                    }
                }
            });
        } catch (_) {}
    }

    // -------------------------------------------------------
    // Config save
    // -------------------------------------------------------

    function bindConfig() {
        const conf = q("learnConfThreshold");
        const ev   = q("learnEvThreshold");
        if (conf) conf.addEventListener("input", () => setText("learnConfValue", parseFloat(conf.value).toFixed(2)));
        if (ev)   ev.addEventListener("input",   () => setText("learnEvValue",   parseFloat(ev.value).toFixed(2)));

        const saveBtn = q("saveLearnConfigBtn");
        if (saveBtn) {
            saveBtn.addEventListener("click", () => {
                const cfg = {
                    objective:          q("learnObjective")?.value,
                    conf_threshold:     parseFloat(q("learnConfThreshold")?.value || 0.6),
                    ev_threshold:       parseFloat(q("learnEvThreshold")?.value || 0.05),
                    auto_bet:           q("learnAutoBet")?.value === "1",
                    codes: {
                        greyhound: q("codeGreyhound")?.checked,
                        horse:     q("codeHorse")?.checked,
                        harness:   q("codeHarness")?.checked,
                    },
                };
                localStorage.setItem("learnConfig", JSON.stringify(cfg));
                setText("learnConfigMeta", "✓ Saved to local storage");
            });
        }

        // Restore saved config
        try {
            const saved = JSON.parse(localStorage.getItem("learnConfig") || "{}");
            if (saved.objective && q("learnObjective")) q("learnObjective").value = saved.objective;
            if (saved.conf_threshold && q("learnConfThreshold")) {
                q("learnConfThreshold").value = saved.conf_threshold;
                setText("learnConfValue", saved.conf_threshold.toFixed(2));
            }
            if (saved.ev_threshold && q("learnEvThreshold")) {
                q("learnEvThreshold").value = saved.ev_threshold;
                setText("learnEvValue", saved.ev_threshold.toFixed(2));
            }
        } catch (_) {}
    }

    // -------------------------------------------------------
    // Boot
    // -------------------------------------------------------

    setInterval(() => { pollLearningStatus(); loadActivityFeed(); }, 30000);

    document.addEventListener("DOMContentLoaded", () => {
        bindTabs();
        bindConfig();
        pollLearningStatus();
        loadActivityFeed();
    });
})();

