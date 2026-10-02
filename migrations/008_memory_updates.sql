CREATE TABLE IF NOT EXISTS memory_updates (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    patch JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_updates_user ON memory_updates (user_id);
