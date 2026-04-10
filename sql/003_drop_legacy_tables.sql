-- sql/003_drop_legacy_tables.sql
-- Drop legacy OddsPro/FormFav tables no longer used in the new Claude-powered pipeline.

DROP TABLE IF EXISTS formfav_race_enrichment;
DROP TABLE IF EXISTS formfav_runner_enrichment;
DROP TABLE IF EXISTS market_snapshots;
DROP TABLE IF EXISTS pipeline_state;
