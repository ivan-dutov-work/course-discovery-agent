ALTER TABLE courses ADD COLUMN IF NOT EXISTS content_hash TEXT
    GENERATED ALWAYS AS (md5(title || E'\n' || coalesce(description, ''))) STORED;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS tagged_content_hash TEXT;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS tagging_outcome TEXT;
