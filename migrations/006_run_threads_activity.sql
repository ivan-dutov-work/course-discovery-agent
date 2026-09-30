ALTER TABLE run_threads
    ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_run_threads_activity ON run_threads (last_activity_at);
