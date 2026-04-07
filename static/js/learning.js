(function () {
    const q = (id) => document.getElementById(id);

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    // -------------------------------------------------------
    // Tab switching (Section 7d fix)
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
            });
        });
    }

    // -------------------------------------------------------
    // Render helpers
    // -------------------------------------------------------

    function renderOverview(data) {
        setText("learnEdgeCount", data.edge_count ?? 0);
        setText("learnErrorCount", data.error_count ?? 0);
        setText("learnPromotionCount", data.promotion_count ?? 0);
        setText("learnShadowStatus", data.shadow_active ? "ON" : "OFF");
        setText("learnTopFinding", data.top_finding || "—");
        setText("learnWeakestArea", data.weakest_area || "—");
        setText("learnBestEdgeType", data.best_edge_type || "—");
        setText("learnFocusArea", data.focus_area || "—");

        const overviewText = q("learnOverviewText");
        if (overviewText) overviewText.textContent = data.summary || "No learning summary loaded yet.";
        setText("learnOverviewMeta", "Learning summary loaded");
    }

    function renderEdges(edges) {
        const rows = q("learnEdgeRows");
        if (!edges?.length) {
            if (rows) rows.innerHTML = `<tr><td colspan="5" class="board-empty">No edge data yet — accumulates as races are analysed.</td></tr>`;
            setText("learnEdgeMeta", "No data");
            return;
        }
        if (rows) rows.innerHTML = edges.map(e => `
            <tr>
                <td>${e.edge_type || "—"}</td>
                <td>${e.samples ?? "—"}</td>
                <td>${e.roi ?? "—"}</td>
                <td>${e.status || "—"}</td>
                <td>${e.action || "WATCH"}</td>
            </tr>
        `).join("");
        setText("learnEdgeMeta", `${edges.length} edge rows`);
    }

    function renderErrors(errors) {
        const wrap = q("learnErrorChips");
        if (!wrap) return;
        if (!errors?.length) {
            wrap.innerHTML = `<div class="quick-feed-empty">No error tags loaded.</div>`;
            return;
        }
        wrap.innerHTML = errors.map(err => `
            <div class="learn-chip">
                <div class="learn-chip-key">${err.tag || "—"}</div>
                <div class="learn-chip-val">${err.count ?? 0}</div>
            </div>
        `).join("");
    }

    function renderStatus(items) {
        const wrap = q("learnStatusList");
        if (!wrap) return;
        if (!items?.length) {
            wrap.innerHTML = `<div class="sim-log-row"><div class="sim-log-main"><div class="sim-log-race">Status unavailable</div></div></div>`;
            return;
        }
        wrap.innerHTML = items.map(item => `
            <div class="sim-log-row">
                <div class="sim-log-main">
                    <div class="sim-log-race">${item.label || "—"}</div>
                    <div class="sim-log-sub">${item.value || "—"}</div>
                </div>
            </div>
        `).join("");
    }

    function renderReverse(reverse) {
        setText("learnWhyWon", reverse?.why_won || "No winning case loaded.");
        setText("learnWhyLost", reverse?.why_lost || "No losing case loaded.");
        setText("learnMissedWinner", reverse?.missed_winner || "No missed-winner case loaded.");
        setText("learnRecommendedChange", reverse?.recommended_change || "No recommendation yet.");
        setText("learnReverseMeta", reverse ? "Reverse analysis loaded" : "No case loaded");
    }

    function renderFilters(filters) {
        const rows = q("learnFilterRows");
        if (!filters?.length) {
            if (rows) rows.innerHTML = `<tr><td colspan="5" class="board-empty">No filter candidates yet.</td></tr>`;
            setText("learnFilterMeta", "No filters loaded");
            return;
        }
        if (rows) rows.innerHTML = filters.map(f => `
            <tr>
                <td>${f.name || "—"}</td>
                <td>${f.type || "—"}</td>
                <td>${f.effect || "—"}</td>
                <td>${f.confidence || "—"}</td>
                <td>${f.state || "WATCH"}</td>
            </tr>
        `).join("");
        setText("learnFilterMeta", `${filters.length} filters loaded`);
    }

    function renderPromotions(promotions) {
        const rows = q("learnPromotionRows");
        if (!promotions?.length) {
            if (rows) rows.innerHTML = `<tr><td colspan="5" class="board-empty">No promotion data yet.</td></tr>`;
            setText("learnPromotionMeta", "No promotions queued");
            return;
        }
        if (rows) rows.innerHTML = promotions.map(p => `
            <tr>
                <td>${p.adjustment || "—"}</td>
                <td>${p.direction || "—"}</td>
                <td>${p.amount || "—"}</td>
                <td>${p.reason || "—"}</td>
                <td>${p.state || "PENDING"}</td>
            </tr>
        `).join("");
        setText("learnPromotionMeta", `${promotions.length} promotion rows`);
    }

    // -------------------------------------------------------
    // Load from real API (Section 7a)
    // -------------------------------------------------------

    async function loadPerformance() {
        try {
            const data = await api("/api/predictions/performance");
            // Map performance data to overview stats
            const overview = {
                edge_count: data.edge_patterns?.length ?? data.edge_count ?? 0,
                error_count: data.error_count ?? 0,
                promotion_count: data.promotion_count ?? 0,
                shadow_active: data.shadow_active ?? false,
                top_finding: data.top_finding || (data.edge_patterns?.[0]?.type) || "—",
                weakest_area: data.weakest_area || "—",
                best_edge_type: data.best_edge_type || (data.edge_patterns?.[0]?.type) || "—",
                focus_area: data.focus_area || "—",
                summary: data.summary || `Model version: ${data.model_version || "—"}. Total evaluated: ${data.total_evaluated ?? data.total_predictions ?? 0}. Correct: ${data.correct ?? 0}.`,
            };
            renderOverview(overview);
            renderEdges(data.edge_patterns || data.edges || []);
            renderErrors(data.errors || []);
            renderStatus(data.status || [
                { label: "Model Version", value: data.model_version || "—" },
                { label: "Total Evaluated", value: String(data.total_evaluated ?? data.total_predictions ?? 0) },
                { label: "Enrichment Rate", value: data.enrichment_rate || "—" },
            ]);
            renderReverse(data.reverse || null);
            renderFilters(data.filters || []);
            renderPromotions(data.promotions || []);
        } catch (_) {
            // No data yet — show placeholder messages
            renderOverview({
                summary: "No learning data yet — predictions run automatically after races are stored.",
            });
            renderEdges([]);
        }
    }

    async function loadToday() {
        try {
            const data = await api("/api/predictions/today");
            if (data.summary) {
                const overviewText = q("learnOverviewText");
                if (overviewText && overviewText.textContent === "No learning summary loaded yet.") {
                    overviewText.textContent = data.summary;
                }
            }
        } catch (_) {}
    }

    // -------------------------------------------------------
    // Boot
    // -------------------------------------------------------

    document.addEventListener("DOMContentLoaded", () => {
        bindTabs();
        loadPerformance();
        loadToday();
    });
})();
