-- Migration 013: Theory Components for Lecture Studio
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。

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

CREATE TABLE IF NOT EXISTS theory_claims (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id        TEXT NOT NULL DEFAULT '',
    chunk_id           UUID REFERENCES chunks(id) ON DELETE CASCADE,
    source_scope       JSONB NOT NULL DEFAULT '{}'::jsonb,
    claim_type         TEXT NOT NULL DEFAULT 'diagnostic_claim',
    text               TEXT NOT NULL,
    normalized_text    TEXT NOT NULL DEFAULT '',
    concepts           JSONB NOT NULL DEFAULT '[]'::jsonb,
    equation           JSONB NOT NULL DEFAULT '{}'::jsonb,
    support_status     TEXT NOT NULL DEFAULT 'source_backed',
    evidence_text      TEXT NOT NULL DEFAULT '',
    review_status      TEXT NOT NULL DEFAULT 'teacher_review_required',
    created_by         UUID REFERENCES users(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_theory_claims_document ON theory_claims(document_id);
CREATE INDEX IF NOT EXISTS idx_theory_claims_chunk ON theory_claims(chunk_id);
CREATE INDEX IF NOT EXISTS idx_theory_claims_review ON theory_claims(review_status);
ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS equation JSONB NOT NULL DEFAULT '{}'::jsonb;

-- claim_type の CHECK を拡張版（equation_* 系5語彙を含む17値）にする。
-- 既に拡張済みであれば何もしない（DROP→ADD の往復を毎起動発生させない）。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'theory_claims_claim_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%equation_definition%%'
    ) THEN
        ALTER TABLE theory_claims DROP CONSTRAINT theory_claims_claim_type_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'theory_claims_claim_type_check'
    ) THEN
        ALTER TABLE theory_claims ADD CONSTRAINT theory_claims_claim_type_check CHECK (claim_type IN (
            'definition', 'assumption', 'approximation', 'equation', 'relation',
            'derivation_step', 'observable_definition', 'correction',
            'uncertainty', 'limitation', 'result', 'diagnostic_claim',
            'equation_definition', 'equation_relation', 'equation_transformation',
            'equation_approximation', 'equation_constraint'
        ));
    END IF;
END $$;

ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS source_scope JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS evidence_claims JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS maturity_level TEXT NOT NULL DEFAULT 'paper_claim';
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS maturity_source TEXT NOT NULL DEFAULT 'llm_proposed';
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'teacher_review_required';
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS cautions JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS connectors JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS component_type_text TEXT NOT NULL DEFAULT '';
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS internal_flow JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS duplicate_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS idx_theory_components_review ON theory_components(review_status);

CREATE TABLE IF NOT EXISTS theory_review_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    old_status      TEXT NOT NULL DEFAULT '',
    new_status      TEXT NOT NULL DEFAULT '',
    changed_by      UUID REFERENCES users(id),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_theory_review_events_entity ON theory_review_events(entity_type, entity_id);

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

CREATE TABLE IF NOT EXISTS theory_component_graphs (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id          TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
    document_id        TEXT NOT NULL,
    scope              JSONB NOT NULL DEFAULT jsonb_build_object('level', 'paper'),
    graph_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_results JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_by         UUID REFERENCES users(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(course_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_theory_component_graphs_course ON theory_component_graphs(course_id);
CREATE INDEX IF NOT EXISTS idx_theory_component_graphs_document ON theory_component_graphs(document_id);
