(function () {

    const _AEST = "Australia/Sydney";
    const q = id => document.getElementById(id);

    let openBets = [];
    let historyBets = [];
    let bankData = { bank: 1000, pl: 0, strike: "0%" };
    let boardItems = [];

    function setText(id, val) {
        const el = q(id);
        if (el) el.textContent = val ?? "—";
    }

    function formatTrack(slug) {
        if (!slug) return "—";
        return slug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    }

    function formatJumpTime(item) {
        if (item.jump_dt_iso) {
            const dt = new Date(item.jump_dt_iso);
            if (!isNaN(dt.getTime())) {
                return dt.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", timeZone: _AEST });
            }
        }
        return item.jump_time || "";
    }

    // -------------------------------------------------------
    // Stats bar
    // -------------------------------------------------------

    function renderStats() {
        const bankEl = q("betBank");
        if (bankEl) {
            bankEl.textContent = `$${Number(bankData.bank || 0).toFixed(2)}`;
            bankEl.className = "stats-bar-value";
        }

        const exposure = openBets.reduce((a, b) => a + Number(b.stake || 0), 0);
        setText("betExposure", `$${exposure.toFixed(2)}`);

        const plEl = q("betPL");
        if (plEl) {
            const pl = Number(bankData.pl || historyBets.reduce((a, b) => a + (b.pl || 0), 0));
            plEl.textContent = `$${pl.toFixed(2)}`;
            plEl.className = "stats-bar-value " + (pl >= 0 ? "positive" : "negative");
        }

        setText("betStrike", bankData.strike || "0%");
    }

    // -------------------------------------------------------
    // Open bets
    // -------------------------------------------------------

    function renderOpen() {
        const tbody = q("openBetsRows");
        if (!tbody) return;
        setText("betOpenMeta", `${openBets.length} open`);

        if (!openBets.length) {
            tbody.innerHTML = `<tr><td colspan="8" class="board-empty">No open bets</td></tr>`;
            return;
        }

        tbody.innerHTML = openBets.map(b => {
            const returns = b.odds && b.stake ? (parseFloat(b.odds) * parseFloat(b.stake)).toFixed(2) : "—";
            return `
                <tr>
                    <td>${b.race_uid || b.race || "—"}</td>
                    <td>${b.runner || "—"}</td>
                    <td>${b.bet_type || b.type || "WIN"}</td>
                    <td>${b.odds || "—"}</td>
                    <td>$${b.stake || "—"}</td>
                    <td>${returns !== "—" ? "$" + returns : "—"}</td>
                    <td>OPEN</td>
                    <td><button class="dp-btn" style="padding:2px 8px;font-size:0.72rem;"
                        onclick="settleManual('${b.id || ""}')">Settle</button></td>
                </tr>
            `;
        }).join("");
    }

    // -------------------------------------------------------
    // Bet history
    // -------------------------------------------------------

    function renderHistory() {
        const tbody = q("betHistoryRows");
        if (!tbody) return;

        if (!historyBets.length) {
            tbody.innerHTML = `<tr><td colspan="7" class="board-empty">No history</td></tr>`;
            return;
        }

        tbody.innerHTML = historyBets.map(b => {
            const pl = b.pl || 0;
            const plStr = `<span style="color:${pl >= 0 ? 'var(--green)' : 'var(--red-1)'}">$${parseFloat(pl).toFixed(2)}</span>`;
            return `
                <tr>
                    <td>${b.race_uid || b.race || "—"}</td>
                    <td>${b.runner || "—"}</td>
                    <td>${b.bet_type || b.type || "WIN"}</td>
                    <td>${b.odds || "—"}</td>
                    <td>$${b.stake || "—"}</td>
                    <td>${b.result || "PENDING"}</td>
                    <td>${plStr}</td>
                </tr>
            `;
        }).join("");
    }

    // -------------------------------------------------------
    // Calculate returns
    // -------------------------------------------------------

    function calcReturns() {
        const odds = parseFloat(q("betOdds")?.value) || 0;
        const stake = parseFloat(q("betStake")?.value) || 0;
        const el = q("betReturns");
        if (el) el.textContent = odds > 0 && stake > 0 ? `$${(odds * stake).toFixed(2)}` : "—";
    }

    // -------------------------------------------------------
    // Load race board
    // -------------------------------------------------------

    async function loadBoard() {
        try {
            const data = await api("/api/home/board");
            boardItems = Array.isArray(data.items) ? data.items : [];

            const sel = q("betRaceSelect");
            if (!sel) return;

            if (!boardItems.length) {
                sel.innerHTML = `<option value="">No races available</option>`;
                return;
            }

            // Sort by soonest
            const sorted = [...boardItems].sort((a, b) => {
                const t = x => x.seconds_to_jump ?? Infinity;
                return t(a) - t(b);
            });

            sel.innerHTML = `<option value="">Select race…</option>` +
                sorted.map(item => {
                    const label = `${formatTrack(item.track)} R${item.race_num || "?"} (${formatJumpTime(item)})`;
                    return `<option value="${item.race_uid || ''}">${label}</option>`;
                }).join("");
        } catch (e) {
            console.error("Board load failed:", e);
        }
    }

    // -------------------------------------------------------
    // Load runners for selected race
    // -------------------------------------------------------

    async function loadRunners(raceUid) {
        const runnerSel = q("betRunnerSelect");
        if (!runnerSel) return;

        if (!raceUid) {
            runnerSel.innerHTML = `<option value="">Select race first…</option>`;
            if (q("betOdds")) q("betOdds").value = "";
            calcReturns();
            return;
        }

        runnerSel.innerHTML = `<option value="">Loading runners…</option>`;

        try {
            const data = await api(`/api/live/race/${encodeURIComponent(raceUid)}`);
            const runners = (Array.isArray(data.runners) && data.runners.length)
                ? data.runners
                : (data.analysis?.all_runners || []);

            const active = runners.filter(r => !r.scratched);

            if (!active.length) {
                runnerSel.innerHTML = `<option value="">No runners found</option>`;
                return;
            }

            runnerSel.innerHTML = `<option value="">Select runner…</option>` +
                active.map(r => {
                    const box = r.box_num ?? r.number ?? r.barrier ?? "";
                    const odds = r.price || r.win_odds || "";
                    const label = `#${box} ${r.name || "—"}${odds ? " ($" + parseFloat(odds).toFixed(2) + ")" : ""}`;
                    return `<option value="${r.name || ''}" data-odds="${odds}">${label}</option>`;
                }).join("");
        } catch (e) {
            console.error("Runner load failed:", e);
            runnerSel.innerHTML = `<option value="">Failed to load runners</option>`;
        }
    }

    // -------------------------------------------------------
    // Load summary stats
    // -------------------------------------------------------

    async function loadSummary() {
        try {
            const data = await api("/api/bets/summary");
            bankData = {
                bank: data.bank ?? 1000,
                pl: data.pl ?? data.today_pl ?? 0,
                strike: data.strike_rate || data.win_rate || "0%",
            };
            renderStats();
        } catch (_) {
            renderStats();
        }
    }

    // -------------------------------------------------------
    // Load open bets
    // -------------------------------------------------------

    async function loadOpenBets() {
        try {
            const data = await api("/api/bets/open");
            openBets = Array.isArray(data.bets) ? data.bets : (Array.isArray(data) ? data : []);
            renderOpen();
        } catch (_) {
            renderOpen();
        }
    }

    // -------------------------------------------------------
    // Load bet history
    // -------------------------------------------------------

    async function loadHistory() {
        try {
            const data = await api("/api/bets/history");
            historyBets = Array.isArray(data.bets) ? data.bets : (Array.isArray(data) ? data : []);
            renderHistory();
        } catch (_) {
            renderHistory();
        }
    }

    // -------------------------------------------------------
    // Place bet
    // -------------------------------------------------------

    async function placeBet() {
        const raceUid = q("betRaceSelect")?.value;
        const runner  = q("betRunnerSelect")?.value;
        const odds    = parseFloat(q("betOdds")?.value) || 0;
        const stake   = parseFloat(q("betStake")?.value) || 0;
        const betType = q("betType")?.value || "WIN";

        if (!raceUid || !runner || !odds || !stake) {
            setText("betCreateMeta", "Fill all required fields");
            return;
        }

        setText("betCreateMeta", "Placing…");

        try {
            const data = await api("/api/bets/place", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ race_uid: raceUid, runner, box: null, odds, stake, bet_type: betType })
            });

            if (data.ok !== false) {
                setText("betCreateMeta", "✓ Bet placed");
                clearForm();
                await loadOpenBets();
                await loadSummary();
            } else {
                setText("betCreateMeta", `✗ ${data.error || "Failed"}`);
            }
        } catch (e) {
            setText("betCreateMeta", "✗ Placement failed");
            console.error("Place bet failed:", e);
        }
    }

    function clearForm() {
        if (q("betRaceSelect"))  q("betRaceSelect").value = "";
        if (q("betRunnerSelect")) q("betRunnerSelect").innerHTML = `<option value="">Select race first…</option>`;
        if (q("betOdds"))   q("betOdds").value = "";
        if (q("betStake"))  q("betStake").value = "";
        if (q("betType"))   q("betType").value = "WIN";
        calcReturns();
    }

    // -------------------------------------------------------
    // Export as CSV
    // -------------------------------------------------------

    function exportCSV() {
        const all = [...openBets.map(b => ({ ...b, result: "OPEN" })), ...historyBets];
        if (!all.length) { alert("No bets to export."); return; }

        const header = "race_uid,runner,bet_type,odds,stake,result,pl";
        const rows = all.map(b =>
            [b.race_uid || b.race, b.runner, b.bet_type || b.type, b.odds, b.stake, b.result, b.pl ?? ""].join(",")
        );
        const csv = [header, ...rows].join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "bets.csv";
        a.click();
        URL.revokeObjectURL(url);
    }

    // -------------------------------------------------------
    // Manual settle (stub)
    // -------------------------------------------------------

    window.settleManual = async function (betId) {
        if (!betId) return;
        const result = prompt("Enter result (WIN/LOSE/PLACE):", "WIN");
        if (!result) return;
        try {
            await api(`/api/bets/settle`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ bet_id: betId, result: result.toUpperCase() })
            });
            await loadOpenBets();
            await loadHistory();
            await loadSummary();
        } catch (_) {}
    };

    // -------------------------------------------------------
    // Boot
    // -------------------------------------------------------

    document.addEventListener("DOMContentLoaded", () => {
        q("placeBetBtn")?.addEventListener("click", placeBet);
        q("clearBetBtn")?.addEventListener("click", clearForm);

        q("resetBankBtn")?.addEventListener("click", async () => {
            const amount = prompt("Enter new starting bank amount:", "1000");
            if (!amount || isNaN(parseFloat(amount))) return;
            try {
                await api("/api/bets/reset-bank", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ amount: parseFloat(amount) })
                });
                await loadSummary();
                await loadOpenBets();
                await loadHistory();
            } catch (_) {
                bankData.bank = parseFloat(amount);
                renderStats();
            }
        });

        q("exportBetsBtn")?.addEventListener("click", exportCSV);

        q("betRaceSelect")?.addEventListener("change", (e) => {
            loadRunners(e.target.value);
        });

        q("betRunnerSelect")?.addEventListener("change", (e) => {
            const sel = e.target;
            const opt = sel.options[sel.selectedIndex];
            const odds = opt?.dataset?.odds;
            if (odds && q("betOdds")) q("betOdds").value = odds;
            calcReturns();
        });

        q("betOdds")?.addEventListener("input", calcReturns);
        q("betStake")?.addEventListener("input", calcReturns);

        loadBoard();
        loadSummary();
        loadOpenBets();
        loadHistory();
    });

})();
