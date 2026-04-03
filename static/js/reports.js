(function () {

    const tabs = document.querySelectorAll(".report-tab");
    const sections = {
        performance: document.getElementById("report-performance"),
        ai: document.getElementById("report-ai"),
        system: document.getElementById("report-system")
    };

    tabs.forEach(tab => {
        tab.addEventListener("click", () => {
            tabs.forEach(t => t.classList.remove("active"));
            tab.classList.add("active");

            Object.values(sections).forEach(s => s.style.display = "none");
            sections[tab.dataset.tab].style.display = "block";
        });
    });

    async function loadReports() {
        try {
            const data = await api("/api/reports/summary");

            document.getElementById("reportTotalBets").textContent = data.total_bets || 0;
            document.getElementById("reportWinRate").textContent = data.win_rate || "0%";
            document.getElementById("reportProfit").textContent = data.profit || "$0";
            document.getElementById("reportROI").textContent = data.roi || "0%";

            const table = document.getElementById("reportTable");
            table.innerHTML = (data.bets || []).map(b => `
                <tr>
                    <td>${b.date}</td>
                    <td>${b.race}</td>
                    <td>${b.selection}</td>
                    <td>${b.odds}</td>
                    <td>${b.stake}</td>
                    <td>${b.result}</td>
                    <td>${b.pl}</td>
                </tr>
            `).join("");

            document.getElementById("aiCorrect").textContent = data.ai.correct || 0;
            document.getElementById("aiFalse").textContent = data.ai.false || 0;
            document.getElementById("aiMissed").textContent = data.ai.missed || 0;
            document.getElementById("aiEdge").textContent = data.ai.edge || "0%";

            document.getElementById("aiLog").innerHTML = (data.ai.logs || [])
                .map(l => `<div class="report-log-row">${l}</div>`)
                .join("");

            document.getElementById("systemLogs").innerHTML = (data.logs || [])
                .map(l => `<div class="report-log-row">${l}</div>`)
                .join("");

        } catch (err) {
            console.error("Reports load failed", err);
        }
    }

    document.addEventListener("DOMContentLoaded", loadReports);

})();
