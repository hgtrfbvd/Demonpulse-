-- DemonPulse v78 Migration 004: Claude Pipeline
-- Run in Supabase SQL Editor before deploying v78.
-- Safe to re-run.

BEGIN;

-- =========================================================
-- Drop FormFav and OddsPro legacy tables
-- =========================================================
DROP TABLE IF EXISTS test_formfav_race_enrichment CASCADE;
DROP TABLE IF EXISTS test_formfav_runner_enrichment CASCADE;
DROP TABLE IF EXISTS test_formfav_debug_stats CASCADE;
DROP TABLE IF EXISTS test_runner_connection_stats CASCADE;
DROP TABLE IF EXISTS formfav_race_enrichment CASCADE;
DROP TABLE IF EXISTS formfav_runner_enrichment CASCADE;
DROP TABLE IF EXISTS formfav_debug_stats CASCADE;
DROP TABLE IF EXISTS runner_connection_stats CASCADE;
DROP TABLE IF EXISTS pipeline_state CASCADE;

-- =========================================================
-- today_races: relax oddspro_race_id NOT NULL, update source default
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'today_races'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'today_races'
              AND column_name = 'oddspro_race_id'
        ) THEN
            ALTER TABLE public.today_races
                ALTER COLUMN oddspro_race_id DROP NOT NULL,
                ALTER COLUMN oddspro_race_id SET DEFAULT '';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'today_races'
              AND column_name = 'source'
        ) THEN
            ALTER TABLE public.today_races
                ALTER COLUMN source SET DEFAULT 'claude';
        END IF;

        ALTER TABLE public.today_races
            ADD COLUMN IF NOT EXISTS derived_json JSONB,
            ADD COLUMN IF NOT EXISTS raw_json JSONB,
            ADD COLUMN IF NOT EXISTS slug TEXT DEFAULT '';
    END IF;
END $$;

-- =========================================================
-- test_today_races: same changes
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'test_today_races'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'test_today_races'
              AND column_name = 'oddspro_race_id'
        ) THEN
            ALTER TABLE public.test_today_races
                ALTER COLUMN oddspro_race_id DROP NOT NULL,
                ALTER COLUMN oddspro_race_id SET DEFAULT '';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'test_today_races'
              AND column_name = 'source'
        ) THEN
            ALTER TABLE public.test_today_races
                ALTER COLUMN source SET DEFAULT 'claude';
        END IF;

        ALTER TABLE public.test_today_races
            ADD COLUMN IF NOT EXISTS derived_json JSONB,
            ADD COLUMN IF NOT EXISTS raw_json JSONB,
            ADD COLUMN IF NOT EXISTS slug TEXT DEFAULT '';
    END IF;
END $$;

-- =========================================================
-- today_runners: add new derived and form fields
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'today_runners'
    ) THEN
        ALTER TABLE public.today_runners
            ADD COLUMN IF NOT EXISTS early_speed_rating NUMERIC(5,2),
            ADD COLUMN IF NOT EXISTS consistency_rating NUMERIC(5,2),
            ADD COLUMN IF NOT EXISTS finish_strength_rating NUMERIC(5,2),
            ADD COLUMN IF NOT EXISTS split_time NUMERIC(7,3),
            ADD COLUMN IF NOT EXISTS last4 TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS last_start_position INTEGER,
            ADD COLUMN IF NOT EXISTS last_start_time NUMERIC(7,3),
            ADD COLUMN IF NOT EXISTS days_since_last_run INTEGER,
            ADD COLUMN IF NOT EXISTS first_up BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS second_up BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS techform_rating INTEGER,
            ADD COLUMN IF NOT EXISTS track_record TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS distance_record TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS track_distance_record TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS wet_record TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS form_last5 TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS last_start_margin NUMERIC(6,2),
            ADD COLUMN IF NOT EXISTS last_start_distance INTEGER,
            ADD COLUMN IF NOT EXISTS derived_json JSONB;
    END IF;
END $$;

-- =========================================================
-- test_today_runners: same new columns
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'test_today_runners'
    ) THEN
        ALTER TABLE public.test_today_runners
            ADD COLUMN IF NOT EXISTS early_speed_rating NUMERIC(5,2),
            ADD COLUMN IF NOT EXISTS consistency_rating NUMERIC(5,2),
            ADD COLUMN IF NOT EXISTS finish_strength_rating NUMERIC(5,2),
            ADD COLUMN IF NOT EXISTS split_time NUMERIC(7,3),
            ADD COLUMN IF NOT EXISTS last4 TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS last_start_position INTEGER,
            ADD COLUMN IF NOT EXISTS last_start_time NUMERIC(7,3),
            ADD COLUMN IF NOT EXISTS days_since_last_run INTEGER,
            ADD COLUMN IF NOT EXISTS first_up BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS second_up BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS techform_rating INTEGER,
            ADD COLUMN IF NOT EXISTS track_record TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS distance_record TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS track_distance_record TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS wet_record TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS form_last5 TEXT DEFAULT '',
            ADD COLUMN IF NOT EXISTS last_start_margin NUMERIC(6,2),
            ADD COLUMN IF NOT EXISTS last_start_distance INTEGER,
            ADD COLUMN IF NOT EXISTS derived_json JSONB;
    END IF;
