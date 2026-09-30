CREATE TABLE IF NOT EXISTS run_threads (
    thread_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_run_threads_user ON run_threads (user_id);
