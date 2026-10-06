CREATE TABLE IF NOT EXISTS chat_updates (
    update_id BIGINT PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_identities (
    chat_id BIGINT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_inbox (
    update_id BIGINT PRIMARY KEY REFERENCES chat_updates (update_id),
    user_id TEXT NOT NULL,
    text TEXT NOT NULL,
    batch_id BIGINT,
    attempts INTEGER NOT NULL DEFAULT 0,
    done_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_inbox_open ON chat_inbox (user_id) WHERE done_at IS NULL;

CREATE TABLE IF NOT EXISTS chat_user_leases (
    user_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    locked_until TIMESTAMPTZ NOT NULL
);
