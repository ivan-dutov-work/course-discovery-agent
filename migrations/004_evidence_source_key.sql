DELETE FROM course_evidence a
USING course_evidence b
WHERE a.id < b.id
  AND a.course_id = b.course_id
  AND a.source_url = b.source_url;

DROP INDEX IF EXISTS uq_course_evidence_content;

CREATE UNIQUE INDEX IF NOT EXISTS uq_course_evidence_source
    ON course_evidence (course_id, source_url);
