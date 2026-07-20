-- Migration 053: teacher-reviewed figure analysis profiles.
--
-- ``analysis_profile`` remains the replaceable AI suggestion.  A teacher
-- confirmation is stored separately so later re-analysis can refresh the AI
-- candidate without overwriting the reviewed functions/ports/connections.

ALTER TABLE document_figures
    ADD COLUMN IF NOT EXISTS reviewed_analysis_mode TEXT
    CHECK (
        reviewed_analysis_mode IS NULL OR reviewed_analysis_mode IN (
            'functional_diagram','data_plot','descriptive_image','mixed','unknown'
        )
    );
ALTER TABLE document_figures
    ADD COLUMN IF NOT EXISTS reviewed_analysis_profile JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE document_figures
    ADD COLUMN IF NOT EXISTS analysis_review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (analysis_review_status IN ('pending','reviewed'));
ALTER TABLE document_figures
    ADD COLUMN IF NOT EXISTS analysis_reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE document_figures
    ADD COLUMN IF NOT EXISTS analysis_reviewed_at TIMESTAMPTZ;
ALTER TABLE document_figures
    ADD COLUMN IF NOT EXISTS analysis_review_source_annotation_id UUID
    REFERENCES element_annotations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_document_figures_analysis_review
    ON document_figures(document_id, analysis_review_status, reviewed_analysis_mode);
