-- ================================================================
-- DEMONPULSE V8 — OPTIONAL VIEWS
-- sql/003_views_optional.sql
-- ================================================================
-- Convenience views for reporting and dashboards.
-- All views use CREATE OR REPLACE — safe to re-run.
--
-- Run AFTER 001_canonical_schema.sql and 002_indexes_constraints.sql.
-- ================================================================

-- ----------------------------------------------------------------
-- v_active_races
-- Today's upcoming/open races with runner count.
-- ----------------------------------------------------------------
CREATE OR REPLACE VIEW v_active_races AS
SELECT
    r.id,
    r.race_uid,
    r.date,
    r.track,
    r.code,
    r.race_num,
    r.jump_time,
    r.status,
    r.grade,
    r.distance,
    COUNT(ru.id)  AS runner_count,
    COUNT(ru.id) FILTER (WHERE ru.scratched = FALSE) AS active_runners
FROM today_races r
LEFT JOIN today_runners ru ON ru.race_uid = r.race_uid
WHERE r.date = CURRENT_DATE
  AND r.status IN ('upcoming', 'open')
GROUP BY r.id, r.race_uid, r.date, r.track, r.code,
         r.race_num, r.jump_time, r.status, r.grade, r.distance
ORDER BY r.jump_time;

-- ----------------------------------------------------------------
-- v_todays_results
-- Official results for today with race context.
-- ----------------------------------------------------------------
CREATE OR REPLACE VIEW v_todays_results AS
SELECT
    rl.id,
    rl.race_uid,
    rl.date,
    rl.track,
    rl.code,
    rl.race_num,
    rl.winner,
    rl.winner_box,
    rl.win_price,
    rl.place_2,
    rl.place_3,
    rl.margin,
    rl.winning_time,
    r.grade,
    r.distance
FROM results_log rl
LEFT JOIN today_races r ON r.race_uid = rl.race_uid
WHERE rl.date = CURRENT_DATE
ORDER BY rl.track, rl.race_num;

-- ----------------------------------------------------------------
-- v_prediction_accuracy
-- Per-model prediction accuracy summary.
-- ----------------------------------------------------------------
CREATE OR REPLACE VIEW v_prediction_accuracy AS
SELECT
    model_version,
    race_code,
    COUNT(*)                                           AS total_races,
    SUM(CASE WHEN winner_hit  THEN 1 ELSE 0 END)      AS winner_hits,
    SUM(CASE WHEN top2_hit    THEN 1 ELSE 0 END)      AS top2_hits,
    SUM(CASE WHEN top3_hit    THEN 1 ELSE 0 END)      AS top3_hits,
    ROUND(
        SUM(CASE WHEN winner_hit THEN 1 ELSE 0 END)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 2
    )                                                  AS winner_pct,
    MIN(evaluated_at)                                    AS first_eval,
    MAX(evaluated_at)                                    AS last_eval
FROM learning_evaluations
GROUP BY model_version, race_code
ORDER BY model_version, race_code;

-- ----------------------------------------------------------------
-- v_daily_betting_summary
-- Per-date betting P/L summary.
-- ----------------------------------------------------------------
CREATE OR REPLACE VIEW v_daily_betting_summary AS
SELECT
    date,
    COUNT(*)                                            AS total_bets,
    SUM(CASE WHEN result = 'WIN'  THEN 1 ELSE 0 END)   AS wins,
    SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END)   AS losses,
    ROUND(SUM(COALESCE(pl, 0))::NUMERIC, 2)             AS total_pl,
    ROUND(AVG(COALESCE(pl, 0))::NUMERIC, 2)             AS avg_pl
FROM bet_log
GROUP BY date
ORDER BY date DESC;

-- ----------------------------------------------------------------
-- v_backtest_summary
-- Aggregated backtest run performance.
-- ----------------------------------------------------------------
CREATE OR REPLACE VIEW v_backtest_summary AS
SELECT
    run_id,
    model_version,
    race_code,
    date_from,
    date_to,
    total_races,
    winner_hits,
    ROUND(winner_accuracy * 100, 2)  AS winner_pct,
    status,
    created_at
FROM backtest_runs
ORDER BY created_at DESC;

-- ================================================================
-- DONE
-- ================================================================
