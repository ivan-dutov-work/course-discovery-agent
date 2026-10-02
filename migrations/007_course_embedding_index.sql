CREATE INDEX IF NOT EXISTS courses_course_embedding_cosine_idx
    ON courses USING hnsw (course_embedding vector_cosine_ops);
