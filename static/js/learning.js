(function () {
    const tabs = document.querySelectorAll("[data-learn-tab]");
    const sections = {
        overview: document.getElementById("learn-overview"),
        reverse: document.getElementById("learn-reverse"),
        filters: document.getElementById("learn-filters"),
        promotions: document.getElementById("learn-promotions"),
    };

    const q = (id) => document.getElementById(id);

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    function bindTabs() {
        tabs.forEach(tab => {
            tab.addEventListener("click", () => {
                tabs.forEach(t => t.classList.remove("active"));
                tab.classList.add("active");
                Object.values(sections).forEach(s => s.style.display = "none");
                sections[tab.dataset.learnTab].style.display = "block";
            });
        });
    }

    function renderOverview(data) {
        setText("learnEdgeCount", data.edge_count ?? 0);
        setText("learnErrorCount", data.error_count ?? 0);
        setText("learnPromotionCount", data.promotion_count ?? 0);
        setText("learnShadowStatus", data.shadow_active ? "ON" : "OFF");

        setText("learnTopFinding", data.top_finding || "—");
        setText("learnWeakestArea", data.weakest_area || "—");
        setText("learnBestEdgeType", data.best_edge_type || "—");
        setText("learnFocusArea", data.focus_area || "—");
        setText("learnOverviewText", data.summary || "No learning summary loaded yet.");
        setText("learnOverviewMeta", "Learning summary loaded");
    }

    function renderEdges(edges) {
        const rows = q("learnEdgeRows");
        if (!edges?.length) {
            rows.innerHTML = `<tr><td colspan="5" class="board-empty">No edge data</td></tr>`;
            setText("learnEdgeMeta", "No data");
            return;
        }

        rows.innerHTML = edges.map(e => `
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
            rows.innerHTML = `<tr><td colspan="5" class="board-empty">No filter candidates</td></tr>`;
            setText("learnFilterMeta", "No filters loaded");
            return;
        }

        rows.innerHTML = filters.map(f => `
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
            rows.innerHTML = `<tr><td colspan="5" class="board-empty">No promotion data</td></tr>`;
            setText("learnPromotionMeta", "No promotions queued");
            return;
        }

        rows.innerHTML = promotions.map(p => `
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

    async function loadLearning() {
        try {
            const data = await api("/api/learning/summary");

            renderOverview(data.overview || {});
            renderEdges(data.edges || []);
            renderErrors(data.errors || []);
            renderStatus(data.status || []);
            renderReverse(data.reverse || null);
            renderFilters(data.filters || []);
            renderPromotions(data.promotions || []);
        } catch (error) {
            console.error("Learning load failed:", error);
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        bindTabs();
        loadLearning();
    });
})();
