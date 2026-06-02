CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    preferred_providers TEXT[] NOT NULL DEFAULT '{}',
    avoided_providers TEXT[] NOT NULL DEFAULT '{}',
    preferred_languages TEXT[] NOT NULL DEFAULT '{}',
    budget_preference TEXT,
    certificate_importance TEXT,
    preferred_level TEXT,
    preferred_course_length TEXT,
    learning_style_notes TEXT,
    career_goals TEXT[] NOT NULL DEFAULT '{}',
    raw_memory_json JSONB NOT NULL DEFAULT '{}',
    profile_embedding vector(1536),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS courses (
    id BIGSERIAL PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    provider TEXT,
    description TEXT,
    topics TEXT[] NOT NULL DEFAULT '{}',
    level TEXT,
    language TEXT,
    price_text TEXT,
    is_free BOOLEAN,
    has_certificate BOOLEAN,
    rating DOUBLE PRECISION,
    published_or_updated TEXT,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    validation_status TEXT NOT NULL DEFAULT 'uncertain',
    validation_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    use_count INTEGER NOT NULL DEFAULT 0,
    course_embedding vector(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS course_evidence (
    id BIGSERIAL PRIMARY KEY,
    course_id BIGINT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    quote_or_summary TEXT NOT NULL,
    supports TEXT[] NOT NULL DEFAULT '{}',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_type TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recommendation_events (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    course_id BIGINT REFERENCES courses(id) ON DELETE SET NULL,
    query TEXT NOT NULL,
    rank INTEGER NOT NULL,
    recommendation_reason TEXT,
    accepted BOOLEAN NOT NULL DEFAULT FALSE,
    rejected BOOLEAN NOT NULL DEFAULT FALSE,
    feedback_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research_runs (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    thread_id TEXT NOT NULL,
    query TEXT NOT NULL,
    parsed_filters_json JSONB NOT NULL DEFAULT '{}',
    research_plan_json JSONB NOT NULL DEFAULT '{}',
    queries_run INTEGER NOT NULL DEFAULT 0,
    cache_hits INTEGER NOT NULL DEFAULT 0,
    tavily_calls INTEGER NOT NULL DEFAULT 0,
    valid_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    uncertain_count INTEGER NOT NULL DEFAULT 0,
    replan_count INTEGER NOT NULL DEFAULT 0,
    unsupported_claim_count INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_courses_filters
    ON courses (validation_status, is_free, has_certificate, level, language, provider);

CREATE INDEX IF NOT EXISTS idx_course_evidence_course_id
    ON course_evidence (course_id);
