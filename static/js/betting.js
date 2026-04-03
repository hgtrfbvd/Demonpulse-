(function () {

    const q = id => document.getElementById(id);

    let openBets = [];
    let historyBets = [];
    let bank = 1000;

    function setText(id, val) {
        if (q(id)) q(id).textContent = val;
    }

    function renderStats() {
        setText("betBank", `$${bank}`);

        const exposure = openBets.reduce((a, b) => a + Number(b.stake || 0), 0);
        setText("betExposure", `$${exposure}`);

        const profit = historyBets.reduce((a, b) => a + (b.pl || 0), 0);
        setText("betPL", `$${profit}`);

        const wins = historyBets.filter(b => b.result === "WIN").length;
        const strike = historyBets.length ? ((wins / historyBets.length) * 100).toFixed(1) : 0;
        setText("betStrike", `${strike}%`);
    }

    function renderOpen() {
        if (!openBets.length) {
            q("openBetsRows").innerHTML = `<tr><td colspan="6" class="board-empty">No open bets</td></tr>`;
            return;
        }

        q("openBetsRows").innerHTML = openBets.map(b => `
            <tr>
                <td>${b.race}</td>
                <td>${b.runner}</td>
                <td>${b.type}</td>
                <td>${b.odds}</td>
                <td>$${b.stake}</td>
                <td>OPEN</td>
            </tr>
        `).join("");
    }

    function renderHistory() {
        if (!historyBets.length) {
            q("betHistoryRows").innerHTML = `<tr><td colspan="7" class="board-empty">No history</td></tr>`;
            return;
        }

        q("betHistoryRows").innerHTML = historyBets.map(b => `
            <tr>
                <td>${b.race}</td>
                <td>${b.runner}</td>
                <td>${b.type}</td>
                <td>${b.odds}</td>
                <td>$${b.stake}</td>
                <td>${b.result}</td>
                <td>$${b.pl}</td>
            </tr>
        `).join("");
    }

    function placeBet() {
        const bet = {
            race: q("betRaceUid").value || "—",
            runner: q("betRunner").value || "—",
            box: q("betBox").value || "—",
            odds: parseFloat(q("betOdds").value || 0),
            stake: parseFloat(q("betStake").value || 0),
            type: q("betType").value
        };

        openBets.push(bet);
        bank -= bet.stake;

        renderOpen();
        renderStats();
    }

    function clearForm() {
        ["betRaceUid","betRunner","betBox","betOdds","betStake"].forEach(id => q(id).value = "");
    }

    function bind() {
        q("placeBetBtn").onclick = placeBet;
        q("clearBetBtn").onclick = clearForm;

        q("resetBankBtn").onclick = () => {
            bank = 1000;
            openBets = [];
            historyBets = [];
            renderAll();
        };
    }

    function renderAll() {
        renderOpen();
        renderHistory();
        renderStats();
    }

    document.addEventListener("DOMContentLoaded", () => {
        bind();
        renderAll();
    });

})();
