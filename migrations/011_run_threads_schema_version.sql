ALTER TABLE run_threads
    ADD COLUMN IF NOT EXISTS schema_version INTEGER;
