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
                if (tab.dataset.learnTab === "insights") loadInsights();
                if (tab.dataset.learnTab === "patterns") loadPatterns();
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

    async function loadInsights() {
        try {
            const data = await api("/api/predictions/performance");
            const insightsList = q("learnInsightsList");
            if (!insightsList) return;

            const total   = data.total_evaluated || 0;
            const winRate = data.win_rate || 0;
            const roi     = data.roi_pct || data.roi || 0;
            const avgOdds = data.avg_winner_odds;

            if (total === 0) {
                insightsList.innerHTML = `<div class="activity-empty">No evaluated races yet.<br>Predictions need results to generate insights.</div>`;
                return;
            }

            const insights = [
                { label: "Win Rate",        value: winRate.toFixed(1) + "%",                         ok: winRate > 25 },
                { label: "ROI",             value: (roi >= 0 ? "+" : "") + roi.toFixed(1) + "%",     ok: roi >= 0 },
                { label: "Avg Winner Odds", value: avgOdds ? "$" + avgOdds.toFixed(2) : "—",         ok: avgOdds > 2 },
                { label: "Top-3 Hit Rate",  value: data.top3_hit_rate ? (data.top3_hit_rate * 100).toFixed(1) + "%" : "—", ok: true },
                { label: "Evaluated Races", value: String(total),                                     ok: true },
                { label: "Current Bank",    value: data.current_bank ? "$" + data.current_bank.toFixed(2) : "—", ok: (data.current_bank || 100) >= 100 },
            ];

            insightsList.innerHTML = insights.map(i => `
                <div style="display:flex;justify-content:space-between;align-items:center;
                            padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
                    <span style="color:var(--text-dim);font-size:0.8rem;letter-spacing:.05em;">${i.label}</span>
                    <span style="font-weight:700;color:${i.ok ? "var(--text)" : "var(--red-1)"};">${i.value}</span>
                </div>
            `).join("");

            const sigEl = q("learnSignalBreakdown");
            if (sigEl) {
                if (data.signal_breakdown && Object.keys(data.signal_breakdown).length) {
                    sigEl.innerHTML = Object.entries(data.signal_breakdown).map(([sig, stats]) => `
                        <div style="display:flex;justify-content:space-between;padding:8px 0;
                                    border-bottom:1px solid rgba(255,255,255,0.05);">
                            <span class="analysis-signal signal-${sig.toLowerCase()}" style="font-size:0.75rem;">${sig}</span>
                            <span style="font-size:0.8rem;color:var(--text-dim);">
                                ${stats.win_rate || "—"} win • ${stats.count || 0} races
                            </span>
                        </div>
                    `).join("");
                } else {
                    sigEl.innerHTML = `<div class="activity-empty">Signal breakdown available after 20+ evaluations per signal.</div>`;
                }
            }

            setText("learnCurrentVersion", data.model_version || "baseline_v1");
            setText("learnEvalCount", String(total));
        } catch (_) {}
    }

    async function loadPatterns() {
        const patEl = q("learnPatternsList");
        if (!patEl) return;
        try {
            const data = await api("/api/predictions/performance");
            const total = data.total_evaluated || 0;
            if (total < 50) {
                patEl.innerHTML = `<div class="activity-empty">Pattern analysis builds after 50+ evaluated races.<br>Currently at ${total} — keep the system running.</div>`;
            } else {
                const winRate = data.win_rate || 0;
                const roi = data.roi || 0;
                const patterns = [];
                if (winRate < 20) patterns.push({ tag: "LOW_WIN_RATE", note: "Model is selecting winners less than 20% of the time" });
                if (roi < -10)   patterns.push({ tag: "NEGATIVE_ROI",  note: "Consistent negative return — review confidence thresholds" });
                if (winRate > 35 && roi < 0) patterns.push({ tag: "ODDS_TOO_SHORT", note: "High win rate but negative ROI — model favouring short-priced runners" });

                if (patterns.length) {
                    patEl.innerHTML = patterns.map(p => `
                        <div style="padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
                            <div style="font-size:0.75rem;font-weight:700;color:var(--amber);
                                        letter-spacing:.08em;margin-bottom:4px;">${p.tag}</div>
                            <div style="font-size:0.8rem;color:var(--text-dim);">${p.note}</div>
                        </div>
                    `).join("");
                } else {
                    patEl.innerHTML = `<div class="activity-empty" style="color:var(--green);">No repeated failure patterns detected. System performing within normal range.</div>`;
                }
            }
        } catch (_) {
            patEl.innerHTML = `<div class="activity-empty">Could not load pattern data.</div>`;
        }
    }

    function bindNewControls() {
        const predictBtn = q("triggerPredictTodayBtn");
        if (predictBtn) {
            predictBtn.addEventListener("click", async () => {
                predictBtn.disabled = true;
                predictBtn.textContent = "Running…";
                try {
                    const data = await api("/api/predictions/today", { method: "POST" });
                    setText("learnPatternsMeta",
                        data.ok ? "✓ Triggered predictions for all upcoming races today"
                                : `Error: ${data.error || "Unknown"}`);
                } catch (_) {
                    setText("learnPatternsMeta", "Failed to trigger predictions");
                }
                predictBtn.disabled = false;
                predictBtn.textContent = "Predict All Today";
            });
        }

        const refreshBtn = q("refreshInsightsBtn");
        if (refreshBtn) {
            refreshBtn.addEventListener("click", () => {
                loadInsights();
                loadPatterns();
                setText("learnPatternsMeta", "Refreshed " + new Date().toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit" }));
            });
        }
    }

    // -------------------------------------------------------
    // Boot (continued)
    // -------------------------------------------------------

    setInterval(() => { pollLearningStatus(); loadActivityFeed(); }, 30000);

    document.addEventListener("DOMContentLoaded", () => {
        bindTabs();
        bindConfig();
        bindNewControls();
        pollLearningStatus();
        loadActivityFeed();
        loadInsights();
    });
})();

