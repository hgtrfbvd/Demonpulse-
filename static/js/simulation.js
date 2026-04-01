// DEMONPULSE V7 — Race Simulation Engine
// Visual only — respects early speed, position changes, finish strength

const BOX_COLORS = ['#e74c3c','#3498db','#2ecc71','#f1c40f','#9b59b6','#e67e22','#1abc9c','#e91e63'];

class RaceSimulator {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.runners = [];
    this.phase = 0;
    this.phases = ['JUMP', 'EARLY', 'MID', 'TURN', 'RUN HOME', 'FINISH'];
    this.interval = null;
    this.running = false;
  }

  setRunners(runners) {
    this.runners = runners.map((r, i) => ({
      ...r,
      boxColor: BOX_COLORS[i % BOX_COLORS.length],
      position: i,
      xPct: 5,
      lane: i,
    }));
    this.render();
  }

  getDefaultRunners() {
    return [
      { box: 1, name: 'Runner 1', style: 'LEADER', speed: 9 },
      { box: 2, name: 'Runner 2', style: 'RAILER', speed: 7 },
      { box: 3, name: 'Runner 3', style: 'CHASER', speed: 8 },
      { box: 4, name: 'Runner 4', style: 'WIDE', speed: 6 },
      { box: 5, name: 'Runner 5', style: 'CHASER', speed: 7 },
      { box: 6, name: 'Runner 6', style: 'LEADER', speed: 8 },
    ];
  }

  render() {
    if (!this.container) return;
    this.container.innerHTML = '';
    const trackH = this.container.offsetHeight || 180;
    const laneH = Math.min(28, (trackH - 20) / Math.max(this.runners.length, 1));

    // Track markers
    [15, 35, 60, 82].forEach((pct, i) => {
      const line = document.createElement('div');
      line.className = 'track-line';
      line.style.left = pct + '%';
      const lbl = document.createElement('div');
      lbl.className = 'track-lbl';
      lbl.style.cssText = `position:absolute;top:4px;left:${pct}%;font-size:7px;color:#444;transform:translateX(-50%);`;
      lbl.textContent = ['JUMP','200m','TURN','RUN'][i];
      this.container.appendChild(line);
      this.container.appendChild(lbl);
    });

    // Finish line
    const finish = document.createElement('div');
    finish.style.cssText = 'position:absolute;right:0;top:0;bottom:0;width:3px;background:#cc0000;opacity:0.7;';
    this.container.appendChild(finish);

    // Runners
    this.runners.forEach((r, i) => {
      const el = document.createElement('div');
      el.className = 'sim-runner';
      el.id = 'runner-' + i;
      const top = 16 + (i * (laneH + 4));
      el.style.cssText = `position:absolute;top:${top}px;left:${r.xPct}%;display:flex;align-items:center;gap:5px;transition:left 0.6s ease;`;
      el.innerHTML = `<div style="width:22px;height:22px;border-radius:3px;background:${r.boxColor};display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:bold;color:#000;flex-shrink:0;">${r.box}</div><div style="font-size:9px;color:#e0e0e0;white-space:nowrap;">${r.name}</div>`;
      this.container.appendChild(el);
    });
  }

  start() {
    if (this.running) return;
    if (this.runners.length === 0) this.setRunners(this.getDefaultRunners());
    this.running = true;
    this.phase = 0;
    this.render();
    this.interval = setInterval(() => this.step(), 900);
  }

  step() {
    if (this.phase >= this.phases.length) {
      this.stop();
      this.updatePhaseLabel('FINISH');
      this.updateSimInfo();
      return;
    }
    this.updatePhaseLabel(this.phases[this.phase]);
    this.moveRunners(this.phase);
    this.updateSimInfo();
    this.phase++;
  }

  moveRunners(phase) {
    const phaseX = [10, 28, 50, 68, 82, 94];
    const targetX = phaseX[Math.min(phase, phaseX.length - 1)];

    // Sort by speed for this phase
    const sorted = [...this.runners].map((r, i) => ({ ...r, idx: i }));

    if (phase === 0) {
      // Jump: leaders go first
      sorted.sort((a, b) => {
        const aScore = (a.style === 'LEADER' ? 3 : a.style === 'RAILER' ? 2 : 1) + (a.speed || 5) * 0.1;
        const bScore = (b.style === 'LEADER' ? 3 : b.style === 'RAILER' ? 2 : 1) + (b.speed || 5) * 0.1;
        return bScore - aScore;
      });
    } else if (phase >= 4) {
      // Run home: chasers come through
      sorted.sort((a, b) => {
        const aScore = (a.style === 'CHASER' ? 3 : a.style === 'RAILER' ? 2.5 : 2) + (a.speed || 5) * 0.1;
        const bScore = (b.style === 'CHASER' ? 3 : b.style === 'RAILER' ? 2.5 : 2) + (b.speed || 5) * 0.1;
        return bScore - aScore;
      });
    } else {
      sorted.sort((a, b) => (b.speed || 5) - (a.speed || 5));
    }

    sorted.forEach((r, rank) => {
      const spread = (rank / Math.max(sorted.length - 1, 1)) * 8;
      const x = targetX - spread;
      const el = document.getElementById('runner-' + r.idx);
      if (el) el.style.left = Math.max(2, x) + '%';
    });

    this.currentLeader = sorted[0];
  }

  updatePhaseLabel(label) {
    const el = document.getElementById('sim-phase');
    if (el) el.textContent = 'Phase: ' + label;
  }

  updateSimInfo() {
    if (this.currentLeader) {
      const leaderEl = document.getElementById('sim-leader');
      if (leaderEl) leaderEl.textContent = this.currentLeader.name;
    }
    const phaseNames = ['—', 'SLOW', 'MODERATE', 'FAST', 'HOT', 'SPRINT'];
    const paceEl = document.getElementById('sim-pace');
    if (paceEl) paceEl.textContent = phaseNames[Math.min(this.phase, phaseNames.length - 1)];
  }

  stop() {
    this.running = false;
    if (this.interval) { clearInterval(this.interval); this.interval = null; }
  }

  reset() {
    this.stop();
    this.phase = 0;
    this.runners.forEach(r => r.xPct = 5);
    this.render();
    const phaseEl = document.getElementById('sim-phase');
    if (phaseEl) phaseEl.textContent = 'Phase: —';
    const statusEl = document.getElementById('sim-status');
    if (statusEl) statusEl.textContent = 'READY';
  }
}

// Global instance
let sim = null;

function initSim() {
  sim = new RaceSimulator('sim-track');
  sim.render();
}

function startSim() {
  if (!sim) initSim();
  sim.start();
  const el = document.getElementById('sim-status');
  if (el) el.textContent = 'RUNNING';
}

function resetSim() {
  if (!sim) initSim();
  sim.reset();
}

// Init on load
document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('sim-track')) initSim();
});
