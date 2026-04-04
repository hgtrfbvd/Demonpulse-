"""
ai/ - DemonPulse Intelligence Layer (Phase 3)
==============================================
Provides feature extraction, prediction, learning storage,
and contamination-safe backtesting on top of the live engine.

Architecture rules:
  - OddsPro remains the authoritative source for all historical and training truth
  - FormFav enrichment may be included only when clearly flagged as non-authoritative
  - Predictions never overwrite official race/result tables
  - Backtesting never uses future information (results are evaluation-only)
  - Prediction lineage is preserved cleanly through feature_snapshots
"""
