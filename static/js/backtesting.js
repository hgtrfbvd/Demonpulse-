(function () {
    const q = (id) => document.getElementById(id);
    let backtestLog = [];

    function esc(str) {
        return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

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
                    <div class="sim-log-race">${esc(item.label)}</div>
                    <div class="sim-log-sub">${esc(item.sub)}</div>
                </div>
                <div class="sim-log-side">${esc(item.time)}</div>
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

    async function generateBacktestRecommendation(result) {
        const recEl = q("btRecommendationBox");
        if (!recEl) return;
        recEl.textContent = "Generating AI recommendation…";

        const prompt = `Backtest complete. In 2 sentences, tell the operator what this means and what to do next.
Samples: ${result.total || result.samples || 0}, Correct: ${result.correct || 0}, ROI: ${result.roi || "0%"}, Profit: $${result.profit || "0"}
Be direct. If ROI is negative, say so clearly.`;

        try {
            const anthropicKey = window.ANTHROPIC_API_KEY || "";
            const resp = await fetch("https://api.anthropic.com/v1/messages", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    ...(anthropicKey ? { "x-api-key": anthropicKey, "anthropic-version": "2023-06-01" } : {})
                },
                body: JSON.stringify({
                    model: "claude-sonnet-4-20250514",
                    max_tokens: 100,
                    messages: [{ role: "user", content: prompt }]
                })
            });
            const data = await resp.json();
            recEl.textContent = data.content?.[0]?.text || "No recommendation available.";
        } catch (e) {
            recEl.textContent = "AI recommendation unavailable.";
        }
    }

    async function runBacktest() {
        const from = q("btDateFrom")?.value;
        const to   = q("btDateTo")?.value;
        const code = q("btCode")?.value || "ALL";
        const batchSize = parseInt(q("btBatchSize")?.value || "50", 10);

        if (!from || !to) { alert("Select date range"); return; }

        setText("btStatus", "RUNNING");
        setText("btControlMeta", "Running backtest...");

        try {
            const data = await api("/api/admin/backtest", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    date_from: from,
                    date_to: to,
                    code_filter: code !== "ALL" ? code : null,
                    batch_size: batchSize,
                })
            });

            setText("btStatus", data.ok !== false ? "DONE" : "ERROR");

            if (data.ok !== false) {
                const summary = data.summary || {};
                const rows    = data.rows    || [];
                const errors  = data.errors  || [];

                setText("btRunCount", summary.samples ?? 0);
                setText("btHitRate", summary.hit_rate || "0%");
                setText("btROI", summary.roi || "0%");

                setText("btScopeFrom", from);
                setText("btScopeTo", to);
                setText("btScopeCode", code);
                setText("btScopeBatch", batchSize);
                setText("btScopeMeta", "Backtest scope loaded");

                setText("btSamples", summary.samples ?? 0);
                setText("btCorrect", summary.correct ?? 0);
                setText("btWrong", summary.wrong ?? 0);
                setText("btProfit", summary.profit || "$0");
                setText("btAvgConfidence", summary.avg_confidence || "—");
                setText("btVerdict", summary.verdict || "—");
                setText("btSummaryBox", summary.summary_text || "No summary.");

                renderDecisionPill(summary.verdict || "—");
                renderRows(rows);
                renderErrors(errors);
                setText("btControlMeta", "Backtest complete");

                backtestLog.push({
                    label: `${code} • ${summary.samples ?? 0} races`,
                    sub: `${summary.hit_rate || "0%"} hit • ${summary.roi || "0%"} ROI`,
                    time: new Date().toLocaleTimeString("en-AU", {
                        hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit"
                    })
                });
                renderLog();

                // AI recommendation (Section 8c)
                generateBacktestRecommendation({
                    total: summary.samples,
                    correct: summary.correct,
                    roi: summary.roi,
                    profit: summary.profit,
                });
            } else {
                setText("btControlMeta", `Error: ${data.error || "Unknown"}`);
                setText("btRecommendationBox", "Backtest failed.");
            }
        } catch (error) {
            console.error("Backtest run failed:", error);
            setText("btStatus", "FAILED");
            setText("btControlMeta", "Backtest failed");
            setText("btRecommendationBox", "Backtest failed — check console for details.");
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        q("runBacktestBtn").addEventListener("click", runBacktest);
        q("clearBacktestBtn").addEventListener("click", clearBacktest);
        renderLog();
    });
})();
