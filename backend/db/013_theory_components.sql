-- Migration 013: Theory Components for Lecture Studio

CREATE TABLE IF NOT EXISTS theory_components (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id           TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
    primary_chunk_id    UUID REFERENCES chunks(id) ON DELETE SET NULL,
    name                TEXT NOT NULL,
    component_type      TEXT NOT NULL DEFAULT 'theory'
                            CHECK (component_type IN ('theory', 'concept', 'law', 'mechanism', 'operator', 'observation')),
    summary             TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'candidate'
                            CHECK (status IN ('candidate', 'draft', 'teacher_reviewed', 'rejected')),
    source_chunks       JSONB NOT NULL DEFAULT '[]'::jsonb,
    inputs              JSONB NOT NULL DEFAULT '[]'::jsonb,
    outputs             JSONB NOT NULL DEFAULT '[]'::jsonb,
    preconditions       JSONB NOT NULL DEFAULT '[]'::jsonb,
    constraints         JSONB NOT NULL DEFAULT '[]'::jsonb,
    invalid_conditions  JSONB NOT NULL DEFAULT '[]'::jsonb,
    dependencies        JSONB NOT NULL DEFAULT '[]'::jsonb,
    blackbox_policy     JSONB NOT NULL DEFAULT jsonb_build_object('default_level', 'summary', 'expand_if_unlearned', true),
    validation_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    teacher_notes       TEXT NOT NULL DEFAULT '',
    created_by          UUID REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_theory_components_course ON theory_components(course_id);
CREATE INDEX IF NOT EXISTS idx_theory_components_chunk ON theory_components(primary_chunk_id);
CREATE INDEX IF NOT EXISTS idx_theory_components_status ON theory_components(status);

CREATE TABLE IF NOT EXISTS theory_component_links (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id             TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
    source_component_id   UUID NOT NULL REFERENCES theory_components(id) ON DELETE CASCADE,
    target_component_id   UUID NOT NULL REFERENCES theory_components(id) ON DELETE CASCADE,
    link_type             TEXT NOT NULL DEFAULT 'output_to_input'
                              CHECK (link_type IN ('output_to_input', 'requires', 'depends_on', 'conflicts_with', 'analogous_to')),
    status                TEXT NOT NULL DEFAULT 'candidate'
                              CHECK (status IN ('candidate', 'valid', 'warning', 'conflict', 'rejected')),
    validation_result     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by            UUID REFERENCES users(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_theory_component_links_course ON theory_component_links(course_id);
CREATE INDEX IF NOT EXISTS idx_theory_component_links_source ON theory_component_links(source_component_id);
CREATE INDEX IF NOT EXISTS idx_theory_component_links_target ON theory_component_links(target_component_id);
