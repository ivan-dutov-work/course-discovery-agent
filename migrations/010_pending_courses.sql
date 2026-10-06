CREATE TABLE IF NOT EXISTS pending_courses (
    run_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    candidate JSONB NOT NULL,
    validation JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, canonical_url)
);

CREATE INDEX IF NOT EXISTS idx_pending_courses_created_at ON pending_courses (created_at);
