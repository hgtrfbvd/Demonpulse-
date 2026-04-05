-- [LEGACY] This file is superseded by sql/001_canonical_schema.sql.
-- Do NOT run this file. It is kept for historical reference only.
-- See docs/supabase_rebuild_notes.md for migration instructions.

-- ================================================================
-- DEMONPULSE V8 — MIGRATION 004: Full User Management
-- Run AFTER 001, 002, 003
-- ================================================================

-- ----------------------------------------------------------------
-- USER ACCOUNTS — per-user persistent data
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_accounts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    -- Bankroll
    bankroll    DECIMAL(12,2) DEFAULT 1000.00,
    total_pl    DECIMAL(12,2) DEFAULT 0.00,
    session_pl  DECIMAL(12,2) DEFAULT 0.00,
    peak_bank   DECIMAL(12,2) DEFAULT 1000.00,
    total_bets  INTEGER DEFAULT 0,
    total_wins  INTEGER DEFAULT 0,
    -- Settings / preferences (JSONB so admin can extend freely)
    settings    JSONB DEFAULT '{}',
    -- Alert preferences
    alerts      JSONB DEFAULT '{"hot_bet":true,"t10_alert":true,"t1_alert":true,"signal_sounds":false}',
    -- Notes admin can attach
    admin_notes TEXT,
    -- Timestamps
    last_session_reset  TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_accounts_user_id ON user_accounts(user_id);

-- ----------------------------------------------------------------
-- USER PERMISSIONS — per-user overrides on top of role defaults
-- admin can grant extra pages or revoke pages from a specific user
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_permissions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    -- granted: pages added beyond role default
    -- revoked:  pages removed from role default
    granted     TEXT[] DEFAULT '{}',
    revoked     TEXT[] DEFAULT '{}',
    -- Full permission set cache (recomputed on each change)
    effective   TEXT[] DEFAULT '{}',
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_by  TEXT
);

CREATE INDEX IF NOT EXISTS idx_user_permissions_user_id ON user_permissions(user_id);

-- ----------------------------------------------------------------
-- USER SESSIONS — active token tracking (enables force logout)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_jti   TEXT NOT NULL UNIQUE,   -- JWT "jti" claim (unique per token)
    ip_address  TEXT,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ,
    revoked     BOOLEAN DEFAULT false,
    revoked_at  TIMESTAMPTZ,
    revoked_by  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id   ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token_jti ON user_sessions(token_jti);
CREATE INDEX IF NOT EXISTS idx_sessions_revoked   ON user_sessions(revoked, expires_at);

-- ----------------------------------------------------------------
-- USER ACTIVITY LOG — per-user searchable action history
-- Complements audit_log (which is system-wide); this is user-centric
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_activity (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action      TEXT NOT NULL,          -- e.g. LOGIN, BET_PLACED, SETTING_CHANGED
    detail      JSONB,
    ip_address  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_activity_user_id    ON user_activity(user_id);
CREATE INDEX IF NOT EXISTS idx_user_activity_created_at ON user_activity(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_activity_action     ON user_activity(action);

-- ----------------------------------------------------------------
-- ALTER bet_log — add user_id FK so bets are owned by users
-- ----------------------------------------------------------------
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_bet_log_user_id ON bet_log(user_id);

-- ----------------------------------------------------------------
-- EXTEND users table — display name, email (optional)
-- ----------------------------------------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email        TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by   TEXT;   -- admin who created them
ALTER TABLE users ADD COLUMN IF NOT EXISTS login_count  INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_ip      TEXT;

-- ----------------------------------------------------------------
-- FUNCTION: bootstrap user_account row on user insert
-- ----------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_user_account()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO user_accounts (user_id)
    VALUES (NEW.id)
    ON CONFLICT (user_id) DO NOTHING;
    INSERT INTO user_permissions (user_id, effective)
    VALUES (NEW.id, '{}')
    ON CONFLICT (user_id) DO NOTHING;
    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    -- M-10: do not let trigger failure roll back user INSERT
    -- The upsert in create_user_full() handles missing rows safely
    RAISE WARNING 'create_user_account trigger failed for user %: %', NEW.id, SQLERRM;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_create_user_account ON users;
CREATE TRIGGER trg_create_user_account
    AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION create_user_account();

-- ----------------------------------------------------------------
-- Backfill existing users
-- ----------------------------------------------------------------
INSERT INTO user_accounts (user_id)
SELECT id FROM users
WHERE id NOT IN (SELECT user_id FROM user_accounts)
ON CONFLICT DO NOTHING;

INSERT INTO user_permissions (user_id, effective)
SELECT id, '{}'
FROM users
WHERE id NOT IN (SELECT user_id FROM user_permissions)
ON CONFLICT DO NOTHING;

-- ================================================================
-- DONE: Migration 004 complete
-- ================================================================
