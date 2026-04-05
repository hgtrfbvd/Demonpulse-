# LEGACY MIGRATIONS — DO NOT USE

These SQL files are **superseded** by `sql/001_canonical_schema.sql`.

**Do NOT run any file in this directory.**  
They are kept for historical reference only.

## What replaced them

| Old file | Status |
|---|---|
| `001_complete_schema.sql` | Replaced by `sql/001_canonical_schema.sql` |
| `002_v8_auth_signals_audit.sql` | Merged into `sql/001_canonical_schema.sql` |
| `003_test_tables.sql` | Merged into `sql/001_canonical_schema.sql` |
| `004_user_management.sql` | Merged into `sql/001_canonical_schema.sql` |
| `005_simulation_log.sql` | Merged into `sql/001_canonical_schema.sql` |
| `006_session_id_backfill.sql` | Merged into `sql/001_canonical_schema.sql` |

## Migration instructions

See `docs/supabase_rebuild_notes.md` for full instructions.

**TL;DR:** Paste `sql/001_canonical_schema.sql` into the Supabase SQL Editor.  
It is fully idempotent and safe to run on an existing database.
