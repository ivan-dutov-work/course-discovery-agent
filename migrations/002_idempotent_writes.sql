DELETE FROM course_evidence a
USING course_evidence b
WHERE a.id > b.id
  AND a.course_id = b.course_id
  AND a.source_url = b.source_url
  AND a.quote_or_summary = b.quote_or_summary;

CREATE UNIQUE INDEX IF NOT EXISTS uq_course_evidence_content
    ON course_evidence (course_id, source_url, quote_or_summary);

ALTER TABLE recommendation_events
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_recommendation_events_idempotency_key
    ON recommendation_events (idempotency_key);
