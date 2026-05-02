-- Migration 014: Section assembly status tracking for retry support

CREATE TABLE IF NOT EXISTS section_assembly_status (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id                   TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
    document_id                 TEXT NOT NULL,
    section_id                  TEXT NOT NULL,
    component_assembly_status   TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (component_assembly_status IN ('pending', 'success', 'failed', 'skipped')),
    error_type                  TEXT NOT NULL DEFAULT '',
    error_message               TEXT NOT NULL DEFAULT '',
    components_generated        INT NOT NULL DEFAULT 0,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(course_id, document_id, section_id)
);

CREATE INDEX IF NOT EXISTS idx_section_assembly_status_course ON section_assembly_status(course_id);
CREATE INDEX IF NOT EXISTS idx_section_assembly_status_status ON section_assembly_status(component_assembly_status);
