-- ================================================================
-- DEMONPULSE V8 - MIGRATION 002: Auth, Signals, Audit
-- Run AFTER 001_complete_schema.sql
-- ================================================================

-- ----------------------------------------------------------------
-- USERS (role-based access)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator' CHECK (role IN ('admin','operator','viewer')),
    active BOOLEAN DEFAULT true,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- ----------------------------------------------------------------
-- SIGNALS (generated per race)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid TEXT UNIQUE NOT NULL,
    signal TEXT NOT NULL CHECK (signal IN ('SNIPER','VALUE','GEM','WATCH','RISK','NO_BET')),
    confidence DECIMAL(5,3),
    ev DECIMAL(6,3),
    alert_level TEXT DEFAULT 'NONE',
    hot_bet BOOLEAN DEFAULT false,
    risk_flags JSONB DEFAULT '[]',
    top_runner TEXT,
    top_box INTEGER,
    top_odds DECIMAL(6,2),
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_race_uid ON signals(race_uid);
CREATE INDEX IF NOT EXISTS idx_signals_signal ON signals(signal);
CREATE INDEX IF NOT EXISTS idx_signals_alert_level ON signals(alert_level);

-- ----------------------------------------------------------------
-- AUDIT LOG
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    username TEXT,
    event_type TEXT NOT NULL,
    resource TEXT,
    data JSONB,
    ip TEXT,
    severity TEXT DEFAULT 'INFO' CHECK (severity IN ('INFO','WARN','ERROR','CRITICAL')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_severity ON audit_log(severity);

-- ----------------------------------------------------------------
-- EXTEND BET_LOG: add placed_by
-- ----------------------------------------------------------------
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS placed_by TEXT;
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS signal TEXT;
ALTER TABLE bet_log ADD COLUMN IF NOT EXISTS exotic_type TEXT;

-- ----------------------------------------------------------------
-- EXOTICS SUGGESTIONS LOG
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exotic_suggestions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    race_uid TEXT NOT NULL,
    signal TEXT,
    exotic_type TEXT,
    selections JSONB,
    cost DECIMAL(10,2),
    est_return DECIMAL(10,2),
    risk_level TEXT,
    accepted BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exotic_race_uid ON exotic_suggestions(race_uid);

-- ----------------------------------------------------------------
-- SYSTEM STATE: add v8 fields
-- ----------------------------------------------------------------
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS confidence_threshold DECIMAL(4,2) DEFAULT 0.65;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS ev_threshold DECIMAL(4,2) DEFAULT 0.08;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS staking_mode TEXT DEFAULT 'KELLY';
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS tempo_weight DECIMAL(4,2) DEFAULT 1.0;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS traffic_penalty DECIMAL(4,2) DEFAULT 0.8;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS closer_boost DECIMAL(4,2) DEFAULT 1.1;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS fade_penalty DECIMAL(4,2) DEFAULT 0.9;
ALTER TABLE system_state ADD COLUMN IF NOT EXISTS simulation_depth INTEGER DEFAULT 1000;

-- ----------------------------------------------------------------
-- ROW LEVEL SECURITY (recommended for production)
-- ----------------------------------------------------------------
-- ALTER TABLE users ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
-- (configure via Supabase dashboard with service role key)

-- ================================================================
-- DONE: V8 schema migration complete
-- ================================================================
