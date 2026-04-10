(function () {
    const _AEST = "Australia/Sydney";

    const MAX_PREDICTION_ATTEMPTS    = 2;
    const PREDICTION_RETRY_BACKOFF_MS = 4000;
    const PREDICTION_RELOAD_DELAY_MS  = 2500;
    const JUMPED_THRESHOLD_SECONDS   = -120;

    let liveRace = null;
    let liveRunners = [];
    let liveAnalysis = null;
    let liveSignal = null;
    let allMeetingRaces = [];
    let countdownTimer = null;
    const aiCommentaryCache = {};  // keyed by race_uid + "_" + box
    const _predAttempts = {};      // per-race-uid prediction auto-trigger counter

    const q = (id) => document.getElementById(id);

    function esc(str) {
        return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    // -------------------------------------------------------
    // Utility
    // -------------------------------------------------------

    function getRaceUid() {
        const params = new URLSearchParams(window.location.search);
        return params.get("race_uid") || "";
    }

    function parseJumpTimeToDate(jumpTime) {
        if (!jumpTime || typeof jumpTime !== "string") return null;
        if (/^\d{4}-\d{2}-\d{2}T/.test(jumpTime) || /^\d{4}-\d{2}-\d{2} /.test(jumpTime)) {
            const dt = new Date(jumpTime);
            return isNaN(dt.getTime()) ? null : dt;
        }
        const parts = jumpTime.split(":");
        if (parts.length < 2) return null;
        const hour = parseInt(parts[0], 10);
        const minute = parseInt(parts[1], 10);
        if (Number.isNaN(hour) || Number.isNaN(minute)) return null;
        const now = new Date();
        return new Date(now.getFullYear(), now.getMonth(), now.getDate(), hour, minute, 0, 0);
    }

    function getRaceJumpDate(race) {
        if (!race) return null;
        if (race.jump_dt_iso) {
            const dt = new Date(race.jump_dt_iso);
            if (!isNaN(dt.getTime())) return dt;
        }
        return parseJumpTimeToDate(race.jump_time || "");
    }

    function formatCountdown(secs, status) {
        const st = (status || "").toLowerCase();
        if (["final", "paying", "result_posted", "abandoned"].includes(st)) return "Resulted";
        if (secs == null) return "—";
        if (secs < 0) return "Awaiting Result";
        if (secs < 60)   return `${secs}s`;
        if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
        return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
    }

    function getStatusBadgeClass(secs, status) {
        const st = (status || "").toLowerCase();
        if (["final", "paying", "result_posted"].includes(st)) return "badge-resulted";
        if (st === "abandoned") return "badge-abandoned";
        if (["jumped_estimated", "awaiting_result"].includes(st) || (secs != null && secs < 0)) return "badge-pending";
        if (secs != null && secs < 120) return "badge-imminent";
        if (secs != null && secs < 600) return "badge-near";
        return "badge-upcoming";
    }

    function getStatusLabel(secs, status) {
        const st = (status || "").toLowerCase();
        if (["final", "paying", "result_posted"].includes(st)) return "RESULTED";
        if (st === "abandoned") return "ABANDONED";
        if (["jumped_estimated", "awaiting_result"].includes(st) || (secs != null && secs < 0)) return "PENDING";
        if (secs != null && secs < 120) return "IMMINENT";
        return "";
    }

    function getSecondsNow(race) {
        const dt = getRaceJumpDate(race);
        if (!dt) return null;
        return Math.floor((dt.getTime() - Date.now()) / 1000);
    }

    function setText(id, value) {
        const el = q(id);
        if (el) el.textContent = value ?? "—";
    }

    function normaliseCode(code) {
        const raw = String(code || "GREYHOUND").toUpperCase();
        if (raw === "THOROUGHBRED") return "HORSE";
        return raw;
    }

    function formatTrack(slug) {
        if (!slug) return "—";
        return slug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    }

    // -------------------------------------------------------
    // Countdown chip
    // -------------------------------------------------------

    function updateCountdownChip() {
        const chip = q("liveCountdownChip");
        if (!chip || !liveRace) return;
        const secs = getSecondsNow(liveRace);
        const status = liveRace?.status || "";
        chip.textContent = formatCountdown(secs, status);

        const badgeClass = getStatusBadgeClass(secs, status);
        if (badgeClass === "badge-pending") {
            chip.className = "race-countdown-chip countdown-pending";
            chip.style.color = "var(--amber)";
            chip.style.background = "rgba(255,179,71,0.12)";
        } else if (badgeClass === "badge-resulted") {
            chip.className = "race-countdown-chip countdown-resulted";
            chip.style.color = "var(--green)";
            chip.style.background = "rgba(61,214,140,0.12)";
        } else if (badgeClass === "badge-imminent") {
            chip.className = "race-countdown-chip countdown-imminent";
            chip.style.color = "var(--red-1)";
            chip.style.background = "rgba(255,31,31,0.12)";
        } else if (badgeClass === "badge-near") {
            chip.className = "race-countdown-chip countdown-near";
            chip.style.color = "var(--amber)";
            chip.style.background = "rgba(255,179,71,0.12)";
        } else {
            chip.className = "race-countdown-chip";
            chip.style.color = "var(--text)";
            chip.style.background = "var(--bg-3)";
        }
    }

    // -------------------------------------------------------
    // Render Race Header
    // -------------------------------------------------------

    function renderRaceHeader() {
        if (!liveRace) {
            setText("liveTrack", "No race selected");
            setText("liveRaceNum", "");
            setText("liveRaceName", "");
            setText("liveCode", "—");
            setText("liveDistance", "—");
            setText("liveGrade", "—");
            setText("liveCondition", "—");
            setText("liveJump", "—");
            updateCountdownChip();
            return;
        }

        const code = normaliseCode(liveRace.code);
        setText("liveTrack", formatTrack(liveRace.track));
        setText("liveRaceNum", liveRace.race_num ? `R${liveRace.race_num}` : "R—");
        setText("liveRaceName", liveRace.race_name || liveRace.grade || "");
        setText("liveDistance", liveRace.distance ? `${liveRace.distance}m` : "—");
        setText("liveGrade", liveRace.grade || "—");
        setText("liveCondition", liveRace.track_condition || liveRace.condition || "—");

        let jumpDisplay = "—";
        if (liveRace.jump_dt_iso) {
            const dt = new Date(liveRace.jump_dt_iso);
            if (!isNaN(dt.getTime())) {
                jumpDisplay = dt.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", timeZone: _AEST });
            }
        } else if (liveRace.jump_time) {
            jumpDisplay = liveRace.jump_time;
        }
        setText("liveJump", jumpDisplay);

        const codeChip = q("liveCode");
        if (codeChip) {
            codeChip.textContent = code;
            codeChip.className = "race-code-chip race-meta-chip code-chip-" + code.toLowerCase();
        }

        updateCountdownChip();
    }

    // -------------------------------------------------------
    // Form string coloring
    // -------------------------------------------------------

    function colorFormString(form) {
        if (!form) return "—";
        return form.split("").map(c => {
            if ("123".includes(c)) return `<span class="form-win">${c}</span>`;
            if ("FW".includes(c))  return `<span class="form-fail">${c}</span>`;
            if (c === "V")         return `<span class="form-mid">${c}</span>`;
            return `<span class="form-dim">${c}</span>`;
        }).join("");
    }

    // -------------------------------------------------------
    // Form string → recent starts rows (for expanded detail)
    // -------------------------------------------------------

    function buildRecentStartsRows(formStr, recentStarts) {
        // Use structured recent_starts array if available
        if (Array.isArray(recentStarts) && recentStarts.length) {
            return recentStarts.slice(-6).reverse().map(s => {
                const finish = s.finish || s.position || s.result || "—";
                const finishNum = parseInt(finish, 10);
                let cls = "finish-bad", label = String(finish);
                if (finishNum === 1)        { cls = "finish-win";   label = "1st"; }
                else if (finishNum === 2)   { cls = "finish-win";   label = "2nd"; }
                else if (finishNum === 3)   { cls = "finish-place"; label = "3rd"; }
                else if (!isNaN(finishNum)) { label = finishNum + "th"; }
                return `
                <div class="recent-run-row">
                    <span class="rr-track">${esc(s.track || s.venue || "—")}</span>
                    <span class="rr-cond">${esc(s.condition || s.track_condition || "—")}</span>
                    <span class="rr-dist">${s.distance ? s.distance + "m" : "—"}</span>
                    <span class="rr-date">${esc(s.date || "—")}</span>
                    <span class="rr-finish ${cls}">${label}</span>
                </div>`;
            }).join("");
        }

        // Fallback: form string chars only
        if (!formStr) return '<div class="rr-empty">No form data.</div>';
        return formStr.slice(-6).split("").map(c => {
            let cls = "finish-bad", label = c + "th";
            if (c === "1")      { cls = "finish-win";   label = "1st"; }
            else if (c === "2") { cls = "finish-win";   label = "2nd"; }
            else if (c === "3") { cls = "finish-place"; label = "3rd"; }
            else if (c === "F") { cls = "finish-bad";   label = "Fell"; }
            else if (c === "W") { cls = "finish-bad";   label = "W/D"; }
            else if (c === "V") { cls = "finish-place"; label = "Vac"; }
            return `
                <div class="recent-run-row">
                    <span class="rr-track">—</span>
                    <span class="rr-cond">—</span>
                    <span class="rr-dist">—</span>
                    <span class="rr-date">—</span>
                    <span class="rr-finish ${cls}">${label}</span>
                </div>`;
        }).join("");
    }

    // -------------------------------------------------------
    // AI Commentary
    // -------------------------------------------------------

    async function generateRunnerCommentary(runner, race) {
        const cacheKey = `${getRaceUid()}_${runner.box}`;
        const el = document.getElementById(`aiCommentary_${runner.box}`);
        if (!el) return;
        if (el.dataset.loaded === "true") return;

        // Check cache
        if (aiCommentaryCache[cacheKey]) {
            el.textContent = aiCommentaryCache[cacheKey];
            el.dataset.loaded = "true";
            return;
        }

        el.innerHTML = `<span class="ai-loading">Analysing…</span>`;

        const prompt = `You are a racing analyst. In 2-3 sentences, give a punter's assessment of this runner's chances.

Race: ${formatTrack(race.track)} R${race.race_num} — ${race.distance || ""} ${race.grade || ""} ${race.condition || ""}
Runner: ${runner.name} (Box/Barrier ${runner.box})
Trainer: ${runner.trainer || "Unknown"}
Form (last 6): ${runner.form || "—"}
Career: ${runner.career || "—"}
Best time: ${runner.bestTime || "—"}
Odds: ${runner.odds ? "$" + parseFloat(runner.odds).toFixed(2) : "—"}
AI Win probability: ${runner.winProb ? runner.winProb + "%" : "—"}

Be direct and useful. Mention key strengths or concerns. Do not use filler phrases.`;

        try {
            const resp = await fetch("/api/ai/commentary", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ prompt })
            });
            const data = await resp.json();
            const text = data.text || "Commentary unavailable.";
            el.textContent = text;
            el.dataset.loaded = "true";
            aiCommentaryCache[cacheKey] = text;
        } catch (e) {
            el.textContent = "Commentary unavailable.";
        }
    }

    // -------------------------------------------------------
    // Build Expandable Runner Cards
    // -------------------------------------------------------

    function buildExpandGrid(r) {
        // ---- Parse career string "starts: W-P-S" ----
        let careerStarts = 0, careerWins = 0, careerPlaces = 0, careerShows = 0;
        const careerStr = r.career || r.stats_career || "";
        if (careerStr) {
            const m = careerStr.match(/^(\d+):\s*(\d+)-(\d+)-(\d+)/);
            if (m) {
                careerStarts = parseInt(m[1], 10);
                careerWins   = parseInt(m[2], 10);
                careerPlaces = parseInt(m[3], 10);
                careerShows  = parseInt(m[4], 10);
            }
        }
        const stats = r.ff_career_stats || {};
        const winPct   = stats.win_pct   != null ? stats.win_pct.toFixed(1) + "%"
                       : careerStarts > 0 ? ((careerWins / careerStarts) * 100).toFixed(1) + "%" : "—";
        const placePct = stats.place_pct != null ? stats.place_pct.toFixed(1) + "%"
                       : careerStarts > 0 ? (((careerWins + careerPlaces) / careerStarts) * 100).toFixed(1) + "%" : "—";

        // ---- Parse class profile ----
        let ratingStr = "—", paceStyle = "—";
        const cp = r.ff_class_profile;
        if (cp) {
            const obj = typeof cp === "string" ? (() => { try { return JSON.parse(cp); } catch(_) { return null; } })() : cp;
            if (obj) {
                const rating = obj.currentRating ?? obj.rating ?? obj.classRating;
                if (rating != null) ratingStr = String(rating);
                paceStyle = obj.paceStyle ?? obj.pace ?? "—";
            }
        }
        // Fallback: derive paceStyle from ff_speed_map if class_profile empty
        if (paceStyle === "—" && r.ff_speed_map) {
            const sm = typeof r.ff_speed_map === "string"
                ? (() => { try { return JSON.parse(r.ff_speed_map); } catch(_) { return null; } })()
                : r.ff_speed_map;
            if (sm) paceStyle = sm.style ?? sm.paceStyle ?? sm.position ?? "—";
        }
        // Fallback: use paceStyle field directly from runner
        if (paceStyle === "—" && r.paceStyle) paceStyle = r.paceStyle;

        // ---- Parse class fit ----
        let classFitStr = "—";
        const cf = r.race_class_fit;
        if (cf) {
            const obj = typeof cf === "string" ? (() => { try { return JSON.parse(cf); } catch(_) { return null; } })() : cf;
            if (obj) {
                const fit   = obj.fit ?? obj.fitScore ?? obj.classMatch;
                const label = obj.label ?? obj.fitLabel ?? obj.verdict;
                if (typeof fit === "number") classFitStr = label ? (fit * 100).toFixed(0) + "% — " + label : (fit * 100).toFixed(0) + "%";
                else if (label) classFitStr = label;
            }
        }

        // ---- AI win prob ----
        const aiWinProb = r.ff_win_prob != null ? r.ff_win_prob.toFixed(1) + "%"
                        : r.winProb     != null ? r.winProb.toFixed(1) + "%" : "—";

        const hasData = (r.career && r.career.trim())
            || (r.form   && r.form.trim())
            || r.bestTime
            || r.ff_win_prob != null
            || r.winProb != null
            || r.odds != null;

        if (!hasData) {
            return `
        <div class="expand-empty-note" style="display:flex;align-items:center;gap:10px;padding:8px 0;">
            <span style="color:var(--text-dim);font-size:0.8rem;">Form data not yet loaded from FormFav.</span>
            <button class="dp-btn dp-btn-small formfav-sync-btn"
                    data-race-uid="${esc(window._currentRaceUid || '')}">⟳ Sync FormFav</button>
            <span class="formfav-sync-status" style="font-size:0.75rem;color:var(--text-dim);"></span>
        </div>`;
        }

        // ---- Career stats grid (Sportsbet-style) ----
        const careerGrid = `
    <div class="form-stats-section">
        <div class="form-stats-label">CAREER &amp; STATS</div>
        <div class="form-career-grid">
            <div class="fcs-cell"><span class="fcs-k">Last 6</span><span class="fcs-v">${esc(r.form || "—")}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Career</span><span class="fcs-v">${esc(r.career || "—")}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Win %</span><span class="fcs-v">${esc(winPct)}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Place %</span><span class="fcs-v">${esc(placePct)}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Best Time</span><span class="fcs-v">${esc(r.bestTime || "—")}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Weight</span><span class="fcs-v">${esc(r.weight || "—")}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Early Speed</span><span class="fcs-v">${esc(r.earlySpeed || "—")}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Pace Style</span><span class="fcs-v">${esc(paceStyle)}</span></div>
        </div>
    </div>`;

        // ---- FormFav AI intel block ----
        const aiBlock = `
    <div class="form-stats-section">
        <div class="form-stats-label">AI INTEL (FORMFAV)</div>
        <div class="form-career-grid">
            <div class="fcs-cell"><span class="fcs-k">AI Win %</span><span class="fcs-v fcs-highlight">${esc(aiWinProb)}</span></div>
            <div class="fcs-cell"><span class="fcs-k">AI Rank</span><span class="fcs-v">${r.ff_model_rank != null ? "#" + r.ff_model_rank : "—"}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Confidence</span><span class="fcs-v">${esc(r.ff_confidence || "—")}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Rating</span><span class="fcs-v">${esc(ratingStr)}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Class Fit</span><span class="fcs-v">${esc(classFitStr)}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Trainer</span><span class="fcs-v">${esc(r.trainer || "—")}</span></div>
            <div class="fcs-cell"><span class="fcs-k">Jockey/Driver</span><span class="fcs-v">${esc(r.jockey || "—")}</span></div>
        </div>
    </div>`;

        return careerGrid + aiBlock;
    }

    function buildDecoratorBadges(decorators) {
        if (!decorators || !decorators.length) return "";
        const badges = Array.isArray(decorators) ? decorators : [];
        if (!badges.length) return "";

        // Color by sentiment, not label
        function sentimentColor(sentiment) {
            if (sentiment === "+") return "var(--green)";
            if (sentiment === "-") return "var(--red-1)";
            return "var(--amber)";
        }

        // Category → icon prefix
        function categoryIcon(category) {
            const icons = {
                "form":          "◆",
                "specialization":"★",
                "conditions":    "☁",
                "fitness":       "⚡",
                "running_style": "→",
                "class":         "▲",
                "barrier":       "▣",
                "connections":   "👤",
            };
            return icons[category] || "";
        }

        return `<div class="decorator-badges">${
            badges.map(d => {
                const label       = typeof d === "string" ? d : (d.label || "");
                const shortLabel  = typeof d === "string" ? d : (d.shortLabel || d.label || "");
                const sentiment   = typeof d === "object" ? (d.sentiment || "+") : "+";
                const category    = typeof d === "object" ? (d.category || "") : "";
                const description = typeof d === "object" ? (d.description || "") : "";
                const detail      = typeof d === "object" ? (d.detail || "") : "";
                const color       = sentimentColor(sentiment);
                const icon        = categoryIcon(category);
                const tooltip     = [description, detail].filter(Boolean).join(" — ");

                return `<span class="decorator-badge decorator-${sentiment === "+" ? "pos" : sentiment === "-" ? "neg" : "neu"}"
                    style="border-color:${color};color:${color}"
                    title="${esc(tooltip || label)}"
                    data-type="${esc(d.type || "")}"
                    data-category="${esc(category)}">${icon ? icon + " " : ""}${esc(shortLabel)}</span>`;
            }).join("")
        }</div>`;
    }

    function buildRunnerCards(runners, analysis) {
        const winProbs = analysis?.all_runners || [];
        const probMap = {};
        for (const r of winProbs) {
            const key = r.number ?? r.box ?? r.box_num;
            if (key != null) probMap[key] = r.win_prob;
        }

        const ranked = [...runners]
            .filter(r => !r.scratched)
            .sort((a, b) => {
                const boxA = a.box_num ?? a.number ?? a.barrier;
                const boxB = b.box_num ?? b.number ?? b.barrier;
                const probA = probMap[boxA] ?? (a.price > 0 ? 100 / a.price : 0);
                const probB = probMap[boxB] ?? (b.price > 0 ? 100 / b.price : 0);
                return probB - probA;
            });
        const rankMap = {};
        ranked.forEach((r, i) => {
            const key = r.box_num ?? r.number ?? r.barrier;
            if (key != null) rankMap[key] = i + 1;
        });

        const container = q("formGuideRows");
        if (!container) return;

        const runnerSelect = q("qbRunner");
        if (runnerSelect) {
            runnerSelect.innerHTML = `<option value="">Select runner…</option>` +
                runners.map(r => `<option value="${r.name || ''}" data-odds="${r.price || r.win_odds || ''}">${r.name || '—'}</option>`).join("");
        }

        // Build normalised runner objects (including FormFav fields)
        const normalised = runners.map((r, idx) => {
            const box = r.box_num ?? r.number ?? r.barrier ?? (idx + 1);
            const odds = r.price || r.win_odds || null;
            const impliedProb = odds > 0 ? (100 / odds) : null;
            const aiProb = probMap[box];
            const winProb = aiProb ?? impliedProb ?? null;
            const rank = rankMap[box];
            return {
                box,
                name: r.name || "—",
                form: r.form_string || r.form || "",
                trainer: r.trainer || "",
                jockey: r.jockey || r.driver || "",
                bestTime: r.best_time || r.bestTime || "",
                weight: r.weight || "",
                career: r.career || r.stats_career || "",
                earlySpeed: r.early_speed || r.earlySpeed || "",
                paceStyle: r.paceStyle || r.pace_style || "",
                odds,
                winProb,
                rank,
                scratched: !!r.scratched,
                // FormFav-specific fields
                ff_win_prob:      r.ff_win_prob,
                ff_model_rank:    r.ff_model_rank,
                ff_confidence:    r.ff_confidence,
                ff_decorators:    Array.isArray(r.ff_decorators) ? r.ff_decorators : [],
                ff_speed_map:     r.ff_speed_map,
                ff_class_profile: r.ff_class_profile,
                race_class_fit:   r.race_class_fit,
                ff_stats_full:    r.ff_stats_full || {},
                ff_career_stats:  r.ff_career_stats,
                recent_starts:    Array.isArray(r.recent_starts) ? r.recent_starts
                             : Array.isArray(r.ff_stats_full?.recent_starts) ? r.ff_stats_full.recent_starts
                             : [],
            };
        });

        // Render cards
        container.innerHTML = normalised.map(r => {
            const oddsStr = r.odds ? `$${parseFloat(r.odds).toFixed(2)}` : "—";
            const probPct = r.winProb != null ? Math.min(100, Math.max(0, r.winProb)).toFixed(1) : null;
            const probBarWidth = r.winProb != null ? Math.min(100, Math.max(0, r.winProb)) : 0;
            const trainerLine = [r.trainer ? `T: ${r.trainer}` : null, r.jockey ? `J: ${r.jockey}` : null]
                .filter(Boolean).join("  ");

            let rankBadge = "";
            if (r.scratched) {
                rankBadge = `<div class="rank-badge rank-scr">SCR</div>`;
            } else if (r.rank === 1) {
                rankBadge = `<div class="rank-badge rank-1">1st</div>`;
            } else if (r.rank === 2) {
                rankBadge = `<div class="rank-badge rank-2">2nd</div>`;
            } else if (r.rank === 3) {
                rankBadge = `<div class="rank-badge rank-3">3rd</div>`;
            } else if (r.rank) {
                rankBadge = `<div class="rank-text">${r.rank}th</div>`;
            } else {
                rankBadge = `<div class="rank-text">—</div>`;
            }

            const recentRows = buildRecentStartsRows(r.form, r.recent_starts);

            // Top decorator badges for summary row
            const topBadges = (r.ff_decorators || []).slice(0, 4).map(d => {
                const shortLabel = typeof d === "string" ? d : (d.shortLabel || d.label || "");
                const sentiment  = typeof d === "object" ? (d.sentiment || "+") : "+";
                const tooltip    = typeof d === "object"
                    ? [d.description, d.detail].filter(Boolean).join(" — ")
                    : "";
                const cls = sentiment === "+" ? "runner-badge-pos"
                          : sentiment === "-" ? "runner-badge-neg"
                          : "runner-badge-neu";
                if (!shortLabel) return "";
                return `<span class="runner-badge ${cls}" title="${esc(tooltip || shortLabel)}">${esc(shortLabel)}</span>`;
            }).join("");

            return `
                <div class="runner-card${r.scratched ? " runner-card-scratched" : ""}" data-box="${esc(r.box)}">
                    <div class="runner-summary-row${r.scratched ? " scratched-row" : ""}"
                         data-runner-name="${esc(r.name)}" data-runner-odds="${esc(r.odds || '')}" data-navigate="runner">
                        <div class="col-box"><div class="box-num">${esc(r.box)}</div></div>
                        <div class="col-runner" style="flex:1; min-width:0;">
                            <div class="runner-name"${r.scratched ? ' style="text-decoration:line-through"' : ''}>${esc(r.name)}${topBadges}</div>
                            ${trainerLine ? `<div class="runner-meta">${esc(trainerLine)}</div>` : ""}
                        </div>
                        <div class="col-form" style="min-width:80px;">
                            <div class="form-string">${colorFormString(r.form)}</div>
                        </div>
                        <div class="col-odds" style="min-width:60px; text-align:right;">
                            <div class="odds-value">${esc(oddsStr)}</div>
                        </div>
                        <div class="col-prob" style="min-width:72px; text-align:right;">
                            ${probPct != null ? `
                                <div class="prob-bar-wrap"><div class="prob-bar" style="width:${probBarWidth}%"></div></div>
                                <div class="prob-text">${esc(probPct)}%</div>
                            ` : '<div class="prob-text" style="color:var(--text-dim)">—</div>'}
                        </div>
                        <div class="col-rank" style="min-width:52px; text-align:right;">${rankBadge}</div>
                    </div>

                    <div class="runner-expand" id="runnerExpand_${esc(r.box)}" style="display:none;">

                        ${buildExpandGrid(r)}
                        ${buildDecoratorBadges(r.ff_decorators)}
                        ${(() => {
                            const detailed = (r.ff_decorators || [])
                                .filter(d => typeof d === "object" && (d.description || d.detail));
                            if (!detailed.length) return "";
                            return `<div class="decorator-detail-list">${
                                detailed.map(d => {
                                    const sentColor = d.sentiment === "+" ? "var(--green)"
                                                    : d.sentiment === "-" ? "var(--red-1)"
                                                    : "var(--amber)";
                                    return `<div class="decorator-detail-row">
                                        <span class="decorator-detail-label" style="color:${sentColor}">${esc(d.shortLabel || d.label)}</span>
                                        <span class="decorator-detail-text">${esc(d.description || "")}${d.detail ? ` <em>${esc(d.detail)}</em>` : ""}</span>
                                    </div>`;
                                }).join("")
                            }</div>`;
                        })()}

                        <div class="expand-recent-starts">
                            <div class="expand-section-title">Recent Starts</div>
                            ${recentRows}
                        </div>

                        <div class="expand-ai-commentary">
                            <div class="expand-section-title">AI Commentary</div>
                            <div class="ai-commentary-text" id="aiCommentary_${r.box}">
                                <span class="ai-loading">Click to generate analysis…</span>
                            </div>
                        </div>

                        <button class="expand-bet-btn" data-runner-name="${esc(r.name)}" data-runner-odds="${esc(r.odds || '')}">
                            Bet This Runner →
                        </button>

                    </div>
                </div>
            `;
        }).join("");

        setText("formGuideMeta", `${runners.length} runner${runners.length !== 1 ? "s" : ""}`);
    }

    // -------------------------------------------------------
    // Toggle runner expand
    // -------------------------------------------------------

    function toggleRunnerExpand(box) {
        const expandEl = document.getElementById(`runnerExpand_${box}`);
        if (!expandEl) return;

        const isOpen = expandEl.style.display !== "none";

        // Close all
        document.querySelectorAll(".runner-expand").forEach(el => { el.style.display = "none"; });
        document.querySelectorAll(".runner-summary-row").forEach(el => el.classList.remove("expanded"));

        if (!isOpen) {
            expandEl.style.display = "block";
            const summaryRow = expandEl.closest(".runner-card")?.querySelector(".runner-summary-row");
            if (summaryRow) summaryRow.classList.add("expanded");

            // Trigger AI commentary
            const cardEl = expandEl.closest(".runner-card");
            if (cardEl && liveRace) {
                const runnerName = expandEl.querySelector(".expand-bet-btn")?.dataset.runnerName || "";
                const runnerOdds = expandEl.querySelector(".expand-bet-btn")?.dataset.runnerOdds || "";
                const runner = {
                    box, name: runnerName, odds: runnerOdds,
                    form: expandEl.querySelector(".es-val")?.textContent || "",
                    career: "", trainer: "", bestTime: "", weight: "", winProb: null
                };
                // find full runner data from liveRunners
                const full = liveRunners.find(r => {
                    const b = r.box_num ?? r.number ?? r.barrier;
                    return String(b) === String(box);
                });
                if (full) {
                    const odds = full.price || full.win_odds || null;
                    const winProb = full.win_prob || null;
                    runner.name = full.name || runnerName;
                    runner.odds = odds;
                    runner.form = full.form_string || full.form || "";
                    runner.career = full.career || "";
                    runner.trainer = full.trainer || "";
                    runner.jockey = full.jockey || full.driver || "";
                    runner.bestTime = full.best_time || "";
                    runner.weight = full.weight || "";
                    runner.winProb = winProb;
                }
                generateRunnerCommentary(runner, liveRace);
            }
        }
    }

    // -------------------------------------------------------
    // Analysis Panel
    // -------------------------------------------------------

    function renderAnalysis() {
        const signal = liveSignal?.signal || liveAnalysis?.signal || "—";
        const decision = liveAnalysis?.decision || "—";

        const sigEl = q("analysisSignal");
        if (sigEl) {
            sigEl.textContent = String(signal).toUpperCase();
            const s = String(signal).toUpperCase();
            sigEl.className = "analysis-signal signal-" + (
                s === "SNIPER" ? "sniper" :
                s === "VALUE"  ? "value"  :
                s === "GEM"    ? "gem"    :
                s === "WATCH"  ? "watch"  :
                s === "RISK"   ? "risk"   :
                s === "NO_BET" ? "no-bet" : "no-bet"
            );
        }

        const decEl = q("analysisDecision");
        if (decEl) decEl.textContent = String(decision).toUpperCase();

        setText("analysisPace", liveAnalysis?.pace_type || "—");
        setText("analysisShape", liveAnalysis?.race_shape || liveAnalysis?.beneficiary || "—");
        setText("analysisCondition", liveRace?.track_condition || liveRace?.condition || "—");
        setText("analysisWeather", liveAnalysis?.weather || "—");
        setText("analysisConfidence", liveAnalysis?.confidence || liveSignal?.confidence || "—");
        setText("analysisEV", liveSignal?.ev ?? liveAnalysis?.ev ?? "—");

        // Auto-trigger prediction if no signal data exists and race is not finished
        const _status = (liveRace?.status || "").toLowerCase();
        const _hasSignal = (liveSignal?.signal && liveSignal.signal !== "—") ||
            (liveAnalysis?.signal && liveAnalysis.signal !== "—");
        if (!_hasSignal && !["final","paying","result_posted","abandoned"].includes(_status)) {
            const _uid = getRaceUid();
            if (_uid && (_predAttempts[_uid] || 0) < MAX_PREDICTION_ATTEMPTS) {
                _predAttempts[_uid] = (_predAttempts[_uid] || 0) + 1;
                const backoff = _predAttempts[_uid] * PREDICTION_RETRY_BACKOFF_MS;
                setTimeout(() => {
                    fetch(`/api/predictions/race/${encodeURIComponent(_uid)}`, { method: "POST" })
                        .then(r => r.json())
                        .then(d => { if (d.ok) setTimeout(loadLiveRace, PREDICTION_RELOAD_DELAY_MS); })
                        .catch(() => {});
                }, backoff);
            }
        }
    }

    // -------------------------------------------------------
    // Quick Bet
    // -------------------------------------------------------

    function calcReturns() {
        const stake = parseFloat(q("qbStake")?.value) || 0;
        const odds  = parseFloat(q("qbOdds")?.value)  || 0;
        const ret   = odds > 0 ? (stake * odds).toFixed(2) : null;
        const el = q("qbReturns");
        if (el) el.textContent = ret && parseFloat(ret) > 0 ? `$${ret}` : "—";
    }

    function selectRunnerRow(rowEl) {
        if (rowEl.classList.contains("scratched-row")) return;
        document.querySelectorAll(".runner-summary-row.selected").forEach(r => r.classList.remove("selected"));
        rowEl.classList.add("selected");

        const name = rowEl.dataset.runnerName;
        const odds = rowEl.dataset.runnerOdds;

        const runnerSel = q("qbRunner");
        if (runnerSel) {
            for (const opt of runnerSel.options) {
                if (opt.value === name) { runnerSel.value = name; break; }
            }
        }
        if (q("qbOdds") && odds) q("qbOdds").value = odds;
        calcReturns();
    }

    async function placeBet() {
        const raceUid = getRaceUid();
        const runner  = q("qbRunner")?.value || "";
        const odds    = q("qbOdds")?.value   || "";
        const stake   = q("qbStake")?.value  || "";
        const betType = q("qbType")?.value   || "WIN";

        if (!raceUid || !runner || !odds || !stake) return;

        try {
            await api("/api/bets/place", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ race_uid: raceUid, runner, odds: parseFloat(odds), stake: parseFloat(stake), bet_type: betType })
            });
        } catch (e) {
            console.error("Place bet failed:", e);
        }
    }

    // -------------------------------------------------------
    // Simulation (client-side)
    // -------------------------------------------------------

    function runClientSideSimulation(runners, runs) {
        const active = runners.filter(r => !r.scratched);
        const total = active.reduce((s, r) => s + (r.winProb || (100 / (r.odds || 10))), 0);

        const results = active.map(r => {
            const prob = (r.winProb || (100 / (r.odds || 10))) / total;
            const winCount = Math.round(prob * runs * (0.85 + Math.random() * 0.3));
            const placeCount = Math.round(Math.min(winCount * 1.8, runs * 0.65));
            return {
                name: r.name, box: r.box,
                winPct: +((winCount / runs) * 100).toFixed(1),
                placePct: +((placeCount / runs) * 100).toFixed(1),
                avgFinish: +(1 + (1 - prob) * active.length * 0.9).toFixed(1),
            };
        }).sort((a, b) => b.winPct - a.winPct);

        return {
            topRunner: results[0]?.name || "—",
            topWinPct: results[0]?.winPct || 0,
            confidence: results[0]?.winPct > 35 ? "HIGH" : results[0]?.winPct > 20 ? "MODERATE" : "LOW",
            chaos: results[0]?.winPct < 20 ? "HIGH" : results[0]?.winPct > 40 ? "LOW" : "MODERATE",
            runners: results,
        };
    }

    const BOX_COLORS = ["#ff2d2d", "#3dd68c", "#5ab4ff", "#ffb340", "#cc44ff", "#ff6b6b", "#44ddff", "#ffdd44"];

    function runSimulation() {
        if (!liveRunners.length) return;
        const RUNS = 500;
        const active = liveRunners.filter(r => !r.scratched);
        const code = (liveRace?.code || "GREYHOUND").toUpperCase();

        const total = active.reduce((s, r) => s + (r.ff_win_prob || r.win_prob || (100 / (r.price || 10))), 0);
        const simRunners = active.map((r, i) => {
            const prob = (r.ff_win_prob || r.win_prob || (100 / (r.price || 10))) / total;
            const winCount = Math.round(prob * RUNS * (0.8 + Math.random() * 0.4));
            return {
                name: r.name || "—",
                box: r.box_num ?? r.number ?? r.barrier ?? (i + 1),
                prob,
                winCount,
                winPct: +((winCount / RUNS) * 100).toFixed(1),
                placePct: +(Math.min(95, (winCount / RUNS) * 100 * 2.2)).toFixed(1),
                earlySpeed: r.early_speed || r.ff_speed_map?.early || Math.random(),
                color: BOX_COLORS[i % BOX_COLORS.length],
            };
        }).sort((a, b) => b.winPct - a.winPct);

        renderSimResults(simRunners);
        runRaceAnimation(simRunners, code);
    }

    function runRaceAnimation(simRunners, code) {
        const container = q("simAnimContainer");
        if (!container) return;

        container.innerHTML = `<canvas id="raceCanvas" width="600" height="${simRunners.length * 36 + 40}" style="width:100%;border-radius:8px;background:var(--bg-3)"></canvas>`;
        const canvas = document.getElementById("raceCanvas");
        const ctx = canvas.getContext("2d");
        const W = canvas.width, H = canvas.height;
        const TRACK_START = 80, TRACK_END = W - 30;
        const TRACK_LEN = TRACK_END - TRACK_START;
        const ROW_H = 36;

        const state = simRunners.map((r, i) => ({
            ...r, x: 0, y: TRACK_START + i * ROW_H + 18,
            speed: 0.004 + r.prob * 0.006 + Math.random() * 0.002,
            finished: false, finishOrder: null,
        }));

        let finishCount = 0;

        function draw() {
            ctx.clearRect(0, 0, W, H);
            state.forEach((r, i) => {
                ctx.fillStyle = i % 2 === 0 ? "rgba(255,255,255,0.02)" : "transparent";
                ctx.fillRect(TRACK_START, i * ROW_H + TRACK_START - 36, TRACK_LEN, ROW_H);

                ctx.fillStyle = r.color;
                ctx.font = "bold 11px sans-serif";
                ctx.fillText(`${r.box}. ${r.name.split(" ")[0]}`, 4, r.y + 4);

                const rx = TRACK_START + r.x * TRACK_LEN;
                ctx.beginPath();
                ctx.arc(rx, r.y, 8, 0, Math.PI * 2);
                ctx.fillStyle = r.color;
                ctx.fill();

                if (r.finished) {
                    ctx.fillStyle = "rgba(255,255,255,0.6)";
                    ctx.font = "9px sans-serif";
                    ctx.fillText(`${r.finishOrder}`, rx - 3, r.y + 3);
                }
            });

            ctx.strokeStyle = "rgba(255,255,255,0.3)";
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(TRACK_END, 0);
            ctx.lineTo(TRACK_END, H);
            ctx.stroke();
            ctx.setLineDash([]);
        }

        function tick() {
            state.forEach(r => {
                if (r.finished) return;
                r.speed += (Math.random() - 0.5) * 0.0004;
                r.speed = Math.max(0.002, Math.min(0.014, r.speed));
                r.x = Math.min(1, r.x + r.speed);
                if (r.x >= 1 && !r.finished) {
                    r.finished = true;
                    r.finishOrder = ++finishCount;
                }
            });
            draw();
            if (finishCount < state.length) {
                window._raceAnimId = requestAnimationFrame(tick);
            } else {
                const winner = state.find(r => r.finishOrder === 1);
                const banner = q("simResultBanner");
                if (banner && winner) {
                    banner.style.display = "block";
                    banner.innerHTML = `<span style="color:var(--green);font-weight:700">🏆 ${esc(winner.name)}</span> wins the simulation`;
                }
            }
        }

        if (window._raceAnimId) cancelAnimationFrame(window._raceAnimId);
        window._raceAnimId = requestAnimationFrame(tick);
    }

    function renderSimResults(simRunners) {
        const idle = q("simIdle");
        const results = q("simResults");
        if (idle) idle.style.display = "none";
        if (results) results.style.display = "block";

        const summary = q("simSummary");
        if (summary) summary.textContent = `Top: ${simRunners[0]?.name} (${simRunners[0]?.winPct}% from 500 sims) • ${simRunners[0]?.winPct > 35 ? "HIGH" : "MODERATE"} confidence`;

        const list = q("simRunnerList");
        if (list) list.innerHTML = simRunners.map(r => `
            <div class="sim-runner-row">
                <span class="sim-runner-name" style="color:${r.color}">${r.box}. ${esc(r.name)}</span>
                <div class="sim-win-bar-wrap"><div class="sim-win-bar" style="width:${r.winPct}%;background:${r.color}"></div></div>
                <span class="sim-win-pct">${r.winPct}%</span>
            </div>
        `).join("");
    }

    // -------------------------------------------------------
    // Prev / Next race navigation
    // -------------------------------------------------------

    async function loadMeetingRaces() {
        if (!liveRace) return;
        try {
            const data = await api("/api/home/board");
            const items = Array.isArray(data.items) ? data.items : [];
            const track = liveRace.track;
            const code  = (liveRace.code || "").toUpperCase();
            allMeetingRaces = items
                .filter(i => i.track === track && (i.code || "").toUpperCase() === code)
                .sort((a, b) => (a.race_num || 0) - (b.race_num || 0));
        } catch (e) {
            allMeetingRaces = [];
        }
    }

    function navigateRace(direction) {
        if (!liveRace || !allMeetingRaces.length) return;
        const idx = allMeetingRaces.findIndex(r => r.race_uid === liveRace.race_uid);
        const nextIdx = idx + direction;
        if (nextIdx < 0 || nextIdx >= allMeetingRaces.length) return;
        const next = allMeetingRaces[nextIdx];
        if (next?.race_uid) {
            window.location.href = `/live?race_uid=${encodeURIComponent(next.race_uid)}`;
        }
    }

    // -------------------------------------------------------
    // Load race data
    // -------------------------------------------------------

    async function loadAndRenderResult(raceUid) {
        try {
            const data = await api(`/api/races/${encodeURIComponent(raceUid)}/results`);
            const container = q("formGuideRows");
            if (!container) return;
            if (data.ok && data.winner) {
                container.innerHTML = `
                    <div style="padding:24px;">
                        <div style="font-size:0.75rem;letter-spacing:.08em;color:var(--text-dim);margin-bottom:12px;">RACE RESULT</div>
                        <div style="display:grid;gap:10px;">
                            <div class="result-row"><span style="color:var(--text-dim);font-size:0.8rem;">WINNER</span>
                                <span style="font-weight:700;font-size:1.1rem;">${esc(data.winner || "—")}</span>
                                <span style="color:var(--amber);font-weight:700;">${data.win_price ? "$" + parseFloat(data.win_price).toFixed(2) : "—"}</span></div>
                            ${data.place_2 ? `<div class="result-row"><span style="color:var(--text-dim);font-size:0.8rem;">2ND</span><span>${esc(data.place_2)}</span></div>` : ""}
                            ${data.place_3 ? `<div class="result-row"><span style="color:var(--text-dim);font-size:0.8rem;">3RD</span><span>${esc(data.place_3)}</span></div>` : ""}
                            ${data.winning_time ? `<div class="result-row"><span style="color:var(--text-dim);font-size:0.8rem;">TIME</span><span>${esc(String(data.winning_time))}</span></div>` : ""}
                            ${data.margin ? `<div class="result-row"><span style="color:var(--text-dim);font-size:0.8rem;">MARGIN</span><span>${esc(String(data.margin))}</span></div>` : ""}
                        </div>
                    </div>`;
            } else if (data.ok) {
                container.innerHTML = `<div style="padding:24px;text-align:center;">
                    <div style="color:var(--amber);font-size:0.85rem;letter-spacing:.06em;">AWAITING OFFICIAL RESULT</div>
                    <div style="color:var(--text-dim);font-size:0.75rem;margin-top:8px;">Results post within 2–3 minutes of jump</div>
                </div>`;
            } else {
                container.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text-dim);">Result not yet available.</div>`;
            }
        } catch (err) {
            // A 404 means the result doesn't exist yet — show the awaiting message.
            const container = q("formGuideRows");
            if (container) {
                container.innerHTML = `<div style="padding:24px;text-align:center;">
                    <div style="color:var(--amber);font-size:0.85rem;letter-spacing:.06em;">AWAITING OFFICIAL RESULT</div>
                    <div style="color:var(--text-dim);font-size:0.75rem;margin-top:8px;">Results post within 2–3 minutes of jump</div>
                </div>`;
            }
        }
    }

    async function loadLiveRace() {
        const raceUid = getRaceUid();
        if (!raceUid) {
            renderRaceHeader();
            renderAnalysis();
            return;
        }

        window._currentRaceUid = raceUid;

        try {
            const data = await api(`/api/live/race/${encodeURIComponent(raceUid)}`);
            liveRace     = data.race     || null;
            liveAnalysis = data.analysis || null;
            liveSignal   = data.signal   || null;

            // Priority: data.runners > analysis.all_runners > []
            let rawRunners = [];
            if (Array.isArray(data.runners) && data.runners.length) {
                rawRunners = data.runners;
            } else if (Array.isArray(liveAnalysis?.all_runners) && liveAnalysis.all_runners.length) {
                rawRunners = liveAnalysis.all_runners;
            }
            liveRunners = rawRunners;

            renderRaceHeader();
            renderAnalysis();

            // If race is resulted, show result panel instead of form guide
            const status = (liveRace?.status || "").toLowerCase();
            if (["final", "paying", "result_posted", "abandoned"].includes(status)) {
                loadAndRenderResult(raceUid);
            } else if (["jumped_estimated","awaiting_result"].includes(status) ||
                       (getSecondsNow(liveRace) !== null && getSecondsNow(liveRace) < JUMPED_THRESHOLD_SECONDS)) {
                // Race has jumped — show result if available, otherwise show runners + awaiting message
                try {
                    await loadAndRenderResult(raceUid);
                } catch (_) {}
                // Always also show runners below the result/awaiting panel
                if (liveRunners.length) {
                    try { buildRunnerCards(liveRunners, liveAnalysis); } catch (_) {}
                } else {
                    // No runners and no result yet — show awaiting message
                    const container = q("formGuideRows");
                    if (container && container.innerHTML.trim() === "") {
                        container.innerHTML = `<div style="padding:32px;text-align:center;">
                            <div style="color:var(--amber);font-size:0.85rem;letter-spacing:.06em;margin-bottom:8px;">
                                AWAITING OFFICIAL RESULT
                            </div>
                            <div style="color:var(--text-dim);font-size:0.75rem;">
                                Results post within 2–3 minutes of jump time.
                            </div>
                        </div>`;
                    }
                }
            } else if (liveRunners.length) {
                try {
                    buildRunnerCards(liveRunners, liveAnalysis);
                } catch (err) {
                    console.error("buildRunnerCards failed:", err);
                    const container = q("formGuideRows");
                    if (container) container.innerHTML = `
                        <div style="padding:24px;text-align:center;">
                            <div style="color:var(--text-dim);margin-bottom:8px;">
                                ${liveRunners.length} runner${liveRunners.length !== 1 ? "s" : ""} loaded
                            </div>
                            <div style="font-size:0.8rem;color:var(--text-dim);">
                                Form display error — runner names available in Quick Bet below.
                            </div>
                        </div>`;
                    // Still populate Quick Bet dropdown even if cards failed
                    const runnerSelect = q("qbRunner");
                    if (runnerSelect) {
                        runnerSelect.innerHTML = `<option value="">Select runner…</option>` +
                            liveRunners.map(r =>
                                `<option value="${esc(r.name || '')}" data-odds="${esc(String(r.price || r.win_odds || ''))}">
                                    ${esc(r.name || "—")}
                                </option>`
                            ).join("");
                    }
                    setText("formGuideMeta", `${liveRunners.length} runners`);
                }
            } else {
                const container = q("formGuideRows");
                if (container) container.innerHTML = `
                    <div style="padding:32px;text-align:center;color:var(--text-dim);">
                        <div style="font-size:1rem;margin-bottom:8px;">No runner data yet</div>
                        <div style="font-size:0.8rem;">
                            Runners will appear once OddsPro confirms the field.<br>
                            FormFav enrichment syncs every 5 minutes.
                        </div>
                    </div>`;
                setText("formGuideMeta", "0 runners");
            }

            await loadMeetingRaces();
        } catch (error) {
            console.error("Live race load failed:", error);
            const container = q("formGuideRows");
            if (container) container.innerHTML = `<div style="padding:24px;text-align:center;color:var(--red-1);">Failed to load race data.</div>`;
        }
    }

    // -------------------------------------------------------
    // Boot
    // -------------------------------------------------------

    document.addEventListener("DOMContentLoaded", () => {
        countdownTimer = setInterval(updateCountdownChip, 1000);

        const prevBtn = q("livePrevRace");
        const nextBtn = q("liveNextRace");
        if (prevBtn) prevBtn.addEventListener("click", () => navigateRace(-1));
        if (nextBtn) nextBtn.addEventListener("click", () => navigateRace(+1));

        const simBtn = q("liveRunSimBtn");
        if (simBtn) simBtn.addEventListener("click", runSimulation);

        const stakeIn = q("qbStake");
        const oddsIn  = q("qbOdds");
        if (stakeIn) stakeIn.addEventListener("input", calcReturns);
        if (oddsIn)  oddsIn.addEventListener("input", calcReturns);

        const placeBtn = q("qbPlaceBtn");
        if (placeBtn) placeBtn.addEventListener("click", placeBet);

        // Event delegation: runner summary row click → expand; bet button click
        document.addEventListener("click", (e) => {
            // FormFav sync button inside expand
            const syncBtn = e.target.closest(".formfav-sync-btn");
            if (syncBtn) {
                e.stopPropagation();
                const statusEl = syncBtn.nextElementSibling;
                syncBtn.disabled = true;
                syncBtn.textContent = "Syncing…";
                if (statusEl) statusEl.textContent = "";
                fetch("/api/formfav/sync", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ date: new Date().toISOString().slice(0, 10) })
                })
                .then(r => r.json())
                .then(data => {
                    if (statusEl) statusEl.textContent = data.ok ? "✓ Synced — reload race to see data" : "✗ Sync failed";
                    syncBtn.textContent = "⟳ Sync FormFav";
                    syncBtn.disabled = false;
                })
                .catch(() => {
                    if (statusEl) statusEl.textContent = "✗ Network error";
                    syncBtn.textContent = "⟳ Sync FormFav";
                    syncBtn.disabled = false;
                });
                return;
            }

            // Bet button inside expand
            const betBtn = e.target.closest(".expand-bet-btn");
            if (betBtn) {
                e.stopPropagation();
                const name = betBtn.dataset.runnerName || "";
                const odds = betBtn.dataset.runnerOdds || "";
                const runnerSel = q("qbRunner");
                if (runnerSel) {
                    for (const opt of runnerSel.options) {
                        if (opt.value === name) { runnerSel.value = name; break; }
                    }
                }
                if (q("qbOdds") && odds) q("qbOdds").value = odds;
                calcReturns();
                // Scroll to quick bet
                const qbBar = document.querySelector(".quick-bet-bar");
                if (qbBar) qbBar.scrollIntoView({ behavior: "smooth" });
                return;
            }

            // Runner summary row toggle
            const row = e.target.closest("[data-navigate='runner']");
            if (row) {
                selectRunnerRow(row);
                const card = row.closest(".runner-card");
                if (card) {
                    const box = card.dataset.box;
                    toggleRunnerExpand(box);
                }
            }
        });

        loadLiveRace();
    });

    // -------------------------------------------------------
    // On-demand live refresh & polling
    // Exposed as window globals so home/board pages can call them.
    // -------------------------------------------------------

    let _racePoller = null;
    let _currentPolledRace = null;

    function showRefreshBadge(msg) {
        let badge = document.getElementById("refresh-badge");
        if (!badge) {
            badge = document.createElement("div");
            badge.id = "refresh-badge";
            badge.style.cssText = (
                "position:fixed;top:12px;right:16px;z-index:9999;"
                + "background:var(--accent,#00bcd4);color:#fff;"
                + "padding:6px 14px;border-radius:6px;font-size:0.78rem;"
                + "letter-spacing:.05em;opacity:0;transition:opacity .3s;"
            );
            document.body.appendChild(badge);
        }
        badge.textContent = msg || "Updated";
        badge.style.opacity = "1";
        setTimeout(() => { badge.style.opacity = "0"; }, 3000);
    }

    async function selectRace(raceUid) {
        _currentPolledRace = raceUid;
        try {
            const data = await fetch(`/api/races/${encodeURIComponent(raceUid)}/live`)
                .then(r => r.json());
            if (data.refreshed) showRefreshBadge("Updated just now");
            if (data.race) liveRace = data.race;
            if (Array.isArray(data.runners) && data.runners.length) liveRunners = data.runners;
            try { renderRaceHeader(); } catch (_) {}
            try { renderAnalysis(); } catch (_) {}
            if (liveRunners.length) {
                try { buildRunnerCards(liveRunners, liveAnalysis); } catch (_) {}
            }
        } catch (err) {
            console.warn("selectRace fetch failed:", err);
        }
        startRacePoller(raceUid);
    }

    function startRacePoller(raceUid) {
        clearInterval(_racePoller);
        _racePoller = setInterval(async () => {
            if (_currentPolledRace !== raceUid) return;
            try {
                const data = await fetch(`/api/races/${encodeURIComponent(raceUid)}/live`)
                    .then(r => r.json());
                if (data.refreshed && data.race) {
                    liveRace = data.race;
                    if (Array.isArray(data.runners) && data.runners.length) liveRunners = data.runners;
                    showRefreshBadge("Updated just now");
                    try { renderRaceHeader(); } catch (_) {}
                    try { renderAnalysis(); } catch (_) {}
                    if (liveRunners.length) {
                        try { buildRunnerCards(liveRunners, liveAnalysis); } catch (_) {}
                    }
                }
            } catch (err) {
                console.warn("racePoller fetch failed:", err);
            }
        }, 60_000);
    }

    function deselectRace() {
        clearInterval(_racePoller);
        _currentPolledRace = null;
    }

    // Expose for external callers (board/home page)
    window.selectRace = selectRace;
    window.startRacePoller = startRacePoller;
    window.deselectRace = deselectRace;
    window.showRefreshBadge = showRefreshBadge;

})();
