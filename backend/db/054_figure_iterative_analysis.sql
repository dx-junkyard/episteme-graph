-- Migration 054: iterative hypothesis-verification analysis for figures (#499).
--
-- AI suggestion layer only, mirroring analysis_profile (052/053): replaced on
-- re-analysis, reset on re-extraction. No teacher confirmation column is
-- added here — confirmation continues to flow only through the existing
-- candidate annotation commit path (element_annotations).

ALTER TABLE document_figures
    ADD COLUMN IF NOT EXISTS iterative_analysis JSONB NOT NULL DEFAULT '{}'::jsonb;
