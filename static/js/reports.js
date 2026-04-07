(function () {

    const q = id => document.getElementById(id);
    let plChart = null;

    // -------------------------------------------------------
    // Tab switching
    // -------------------------------------------------------

    document.querySelectorAll(".report-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".report-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            document.querySelectorAll(".report-section").forEach(s => s.style.display = "none");
            const sec = document.getElementById(`report-${tab.dataset.tab}`);
            if (sec) sec.style.display = "block";

            if (tab.dataset.tab === "system") loadSystemLogs();
            if (tab.dataset.tab === "ai") loadAIPerformance();
        });
    });

    // -------------------------------------------------------
    // P/L Chart
    // -------------------------------------------------------

    function drawPLChart(bets) {
        const ctx = q("profitChart");
        const emptyMsg = q("profitChartEmpty");

        const settled = bets.filter(b => b.result && b.result !== "PENDING");
        if (!settled.length) {
            if (ctx) ctx.style.display = "none";
            if (emptyMsg) emptyMsg.style.display = "block";
            return;
        }

        if (ctx) ctx.style.display = "block";
        if (emptyMsg) emptyMsg.style.display = "none";

        let running = 0;
        const labels = [];
        const values = [];
        for (const b of settled) {
            running += parseFloat(b.pl || 0);
            labels.push(b.date || "");
            values.push(parseFloat(running.toFixed(2)));
        }

        if (plChart) plChart.destroy();

        plChart = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [{
                    data: values,
                    borderColor: running >= 0 ? "#3dd68c" : "#ff2d2d",
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
    }

    // -------------------------------------------------------
    // Summary stats
    // -------------------------------------------------------

    async function loadSummary() {
        try {
            const data = await api("/api/bets/summary");
            const pl = parseFloat(data.pl ?? data.profit ?? 0);

            const totalEl = q("reportTotalBets");
            const profEl  = q("reportProfit");
            const roiEl   = q("reportROI");

            if (totalEl) totalEl.textContent = data.total_bets ?? "0";
            if (q("reportWinRate")) q("reportWinRate").textContent = data.win_rate || data.strike_rate || "0%";

            if (profEl) {
                profEl.textContent = `$${pl.toFixed(2)}`;
                profEl.className = "stats-bar-value " + (pl >= 0 ? "positive" : "negative");
            }
            if (roiEl) roiEl.textContent = data.roi || "0%";
        } catch (_) {}
    }

    // -------------------------------------------------------
    // Bet history
    // -------------------------------------------------------

    async function loadHistory() {
        try {
            const data = await api("/api/bets/history");
            const bets = Array.isArray(data.bets) ? data.bets : (Array.isArray(data) ? data : []);

            const tbody = q("reportTable");
            if (tbody) {
                if (!bets.length) {
                    tbody.innerHTML = `<tr><td colspan="7" class="board-empty">No betting history yet.</td></tr>`;
                } else {
                    tbody.innerHTML = bets.map(b => {
                        const pl = parseFloat(b.pl || 0);
                        const plStr = `<span style="color:${pl >= 0 ? 'var(--green)' : 'var(--red-1)'}">$${pl.toFixed(2)}</span>`;
                        return `
                            <tr>
                                <td>${b.date || "—"}</td>
                                <td>${b.race_uid || b.race || "—"}</td>
                                <td>${b.runner || b.selection || "—"}</td>
                                <td>${b.odds || "—"}</td>
                                <td>$${b.stake || "—"}</td>
                                <td>${b.result || "PENDING"}</td>
                                <td>${plStr}</td>
                            </tr>
                        `;
                    }).join("");
                }
            }

            drawPLChart(bets);
        } catch (_) {
            const tbody = q("reportTable");
            if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="board-empty">No betting history yet.</td></tr>`;
            drawPLChart([]);
        }
    }

    // -------------------------------------------------------
    // AI performance
    // -------------------------------------------------------

    async function loadAIPerformance() {
        try {
            const data = await api("/api/predictions/performance");

            if (q("aiTotal")) q("aiTotal").textContent = data.total_predictions ?? data.total_evaluated ?? "0";
            if (q("aiCorrect")) q("aiCorrect").textContent = data.correct ?? data.correct_picks ?? "0";
            if (q("aiModelVersion")) q("aiModelVersion").textContent = data.model_version || "—";
            if (q("aiEnrichRate")) q("aiEnrichRate").textContent = data.enrichment_rate || "—";

            const log = q("aiLog");
            if (log) {
                const edges = data.edge_patterns || [];
                if (edges.length) {
                    log.innerHTML = edges.map(e =>
                        `<div class="report-log-row">${e.type || e}: ${e.roi || ""}</div>`
                    ).join("");
                } else {
                    log.textContent = "No prediction performance data available yet. Data accumulates as races are analysed.";
                }
            }
        } catch (_) {
            if (q("aiLog")) q("aiLog").textContent = "No prediction performance data available yet.";
        }
    }

    // -------------------------------------------------------
    // System logs
    // -------------------------------------------------------

    async function loadSystemLogs() {
        const container = q("systemLogs");
        if (!container) return;

        try {
            const data = await api("/api/health/live");
            const entries = [];

            if (data.last_bootstrap) entries.push(`Last bootstrap: ${data.last_bootstrap}`);
            if (data.last_refresh)   entries.push(`Last refresh: ${data.last_refresh}`);
            if (data.scheduler_status) entries.push(`Scheduler: ${data.scheduler_status}`);
            if (data.board_count != null) entries.push(`Board races: ${data.board_count}`);
            if (data.formfav_synced != null) entries.push(`FormFav synced: ${data.formfav_synced}`);

            const logs = data.logs || data.recent_logs || [];
            for (const l of logs) entries.push(typeof l === "string" ? l : JSON.stringify(l));

            if (!entries.length) {
                container.innerHTML = `<div class="quick-feed-empty">No system logs available.</div>`;
            } else {
                container.innerHTML = entries.map(e =>
                    `<div class="report-log-row">${e}</div>`
                ).join("");
            }
        } catch (_) {
            container.innerHTML = `<div class="quick-feed-empty">System logs unavailable.</div>`;
        }
    }

    // -------------------------------------------------------
    // Boot
    // -------------------------------------------------------

    document.addEventListener("DOMContentLoaded", () => {
        loadSummary();
        loadHistory();
    });

})();
