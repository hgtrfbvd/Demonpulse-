# Legacy

This directory contains modules that have been superseded by the new architecture.

## Files

### `claude_scraper.py`

**Superseded by:** `modules/dogs_capture/capture_pipeline.py` + `modules/dogs_analysis/v7_engine.py`

The original Anthropic/Claude-based scraper used for the horse pipeline (Phase 2).
Kept here for reference. The greyhound pipeline now uses the Playwright-based
module system under `modules/`.

The `anthropic` dependency has been removed from `requirements.txt` as part of this change.
If the horse pipeline (Phase 2) is re-enabled, the dependency and this module will need
to be reinstated.
