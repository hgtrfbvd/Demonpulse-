(function () {
    const q = (id) => document.getElementById(id);
    let backtestLog = [];

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    function renderDecisionPill(state) {
        const el = q("btDecisionPill");
        if (!el) return;
        const txt = String(state || "—").toUpperCase();
        el.textContent = txt;
        el.className = "decision-pill decision-none";
        if (txt === "PASS") el.classList.add("decision-pass");
        else if (["APPROVE", "PROMOTE", "BETTER"].includes(txt)) el.classList.add("decision-bet");
        else if (["CAUTION", "MIXED"].includes(txt)) el.classList.add("decision-caution");
    }

    function renderRows(rows) {
        if (!rows?.length) {
            q("btRows").innerHTML = `<tr><td colspan="7" class="board-empty">No backtest results yet.</td></tr>`;
            setText("btTableMeta", "No rows");
            return;
        }

        q("btRows").innerHTML = rows.map(r => `
            <tr>
                <td>${r.date || "—"}</td>
                <td>${r.race || "—"}</td>
                <td>${r.selection || "—"}</td>
                <td>${r.actual || "—"}</td>
                <td>${r.decision || "—"}</td>
                <td>${r.confidence || "—"}</td>
                <td>${r.pl || "—"}</td>
            </tr>
        `).join("");
        setText("btTableMeta", `${rows.length} rows`);
    }

    function renderErrors(errors) {
        const wrap = q("btErrorChips");
        if (!errors?.length) {
            wrap.innerHTML = `<div class="quick-feed-empty">No backtest error data.</div>`;
            return;
        }

        wrap.innerHTML = errors.map(err => `
            <div class="learn-chip">
                <div class="learn-chip-key">${err.tag || "—"}</div>
                <div class="learn-chip-val">${err.count ?? 0}</div>
            </div>
        `).join("");
    }

    function renderLog() {
        const wrap = q("btLogList");
        if (!backtestLog.length) {
            wrap.innerHTML = `<div class="quick-feed-empty">No backtest history this session.</div>`;
            return;
        }

        wrap.innerHTML = backtestLog.slice().reverse().map(item => `
            <div class="sim-log-row">
                <div class="sim-log-main">
                    <div class="sim-log-race">${item.label}</div>
                    <div class="sim-log-sub">${item.sub}</div>
                </div>
                <div class="sim-log-side">${item.time}</div>
            </div>
        `).join("");
    }

    function clearBacktest() {
        ["btDateFrom", "btDateTo"].forEach(id => q(id).value = "");
        q("btCode").value = "ALL";
        q("btBatchSize").value = "50";

        setText("btRunCount", "—");
        setText("btHitRate", "—");
        setText("btROI", "—");
        setText("btStatus", "IDLE");
        setText("btScopeFrom", "—");
        setText("btScopeTo", "—");
        setText("btScopeCode", "—");
        setText("btScopeBatch", "—");
        setText("btScopeMeta", "No backtest run yet");
        setText("btSamples", "—");
        setText("btCorrect", "—");
        setText("btWrong", "—");
        setText("btProfit", "—");
        setText("btAvgConfidence", "—");
        setText("btVerdict", "—");
        setText("btSummaryBox", "No backtest run yet.");
        setText("btRecommendationBox", "No recommendation yet.");
        setText("btControlMeta", "Cleared");
        renderDecisionPill("—");
        renderRows([]);
        renderErrors([]);
    }

    async function runBacktest() {
        const payload = {
            date_from: q("btDateFrom").value || null,
            date_to: q("btDateTo").value || null,
            code: q("btCode").value || "ALL",
            batch_size: parseInt(q("btBatchSize").value || "50", 10),
        };

        setText("btStatus", "RUNNING");
        setText("btControlMeta", "Running backtest...");

        try {
            const data = await api("/api/backtesting/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const summary = data.summary || {};
            const rows = data.rows || [];
            const errors = data.errors || [];
            const recommendation = data.recommendation || "No recommendation yet.";

            setText("btRunCount", summary.samples ?? 0);
            setText("btHitRate", summary.hit_rate || "0%");
            setText("btROI", summary.roi || "0%");
            setText("btStatus", "COMPLETE");

            setText("btScopeFrom", payload.date_from || "—");
            setText("btScopeTo", payload.date_to || "—");
            setText("btScopeCode", payload.code || "ALL");
            setText("btScopeBatch", payload.batch_size);
            setText("btScopeMeta", "Backtest scope loaded");

            setText("btSamples", summary.samples ?? 0);
            setText("btCorrect", summary.correct ?? 0);
            setText("btWrong", summary.wrong ?? 0);
            setText("btProfit", summary.profit || "$0");
            setText("btAvgConfidence", summary.avg_confidence || "—");
            setText("btVerdict", summary.verdict || "—");
            setText("btSummaryBox", summary.summary_text || "No summary.");
            setText("btRecommendationBox", recommendation);

            renderDecisionPill(summary.verdict || "—");
            renderRows(rows);
            renderErrors(errors);
            setText("btControlMeta", "Backtest complete");

            backtestLog.push({
                label: `${payload.code} • ${summary.samples ?? 0} races`,
                sub: `${summary.hit_rate || "0%"} hit • ${summary.roi || "0%"} ROI`,
                time: new Date().toLocaleTimeString("en-AU", {
                    hour12: false,
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit"
                })
            });
            renderLog();
        } catch (error) {
            console.error("Backtest run failed:", error);
            setText("btStatus", "FAILED");
            setText("btControlMeta", "Backtest failed");
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        q("runBacktestBtn").addEventListener("click", runBacktest);
        q("clearBacktestBtn").addEventListener("click", clearBacktest);
        renderLog();
    });
})();
