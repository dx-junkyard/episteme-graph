-- Migration 052: figure presentation classification and teacher review (#496)
--
-- Vision suggestions and mode-specific analysis are stored separately from a
-- teacher override.  Re-extraction may refresh suggested_* / analysis_profile,
-- but must not overwrite reviewed_mode or its audit attribution.

ALTER TABLE document_figures ADD COLUMN IF NOT EXISTS suggested_mode TEXT NOT NULL DEFAULT 'unknown'
    CHECK (suggested_mode IN ('functional_diagram','data_plot','descriptive_image','mixed','unknown'));
ALTER TABLE document_figures ADD COLUMN IF NOT EXISTS mode_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE document_figures ADD COLUMN IF NOT EXISTS analysis_profile JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE document_figures ADD COLUMN IF NOT EXISTS reviewed_mode TEXT
    CHECK (reviewed_mode IS NULL OR reviewed_mode IN ('functional_diagram','data_plot','descriptive_image','mixed','unknown'));
ALTER TABLE document_figures ADD COLUMN IF NOT EXISTS mode_review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (mode_review_status IN ('pending','reviewed'));
ALTER TABLE document_figures ADD COLUMN IF NOT EXISTS mode_reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE document_figures ADD COLUMN IF NOT EXISTS mode_reviewed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_document_figures_presentation_mode
    ON document_figures(document_id, suggested_mode, reviewed_mode);