END $$;

-- =========================================================
-- meetings: update source default
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'meetings'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'meetings' AND column_name = 'source'
    ) THEN
        ALTER TABLE public.meetings
            ALTER COLUMN source SET DEFAULT 'claude';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'test_meetings'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'test_meetings' AND column_name = 'source'
    ) THEN
        ALTER TABLE public.test_meetings
            ALTER COLUMN source SET DEFAULT 'claude';
    END IF;
END $$;

-- =========================================================
-- results_log: update source default
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'results_log'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'results_log' AND column_name = 'source'
    ) THEN
        ALTER TABLE public.results_log
            ALTER COLUMN source SET DEFAULT 'claude';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'test_results_log'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'test_results_log' AND column_name = 'source'
    ) THEN
        ALTER TABLE public.test_results_log
            ALTER COLUMN source SET DEFAULT 'claude';
    END IF;
END $$;

-- =========================================================
-- sectional_snapshots: update source defaults
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'sectional_snapshots'
    ) THEN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'sectional_snapshots' AND column_name = 'source'
        ) THEN
            ALTER TABLE public.sectional_snapshots
                ALTER COLUMN source SET DEFAULT 'claude';
        END IF;

        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'sectional_snapshots' AND column_name = 'source_type'
        ) THEN
            ALTER TABLE public.sectional_snapshots
                ALTER COLUMN source_type SET DEFAULT 'claude_result';
        END IF;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'test_sectional_snapshots'
    ) THEN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'test_sectional_snapshots' AND column_name = 'source'
        ) THEN
            ALTER TABLE public.test_sectional_snapshots
                ALTER COLUMN source SET DEFAULT 'claude';
        END IF;

        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'test_sectional_snapshots' AND column_name = 'source_type'
        ) THEN
            ALTER TABLE public.test_sectional_snapshots
                ALTER COLUMN source_type SET DEFAULT 'claude_result';
        END IF;
    END IF;
END $$;

-- =========================================================
-- learning_evaluations: update evaluation_source default
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'learning_evaluations'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'learning_evaluations' AND column_name = 'evaluation_source'
    ) THEN
        ALTER TABLE public.learning_evaluations
            ALTER COLUMN evaluation_source SET DEFAULT 'claude';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'test_learning_evaluations'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'test_learning_evaluations' AND column_name = 'evaluation_source'
    ) THEN
        ALTER TABLE public.test_learning_evaluations
            ALTER COLUMN evaluation_source SET DEFAULT 'claude';
    END IF;
END $$;

-- =========================================================
-- race_shape_snapshots: keep column, ensure default FALSE
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'race_shape_snapshots'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'race_shape_snapshots' AND column_name = 'formfav_enrichment_used'
    ) THEN
        ALTER TABLE public.race_shape_snapshots
            ALTER COLUMN formfav_enrichment_used SET DEFAULT FALSE;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'test_race_shape_snapshots'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'test_race_shape_snapshots' AND column_name = 'formfav_enrichment_used'
    ) THEN
        ALTER TABLE public.test_race_shape_snapshots
            ALTER COLUMN formfav_enrichment_used SET DEFAULT FALSE;
    END IF;
END $$;

-- =========================================================
-- New indexes
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'today_races'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_today_races_slug
            ON public.today_races (slug)
            WHERE slug <> '';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'today_runners'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_today_runners_early_speed
            ON public.today_runners (race_uid, early_speed_rating);
    END IF;
END $$;

-- =========================================================
-- Fix source labels on existing rows
-- =========================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'today_races'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'today_races' AND column_name = 'source'
    ) THEN
        UPDATE public.today_races
        SET source = 'claude'
        WHERE source = 'oddspro';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'meetings'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'meetings' AND column_name = 'source'
    ) THEN
        UPDATE public.meetings
        SET source = 'claude'
        WHERE source = 'oddspro';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'results_log'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'results_log' AND column_name = 'source'
    ) THEN
        UPDATE public.results_log
        SET source = 'claude'
        WHERE source = 'oddspro';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'sectional_snapshots'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'sectional_snapshots' AND column_name = 'source'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'sectional_snapshots' AND column_name = 'source_type'
    ) THEN
        UPDATE public.sectional_snapshots
        SET source = 'claude',
            source_type = 'claude_result'
        WHERE source IN ('oddspro', 'oddspro_result');
    END IF;
END $$;

COMMIT;
